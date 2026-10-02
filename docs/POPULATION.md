# Население мира: реестр, шаблоны, рождение жителя (ORG-040..044)

Статус: код, шаблоны и тесты в песочнице. **В игре не проверено**: ни один житель по этой процедуре ещё не родился,
плагин autoCreate проверен только тестом на заглушках (`bots/tests/auto_create.t`).

## 1. Реестр жителей (ORG-040)

`brain/world/roster.json` — единственное место, где записано, кто житель мира:

```json
"bot01": {"name": "Arkady", "job": "Swordsman", "template": "swordsman", "persona": "bot01",
          "home_town": "prontera", "active": true, "born": null}
```

| поле | смысл |
|---|---|
| ключ `botNN` | профиль OpenKore `bots/botNN`, переменные `BOTNN_*` в env-файле |
| `name` | имя персонажа в игре = `name` персоны; 4–23 латинских букв/цифр (rAthena `char_name_option: 1`, `char_name_letters`, `char_name_min_length: 4` в `conf/char_athena.conf`) |
| `job` | первая профессия — цель жителя (рождается он Novice) |
| `template` | архетип `bots/templates/<template>` |
| `persona` | файл характера `brain/personas/<persona>.json` |
| `home_town` | город отдыха; сейчас только `prontera` — точки `social.points` в `brain/world/goals.json` есть только там |
| `active` | житель мира. Только активные попадают в `residents`/`dealAuto_names` других и в `peer_names` мозга |
| `born` | дата рождения (UTC, `YYYY-MM-DD`), ставит оператор; по ней проверяется темп «не чаще раза в неделю» |

Откуда берётся список жителей:

- **Тело, runtime.** `scripts/lib/render.py bot` (вызывается из `scripts/lab start`/`up`/сторожа при каждом старте бота,
  в режимах existing и release, если не задан `BOTNN_CONTROL_DIR`) подставляет в `run/bots/<bot>/control/config.txt`:
  `residents` = активные жители кроме себя ∩ `LAB_BOTS`; `dealAuto_names` = то же, а если там пусто — все активные
  жители кроме себя; `autoCreate_name` = имя из реестра; `autoCreate_sex` = `BOTNN_SEX`. Подставляются только строки,
  которые уже есть в профиле. Бота нет в реестре или реестра нет — профиль копируется как раньше.
  **`dealAuto_names` никогда не пустой**: в OpenKore пустой список значит «принимать сделки от всех»
  (`upstream/openkore/src/AI/CoreLogic.pm:1048`). Тогда рендер останавливается с ошибкой.
- **Тело, Git.** В `bots/*/control/config.txt` лежат все активные жители кроме себя (без учёта `LAB_BOTS`).
  `scripts/lab roster` (и раздел «жители» в `scripts/lab doctor`) показывает расхождения; `scripts/lab roster sync`
  печатает их, `scripts/lab roster sync --write` правит строки `residents`/`dealAuto_names` в checkout (затем commit).
- **Мозг.** `peer_names` (`brain/live_brain/__main__.py`) — активные жители реестра ∩ `LAB_BOTS`, имя из персоны по
  полю `persona`. Реестра нет — прежнее поведение (персоны `LAB_BOTS`, без него — все персоны).

Добавление жителя меняет один файл (`roster.json`), остальные — производные: рендер при `start` и `roster sync`.
`scripts/check.py` реестр пока не проверяет (файл вне зоны этой работы) — проверяют `scripts/lab roster`/`doctor`
и `brain/tests/test_roster.py`.

## 2. Шаблоны жителей (ORG-041)

`bots/templates/<архетип>/template.json` (+ `persona.json` у новых). Профиль берётся из общего `bots/bot01/control`
(поле `base`), шаблон меняет только то, что отличается. Бой (дистанция, навыки, статы, отдых по SP) задаёт плагин
combatProfile по классу из `bots/combat/classes.json`, поэтому боевых параметров в шаблонах нет.

| шаблон | профессия | бой (classes.json) | отличие профиля | персона |
|---|---|---|---|---|
| swordsman | Swordsman | melee | `items_control`: набор Sir Andrew не продаётся (квест Knight) | своя (`--persona`) |
| acolyte | Acolyte | melee | `economy_storeIds` + Blue Gemstone 717 | своя (`--persona`) |
| merchant | Merchant | melee | `economy_storeIds` + Iron Ore 1002, Iron 998 (товар лавки) | Bram |
| archer | Archer | ranged | `economy_storeIds` + Trunk 1019 (стрелы) | Ilsa |
| thief | Thief | melee | только внешность | Rook |
| mage | Mage | caster | `economy_storeIds` + самоцветы 715–717 | Odette |

Общее для всех: `items_control`/`pickupitems` из bot01; строки «не продавать — квест X» сбрасываются в «продавать»,
если X не в `keep_quest_items` шаблона (для Acolyte результат совпадает с `bots/bot02/control/items_control.txt` —
проверено тестом). В `sys.txt` добавляется плагин `autoCreate`, в `config.txt` — блок `autoCreate*`.
Логина и пароля в профиле нет: `username`/`password` пустые, их подставляет рендер из `BOTNN_USER`/`BOTNN_PASS`.

Новые жители (характер, речь, черты, сон, любимые места — `persona.json` шаблона):

| имя | кто | характер | сон (мир) | места |
|---|---|---|---|---|
| Bram | Merchant, М | расчётливый, честный, любит торг; жадность 0.8, терпение 0.8 | жаворонок, 22:30, 7–8 ч | Kafra, Tool Dealer |
| Ilsa | Archer, Ж | зоркая, непоседливая, прямолинейная; любопытство 0.9, терпение 0.3 | голубь, 00:30, 6.5–8 ч | фонтан |
| Rook | Thief, М | насмешливый, осторожный, собирает слухи; причудливость 0.7 | сова, 04:00, 6–7.5 ч | фонтан, Kafra |
| Odette | Mage, Ж | вдумчивая, книжная, рассеянная; терпение 0.8, любопытство 0.8 | сова, 01:30, 7–9 ч | собор |

Фраз — по 5–6 на ключ (`hello weather hunt loot tired death level congrats condolence thanks bye`), подстановки как у
Arkady/Vera. Карты охоты — те же поля Пронтеры (`prt_fild08/07/05`) в разном порядке: стартовые карты «по атласу»
для уровня новичка не подбирались.

Генератор:

```
scripts/lab new-resident bot03 merchant Bram            # или: python3 scripts/lib/roster.py new bot03 merchant Bram
scripts/lab new-resident bot04 swordsman Gerold --persona path/to/gerold.json
```

Создаёт `bots/botNN/control` и `tables` (копия bot01 + шаблон), `brain/personas/botNN.json` (персона шаблона, имя
заменено), запись в реестре с `active: false`. Отказывает, если профиль/персона уже есть, имя занято (без учёта
регистра) или не подходит, у шаблона нет персоны и не дан `--persona`. При ошибке полупрофиль удаляется.

## 3. Автосоздание персонажа (ORG-042)

Плагин `bots/plugins/autoCreate/autoCreate.pl`, только при `autoCreate 1`:

- хук `charSelectScreen` (`src/Misc.pm:1617`): персонаж с именем `autoCreate_name` есть — выставить `char` на его слот
  (вход штатный при autoLogin, иначе `sendCharLogin` от плагина); нет — `Misc::createCharacter(slot, name,
  hairStyle, hairColor, 'novice', M|F)` (`src/Misc.pm:2208`, ветка `char_create_version 0x0A39`: serverType
  `kRO_RagexeRE_2018_06_20e` наследует `Send/kRO/RagexeRE_2015_10_01b.pm:72`). rAthena при PACKETVER ≥ 20151001
  берёт пол из пакета (`src/char/char_clif.cpp:1273`); рендер ставит `autoCreate_sex` из `BOTNN_SEX`;
- успех — `char_created`, затем `charSelectScreen()` без autoLogin (`Receive.pm:994`): плагин сам входит новым
  персонажем;
- отказ — пакет 006E (`Receive/ServerType0.pm:69`, поле `type`): хук `packet_pre/character_creation_failed`
  пишет метку `$LAB_ROOT/logs/<bot>/autoCreate.refused` (имя, код, причина) и останавливает автосоздание. Пока
  метка есть, плагин не создаёт персонажа и после перезапуска сторожем; `doctor` показывает её как [FAIL].
  Оператор устраняет причину (другое имя в реестре, свободный слот) и удаляет метку вручную;
- параметры: `autoCreate_slot` (пусто — первый свободный; занят другим — стоп), `autoCreate_hairStyle`
  (rAthena 0–42), `autoCreate_hairColor` (0–8, `conf/battle/client.conf`); нет ответа сервера 60 с — стоп.

## 4. Рождение жителя (ORG-043)

`scripts/lab birth botNN` только проверяет и печатает шаги; `scripts/lab birth botNN --yes` дополнительно вызывает
**уже существующие** команды: `db-backup` (если свежего бэкапа нет) и `db-add-account botNN` (если аккаунта нет).
Больше ничего в БД не пишется. Темп — не чаще одного жителя в неделю (по полю `born`; нарушение — предупреждение).

Кто что делает. Разработчик (здесь, в Git): шаги 1 и 6. Оператор Hermes на VPS (от пользователя лаборатории,
`sudo -u ro-lab`): шаги 2–5 и 7. Владелец даёт разрешение на изменение БД (шаг 3) и на запуск нового жителя.

1. **Профиль.** `scripts/lab new-resident botNN <шаблон> <Имя>`; просмотреть `git diff`, commit, push; доставка на
   VPS (existing: `git pull` в checkout; release: `deploy`/`activate`). Житель пока `active: false`.
2. **env.** В `$LAB_ROOT/secrets/live_ro.env` (права 600, вне Git): `BOTNN_USER`, `BOTNN_PASS` (1–23 печатных ASCII без
   пробелов — буфер rAthena 24 байта), `BOTNN_SEX=M|F`, при желании `BOTNN_RUNNER=tmux`. `LAB_BOTS` пока не трогать.
3. **БД.** `scripts/lab birth botNN` → нет [FAIL] → `scripts/lab birth botNN --yes`. Что требует `db-add-account`
   (`scripts/lab`, `cmd_db_add_account`): бэкап `$LAB_ROOT/backups/${DB_NAME}-*.sql.gz` не старше 24 ч (`find -mmin
   -1440`); `BOTNN_USER/PASS` заданы и проходят проверку формата; `BOTNN_SEX` — M или F; аккаунта с таким userid ещё
   нет (иначе отказ без изменений). Вставка — одна строка `login (userid, user_pass, sex, email='botNN@lab.local')`,
   SQL через stdin (пароль не попадает в argv и вывод ошибки). `db-backup` проверяет архив (`gzip -t`, маркер
   `Dump completed`). В отчёт владельцу — путь бэкапа из вывода `db-add-account`.
4. **Первый вход.** `scripts/lab start live botNN` (явное имя бота работает и без `LAB_BOTS`). autoCreate создаёт
   персонажа с именем из реестра. Наблюдать: `tmux attach -t live_ro_botNN` или `logs/botNN/console.log`
   («[autoCreate] … создаю», «Character … created»).
5. **Проверка входа.** `scripts/lab doctor`: строка `botNN: <Имя> slot 0 lv 1 map …`; нет метки
   `autoCreate.refused`. Процесс и порт — не доказательство игры: смотреть позицию/карту в логе и в БД.
6. **Активация в Git.** `roster.json`: `active: true`, `born: <дата>`; `scripts/lab roster sync --write`
   (residents/dealAuto_names других); commit, доставка.
7. **Мир.** `LAB_BOTS += botNN` в env; `scripts/lab down && scripts/lab up` — рендер обновит residents всех, мозги
   увидят нового жителя в `peer_names`. Сутки наблюдать `scripts/lab report`, `alerts`.

Известные риски и пробелы (честно):

- **Стартовая точка и первая профессия** — см. раздел 6 «Первые шаги новичка»: без решения владельца по
  `start_point` новичок застрянет в учебном полигоне iz_int.
- Событие `world_events: new_resident` и слух о новом жителе не реализованы (mind/social вне зоны этой работы).
- Генератор не создаёт боевой профиль, отличный от combatProfile; Archer без стрел в инвентаре — не проверено.

## 5. Диспетчер смен (ORG-044)

`LAB_MAX_ONLINE=N` в env-файле (пусто — прежнее поведение: все `LAB_BOTS`). `scripts/lib/roster.py shift` считает,
кто бодрствует сейчас по `persona.sleep` (окно `[start, start + max(hours))` местного времени мира,
`timezone_offset_hours` из `goals.json`), и отдаёт первых N бодрствующих по порядку `LAB_BOTS`. `scripts/lab up` и
сторож (`supervise`) поднимают процесс бота только из этого списка и только пока запущено меньше N ботов; мозги
поднимаются как раньше. Спящего сторож не будит. Запущенных не останавливает: засыпание — это relog внутри OpenKore
по команде мозга, поэтому процесс спящего, если он не падал, продолжает занимать место до ручной остановки
(`scripts/lab stop botNN`). Освобождение процесса во сне (мозг просит сторожа остановить тело на ночь) — следующий
шаг, требует правки routine.py/brainBridge (вне зоны этой работы). Проверено: выбор (`brain/tests/test_roster.py`),
`bash -n`; цикл сторожа с лимитом в работе не запускался.

## 6. Первые шаги новичка (newborn)

Статус: данные и правила проверены тестами разбора (`brain/tests/test_newborn.py`, `bots/tests/job_change.t`).
**В игре ни один шаг не проходился.**

### 6.1. Где появляется новичок и почему он там застрянет

rAthena renewal ставит нового персонажа случайно в одну из `iz_int`, `iz_int01`…`iz_int04` 18,26
(`conf/char_athena.conf:115`; в renewal читается `start_point`, `src/char/char.cpp:3008-3012`). Эта же точка
становится точкой сохранения (`src/char/char.cpp:1503-1509`).

Выход по скриптам — три перехода без меню, диалогов-тестов нет:

1. iz_int 27,30 — warp `#room_out` → 51,30 (`npc/re/warps/cities/izlude.txt:57`);
2. iz_int 56,15 — `#ship_out` (OnTouch): savepoint int_land 77,101, warp int_land 85,107 (`:69-77`);
3. int_land 49,57 — `#intro_to_izlude` (OnTouch): без квеста 21008 меню нет, только `mes` и `close2` (OpenKore
   закрывает такой диалог сам, `Task/TalkNPC.pm:348-353`); warp izlude 196,209, savepoint izlude 128,142 (`:83-109`).
   Копии: iz_int0N → int_land0N → izlude_a…d.

Бот этого не сделает, и сценарий выхода **не добавлен**: на данных OpenKore он не работает.

- `fields/iz_int.fld2.gz` в OpenKore — старая карта 200x200, а у сервера iz_int 80x80 (`db/map_cache.dat`). Клетка
  старта 18,26 для OpenKore непроходима, маршрут не строится. Обойти можно: `field_iz_int iz_int01` в `servers.txt`
  (`src/Field.pm:856`), поле `iz_int01` совпадает с картой сервера клетка в клетку (проверено тестом).
- Выход ведёт в izlude, а он в renewal перестроен: у сервера 268x300, поле OpenKore 268x268, переходы izlude в
  `tables/portals.txt` старые. После выхода точка сохранения тоже izlude, поэтому после смерти житель вернётся в
  город, где OpenKore не ориентируется. Нужен патч `portals.txt` (задача NB-1 в `docs/PROGRESSION.md`).
- Старый полигон `new_1-1` в renewal не грузится (`npc/re/scripts_jobs.conf:37`, novice.txt закомментирован), поля
  `new_1-1` в OpenKore нет. Как стартовая точка он не подходит.

Мозг это видит: `progression.blocked()` и `plan()` для жителя на картах `start.maps` (iz_int*, int_land*) честно
сообщают «учебный полигон …: выход не автоматизирован», и этап смены профессии не запускается.

### 6.2. Решение владельца: стартовая точка в Пронтере

Готовый файл: `server/conf/optional/char_start_point.txt` (`start_point: prontera,156,180`, у фонтана). **Не включён.**
Это изменение сервера. Что оно даёт: новые персонажи появляются и возрождаются в Пронтере (`home_town` всех
жителей), учебный полигон пропускается. Существующих персонажей и БД оно не трогает. Клетка проходима и на карте
сервера, и на поле OpenKore (тест `MapsTest.test_start_point_walkable`).

Включение, затем перезапуск char-server:

- рендер conf/import (release или `RENDER_SERVER_CONF=1`): дописать строку `start_point: prontera,156,180` в
  `server/conf/import-tmpl/char_conf.txt`, поставить `"enabled": true` в `start.override` файла
  `brain/world/progression.json` (тест сверяет их), commit, доставка, `scripts/lab start`;
- existing: дописать ту же строку в `$RATHENA/conf/import/char_conf.txt` вручную (её импортирует
  `conf/char_athena.conf:305`, последнее значение побеждает).

Без этого новичка придётся выводить вручную, и оба способа требуют решения владельца: GM-аккаунт оператора и
`@warp`/`@recall` в нашей лаборатории или правка `last_map`/`save_map` персонажа в БД. Правка БД возможна только
отдельной миграцией с бэкапом и при остановленном char-server.

### 6.3. Novice → первая профессия

Сценарии шести профессий выведены из `npc/re/jobs/1-1/*.txt`. Подробности, ссылки на строки и маршруты — в
`docs/PROGRESSION.md`, раздел 9. Кратко: в renewal испытаний нет, нужен только NV_BASIC 9, то есть job 10, и
диалог из 1–3 меню (Thief: два NPC). Путь выбирается по цели жителя — полю `job` в `roster.json`, без него — по
`job` шаблона; поле `job` персоны важнее. Профиль Novice в `classes.json` уже учит NV_BASIC 9.

| цель | путь | что мешает |
|---|---|---|
| Acolyte, Mage, Archer, Merchant | ok | ничего, кроме общего флага `progression.auto_job_change` (false) и того, что в игре не проверено |
| Swordsman | blocked | izlude перестроен (как в 6.1); нужен патч OpenKore (NB-1) |
| Thief | blocked | пешего пути в Морокко по таблицам OpenKore нет; телепорт Kafra не сверен (NB-2) |

Порядок для нового жителя после включения 6.2: охота Novice до job 10 (NV_BASIC 9 ставит combatProfile) →
`career` видит готовность → при `auto_job_change: true` (решение владельца) идёт в гильдию по шагам из
`progression.json`. Пока флаг выключен, цель «первая профессия» только видна в промпте и отчёте, а сменить
профессию оператор может вручную.
