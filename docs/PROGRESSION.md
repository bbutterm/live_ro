# Прогрессия жителей: смена профессии, цели роста, снаряжение

Закрывает данные и правила для AUT-049..054 и AUT-079..084 (`docs/AUTONOMY_BACKLOG.md`).
Интеграции в mind/routine/brainBridge пока нет. Ниже описано, куда всё это подключать.

**Статус.** Сценарии выведены из скриптов rAthena и проверены тестом разбора: каждый ответ сверяется
со строкой `select(...)` в скрипте, каждая точка маршрута проверяется по проходимости поля OpenKore.
В игре ни один этап не проходился. Утверждать, что «бот сменит профессию», пока нельзя.

| файл | что это |
|---|---|
| `brain/world/progression.json` | сценарии Swordman → Knight, Acolyte → Priest и Novice → шесть первых профессий (ручной, со ссылками `файл:строка`), стартовая точка, классы, правила целей |
| `brain/world/jobs/catalog.json` | сгенерированный справочник: снаряжение в магазинах Пронтеры и Изюда, источники предметов квеста |
| `scripts/gen_progression.py` | генератор каталога (rAthena: item_db, mob_db, NPC — через разбор `gen_atlas.py`) |
| `brain/live_brain/progression.py` | чистые функции: план, готовность, этап, ответы в меню, снаряжение, сводка |
| `brain/tests/test_progression.py` | тесты: данные, сверка со скриптами и полями, план, снаряжение |
| `bots/plugins/jobChange/jobChange.pl` | исполнитель одного этапа квеста в OpenKore (шаги присылает мозг) |
| `bots/tests/job_change.t` | тест плагина на заглушках |
| `brain/tests/test_newborn.py` | первые профессии: скрипты 1-1, маршруты по данным OpenKore против сервера, выбор пути по цели жителя (раздел 9) |
| `scripts/okroute.py` | поиск пешего маршрута по полям и `portals.txt` OpenKore (им записаны `route.hops`) |

```sh
python3 scripts/gen_progression.py upstream/rathena > brain/world/jobs/catalog.json   # ~40 с, нужен PyYAML
cd brain && python3 -m unittest -v tests.test_progression
perl -Ibots/tests/stubs bots/tests/job_change.t
```

Сверку со скриптами (`ScriptRefsTest`) и с полями (`FieldsTest`) тест делает, только если на месте
сабмодули `upstream/rathena`, `upstream/openkore` или заданы пути в `LIVE_RO_RATHENA` / `LIVE_RO_OPENKORE`.
Иначе эти проверки пропускаются (skipped).

## 1. Какие скрипты реально работают в renewal

`npc/re/scripts_main.conf:31` подключает `npc/scripts_jobs.conf`. Из него `:14` грузит
`npc/jobs/2-1/knight.txt`, а `:15` грузит `npc/jobs/2-1/priest.txt`. Отдельных renewal-версий
Knight и Priest в `npc/re/jobs` нет: там есть только 1-1, 2-2/crusader и третьи профессии. Паломники
Priest (Rubalkabara, Mathilda, Yosuke) объявлены в `npc/re/jobs/1-1/acolyte.txt:113/190/259` и
вызывают функции `F_FatherRub`, `F_MotherMart`, `F_FatherYos` из `priest.txt:1727-1863`.
`npc/re/jobs/repair.txt` переменные KNIGHT_Q/PRIEST_Q не трогает. Его NPC Valerie (prt_in 38,104)
сбрасывает сломанный квест, боту он не нужен.

Скрипт хранит прогресс в переменной персонажа (KNIGHT_Q, PRIEST_Q), и OpenKore её не видит. Зато
OpenKore видит журнал квестов (`$questList`), поэтому этап определяется по номеру квеста
(`db/re/quest_db.yml`: Knight 9000–9012, Priest 8009–8016).

## 2. Swordman → Knight (`npc/jobs/2-1/knight.txt`)

**Требования:** job ≥ 40 (`:125`) и ни одного свободного очка навыков (`:132`, снова перед сменой `:305`).
Базовый уровень и зени скрипт не проверяет. Предметы просит Sir Andrew, набор выбирается случайно
(`rand(1,2)`, `:576-584`):

| набор | квест | предметы (по 5 штук) |
|---|---|---|
| A | 9001 | 1040 Elder Pixie's Moustache, 7006 Wing of Red Bat, 931 Orcish Voucher, 1057 Moth Dust, 903 Reptile Tongue, 1028 Mane |
| B | 9002 | 1042 Bug Leg, 950 Heart of Mermaid, 1032 Maneater Blossom, 966 Clam Flesh, 7031 Old Frying Pan, 946 Snail's Shell |

**Job 50 отменяет предметы.** При job 50 Sir Andrew сразу ставит KNIGHT_Q 4 и квест 9003 (`:547-566`).
Решать нужно до первого разговора с ним: после rand при KNIGHT_Q 2 или 3 уровень больше не проверяется
(`:607-645`). Ни один из 12 предметов не продаётся. По `db/re/mob_db.yml` в каждом наборе есть
предметы, которые падают только с монстров уровня 53–62 и выше (Moth Dust, Mane, Reptile Tongue;
Heart of Mermaid, Maneater Blossom, Old Frying Pan). Для Arkady (base ~41) планировщик поэтому
советует job 50.

Все NPC стоят в здании рыцарского ордена: prt_in, вход с prontera 45,346 → prt_in 80,113.

| этап | NPC (prt_in) | квест до → после | ответы: select «текст» | при неудаче |
|---|---|---|---|---|
| apply | Captain Herman 88,101 (`:45`) | — → 9000 | 1 «I want to change my job to a Knight.» (`:102`), 1 «Yes, I would like to apply.» (`:123`) | job < 40 или есть очки — close, без изменений |
| andrew_items | Sir Andrew 75,107 (`:469`) | 9000 → 9001/9002 (rand) или 9003 (job 50) | 1 «I would like to take the test.» (`:527`) | — |
| andrew_deliver | Sir Andrew | 9001/9002 → 9003 | меню нет: скрипт проверяет `countitem` и сам забирает предметы (`:619-631`) | предметов не хватает — close |
| siracuse_quiz | Sir Siracuse 71,91 (`:673`) | 9003 → 9004 | 1 «Sir Andrew sent me…» (`:809`) или повтор 1 «I wish to take the test again.» (`:841`); 4 Flamberge (`:866`); 3 Provoke Lv.10 (`:880`); 3 Spear Boomerang Lv.3 (`:890`); 1 Zephyrus (`:903`); 2 «80 % of normal attack speed» (`:918`); 1 «Tell the Novice of a reasonable hunting area.» (`:935`); 1 «Protect everyone in the front of the battle.» (`:958`); 1 Honor (`:982`) | любой неверный ответ — KNIGHT_Q 5, повтор |
| windsor_arena | Sir Windsor 79,94 (`:1058`) | 9004 → 9005/9006 → 9007 | 1 «Sir Siracuse sent me to you.» (`:1101`) или повтор 1 «I want to try again!» (`:1106`) → warp job_knt 89,101 | см. ниже |
| amy_etiquette | Lady Amy 69,107 (`:1449`) | 9007 → 9008 → 9009 | 1 «Sir Windsor told me to--» (`:1549`) / повтор (`:1588`); дальше 10 вопросов, за верный +10, нужно 90 или 100 (`:1718-1738`): 3, 1, 1, 1, 3, 3, 3, 2, 1, 2 (тексты в JSON) | < 90 — KNIGHT_Q 9, повтор |
| edmond_patience | Sir Edmond 70,99 (`:1791`) | 9009 → 9010 → 9011 | 1 «Lady Amy sent me.» (`:1862`) или повтор 1 «I'm sorry, I didn't mean to...» (`:1903`) → warp job_knt 143,57 | см. ниже |
| gray_interview | Sir Gray 87,92 (`:2006`) | 9011 → 9012 | 1 (`:2127`) / повтор 1 «I've been thinking a lot.» (`:2163`); 1 «To become stronger...» (`:2190`); 3 «I can protect others.» (`:2200`); 2 «There are those waiting for me.» (`:2314`); 1 «My friends.» (`:2346`): сумма 0, проходит 0/5/10 (`:2411-2452`) | KNIGHT_Q 13, повтор |
| captain_final | Captain Herman | 9012 → Knight | меню нет. `completequest 9012`, `Job_Change`, награда 7 × Awakening Potion (`:429-438`) | есть очки — close |

**Арена Windsor** (`:1153-1445`). В job_knt у Windsor Benedict (89,106) открыта комната ожидания
«Waiting Room» на 20 мест, событие срабатывает от одного вошедшего (`:1232`). Вход в комнату
переносит на 43,146. Дальше три волны, на каждую по 180 с (`OnTimer180000`). В renewal (`checkre(0)`)
так: волна 1 — 8 монстров (Piere ×2, Andre ×2, Deniro ×2, Argos ×2), затем перенос на 43,52; волна 2 —
6 монстров (Frilldora ×2, Drainliar ×4), затем 143,152; волна 3 — Goblin ×5, затем KNIGHT_Q 8,
квест 9007 и выход в prt_in 80,100. Кто не успел, того `areawarp` выбрасывает в prt_in 80,100,
KNIGHT_Q остаётся 7, и пробовать можно снова через Windsor. Уровни монстров: Argos 47, Drainliar 47,
Frilldora 57, Goblin 44–56. Телепорт на карте запрещён (`npc/mapflag/noteleport.txt:144`), nopenalty
нет, то есть смерть стоит опыта. Hideonnpc у Windsor Benedict ставит только OPTION_HIDE, комнату
клиент всё равно видит (`src/map/npc.cpp:1077-1084`, `src/map/clif.cpp:5094-5099`).

**Тест терпения Edmond.** На job_knt 143,57 стоят пассивные Poring, Lunatic, Chonchon и Thief
Mushroom (Ai 01/02/06). Таймер `Timer#knt` общий, он не запускается при входе: на 300-й секунде
цикла включается `Warp#knt` (OnTouch, радиус 22, `:1993-2001`), который ставит KNIGHT_Q 12 и
переносит в prt_in. Включённый OnTouch-NPC срабатывает и для того, кто уже стоит в зоне
(`npc.cpp`: проверка «standing on a OnTouchArea» после enablenpc). Если убить любого монстра,
переносит на prt_fild05 353,251 (`:1988-1989`). Ждать придётся до ~5 минут с attackAuto 0.

## 3. Acolyte → Priest (`npc/jobs/2-1/priest.txt`)

**Требования:** job ≥ 40 (`:230`), нет свободных очков навыков (`:238`, `:493`). Предметы и зени не
нужны. Rosary (2608) проверяется только у Priest-помощника (`:85`). **Job 50** отменяет паломничество:
после заявки сразу начинается испытание (`:264-289`). Награда — Book (1550), а при job 50 Bible
(1551) (`:520-526`).

| этап | где | квест | ответы | при неудаче |
|---|---|---|---|---|
| apply | High Bishop (Bishop Paul), prt_church 16,41 (`:35`) | — → 8009 → 8010 (или 8011 при job 50) | 1 «I want to be a Priest.» (`:214`), 1 «Yes, I do.» (`:229`), при job 50 ещё 1 «I am ready.» (`:279`) | job < 40 / очки — close |
| pilgrimage | Rubalkabara prt_fild03 365,255 → Mathilda moc_fild07 41,355 → Yosuke prt_fild00 208,218 | 8010 (журнал не меняется) | меню нет | — |
| spiritual_test | Bishop → job_prist | 8010/8011 → 8012 → 8013 | Bishop: 1 «I'm ready.» (`:412`, `:431`) или 1 «I'll try again.» (`:453`); Peter (job_prist 24,187): 1 «Yes, I do.» (`:912`), 1 «I'm ready.» (`:940`/`:984`); демоны: 2/2/2/2/2/2/2 (см. ниже) | см. ниже |
| oath | Sister Cecilia, prt_church 27,24 (`:535`) | 8013 → 8014 → 8015 | строго по порядку: Yes, No, Yes, Yes, No, No, Yes, «I do.» (`:721-811`) | PRIEST_Q 8, повтор теми же ответами |
| bishop_final | Bishop | 8015 → Priest | меню нет (`:503-515`) | есть очки — close |

**Паломничество** журнал не меняет: квест всё время 8010. Порядок обязателен, но повторный визит
ничего не ломает: при другом PRIEST_Q паломник только говорит и закрывает диалог (`:1749`, `:1793`,
`:1853`). Поэтому бот проходит всех троих по порядку. Успех определяется по тексту Yosuke «Hereby, the
first of…» (`:1845`) или «I told you to go back to church.» (`:1854`). У каждого паломника скрипт
**меняет точку сохранения** на поле (`savepoint`, `:1741`, `:1789`, `:1849`). После квеста нужно
перезаписаться у Kafra в городе.

**Испытание** (`:847-1723`):
1. Зомби. Peter переносит на job_prist 24,44. На полосах y = 52, 62, 72, 82, 92 появляются 13 Zombie
   (уровень 17, агрессивные). Выход 24,109 пускает дальше только при `.MyMobs < 1`. Через 300 с
   (`OnTimer300000`) всех выбрасывает в prontera 234,318, PRIEST_Q остаётся 5/6, и повтор снова
   через епископа.
2. Коридор искушений (168,17 → 168,180). Диалоги начинают сами NPC (OnTouch, зона 8×1). Верный
   ответ всегда второй: Deviruchi «Out of my sight, demon!» (`:1350`), «Silence!» (`:1380`);
   Doppelganger «No deal... Doppelganger.» (`:1436`), «I'll never listen to you!» (`:1457`);
   Dark Lord «God will protect me.» (`:1506`), «Begone, vile fiend!» (`:1519`); Baphomet
   «No, Baphomet. You lose.» (`:1578`). Неверный ответ выбрасывает на опасные карты (c_tower2,
   mjolnir_05, gef_dun02, gl_church, glast_01).
3. Зал мумий (98,40 → 98,105). Шесть Mummy (уровень 55, агрессивные) появляются на полосах y = 55,
   70, 85. Убивать их не нужно: на 98,105 ставится PRIEST_Q 7, квест 8013 и перенос в prt_church
   16,37. Для Acolyte уровня 30 это опасно. На job_prist действует nopenalty
   (`npc/mapflag/nopenalty.txt:157`).

## 4. Как OpenKore проходит диалоги

- `talknpc <x> <y> [последовательность]` (`Commands.pm:6026`, `Task::TalkNPC`). Шаги: `c` (далее),
  `r#` (пункт меню **с 0**, то есть r = select − 1), `r~/regex/`, `r=текст`, `n`, `w#`, `if~/regex/,шаг`.
  Без последовательности задача только начинает диалог и ждёт команд.
- `talk resp <n>` добавляет шаг в текущую задачу NPC (`Commands.pm:5870-6024`).
- `autoTalkCont 1` сам нажимает «далее» (`TalkNPC.pm:501-512`). В профилях сейчас стоит 0.
- Диалог, начатый NPC (OnTouch), OpenKore оформляет задачей `autotalk` (`Misc.pm:6929`, хук `npc_autotalk`).
- Комнаты ожидания: `%chatRooms` / `@chatRoomsID`, команда `chat join <#>`.
- Журнал квестов: `$questList`. Очки навыков: `$char->{points_skill}`.
- Поля `fields/job_knt.fld2.gz` и `fields/job_prist.fld2.gz` в OpenKore есть, ходить по тестовым
  картам бот может. Все точки `move`/`walk` из JSON проверены на проходимость (`FieldsTest`), а
  связность — BFS при разработке (например prt_church 27,19 → 17,39). В prt_church к епископу ведёт
  внутренний портал 90,81 → 27,19 (`npc/warps/cities/prontera.txt:76`, в `tables/portals.txt` он есть).

**Почему ответы идут по тексту, а не готовой строкой `r0 r3 …`.** Число меню зависит от ветки (Peter
при PRIEST_Q 5 и 6, заявка Priest при job 50, первый и повторный визит). Плагин отвечает в хуке
`npc_talk_responses`: берёт первый ответ шага, текст которого точно совпал с пунктом меню. Для
Cecilia ответы одинаковые («Yes.» / «No.»), там включён `ordered`, и ответ берётся строго по порядку.
Если меню не из сценария, плагин отвечает `talk no` и этап проваливается: наугад он не выбирает
(AUT-078). То же правило есть в Python (`progression.choose_answer`), и тест разбора проверяет, что на
каждое меню из скрипта оно даёт ровно указанный пункт.

### Плагин jobChange (не проверен в игре)

`jobChange::start({id, path, stage, steps, success})` исполняет шаги одного этапа: `move`, `talk`,
`chat_join`, `fight`, `wait`, `walk` (с `autotalk`), учитывая `unless_map`. На время этапа он ставит
`lockMap` пустым, `route_randomWalk 0`, `autoTalkCont 1`, а `attackAuto` выбирает по шагу: 2 в бою,
0 в тесте терпения и в зале мумий. Потом возвращает прежние значения. Успех проверяется по `success`
(квест в журнале, профессия, карта/точка, текст NPC). Итог приходит событием
`job_change_result {id, path, stage, step, ok, reason}` через `brainBridge::event`.
`jobChange::status()` возвращает `{running, stage, step, quests, skill_points, items}`.

Ограничения: исход боя на арене и у зомби зависит от силы персонажа. Плагин его не гарантирует: при
провале он сообщает об этом, повтор решает мозг. Пункт «Cancel Chat» распознаётся по английскому
тексту (OpenKore без перевода).

## 5. Планировщик `progression.py`

Все функции чистые. Входной `state` — как у brainBridge: `job` (имя OpenKore: Swordsman, Acolyte…),
`lv`, `job_lv`, `zeny`, `sex`, `items`. Необязательные поля: `skill_points`, `quests`,
`equip {weapon|Armor|Left_Hand|Shoes|Garment|Head_Top: id}`, `job_change` (статус плагина).
Отсутствующее поле считается «неизвестным» и не засчитывается как выполненное.

- `plan(state, data, now, done)` строит цель: этапы (`job_lv`, при трудных наборах `job_skip`,
  `skill_points`, этапы квеста) и следующий шаг `next {kind, text, target, have, expires, …}`.
  Срок годности задаётся по виду шага (`goals.ttl_hours`). `unreachable` выставляется, если в
  выбранном наборе есть предмет без магазина и без монстра, появляющегося хоть на одной карте.
- `readiness(state, data)` возвращает `missing` (job_lv, skill_points, предметы с источником и лучшим
  монстром), `unknown` (нет skill_points в state, набор Sir Andrew ещё не выбран) и `unreachable`.
- `current_stage` / `stage_action` определяют этап по журналу квестов. Действие для тела —
  `{action: "job_change", path, stage, steps, success}`. Пока требования этапа не выполнены (job,
  очки, предметы), действия нет.
- `item_sources` / `farm_maps(item, data, lv, atlas)` показывают, где взять предмет. С атласом
  (`live_brain.atlas`) карты сортируются по `danger_for`, карты вне атласа оцениваются по уровню монстра.
- `next_equipment(state, budget)` находит вещь из магазинов Пронтеры и Изюда (prontera, prt_in,
  prt_church, izlude, izlude_in), которую профессия носит (`Jobs`) на уровне `lv` (`EquipLevelMin`),
  подходящую по полу, не дороже бюджета и лучше надетой (ATK для оружия, DEF для брони). Слоты
  перебираются по порядку `equipment.slots`. Тип оружия задаётся классом: Swordsman — 1hSword
  (SM_SWORD в профиле), Knight — 2hSword (двуручный исключает щит), Acolyte/Priest — Mace.
  Пример: Swordsman lv 40 с бюджетом 20000 получает Scimitar (ATK 85, 17000z, prt_in Weapon
  Dealer). Карты, заточка и ATK по формуле renewal не учитываются.
- `summary(state)` выдаёт одну строку для промпта модели.

Каталог берёт магазины тем же разбором NPC, что атлас (`gen_atlas.collect_npcs`: duplicate,
выгрузка старых торговцев при `feature.barter`), а монстров считает по всем загруженным спавн-файлам.
Регион атласа для доступности не используется: в нём, например, нет iz_dun02, где живёт Obeaune.

## 6. Выведено из скрипта / не проверено

**Выведено из скрипта и проверено тестом разбора:**
- какие файлы грузятся в renewal; требования job 40, очки навыков = 0, отсутствие зени и базового уровня;
- все 57 ответов (номер пункта и текст сверены со строкой меню), последовательность NPC с координатами,
  номера квестов каждого этапа, варианты при неудаче и повторе, случайный выбор набора и его номера квестов;
- пропуск предметов (Knight) и паломничества (Priest) при job 50;
- проходимость всех точек маршрута по полям OpenKore.

**Не проверено в игре / требует доработки:**
- ни один этап не проходился ботом на сервере;
- что `talknpc x y` без последовательности и `talk resp` из хука ведут себя так, как описано (по коду
  OpenKore это так, но прогона не было);
- что OpenKore видит «Waiting Room» на скрытом NPC и входит в неё (по коду rAthena видит);
- арена Knight: хватит ли силы Arkady на Argos/Frilldora/Goblin за 3 минуты. Оценка, не из скрипта:
  нужен заметно более высокий base, чем 41;
- зал мумий для Vera (base ~30) и зомби за 5 минут;
- прохождение через полосы спавна (OnTouch) маршрутом OpenKore;
- тест терпения: что бота не тронут пассивные монстры и что он простоит 5 минут без действий.

## 7. Что нужно для интеграции

1. **brainBridge**: действие `job_change` передавать в `jobChange::start` (как `give` в economy). В
   state добавить `job_change => jobChange::status()`, а также `skill_points` и `equip` (слоты →
   nameID). Плагин добавить в `loadPlugins_list` (`bots/*/control/sys.txt`).
2. **safety**: `job_change` положить в PLAN_ACTIONS (только от правил, не от модели). Path и stage
   брать только из progression.json, шаги на стороне мозга не править.
3. **mind/routine**: проверять `plan()` раз в N минут и на событиях level_up и job_level_up. Идти сдавать
   этап, если: режим town (отдых в городе), `stage_action()` не None (все требования есть), нет
   активного плана встречи, HP > safe_hp. Арбитр — owner `"plan"`, чтобы распорядок не прислал `hunt`
   посреди квеста. На время этапа охоту и follow не включать. После `job_change_result`: ok — записать
   этап в память (для паломничества — `done`), иначе разобрать reason, повтор не чаще раза в час, после
   3 провалов подряд отложить цель (`expires`) и сообщить владельцу.
4. **economy/items_control**: предметы набора Sir Andrew (12 ID) не продавать и не складывать (сейчас
   `sellAuto` продаёт всё, что не помечено). Учитывать их в `itemCounts` (или брать из
   `job_change.items`).
5. **Очки навыков**: профиль Swordsman в `bots/combat/classes.json` расходует 35 очков, а на job 40
   их 39. К тому же SM_ENDURE требует SM_PROVOKE 5 (`db/re/skill_tree.yml`). Если raiseSkill не
   возьмёт нужные навыки, останутся свободные очки, и капитан откажет (`knight.txt:132`).
6. **После смены профессии** combatProfile сам выберет профиль Knight/Priest по jobID. Точку сохранения
   после паломничества переписать у Kafra.
7. **Снаряжение**: `next_equipment(state, budget)` с бюджетом, например, 70% зени (`max_share_of_zeny`).
   Покупка и надевание — отдельная задача: купленная вещь должна быть защищена от продажи
   (economy уже держит оружие и броню на складе по умолчанию).

## 8. Новые задачи для backlog

- **AUT-121 · P1 — Прогон этапа квеста в лаборатории.** Один простой этап (заявка Priest или Siracuse)
  на локальном сервере с логами jobChange. **Готово:** квест в журнале сменился, событие ok совпало с
  `$questList`.
- **AUT-122 · P1 — State для прогрессии.** skill_points, журнал квестов, надетые вещи по слотам в
  state brainBridge. **Готово:** `progression.readiness` без «неизвестно» на живом боте.
- **AUT-123 · P1 — Защита квестовых предметов.** items_control и подбор лута для 12 предметов Sir
  Andrew. **Готово:** предмет из набора не продан и не выброшен за сутки охоты.
- **AUT-124 · P1 — Сбор предметов по плану.** Цель collect_items → карта из `farm_maps` с допустимым
  риском, счётчик и срок. **Готово:** 5 предметов получены фактически или цель снята по сроку.
- **AUT-125 · P2 — Сила для испытаний.** Критерий готовности к арене Knight и залу мумий: base, HP,
  зелья, доля побед на картах похожего уровня (map_stats). **Готово:** слабый житель не идёт на арену.
- **AUT-126 · P2 — Покупка снаряжения.** Поход к магазину из `next_equipment`, покупка, надевание,
  проверка сервером. **Готово:** вещь надета, старая не продана без правила.
- **AUT-127 · P2 — Профили очков навыков под job 40/50.** Профиль Swordsman/Acolyte без остатка очков
  и с prerequisites. **Готово:** на job 40 свободных очков 0.
- **AUT-128 · P2 — Перезапись точки сохранения.** После паломничества и других квестов с `savepoint`
  вернуть сохранение в город. **Готово:** после смерти житель появляется в городе.

## 9. Novice → первая профессия (newborn)

**Статус.** Выведено из скриптов rAthena и проверено тестами разбора (`brain/tests/test_newborn.py`, общие
`ScriptRefsTest`/`FieldsTest` и `bots/tests/job_change.t`). В игре ни один этап не проходился.

### Что грузит renewal

`npc/re/scripts_main.conf:39` импортирует `npc/re/scripts_jobs.conf`, а он в `:7-12` грузит
`npc/re/jobs/1-1/{acolyte,archer,mage,merchant,swordman,thief}.txt`. Старых pre-renewal квестов
(`npc/jobs/1-1`) в дереве нет. **Испытаний, предметов и платы в renewal нет**: тестов на знания, полосы
препятствий Thief, сбора грибов и зелий Mage/Archer, взноса Merchant. Везде одно условие —
`F_CanChangeJob` (`npc/other/Global_Functions.txt:626-628`): `!basicskillcheck() || NV_BASIC > 8`, а
`basic_skill_check: yes` (`conf/battle/player.conf:47`). NV_BASIC 9 — это все 9 очков навыков Novice, они
есть только на job 10. Профиль Novice в `bots/combat/classes.json` уже учит `NV_BASIC 9`. Поэтому требования
в JSON такие: `job_lv 10`, `skill_points 0`. Журнал квестов скрипты не трогают, и успех проверяется по
профессии (`success.job`, имя OpenKore).

| путь | NPC (карта x,y) | ответы: select «текст» | смена / награда | маршрут |
|---|---|---|---|---|
| swordman | Swordman#swd, izlude_in 74,172 (`swordman.txt:15`) | 2 «I want to be a Swordman.» (`:65`), 1 «Yes, I do.» (`:107`) | `:119` / N_Falchion 13415 (`:120`) | **blocked** — izlude |
| acolyte | Cleric#aco, prt_church 184,41 (`acolyte.txt:16`) | 1 «Change your job to acolyte.» (`:65`) | `:89` / N_Mace 1545 | ok, 2 перехода |
| merchant | Merchant#mer, alberta_in 53,43 (`merchant.txt:15`) | 1 «I want to be a merchant.» (`:59`) | `:93` / N_Battle_Axe 1381 | ok, 10 переходов (через Пайон) |
| archer | Archer Guildsman#archer, payon_in02 64,71 (`archer.txt:15`) | 1 «I want to be an Archer.» (`:65`) | `:95` / N_Composite_Bow 1742 и колчаны | ok, 7 переходов |
| thief | Thief Guide#thief 39,129 → Thief Guildsman#thief 42,133, moc_prydb1 (`thief.txt:15`, `:157`) | Guide: 1 «I want to be a Thief.» (`:92`), 1 «Yes.» (`:106`), 1 «Yes, I do.» (`:130`) → `q_job_thief 1` (`:153`); Guildsman без меню | `:167` / N_Main_Gauche 13041 | **blocked** — нет пешего пути в Морокко |
| mage | Mage Guildsman#mage, geffen_in 164,124 (`mage.txt:15`) | 1 «I want to be a Mage» (`:67`), 1 «I want to be a Mage.» (`:81`) | `:106` / N_Rod 1639 | ok, 6 переходов |

У Mage два меню, пункты которых отличаются только точкой. Ответ выбирается по точному тексту
(`choose_answer`), поэтому путаницы нет: это проверяет тест разбора на реальных строках `select(...)`.

### Маршруты (`paths.*.route`)

Плагин делает `move x y map`, маршрут строит OpenKore по своим полям (`fields/*.fld2.gz`) и
`tables/portals.txt`. Найдены расхождения этих данных с renewal-сервером, поэтому для каждого пути записан
маршрут от точки старта (prontera 156,180), найденный `scripts/okroute.py`. Тест `MapsTest.test_routes`
проверяет каждый переход: он есть в `portals.txt` и как warp сервера (`npc/re/warps`, в пределах 3 клеток);
размер карты в OpenKore совпадает с `db/re/map_cache.dat`; клетки между переходами связны по полю OpenKore
(с клеткой прибытия по серверу). OpenKore может выбрать другой маршрут: проверено только, что хотя бы один
существует. Опасность оценена только по атласу: монстры на полях маршрутов до 29 ур., агрессивные есть только на
prt_fild04 (путь Mage, до 18 ур., 0,7 % монстров). Самый
короткий путь в Алберту идёт через moc_fild03 (агрессивные до 47 ур.), поэтому у Merchant есть промежуточный
`move` в payon 22,143. Длинные переходы получили `time_limit` 1800 с вместо 900 с по умолчанию: плагин берёт
`time_limit` у любого шага.

- **Swordman: blocked.** В renewal izlude перестроен: у сервера `izlude` 268x300, у OpenKore поле 268x268, а
  переходы в `portals.txt` старые (`izlude 52 140 → izlude_in`, `prt_fild08 371 212 → izlude 30 78`; у сервера
  `npc/re/warps/cities/izlude.txt:27` izlude 52,172 и `:22` → izlude 24,98). Поле поправить можно строкой
  `field_izlude izlude_a` в `servers.txt` (`src/Field.pm:856`, `izlude_a.fld2` совпадает с сервером с точностью
  до 184 клеток). Переходы — только патчем `tables/portals.txt` (`server/patches/openkore`), это не сделано.
- **Thief: blocked.** Пешего пути prontera → morocc по простым переходам `portals.txt` нет. Есть только
  телепорт Kafra (`portals.txt`: `prontera 146 89 morocc 156 47` с диалогом и платой), а его сверка с
  renewal-скриптом `npc/re/kafras` не делалась. От morocc до гильдии путь есть (`route.hops` от morocc 156,47),
  но он идёт через подземелье moc_pryd01 (монстры 24–30 ур.).
- Arkady и Vera уже Swordsman/Acolyte, им blocked не мешает.

### Выбор пути по жителю

`classes.Novice.next_by_target` связывает цель с путём: Swordsman → swordman и т. д. Цель задаётся полем `job`
персоны, а без него — записью жителя в `brain/world/roster.json` по имени персоны (`job`, иначе `job`
шаблона). Функции `progression.target_job(name)`, `path_for(state, data, target)`, а у `plan`, `readiness`,
`current_stage`, `stage_action`, `summary` есть параметр `target`. `career.py` один раз вычисляет `target` при
загрузке данных и передаёт его дальше. `blocked(state, data, path)` даёт причину, по которой этап не
запускается: учебный полигон или `route.status: blocked`. В этом случае `stage_action` возвращает None, а
цель (`plan.next`, сводка в промпте) честно об этом сообщает.

Id этапа — `first_job`, а не `apply`: `career` хранит пройденные этапы без пути (`done`), и `apply` Novice
пропустил бы потом `apply` у Knight/Priest.

### Не проверено / осталось

- Ни один этап в игре не проходился; `auto_job_change` по-прежнему false.
- Archer получает колчаны (`archer.txt:97-99`), а не стрелы: тело их не открывает, боя луком без стрел нет.
- После смены профессии combatProfile выбирает профиль по jobID. Профили Merchant/Archer/Thief/Mage есть в
  `bots/combat/classes.json`, в игре не проверялись.
- `next_equipment` для Novice предлагает Knife из izlude_in — магазин на заблокированной карте.
- **NB-1 · P1 — Патч izlude для OpenKore** (`server/patches/openkore`: переходы izlude в `portals.txt`,
  `field_izlude izlude_a`). **Готово:** `okroute.py` находит путь prontera → izlude_in 73,172, а тест
  `MapsTest` снимает blocked у swordman.
- **NB-2 · P2 — Путь в Морокко.** Пеший маршрут или сверенный телепорт Kafra (renewal-диалог, цена).
  **Готово:** thief route ok.
- **NB-3 · P2 — Прогон Novice → Acolyte в лаборатории** (самый короткий маршрут). **Готово:** профессия
  сменилась, событие `job_change_result ok` совпало с jobID.
