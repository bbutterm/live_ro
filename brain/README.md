# Мозг ботов (live_brain)

Один лёгкий Python-процесс на бота (только стандартная библиотека Python 3.8+).
OpenKore остаётся телом: бой, ходьба, лут и отдых. Мозг решает цели и общается.

```
OpenKore + плагин brainBridge  ⇄  Unix-сокет $LAB_ROOT/run/brain/bot01.sock  ⇄  live_brain  →  OpenRouter
   (bots/plugins/brainBridge)       JSON-строки: состояние, события / действия      память SQLite
```

## Поток решения
```
событие тела ─► decision gate ─► действия по правилам ─┐
                     │                                  ├─► SafetyPolicy ─► brainBridge ─► OpenKore
                     └─► (если нужно и BRAIN_LLM включён) LLM ─┘
```
- **Decision gate** (`gate.py`) — первый слой, без LLM. Сейчас `RuleGate`:
  - приветствие при входе в игру (не чаще раза в 6 ч);
  - ответ статусом на `!status` / `!статус` в личку;
  - смерть и новый уровень — в память и повод для LLM;
  - бой и лут — только журнал.

  `JevGate` (`BRAIN_GATE=jev`, `JEV_*`) — быстрая внешняя модель. Транспорт `JEV_PROVIDER`:
  `typesafe` — нативный TypeSafe Decisions API (`typesafe.py`, закрытый выбор deepseek/ignore,
  проверка confidence); `openai` — OpenAI-совместимый chat/completions (может дать короткую реплику
  say/whisper). Сбой, таймаут или лимит `JEV_DAILY_LIMIT` (резервируется до вызова) — решают правила.
- **Несколько ботов:** `LAB_BOTS`, характер `brain/personas/<bot>.json` на каждого. Мозги знают
  друг друга как жителей: не больше `BRAIN_PEER_REPLIES_PER_HOUR` ответов другому боту в час, повод
  заговорить раз в `BRAIN_PEER_SMALLTALK` секунд (только при включённом LLM).
- **SafetyPolicy** (`safety.py`) — критическая безопасность на правилах; через неё проходит любое
  действие, и от правил, и от модели:
  - мёртвому персонажу никаких действий;
  - при HP < `BRAIN_SAFE_HP` нельзя менять карту и снимать паузу;
  - карты только из `hunt_maps`;
  - лимиты общего чата и лички;
  - пауза не дольше 10 минут, затем `resume` по правилу.

  Модель не может выполнить ничего, кроме 5 игровых действий: никаких shell-команд.
- **LLM выключен по умолчанию** (`BRAIN_LLM=off`), даже если ключ есть в env. Включается
  `BRAIN_LLM=openrouter` только после согласования модели и бюджета.

## Игровые действия
`say`, `whisper`, `set_hunt_map`, `pause`, `resume`, `party_create` (имя группы всегда `LR_<имя>`),
`party_invite`, `party_accept`, `party_leave`, `follow`, `unfollow`. Только у правил (модель не получает):
точка встречи, охота, сесть/встать, `unstuck`, `give`, `shop_open`/`shop_close`, `emote` (эмоция из allowlist). Группа и следование — только
с жителями. Приглашение в группу жителя принимает правило. Лечение партнёра (Vera, `AL_HEAL`)
делает OpenKore по `partySkill` в профиле, без LLM.

## Глобальные цели и распорядок дня
`brain/world/goals.json` — общие для всех жителей цели (уровень, профессия, друзья, выживание) и распорядок:
норма охоты **4–5 часов в сутки**, сессии по 60–100 минут, между ними и после нормы — отдых в городе
(`prontera 156,185`): дойти, сесть, общаться (разговор с жителем раз в 10 минут, а не 30).
Персональные отличия — `routine` / `goals_extra` в `brain/personas/<bot>.json`.
- Исполнитель `routine.py` — правила без LLM: усталость = фактическое время на карте охоты (жив);
  состояние в SQLite (`kv routine`); каждый тик сверка настройки OpenKore с режимом.
- Модель видит распорядок и цели с прогрессом; `set_hunt_map` выбирает карту (в городе — на следующую сессию).
- Встреча важнее распорядка: пока план встречи активен, распорядок не переключает режим.
- Безопасность важнее расписания: после смерти — город и отдых, на охоту только при HP ≥ `min_hp_to_hunt`
  (80%); на охоте HP < 25% без зелий — в город. Застревание не считается, пока сидит, торгует или дерётся.
- Цель в памяти пишет распорядок при смене режима (`goal_source`), чтобы текст цели не расходился с телом.
- Оператор: `scripts/lab routine BOT rest|hunt|show`. Команды ждут первого состояния тела, старше 10 мин — отказ.

## Хозяйство: продажа, склад, взаимопомощь, лавка
Тело (OpenKore + плагин `bots/plugins/economy`), без LLM:
- **Продажа лута** торговцу Tool Dealer `prt_in 126,76` при рюкзаке ≥ 48% (`sellAuto`); по умолчанию
  продаётся всё неэкипированное (`items_control.txt: all 0 0 1`), кроме зелий и крыльев.
- **Закупка**: Red Potion до 40 шт., когда их < 10 и зени > 3000 (`buyAuto`).
- **Склад Kafra** `prontera 146,89` (плата 40z, нужно Basic Skill 6): карты (тип 6) и руда
  (`economy_storeIds`: Oridecon, Elunium и необработанные) не продаются, а идут на склад вместе с продажей.
  Своя строка в `items_control.txt` важнее этого правила.
- **Сделки** только с жителями: `dealAuto 3` + `dealAuto_names`; чужим — явный отказ.

Мозг (`economy.py`, правила `goals.json → economy`):
- в городе, если житель рядом (≤ 8 клеток), а у меня Red Potion < 5 или зени < 500 — прошу шёпотом
  с меткой `[need:<id>:<предмет>:<сколько>]`, не чаще раза в 20 минут;
- житель отдаёт, если после передачи у него остаётся не меньше `keep` и за сутки отдал < 6 раз:
  `[need:<id>:ok]` и действие `give` — тело подходит на 2 клетки, предлагает сделку, кладёт, подтверждает;
- доказательства: «отдал» — `give_result ok` (сервер завершил сделку), «получил» — выросло количество
  в моём состоянии из игры. Память и отношение +1 у обоих.
- **Лавка** (`vend_in_town`): только Merchant-ветка с навыком `MC_VENDING` и тележкой — открывает в городе
  (товары и цены из `bots/<bot>/control/shop.txt`, вещи должны лежать в тележке), закрывает перед охотой
  и перед встречей. У Arkady и Vera лавки нет (не торговцы) — функция спит.
- Оператор: `scripts/lab gift BOT ITEM N` — попросить сейчас (проверка обмена).

Чего нет: торговли с людьми через переговоры, цен «по рынку», закупки у других игроков.

## Структура модулей
Назначение, выключатели и что включено по умолчанию — [`docs/STATUS.md`](../docs/STATUS.md), раздел «Мозг: модули».
Состояние AUT — `docs/AUTONOMY_STATUS.md`, ORG — `docs/ORGANIC_BACKLOG.md`.

**Ядро** (создаёт `mind.py`, не выключается): `__main__.py` (запуск, `--report`, `--routine`, метрики ORG-046),
`config.py` (env, `BRAIN_DISABLE`), `bridge.py` (сокет плагина brainBridge), `mind.py` (тик 1 с, события, промпт),
`gate.py` + `typesafe.py` (decision gate: правила или JEV), `safety.py` (SafetyPolicy), `lifecycle.py` (состояние и
арбитр движения survival > plan > economy > party > routine), `plans.py` (встреча, тикает первым после safety),
`postmortem.py` (разбор смерти), `maps.py` (опыт по картам, выбор карты), `needs.py` (мотивы и характер числами),
`memory.py` (SQLite жителя), `llm.py` + `budget.py` (модель и общий бюджет).

**Модули реестра** `modules.py` (W8): модуль объявляет о себе атрибутами класса (`ATTR`, `FEATURE`, `CONFIG`,
`ENABLED`, `REQUIRES`, `TICK_ORDER`, `TAGS`, `EVENTS`, `PROMPT` — описание в начале `modules.py`); новый модуль —
одна строка в `MODULES`, `mind.py` не правится. Порядок вызовов фиксирует `tests/test_mind_order.py`.

| Файл | `mind.<ATTR>` | Тик (`TICK_ORDER`) | До SafetyPolicy | Нужно для создания |
|---|---|---|---|---|
| `home.py` | `home` | 100 | да | мир |
| `mood.py` | `mood` | — |  | — |
| `world_calendar.py` | `calendar` | — |  | мир |
| `career.py` | `career` | 40 |  | мир |
| `routine.py` | `routine` | 10 |  | мир |
| `economy.py` | `economy` | 20 |  | мир, раздел `economy` |
| `party.py` | `party` | 30 |  | мир, другие жители |
| `activity.py` | `activities` | 50 |  | routine |
| `bonds.py` | `bonds` | 60 |  | другие жители |
| `crew.py` | `crew` | 90 |  | party |
| `pets.py` | `pets` | 80 |  | мир |
| `social.py` | `social` | 70 |  | мир, другие жители |
| `rumors.py` | `rumors` | 120 |  | — |
| `society.py` | `society` | 130 |  | другие жители |
| `aims.py` | `aims` | 150 |  | — |
| `guild.py` | `guild` | 160 |  | мир, другие жители |
| `explore.py` | `explorer` | 110 |  | routine |
| `boss.py` | `boss` | 115 |  | мир, party, crew, explorer |
| `strangers.py` | `strangers` | 140 |  | — |
| `world_bus.py` | `world` | 180 |  | — |
| `rivalry.py` | `rivalry` | 190 |  | другие жители |
| `crowd.py` | `crowd` | 200 |  | — |
| `episodes.py` | `episodes` | 210 |  | другие жители |
| `tradition.py` | `tradition` | 170 |  | мир |
| `collection.py` | `collection` | 220 |  | — |
| `gossip.py` | `gossip` | 125 |  | другие жители |
| `habits.py` | `habits` | 205 |  | — |
| `healer.py` | `healer` | 135 |  | мир |
| `orders.py` | `orders` | 25 |  | другие жители, economy |
| `dream.py` | `dream` | 145 |  | — |
| `savings.py` | `savings` | 147 |  | dream |
| `memoir.py` | `memoir` | 230 |  | — |
| `director.py` | `director` | 230 |  | мир |

**Чистые функции и данные** (не модули реестра): `atlas.py` (атлас мира `world/atlas.json`), `prices.py` (цены
`world/prices.json`), `progression.py` (сценарии профессий `world/progression.json`), `topics.py` (темы разговора
для `social`), `weather.py` (погода мира), `replay.py` (запись и прогон потока тела, ORG-050).

**Только чтение, для владельца** (`python3 -m live_brain.<имя>`, обёртки в `scripts/lab`): `chronicle.py`
(`chronicle`), `dashboard.py` (`dashboard`), `episode.py` (`episode`), `memoir.py` (`memoir`, он же модуль
реестра), `census.py` (`census`, ORG-088), `resources.py` (`resources`, ORG-047).

Тело: плагины `brainBridge` (мост), `combatProfile` (бой по классу), `economy` (склад/сделки/лавка),
`survival` (экстренное выживание), `lowHpGuard`, `gracefulStop`.
Оповещения владельцу: `scripts/lab alerts`. Выключить модуль: `BRAIN_DISABLE=party,economy,routine`.

## Социальная жизнь
`social.py` — правила без LLM (тик 1 с), настройки `goals.json → social`, переопределения — `social` в персоне,
шаблонные реплики — `phrases` в персоне (ключи `hello`, `weather`, `hunt`, `loot`, `tired`, `death`, `level`,
`congrats`, `condolence`, `thanks`, `bye`; по 5–6 вариантов до 80 символов, подстановки `{name}`, `{lv}`,
`{kills}`, `{loot}`, `{map}`, `{death_map}`, `{hours}`, `{amount}`, `{peer_lv}`).
- **Город:** в режиме отдыха, после прибытия, раз в 10–25 мин житель встаёт и переходит к другой точке
  Пронтеры (фонтан 156,185; Kafra 148,93; вход к Tool Dealer 140,215; собор 237,310 — проходимость проверена
  по map_cache rAthena) и садится. Веса по характеру: Arkady — у торговцев, Vera — у собора и Kafra; с шансом
  идёт туда, где стоит друг. Точка отдыха распорядка на время прогулки сдвигается (routine.town), вне города
  возвращается. Не ходит при плане встречи, запрете арбитра, открытой лавке, за 5 мин до конца отдыха и ночью.
- **Разговор:** житель в ≤ 6 клетках — шёпот с меткой `[chat:<тема>:<шаг>]`: 1 приветствие (эмоция) →
  2 ответ → 3 тема по фактам дня из памяти (гибель, уровень, победы, добыча, усталость, иначе погода) →
  4 прощание. На шаг 4 не отвечают — не больше 2 обменов; пара не чаще раза в 15 мин (не друзьям ×1.5,
  неприязнь ×3, ночью ×3). Фразы не повторяются, пока не исчерпаны варианты; во время боя/спасения — тишина.
  Реплики — обычный чат через safety (лимиты лички, без повтора за час).
- **Реакции:** мой уровень — рассказать жителям (ответ — поздравление); уровень жителя вырос (по `players`) —
  поздравить; `[party:dead]` — посочувствовать; Heal жителя по пакету сервера — поблагодарить. Не чаще раза
  в 30 мин на вид и жителя.
- **Эмоции:** действие `emote {id}` → OpenKore `e <команда>`; только номера из `safety.EMOTES`
  (вопрос, свист, сердце, идея, «...», привет, спасибо, извини, хе-хе, хмм, молодец, плач, gg, ок),
  только от правил, не больше 6 за 10 мин.
- **Отношения (AUT-102):** 30 мин рядом за сутки (город или охота) — affinity +1, не чаще раза в сутки;
  проигнорированное сообщение ничего не меняет.
- **Ночь** (01:00–07:00 по `timezone_offset_hours`): не гуляют, сидят, болтают втрое реже.
- **С LLM** (`BRAIN_LLM=openrouter`): шаблонов нет — модуль даёт повод `trigger(kind="chat")` с фактами дня.
Проверено тестами `tests/test_social.py`; работу в игре этот раздел не утверждает.

## Исполняемые планы: встреча
`plans.py` превращает разговор в действие. Модель выбирает цель (`propose_meeting`, `accept_meeting`,
`decline_meeting`, `cancel_plan`), исполняет и проверяет — правило без LLM (тик 1 с):
```
Vera: propose  ─шёпот [meet:id:map:x:y]─►  Arkady: offer → accept (модель или правило через 45 с)
                ◄─шёпот [meet:id:ok]──────
оба: meet_point (OpenKore lockMap_x/y) → дошёл (≤4 клетки) → партнёр рядом (≤8) → completed
                ◄─шёпот [meet:id:done]──►  clear_point → обратно к охоте
```
- Статусы: `planned` → `executing` → `completed` | `failed`; план и история — в SQLite (`plans`).
- Память различает «предложил», «согласился», «дошёл», «встретился» (важность 4), «не состоялась».
- После перезапуска мозга план, созданный до старта, сверяется с игрой (срок, выставлена ли
  точка, позиция); точка выставляется заново, только если её в OpenKore нет.
- Оператор: `scripts/lab plan bot02 meet Arkady`, `plan BOT cancel`, `plan BOT show`.

## Доставка и контекст
- `ack` — команда исполнена в OpenKore. `delivery` — ответ сервера: шёпот доставлен или нет
  (не в сети / игнор / не принимает), общий чат подтверждён эхом; без ответа 15 с — `timeout`.
- Класс, пол и уровень бота и игроков рядом приходят из игры и запоминаются (`known_players`);
  модели запрещено угадывать пол и профессию.
- Лимиты: запросов в сутки (`BRAIN_DAILY_LIMIT`, `JEV_DAILY_LIMIT`), денег по `usage.cost`
  (`BRAIN_DAILY_USD_LIMIT`), размера промпта (`BRAIN_MAX_PROMPT_CHARS`).

## Что делает
- **Слушает тело:** раз в 15 с приходит состояние (HP, уровень, карта, зени); события —
  вход, смерть, новый уровень, убийство, лут, чат.
- **Помнит** в `$LAB_ROOT/state/bot01/memory.sqlite` (вне Git): события, воспоминания с важностью,
  отношения к игрокам, текущую цель и настроение. Память переживает перезапуск.
- **Думает редко:**
  - по плану раз в `BRAIN_DECIDE_INTERVAL` (300 с);
  - после смерти или нового уровня (не чаще `BRAIN_EVENT_MIN_GAP`);
  - когда ему пишут в личку или называют по имени в чате (не чаще `BRAIN_CHAT_MIN_GAP`).

  Не больше `BRAIN_DAILY_LIMIT` запросов за 24 часа.
- **Действует ограниченно:** `say`, `whisper`, `set_hunt_map` (только карты из `hunt_maps`
  характера), `pause` (перестать искать монстров — отбиваться и лечиться тело продолжает; не `ai manual`),
  `resume`. Не больше 2 действий за решение. Плагин исполняет действие
  командой OpenKore и подтверждает исполнение.
- **Без LLM** (`BRAIN_LLM=off`, нет ключа, исчерпан лимит, ошибка API — пауза 60 с) работают
  правила gate и safety, пишутся память и журнал, а бот играет по профилю OpenKore.
- **Журнал решений:** `$LAB_ROOT/state/bot01/decisions.jsonl` — строки `decision`
  (мысль, цель, действия, отклонённые действия), `ack` (что исполнено в игре), `fallback`, `llm_error`.

## Характер
`brain/personas/bot01.json`: имя (должно совпадать с именем персонажа), характер, манера речи,
цели и разрешённые карты охоты. Список `hunt_maps` проверьте под уровень персонажа.

## Настройки
В `$LAB_ROOT/secrets/live_ro.env`: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`
(по умолчанию `deepseek/deepseek-chat`, сверить с каталогом OpenRouter), `BRAIN_*`
(см. `live_ro.env.example`). Ключ не пишется в логи и журнал.

## Команды
```sh
scripts/lab brain-check     # один короткий платный запрос; при BRAIN_LLM=off — CHECK SKIP без запроса
scripts/lab start live [BOT|all]   # мозг, затем бот (с плагином brainBridge)
scripts/lab brain-check-jev  # один запрос к JEV (только при BRAIN_GATE=jev)
scripts/lab start brain     # только мозг (бот уже запущен с этим commit)
scripts/lab stop brain      # только мозг; бот продолжает играть сам
scripts/lab status
tail -f $LAB_ROOT/logs/bot01/brain.log
tail -n 20 $LAB_ROOT/state/bot01/decisions.jsonl
```

## Тесты
```sh
cd brain && python3 -m unittest -v tests.test_brain tests.test_rules tests.test_typesafe tests.test_limits tests.test_plans tests.test_routine tests.test_economy tests.test_inbox \
  tests.test_party tests.test_postmortem tests.test_lifecycle tests.test_reliability tests.test_maps
for t in bots/tests/*.t; do perl -Ibots/tests/stubs $t | tail -1; done   # из корня
```
Сквозной тест без сети: фейковый OpenRouter, настоящий процесс мозга, фейковый плагин.
Проверяет решение, исполнение, память после перезапуска, работу без ключа, `--check` и то,
что ключ не попадает в лог.

**Тесты не зависят от часа и дня запуска.** Тест, который не о времени суток, живёт в полдень мира
фиксированного дня: свои часы `Clock` у модулей или `tests/worldtime.py` (`shift_time` подменяет
`time.time()` в тесте, `brain_argv` запускает процесс мозга с тем же сдвигом; день — `REF_DAY`, обычный
четверг без праздника). Проверить устойчивость ко времени запуска (для разработчика, не для CI, долго —
каждый сдвиг это полный прогон):
```sh
python3 scripts/test_all_hours.py                      # сдвиги 0,3,…,21 ч + каждый день недели
python3 scripts/test_all_hours.py --hours 0-23 --days 0 -j 8
```
Скрипт сдвигает `time.time()`, `time.localtime()/gmtime()/strftime()` без аргумента и `datetime.now()`;
время в bash (`date`) и mtime файлов не сдвигаются.

**Нестабильные тесты (flaky).** Полный прогон должен быть зелёным каждый раз, в том числе под нагрузкой.
Известные причины падений и их исправления (комментарии `# flaky:` / `timefix:` в коде):
- общая `world.sqlite` создавалась двумя процессами — `world_bus.enable_wal`;
- зависимость от часа запуска — `tests/worldtime.py`, `scripts/test_all_hours.py`;
- потеря команды оператора: `read_inbox` читал inbox и делал unlink, а писатель (`>>` в `scripts/lab`,
  `open("a")`) создаёт файл раньше, чем пишет, — команда уходила в удалённый inode. Падал
  `test_plans.MeetingTest` (~1 из 20 прогонов). Теперь inbox забирается rename в `<inbox>.taken` и
  читается на следующем тике (тест `test_inbox.test_command_written_after_open_not_lost`);
- тесты с процессом мозга ждали фиксированный `sleep(1–3 с)` — теперь `BrainHarness.wait_for(условие)`
  ждёт записи в журнале с таймаутом; `FakeOpenRouter.jev_reply` сбрасывается в `setUp`.

Если тест «то падает, то нет», ищите: `sleep` вместо ожидания условия, общий файл или атрибут класса
между тестами, случайность без seed, реальное время, гонку между процессами. Не ослабляйте проверку —
ждите условие или изолируйте состояние. Проверка — много полных прогонов параллельно (это заодно нагрузка),
логи с `-v`, чтобы видеть имя упавшего теста:
```sh
cd brain && seq 1 15 | xargs -P 3 -I{} sh -c 'python3 -m unittest discover -s tests -v > /tmp/run-{}.log 2>&1; echo "{} $?"'
grep -l "^FAILED" /tmp/run-*.log; grep -h -A15 "^FAIL:\|^ERROR:" /tmp/run-*.log
python3 ../scripts/test_all_hours.py --hours 0,4,8,12,16,20,1.5,23.5 --days 0,3 -j 2   # время запуска
```
Статистика (2026-10, 4 CPU): до исправления — 1 падение на 20 полных прогонов (4 параллельно) и 1 на
24 прогона `tests.test_plans`; после — 0 на 18 полных прогонов (3 параллельно), 0 на 24 прогона тестов
с процессом мозга (`test_plans`, `test_inbox`, `test_brain`, `test_limits`, `test_rules`) под нагрузкой
и 0 на 10 сдвигах времени.
