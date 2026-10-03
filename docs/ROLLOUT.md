# План поэтапного включения (rollout) модулей мозга и тела

Для владельца и Hermes. Сверено с кодом: атрибуты классов реестра `brain/live_brain/modules.py`
(`FEATURE`, `CONFIG`, `ENABLED`, `REQUIRES`), `brain/world/goals.json`, профили `bots/bot0{1,2}/control`, плагины
`bots/plugins/*`, `server/conf/optional/*` (ветка `agent/rollout`, 2026-10-02).

**Ничего из этого документа в игре не проверено.** Последний отчёт Hermes — `docs/qa/HERMES-cbeb2a9.md`
(задание №9: распорядок rest/hunt по командам, боевые профили). Задания №10–16 в `docs/HANDOFF.md` выданы, отчётов по
ним нет. Значит, на VPS непроверенным остаётся всё после cbeb2a9, а не только ночные модули. «Работает» пишем только
после строки из `console.log`/`decisions.jsonl`, которая это показывает.

## Как включать и выключать

| Рычаг | Где | Что делает | Вступает в силу |
|---|---|---|---|
| `BRAIN_DISABLE=a,b,c` | `$LAB_ROOT/secrets/live_ro.env` (один файл на всех жителей) | модуль не создаётся (`mind.<ATTR> = None`), память и kv остаются. **Включить модуль с `enabled: false` нельзя** — только выключить | `scripts/lab stop brain && scripts/lab start brain` (тело продолжает играть) |
| `"<раздел>": {"enabled": …}` | `brain/world/goals.json` в checkout (`/opt/ro-bot-lab/src/live_ro-qa`) | включает/выключает модуль с `ENABLED` и флаги внутри раздела | то же; правка делает checkout «грязным»: прислать `git diff brain/world/goals.json`, откат — `git checkout -- brain/world/goals.json` |
| профиль OpenKore | `bots/bot0N/control/*.txt` | плагины (`sys.txt`), `buyAuto`, `items_control.txt` | перезапуск тела (`scripts/lab stop live botNN && scripts/lab start live botNN`) |
| сервер | `server/conf/optional/*.txt` → `conf/import` | правила rAthena для **всех** игроков | перезапуск char/map — только решение владельца |

Проверка до перезапуска (только чтение, добавлено этим планом):
```sh
scripts/lab modules bot01                                   # что создастся при текущем env и goals.json
scripts/lab modules bot02 --disable "home,explore"          # примерка строки следующего этапа без правки env
```
Команда печатает выключенные модули с причиной (`BRAIN_DISABLE=…`, `goals.json …enabled: false`, `нет модуля X`,
`нет других жителей`), список включённых, флаги `goals.json` и предупреждает об именах в `BRAIN_DISABLE`, которые ничего
не выключают (опечатка: `explorer` вместо `explore`, `market` вместо `market_day`, `look` вместо `gaze`).

Имена для `BRAIN_DISABLE` — это `FEATURE`, а не имя файла: `activity` (activity.py), `calendar` (world_calendar.py),
`explore` (explore.py), `market_day` (market.py), `world_bus` (world_bus.py). Ещё три имени выключают не модули, а
слои: `grammar` (грамматика реплик social), `weather` (погода в речи), `topics` (темы жизни мира в разговоре).
У `rumors` выключателя нет.

Зависимости (`REQUIRES`): выключение одного модуля выключает зависящие —
`routine` → `activity`, `explore`, `herbal`, `arrows`, `trek`; `party` → `crew`, `boss`, `trek`; `explore` → `boss`,
`trek`; `economy` → `orders`, `market_day`; `calendar` → `market_day`; `social` → `gaze`; `dream` → `savings`.
Модули с `REQUIRES: peers` (другие жители в `LAB_BOTS`) при одном боте не создаются.

## 1. Матрица

Колонка «по умолчанию» — по коду: `ENABLED` класса и `goals.json`. «Нужно» — модули/жители (из `REQUIRES`) и что
нужно от сервера/OpenKore. Риск: **тело** (смерть, застревание, уход с карты), **деньги** (зени/предметы),
**необр.** (необратимо), **люди** (видно/слышно посторонним), **сервер** (правка сервера). Этап — раздел 2.

### 1.1 Модули мозга (порядок создания `MODULES`)

| `BRAIN_DISABLE` | Что делает | По умолчанию | Нужно | Риск | Этап |
|---|---|---|---|---|---|
| `home` | дом из `homes.json`, сохранение у Kafra (jobChange `path home`, пункт «Save»), отдых в доме, учёт возрождения | вкл. (выключателя в goals нет) | мир; плагин jobChange; Kafra-диалог по тексту | тело: сам идёт к Kafra и говорит с NPC; точка сохранения в БД персонажа (обратимо новым сохранением) | 3 |
| `mood` | настроение −1..1 из фактов 48 ч: окраска фраз, пауза разговоров | вкл. (`mood.enabled` по умолч. true) | — | действий нет; может приглушить разговоры | 1 |
| `calendar` | день недели, праздники, дни рождения → множители мотивов, темы | вкл. | мир, `calendar.json` | действий нет; меняет выбор занятий | 1 |
| `career` | цель прогрессии в report/промпт; этап jobChange — только при `progression.auto_job_change` | вкл., авто-смена **выкл.** | мир; jobChange (при флаге) | при флаге — необратимая смена профессии | 0 (флаг — 7) |
| `routine` | распорядок: охота/отдых/сон (`relog`)/сервис/лавка, застревание | вкл. | мир; lockMap, sitAuto_idle, relog | тело; сон через relog → возможен «still recognizes» | 0 |
| `economy` | взаимопомощь `[need:]`, рынок `[offer:]`, почта RODEX, лавка Merchant | вкл. (есть раздел `economy`) | плагин economy, `dealAuto 3`; RODEX-пакеты не проверены | деньги между жителями; почта | 0 |
| `party` | группа `LR_<лидер>`, темп, поводок, помощь в опасности | вкл. | жители; `partyAuto` | низкий | 0 |
| `activity` | выбор занятия по мотивам, цепочки предусловий | вкл. | routine | — | 0 |
| `bonds` | встреча с продолжением, `friend_request`, весточки | вкл. | жители | список друзей на сервере | 0 |
| `crew` | карта группы вместе, чат группы, прогулка за лидером | вкл. | party | чат группы (видят участники) | 0 |
| `pets` | приручение, вылупление, корм | вкл. | плагин pets, `pet_autoFeed 1`; `buyAuto Pet Food` выкл. | деньги: тратит предмет приручения; пакеты не проверены | 0 |
| `social` | прогулки по Пронтере, разговоры шёпотом `[chat:]`, эмоции, реакции | вкл. | мир, жители | люди: эмоции видны рядом стоящим | 0 |
| (`rumors`) | слухи v2: автор, доверие, затухание, проверка слуха | вкл., **выключателя нет** | — | шёпоты жителям | 0 |
| `society` | эмоции на события, чат-комната-вывеска, ссоры/примирения | вкл. | жители | люди: вывеска видна в городе | 0 |
| `aims` | цели недели по фактам | вкл. | — | — | 0 |
| `guild` | основатель, согласие, `guild create`/`request`, чат гильдии | **выкл.** (`guild.enabled: false`) | Emperium (714) или `guild_no_emperium.txt` | необр., сервер, люди (чат гильдии) | 7 |
| `explore` | экспедиции на известные непосещённые карты, группой за лидером | **вкл. через goals.json** (`explore.enabled: true`; класс — false) | routine; `explore_reach.json`, `portals.txt` профиля | тело: новые карты, смерть, уход с карт охоты | 6 |
| `boss` | мини-босс Vocal/Eclipse группой | **выкл.** | мир, party, crew, explore | тело: опасно; наведения по имени нет | 7 |
| `strangers` | люди рядом: «знакомый в лицо», `e wav` днём в городе, шаблонный ответ на шёпот (при LLM off) | вкл. (раздела в goals нет) | — | люди: отвечает людям (≤ 3/ч, одному раз в 10 мин) | 5 |
| `world_bus` | шина `state/shared/world.sqlite`: публикация событий, новости | вкл. | — | общий SQLite; без шины многие модули молчат | 0 |
| `rivalry` | соперник недели, обгон, подначки (≤ 3/сутки) | вкл. | жители | шёпот/чат группы | 2 |
| `crowd` | `presence` в шине, штраф людных карт и занятий | вкл. | — | действий нет | 1 |
| `episodes` | «помнишь?» — эпизоды пары, тема `remember` | вкл. | жители | шёпоты | 2 |
| `tradition` | вечерний круг у фонтана 20–21 ч мира | вкл. | мир | тело: ходит в городе | 3 |
| `collection` | альбом карт и трофеи по `kill`/`loot`; карты альбома не предлагаются жителям | вкл. | — | действий нет (первый запуск молча) | 0 |
| `places` | имена мест в речи и летописи, консенсус в шине | вкл. | — | только текст | 0 |
| `gossip` | сплетни о жителях, репутация, остывание отношений | вкл. | жители | шёпоты с меткой `[gossip:]` | 2 |
| `habits` | привычки и скука за 7 суток | вкл. | — | действий нет; меняет выбор | 1 |
| `healer` | лекарь у собора: пост, вывеска «Лечу у собора», Heal по просьбе жителя `[heal:ask:]` **и человека** (слово heal/лечи), Blessing/Inc AGI | вкл.; роль — Acolyte-ветка с generosity ≥ 0.6 и Heal (у Vera 0.8) | мир; `skill_on_player` → `sp`, chat room | люди: отвечает на слова людей и кастует на них; SP; тело уходит к собору | 5 |
| `orders` | заказы через шину, доставка сделкой рынка | вкл. | жители, economy | деньги между жителями | 4 |
| `market_day` | суббота мира: пороги рынка мягче, сбор у площади, лавка чаще | вкл. | мир, calendar, economy | деньги: больше сделок; люди: вывески | 4 |
| `refine` | заточка своего оружия ур. 1–2 у Hollgrehenn только до безопасного предела | **выкл.** (`refine.enabled: false`) | плагин refine (загружен всегда), Refine UI (0AA0/0AA2), Phracon/Emveretarcon | деньги; тело снимает и надевает оружие | 7 |
| `gaze` | поворот к собеседнику-жителю | вкл. | social, жители; `look_at` → `lookp` | низкий: поворот виден | 3 |
| `dream` | мечта на месяцы, этапы по фактам, мотив этапа | вкл. | — | действий нет; меняет мотивы | 1 |
| `savings` | копилка мечты (резерв: не дарит зени, не перекупает); банк — флаг | вкл., **банк выкл.** (`savings.bank: false`) | dream; банк: `feature.banking`, пакеты 09A6–09AB | деньги (резерв меняет щедрость); банк — зени на счёт | 4 (банк — 7) |
| `memoir` | раз в неделю `state/<bot>/memoir.md` | вкл.; `memoir.llm: false` | — | только файл | 0 |
| `mentor` | наставник новичку (< 20 ур. или `born` < 14 дн.): советы, зелья, выпуск на 25 | вкл.; спит без новичка | жители | шёпоты, зелья через `[need:]` | 2 |
| `bestiary` | счёт видов, «первый среди жителей» в шине | вкл. | — | действий нет (первый запуск молча) | 0 |
| `spar` | спарринг жителей на PvP Yoyo по согласию | **выкл.** | плагин spar (загружен всегда), Gate Keeper 500 z, ур. ≥ 31, Butterfly Wing | тело (PvP-карта), деньги | 7 |
| `achieve` | достижения сервера по пакетам 0A23/0A24; награда — флаг | вкл., **`claim_rewards: false`** | `feature.achievement` сервера | без флага действий нет | 0 (флаг — 7) |
| `herbal` | травник: травы не продаются, поездка к Old Pharmacist в Альберту | **выкл.**; и травника нет (`herbal.residents: []`, `persona.herbalist` нет) | routine; jobChange `path herbal`, `buyAuto Empty Bottle` | тело: 11 переходов до Альберты; деньги | 6 |
| `arrows` | Arrow Crafting для ветки Archer | вкл., **спит** (лучника нет); `quest_auto: false` | routine; пути до `moc_ruins` нет | — пока нет лучника | 0 |
| `trek` | дальний поход группой в город другого региона, привалы с Kafra-сохранением | **выкл.** | routine, explore, party, crew | тело: дальние карты; точка сохранения | 6 |
| `director` | режиссёр: «день осторожности», «помочь», повод в тишину | вкл. | мир | множители мотивов; шёпот `[info:rich]` жителю | 2 |

Ядро (`mind.py`, не выключается): bridge, gate, safety, lifecycle, plans, postmortem, maps, needs, memory, llm/budget.
LLM выключен по умолчанию (`BRAIN_LLM=off`).

### 1.2 Флаги внутри `goals.json`

| Флаг | По умолчанию | Что включает | Риск | Этап |
|---|---|---|---|---|
| `progression.auto_job_change` | false | житель сам проходит квест профессии (jobChange) | **необр.**: смена профессии | 7 |
| `routine.auto_hunt_maps` | false | осваивать новые карты охоты по атласу | тело | 6, после `scripts/lab doctor` (карты включены на сервере) |
| `routine.vend_in_town` | true | лавка Merchant | спит: Merchant среди жителей нет | — |
| `savings.bank` | false | вклад/снятие в банке rAthena | деньги | 7 |
| `achieve.claim_rewards` | false | `achieve_reward {id}` за выполненное | предметы от сервера | 7 |
| `arrows.quest_auto` | false | этап квеста Roberto | пути до Морокка нет | не включать |
| `guild.emperium_check` | true | false — мозг не ждёт Emperium | только вместе с серверным файлом | 7 |
| `explore.group` | true | лидер зовёт группу в экспедицию | тело | 6 |
| `trek.halt_save` | true | Kafra-сохранение на привале | точка сохранения | 6 |
| `memoir.llm`, `episode.llm` | false | окраска моделью (платно) | деньги (API) | вне плана |

### 1.3 Тело, профили, сервер (BRAIN_DISABLE не действует)

| Что | Где | Состояние | Риск | Выключить без отката кода |
|---|---|---|---|---|
| мост `brainBridge` | плагин, +435 строк после №16 (`look_at`, `skill_on_player`, `bank_*`, `refine`, `spar`, `arrowcraft`, `craft_setup`, `achieve_reward`, `guild_*`) | всегда | обновлять вместе с мозгом (HANDOFF №16) | нет — откат commit |
| плагины `refine`, `spar` | `loadPlugins_list` обоих `sys.txt` | загружаются всегда, ждут действия мозга | ошибка загрузки Perl сломает тело | убрать из `loadPlugins_list`, перезапустить тело |
| плагин `pets` | `sys.txt` | всегда; корм — `pet_autoFeed 1` | — | убрать из списка |
| `buyAuto Pet Food`, `buyAuto Empty Bottle` | `config.txt` | `disabled 1` (Empty Bottle включает только herbal через `craft_setup`) | деньги | уже выкл. |
| продажа лута `items_control.txt all 0 0 1` (после cbeb2a9; было `all 0 1 0` — склад) | `items_control.txt` | продаётся всё, кроме зелий/крыльев, набора Sir Andrew, раздела питомцев; карты/руда/снаряжение — склад (плагин economy) | деньги, **необр.** (продажа NPC) | вернуть строку `all` — правка профиля |
| таблица переходов `bots/common/tables/portals.txt` + `field_*` в `bots/bot0N/tables/servers.txt` | профиль | всегда: правки izlude/iz_int/morocc, Kafra Пронтеры/Излуда/Морокка | тело: маршруты ко всем NPC и картам | убрать `bots/bot0N/tables/portals.txt` (тогда — upstream) |
| `servers.txt` профиля | `bots/bot0N/tables/servers.txt` | `127.0.0.1:6900`, `kRO_RagexeRE_2018_06_20e` | вход: адрес/порт должны совпасть с VPS | — |
| `char_start_point.txt` | `server/conf/optional/` | **не подключён** | сервер (все новые персонажи) | удалить строку, перезапуск char |
| `guild_no_emperium.txt` | `server/conf/optional/` | **не подключён** | сервер (гильдия без Emperium для всех игроков) | удалить строку, перезапуск map |
| память v3 | `memory.py` | при первом старте копия `memory.sqlite.bak-v<N>`, затем индекс `events_kind_ts` | откат кода совместим: старый код (v2) видит версию ≥ своей и не трогает БД | — |

Не в коде, включать нечего: свадьба (ORG-062), штатные ивенты rAthena (ORG-087), наследие (ORG-083) — только ТЗ в
`docs/IDEAS.md`.

## 2. Этапы

Каждый этап — 1–2 дня наблюдения Hermes, оба жителя (`LAB_BOTS="bot01 bot02"`). Следующий — только после «прошло»
по предыдущему. Правила заданий не меняются: login/char/map не перезапускать (кроме этапа 7 по решению владельца),
БД не трогать, без SQL/GM/ручного respawn.

### Общее для всех этапов

Включить этап: заменить строку `BRAIN_DISABLE=` в `$LAB_ROOT/secrets/live_ro.env` на строку этапа, проверить
`scripts/lab modules bot01` и `bot02`, затем `scripts/lab stop brain && scripts/lab start brain`.
Откат этапа: вернуть строку предыдущего этапа и перезапустить мозги. Память не теряется. Если тело в этот момент
выполняет этап плагина (`job_change`, `refine`, `spar`), плагин доводит его сам — дождаться `*_result` в
`decisions.jsonl`.

Смотреть каждый день (`$L=$LAB_ROOT`):
```sh
scripts/lab report; scripts/lab alerts; scripts/lab chronicle; scripts/lab dashboard; scripts/lab resources
grep -c Traceback $L/logs/bot0*/brain.log                                    # цель — 0
grep -hiE "error|can't locate|syntax error" $L/logs/bot0*/console.log | tail  # ошибки плагинов
grep -h '"rejected": \[{' $L/state/bot0*/decisions.jsonl | tail               # что safety не пропустил
grep -h '"type": "<модуль>"' $L/state/bot0*/decisions.jsonl | tail           # решения модуля
grep -h '"source": "<модуль>"' $L/state/bot0*/decisions.jsonl | tail         # действия модуля
```
Откатить этап сразу, если: повторяющийся Traceback; смертей за сутки больше, чем на этапе 0; тело стоит или
мечется дольше 30 мин без боя/отдыха; нужно ручное вмешательство; зени жителя упали больше чем на 20 % за сутки без
покупок зелий; модуль, которому не положено (всё, кроме `strangers`/`healer`), пишет не жителю; оповещение в `alerts`.

### Этап 0 — базовая линия (задания №10–16) без ночных новинок

Цель: подтвердить то, что выдано Hermes в №14–16 (распорядок, сон, город, рынок, группа, питомцы, вывески, жизнь
группы) на новом коде, пока ночные модули не шумят. Выкладка: шаг 0 задания №16 (`git checkout --detach <COMMIT>`,
`scripts/check.py`, тесты, `scripts/lab doctor`), затем — **до** `scripts/lab down; scripts/lab up` — строка ниже в env
и `scripts/lab modules bot01`. До первого старта — копия памяти: `scripts/lab down; cp -a $L/state $L/state.before-rollout`
(мозг сам сделает `memory.sqlite.bak-v<N>` при миграции схемы). Мост и мозг обновляются вместе (тела тоже
перезапускаются).

```
BRAIN_DISABLE=home,explore,strangers,healer,gaze,tradition,rivalry,gossip,episodes,mentor,director,orders,market_day,savings,dream,mood,calendar,crowd,habits,wed,fest
```
Включены остаются: `career`, `routine`, `economy`, `party`, `activity`, `bonds`, `crew`, `pets`, `social`, `rumors`,
`society`, `aims`, `world_bus` (база №14–16) и пассивные летописцы `collection`, `places`, `bestiary`, `achieve`,
`memoir`, `arrows` (спит). `guild`, `boss`, `refine`, `spar`, `herbal`, `trek` выключены в `goals.json`.

Смотреть: пункты задания №16 (1–8) и №15 (1–7); плюс тело на новом профиле — `doctor` видит плагины `refine`, `spar`,
`pets`; в `console.log` нет ошибок загрузки; маршруты к Tool Dealer `prt_in 126,76`, Kafra и на карты охоты
(`Calculating route` без `Unable to calculate`); продажа (`Selling`) не уносит зелья, крылья и предметы набора Sir
Andrew; `achieve`: события `achievement_list`, если пакет пришёл (нет — «не наблюдалось», это нормально);
`scripts/lab modules` совпадает со строкой выше.
Прошло: ручных вмешательств 0, Traceback 0, оба жителя живут сутки (охота → город → сон → охота), смерти записаны —
это базовая цифра для следующих этапов. Откатить: ошибки плагинов/маршрутов — откат commit (BRAIN_DISABLE тут не
поможет: это тело).

### Этап 1 — внутренний мир без новых действий

```
BRAIN_DISABLE=home,explore,strangers,healer,gaze,tradition,rivalry,gossip,episodes,mentor,director,orders,market_day,savings,wed,fest
```
Включаются: `mood`, `calendar`, `crowd`, `habits`, `dream`. Ни одного нового действия моста — только мотивы, выбор
занятий и окраска фраз.
Смотреть: `report` — «настроение», цель недели/мечта; `type: dream` (`dream_new`) и `type: habits` в decisions;
разнообразие занятий в `report` (ORG-046); заголовок дня календаря в `chronicle`.
Прошло: охота за сутки не меньше 80 % нормы этапа 0, сон в своё окно, разговоры жителей не пропали совсем (mood может
их приглушить, но не до нуля за сутки). Откатить: житель «залип» в одном занятии или перестал охотиться.

### Этап 2 — речь между жителями

```
BRAIN_DISABLE=home,explore,strangers,healer,gaze,tradition,orders,market_day,savings,fest
```
Включаются: `episodes`, `gossip`, `rivalry`, `director`, `mentor`. Всё — шёпоты жителю (или чат группы у `rivalry`).
Смотреть: `grep -h '"action": "whisper"' …decisions.jsonl | wc -l` по дням против этапа 1; метки `[gossip:`,
`[mentor:`, `[info:` в `console.log`; `rival_overtook` в `chronicle`; режиссёр — `type: director`, раздел в
`dashboard`. `mentor`: Vera — «новичок», если её уровень < 20 и Arkady выше на 15+; иначе модуль спит — «не наблюдалось».
Прошло: шёпотов не больше чем вдвое против этапа 1, нет пинг-понга одной меткой, адресаты — только жители.
Откатить: шквал шёпотов, ссоры по кругу (`society_quarrel` чаще раза в сутки).

### Этап 3 — тело в городе

```
BRAIN_DISABLE=explore,strangers,healer,orders,market_day,savings
```
Включаются: `gaze` (новое действие моста `look_at` → `lookp`), `tradition` (20–21 ч мира у фонтана), `home`
(Kafra-сохранение, отдых в доме). Можно разнести на два прогона: сначала `gaze,tradition`, затем `home`.
Смотреть: `lookp` в `console.log`, `type: gaze` (`look_at`/`seen`); вечером — занятие `gathering`, `type: tradition`;
`type: home`, действие `job_change` с `path home`, в `console.log` фраза Kafra «Your Respawn Point has been saved here»;
после смерти — `type: home`, `event: death` с полем `expect` (где ждёт возрождения), и карта возрождения совпала с ним
(`revived_in_place` — подняли на месте, это не возрождение).
Прошло: сохранение подтверждено фразой и картой; тело после этапа вернулось к распорядку; круг у фонтана не мешает
сну. Откатить: повторные провалы `job_change` у Kafra, житель застревает у NPC. `BRAIN_DISABLE=home` — точка отдыха
снова из `goals.json` (`prontera 156,185`); точка сохранения на сервере остаётся последней сохранённой.

### Этап 4 — экономика

```
BRAIN_DISABLE=explore,strangers,healer
```
Включаются: `savings` (банк выключен), `orders`, `market_day`.
Смотреть: `type: orders`, метки `[order:` в `console.log`, `Deal complete` у обоих; `savings_progress`, строка
копилки в `report`; `market_day_open`/`market_day_summary` — только в субботу мира (день 6 календаря по
`timezone_offset_hours`); иначе «не наблюдалось». Экономика за сутки — `report`, `chronicle` («экономика:»).
Прошло: каждое `trade_sold`/`trade_bought` подтверждено `Deal complete`; зени не ниже `economy.market.keep_zeny` (5000 по умолчанию); нет циклов
отмен `deal`. Откатить: сделки без подтверждения, `trade_unverified` чаще одной в день, утечка зени.

### Этап 5 — посторонние люди

Только с согласия владельца: модули отвечают людям. Проверять в своей лаборатории, человек — сам владелец со своего
клиента.
```
BRAIN_DISABLE=explore
```
Включаются: `strangers`, `healer`.
Смотреть: `type: strangers` — встречи, `e wav` только днём в городе; на шёпот владельца — ответ шаблоном
(`stranger_hello`/`stranger_reply`), не чаще раза в 10 мин одному и 3 в час всего; на шёпот с меткой `[offer:` — без
ответа. `healer` (Vera): `healer_post_start`, `chat create "Лечу у собора"`, на просьбу «heal» — `sp` в `console.log` и
событие `support` с её именем; `healer_shift` в `chronicle`.
Прошло: ни одного действия по тексту человека, кроме ответа шаблоном и Heal; SP Vera не уходит в ноль на охоте.
Откатить: ответы чаще лимитов, лечение не того, уход с охоты на пост слишком часто.

### Этап 6 — движение по миру

6а. Экспедиции (`explore` уже `enabled: true` в `goals.json`):
```
BRAIN_DISABLE=
```
Смотреть: `type: explore`, `explore_found`, смена `lockMap` в `console.log`, возвращение домой; смерти на новых
картах; `place_first` в шине (`bestiary`). Прошло: экспедиции кончаются возвращением, смертей не больше этапа 0.
Откатить: `BRAIN_DISABLE=explore` (выключит и `boss`, `trek`).

6б. Освоение карт охоты: после `scripts/lab doctor` (карты включены на сервере) — `goals.json`
`routine.auto_hunt_maps: true`. Откат — `git checkout -- brain/world/goals.json`.

6в. Дальний поход: `goals.json` `"trek": {"enabled": true, …}`. Смотреть `trek_start`/`trek_halt`/`trek_done`, Kafra
на привале (`job_change path trek`), после возвращения — пересохранение дома (`home`). Только днём, раз в 7 дней.
Откат — `"enabled": false` или `BRAIN_DISABLE=trek`.

6г. Травник: `"herbal": {"enabled": true, "residents": ["Vera"], …}` — без жителя в `residents` модуль спит.
Смотреть `craft_setup`, `buyAuto Empty Bottle` включился и выключился, `job_change path herbal`, зелья в рюкзаке
выросли (`herbal_brewed`). Откат — `"enabled": false`; если поездка оборвалась, проверить, что `Empty Bottle` снова
`disabled 1` (мост держит `%items_control` до перезагрузки — перезапуск тела возвращает профиль).

### Этап 7 — дорогое, необратимое, серверное

По одному пункту, каждый — отдельное решение владельца, перед каждым `scripts/lab db-backup`. Порядок — по росту
риска:

| Шаг | Включить | Смотреть | Откат |
|---|---|---|---|
| 7.1 награды достижений | `achieve.claim_rewards: true` | `achieve_reward` и предмет в рюкзаке | `false` |
| 7.2 заточка | `refine.enabled: true` | `refine_result ok` и `upgrade` в `state.refine`; оружие снова надето; зени | `false`; если оружие осталось снятым — `eq` вручную через консоль OpenKore (это ручное вмешательство — записать) |
| 7.3 банк | `savings.bank: true` | `bank_result ok`, `bank_balance`; вклад только излишка | `false`; деньги на счёте остаются (снять — включить снова или клиентом) |
| 7.4 мини-босс | `boss.enabled: true` | `[boss:ask:]`, оценка группы, `boss_victory` только по `kill` | `false`; смерть → бан босса 2 ч сам |
| 7.5 спарринг | `spar.enabled: true` | Gate Keeper, комната пустая, `spar_result`, выход крылом; посторонний — `aborted stranger` | `false`; плагин сам уходит при постороннем |
| 7.6 смена профессии | `progression.auto_job_change: true` | этапы `job_change`, `job_changed` по `state.job` | **необратимо**: выключить флаг можно, профессию — нет |
| 7.7 гильдия | `guild.enabled: true`; без Emperium — ещё `guild_no_emperium.txt` в `conf/import` + `emperium_check: false`, перезапуск map | `guild_founded` по пакету, `guild_joined` | `enabled: false`; гильдия на сервере остаётся; серверный файл — удалить строку, перезапуск map |
| 7.8 стартовая точка | `char_start_point.txt` в `conf/import/char_conf.txt`, перезапуск char | только для новых персонажей | удалить строку, перезапуск char |
| 7.9 рождение жителей | `scripts/lab birth` (docs/POPULATION.md) | после 7.8 | БД — из бэкапа |

`arrows.quest_auto` не включать: лучника нет, пути до `moc_ruins` в таблицах OpenKore нет.

## 3. Рекомендация для первого прогона

Первый прогон — строка этапа 0:
```
BRAIN_DISABLE=home,explore,strangers,healer,gaze,tradition,rivalry,gossip,episodes,mentor,director,orders,market_day,savings,dream,mood,calendar,crowd,habits,wed,fest
```
Почему так, а не «всё по умолчанию» (по умолчанию включено 38 модулей реестра из 44):
- **Неизвестна сама база.** Отчётов по №10–16 нет; на VPS последний проверенный код — cbeb2a9. Если включить всё,
  любой сбой придётся делить между 30+ модулями и новым телом (мост, таблицы переходов, продажа лута).
- **`home` ведёт к Kafra** этапом jobChange в первый же отдых — новое движение и диалог с NPC, ещё и меняет точку
  сохранения в БД персонажа.
- **`explore` уводит с карт охоты** — `goals.json` включает его, хотя у класса `ENABLED = False`; новые карты = новые
  смерти, а смерть сейчас — главный известный дефект (HERMES-cbeb2a9: восстановление после смерти требовало владельца).
- **`healer` и `strangers` отвечают людям** (шёпоты, Heal по слову «heal»), а `healer` у Vera (generosity 0.8)
  ещё и уводит её к собору — это видно посторонним и меняет её распорядок.
- **`gaze`, `tradition`** — новые действия тела в городе; `gaze` — новое действие моста.
- **`rivalry`, `gossip`, `episodes`, `mentor`, `director`** — новые шёпоты; вместе с `social` их станет трудно
  отличить от разговоров №14.
- **`orders`, `market_day`, `savings`** меняют пороги и щедрость экономики, которую №15 проверяет как есть.
- **`mood`, `calendar`, `crowd`, `habits`, `dream`** действий не шлют, но меняют выбор занятий и паузы разговоров —
  при первой проверке распорядка это шум.
- Оставлены пассивные `collection`, `places`, `bestiary`, `achieve`, `memoir`, `arrows`: действий не шлют (кроме
  тем разговора у первых четырёх), первый запуск молча; `achieve` заодно покажет, приходят ли пакеты достижений.

Строка этапа 0 — комментарием в `live_ro.env.example`.

## 4. Что прислать по этапу

`docs/qa/HERMES-<sha7>-stage<N>.md`: строка `BRAIN_DISABLE` и вывод `scripts/lab modules bot01`/`bot02`; правки
`goals.json` (`git diff`); по каждому включённому модулю — «подтверждено / не наблюдалось / провал» с выдержкой
`decisions.jsonl`/`console.log`; `report`, `chronicle`, `resources` за день; число смертей, шёпотов, сделок против
этапа 0; ручные вмешательства (цель — 0); решение «прошло / откат».

### Дополнение (слияние ORG-062/083/087)
Модули `wed` (помолвка: шёпоты, подарок 500z письмом) и `fest` (реакция на ивенты сервера: идёт к месту ивента) включены
по умолчанию, поэтому добавлены в строки `BRAIN_DISABLE` этапов 0–1; `fest` — ещё и этапа 2. Включать `wed` — на этапе 2
(шёпоты между жителями), `fest` — на этапе 3 (тело в городе) и только если ивенты включены на сервере
(`server/conf/optional/events_custom.txt`, решение владельца). `legacy` выключен (`goals.json legacy.enabled: false`).

## 5. Что смотреть в первую неделю: `scripts/lab organic` (ORG-113/114, Т-40)

Код, в игре не проверено; пороги — гипотеза из `docs/IDEAS2.md` §4.2, пересмотреть после первой недели в
`brain/world/organic.json`. Команда только читает `state/<bot>/decisions.jsonl` (с ротированными `.1`–`.3`),
`memory.sqlite`, `state/shared/world.sqlite` (mode=ro) и `logs/<bot>/brain.log`; поведение жителей не меняет.

```sh
scripts/lab organic                     # все жители (LAB_BOTS) и мир за последние 24 ч: таблица M1–M22
scripts/lab organic bot02 --days 3      # один житель (botNN или имя), среднее по трём суткам
scripts/lab organic all --json          # то же в JSON (для отчёта Hermes: приложить к docs/qa/HERMES-…-stageN.md)
scripts/lab organic --alerts            # + «шумно/мертво» 2 суток подряд → run/alerts.log; смотреть: scripts/lab alerts
scripts/lab dashboard                   # в дашборде: блок «Органичность» с цветом и полоса «кто вёл тело»
```

**Как читать.** Строка — метрика, столбец — житель и «мир»; у значения оценка «мёртво / живо / шумно»
(«·» — метрика только мировая, «—» — нет данных: например, нет `decisions.jsonl`). Сутки — скользящие 24 ч до
запуска; `--days N` — среднее N суточных окон. Под таблицей: итог дня жителя и мира («мёртвый день» — ≥ 2 «мертво» из
M1, M2, M7, M12, M16; «шумный» — ≥ 3 «шумно» из M2–M4, M6, M9, M10, M12–M15; иначе «живой»), какие метрики вне
нормы, «кто вёл тело» (доли времени по источникам решений) и найденный пинг-понг меток. Метрика жителя считается
«мёртвой/шумной» для мира, если так у половины жителей и больше. Тихий день (вид шины `quiet_day`, Т-39) — без M2 и M16.

**Что считается и откуда** (подробнее — docstring `brain/live_brain/organic.py`):
- «Кто вёл тело» (M13): интервал владения — от решения с `meet_point/hunt/sit/service/job_change/follow/explore` до
  следующего такого; владелец — `source`, а у `social`/`routine` уточняется по `reason` («собор» → лекарь, «ивент» →
  ивент…) и по занятию, выбранному ≤ 3 мин назад (`gathering` → традиция, `healer_post` → лекарь).
- M2/M3 — шёпоты из `actions` решений: начатые — метки `[chat:*:1]`, `[gossip:`, `[info:`, `[chat:rival`,
  `[mentor:tip|cheer`; ответы — `[chat:*:2…9]`; остальное — протокол. M4 = M2 / дела (уровень, смерть, сделка,
  подарок, лечение, новое место, карта, профессия… + 1 за 100 побед; ключ «дел» добавлен и в `report`).
- M9 — меньший из CV интервалов начатых шёпотов и смен занятия (нужно ≥ 5 событий). M10 — доля времени, когда целью
  тела была клетка ≤ 6 от фонтана, среди времени с целью в Пронтере (не координаты тела: позиций в журнале нет;
  меньше 30 мин в городе — «—»). M12 — бодрствующие часы (сон по `routine_sleep/wake`) без начатых шёпотов и эмоций.
- M15 — `director` в шине; M16 — событие жителя в шине (смерть, подарок, уровень, слух) → реакция другого за 24 ч
  (шёпот с темой `condolence/thanks/congrats` этому жителю или событие памяти о нём / о той же карте слуха).
- M17 — прирост affinity к одному жителю по `relation_log`; M19 — по текущему kv `needs` (снимок, не история).

**Ограничения (честно).** M18 (сток/приток) считается только при событиях трат (`npc_bought`, `service_paid`,
`refine_paid` — список `sink_kinds`); покупки `buyAuto` у NPC сейчас в память не пишутся, поэтому обычно «—». M22 —
только число `Traceback` в `brain.log` (ручные вмешательства и смерти против этапа 0 — по-прежнему по отчёту Hermes).
M3 «рост ×2 против этапа 1» и M17 «3 дня подряд» автоматически не сравниваются — смотреть `--days`. В таблице 4.2
порог «мёртво» у M12 записан как «< 10 % при охоте < 50 %»; в коде принято «> 90 % тихих часов при охоте < 50 %
нормы» (мёртвый мир молчит) — правится в `organic.json`.

**Оповещения (`--alerts`).** По каждой метрике, оценённой одинаково «шумно» или «мертво» в двух последних суточных
окнах, — строка в `run/alerts.log` в формате AUT-118 `{"ts", "bot", "kind": "organic:M7", "text"}`; не чаще раза в
сутки (дата UTC) на метрику и жителя, повторный запуск в тот же день не дублирует. Запускать вручную раз в день (или
владелец ставит cron сам — команда ничего не перезапускает).

**Порядок по дням** — `docs/IDEAS2.md` §4.3: дни 1–2 (этап 0) — записать `scripts/lab organic --json` как норму;
день 3 — M7–M9; дни 4–5 (речь) — M2–M6, M12, M15, M17, M20 (при M2 > 40 или M12 < 15 % — параметры из IDEAS2 §1.3
и Т-38); дни 6–7 — M10, M11, M13, M14; конец недели — M18, M19, M21. Числа — подсказка, а не доказательство:
каждый день по-прежнему прочитать глазами 3 случайных 10-минутных отрезка `decisions.jsonl`.
