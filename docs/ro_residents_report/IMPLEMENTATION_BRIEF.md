# IMPLEMENTATION BRIEF — этапы 0 и 1: свидетель и один персонаж с подтверждаемым действием

Это самостоятельное задание разработчику. Остальные этапы **не реализуются**. Контекст: ARCHITECTURE.md §2–§3.6 и §5; причины, по которым всё устроено так строго, — RESEARCH.md §5.

## 1. Цель

Доказать в игре самое узкое звено. Один житель (обычный игровой персонаж под OpenKore):

- передаёт оркестратору свежее состояние;
- выполняет **ограниченные действия** `travel_to` и `whisper`;
- каждое действие проходит полный жизненный цикл: `planned → dispatched → accepted → running → verifying → confirmed | failed | timed_out | cancelled | unknown → (сверка)`;
- `confirmed` ставится **только** по свидетельству сервера rAthena, а не по ack тела.

## 2. Входит в объём

### 2.1 Этап 0 — свидетель на сервере (без изменения ядра rAthena)

1. **`conf/import/log_conf.txt`:** `log_zeny: 1`; `log_chat` с битами global|whisper|party (= 7; проверить фактическую семантику, U9 в RISKS); `log_commands: yes`. Остальное — по умолчанию.
2. **Схема `residents`** в MariaDB:
   - `registry(char_id INT PRIMARY KEY, resident_id VARCHAR(24) UNIQUE, enabled TINYINT)`;
   - `resident_events` — по ARCHITECTURE §3.3.
3. **NPC-скрипт `npc/custom/residents/witness.txt`:**
   - `OnInit`: загрузить char_id жителей из `registry` в массив NPC;
   - `OnPCLoginEvent`, `OnPCLogoutEvent`, `OnPCDieEvent`, `OnNPCKillEvent`, `OnPCBaseLvUpEvent`, `OnPCJobLvUpEvent`, `OnPCLoadMapEvent`: только для char_id из списка → `query_sql "INSERT INTO residents.resident_events ..."` с `getmapxy`;
   - mapflag `loadevent` — на картах теста (`prontera`, `prt_fild08`, `izlude` или `payon` — выбрать по фактическому набору карт сервера);
   - `OnTimer5000`: позиционная проба онлайн-жителей, запись только при сдвиге > 3 клеток или смене карты.
4. **БД-пользователь `residents_ro`:** только `SELECT` на `residents.*`, `<logs_db>.picklog`, `zenylog`, `chatlog`, `atcommandlog`, `<main_db>.char`, `party`.
5. **Замеры** RSS и CPU rAthena и MariaDB в простое (U2).

### 2.2 Этап 1 — тело

Плагин OpenKore `residentBody`. Пишется заново. Из live_ro разрешено переносить только проверенные фрагменты: подключение к Unix-сокету и разбор JSON-строк из `brainBridge.pl`, вычисление позиции через `Utils::calcPosition` из `economy.pl:172`, `lowHpGuard` и `survival` как отдельные плагины-рефлексы.

1. **`hello`, `telemetry` раз в 1 с, `event`** — по протоколу ARCHITECTURE §3.1:
   - позиция только через `Utils::calcPosition($char)`, отдельно `dest` = `pos_to`;
   - `seq` монотонный;
   - `body_epoch` — новый UUID при каждой загрузке плагина.
2. **Навык `travel_to {map, x?, y?, r, deadline_ts}`:**
   - создать задачу через `$char->route($map, $x, $y, maxRouteTime => …, distFromGoal => $r, notifyUponArrival => 1)` (`openkore/src/Actor.pm:838-867`) и **держать ссылку на объект задачи**;
   - по завершении: статус `DONE` без ошибки → `skill_result done code=ARRIVED`;
   - `TOO_MUCH_TIME` → `TIMEOUT`, `CANNOT_CALCULATE_ROUTE` → `NO_ROUTE`, `STUCK` → `STUCK` (коды из `openkore/src/Task/Route.pm:44-58`);
   - смерть → `DIED`, отмена → `CANCELLED`;
   - до старта проверяется проходимость клетки, если персонаж на той же карте → `skill_rejected code=UNWALKABLE`.
3. **Навык `whisper {to_name, text}`:**
   - `pm "<name>" <text>` после проверки длины и символов (как `brainBridge.pl:645-650`);
   - итог по хуку `packet_pre/private_message_sent`: `0` → `SENT`, `1` → `OFFLINE`, `2` → `IGNORED`, `3` → `REFUSED` (`brainBridge.pl:464-475`).
4. **Режимы `IDLE_SAFE` и `TRAVEL`** — таблица ARCHITECTURE §3.2:
   - ключи меняются **только в памяти** (`$config{…} = …`), никаких `conf` и `configModify`;
   - `config.txt` перед стартом и после теста должен иметь ту же контрольную сумму;
   - если OpenKore не подхватывает изменение в памяти — зафиксировать как U3 и перейти на runtime-копию профиля, пересоздаваемую при старте.
5. **Леджер** последних 500 `action_id`/`idem_key` с итогами (в памяти и `run/<resident>/ledger.jsonl`). Повтор → `skill_rejected code=DUPLICATE`, `prev_outcome`.
6. **Сторож связи:** 60 с без оркестратора → отмена текущего навыка и режим `IDLE_SAFE`.
7. **E-stop:** файл `run/ESTOP` или сообщение `safe_stop` → отмена навыка и `IDLE_SAFE` за ≤ 2 с.

### 2.3 Этап 1 — оркестратор (Python 3.11+, только stdlib + драйвер MariaDB)

1. **BodyGateway:** Unix-сокет, по одному на жителя, права 600. Разбор протокола, `skill_start`/`skill_cancel`/`safe_stop`.
2. **WitnessReader:** опрос раз в 1 с, курсор `id > last_id` по `resident_events`, `picklog`, `zenylog`, `chatlog`, `atcommandlog`. Курсор и вставка событий — одна транзакция SQLite.
3. **Журнал (SQLite WAL):** таблицы `events` (уникальный `(source, source_key)`), `actions`, `action_transitions`, `operator_log`.
4. **StateStore:** нормализованное состояние (ARCHITECTURE §10), у каждого поля `source` и `ts`. При расхождении тела и сервера по карте, онлайну или смерти пишется событие `divergence`.
5. **SkillExecutor** — жизненный цикл ARCHITECTURE §3.6:
   - предусловия: телеметрия ≤ 3 с, жив, нет действия в работе;
   - **критерий `confirmed` для `travel_to`:** событие свидетеля `loadmap` или `pos` на нужной карте (если карта меняется) **и** позиция по телеметрии в пределах `r`. Для перемещения внутри карты — позиционная проба свидетеля в пределах `r + 3` или, если пробы нет за окно, `unknown` (не `confirmed`!);
   - **критерий для `whisper`:** `SENT` от тела **и** строка в `chatlog` (whisper, char_id отправителя, адресат, текст) в окне 10 с;
   - `unknown` → сверка; без доказательства за 60 с → `escalated` и оповещение.
6. **CLI:**
   - `residents status`;
   - `residents run <resident> travel_to --map … --x … --y … --r 3 --deadline 420`;
   - `residents run <resident> whisper --to … --text …`;
   - `residents cancel <action_id>`;
   - `residents trace <action_id>`;
   - `residents safe-stop`;
   - `residents export --since … --until …` — пакет доказательств.
7. **Без LLM, без планировщика, без памяти и отношений.** Команды даёт только оператор через CLI.

## 3. Не входит (запрещено в этой задаче)

- Охота, обслуживание, группа, сделки, лечение, социальные модули, договорённости, LLM, голос, несколько жителей.
- Изменения ядра rAthena или исходников OpenKore. Нужен патч — остановиться и согласовать.
- Любые GM-команды над жителем во время тестов, кроме создания персонажа до теста. Записи в БД со стороны оркестратора. Команда `conf` OpenKore.
- Обновление пинов, PACKETVER, upstream.
- Перенос модулей live_ro `brain/live_brain/*` целиком.

## 4. Матрица проверок в игре

Тесты проводятся на стенде владельца. Рядом стоит тестовый персонаж-человек: он нужен для шёпотов и наблюдения.

| # | Сценарий | Повторы | Ожидаемый итог |
|---|---|---|---|
| T1 | `travel_to` в пределах карты (`prontera` в точку у фонтана) | 5 | `confirmed ARRIVED`, проба свидетеля ≤ `r+3` |
| T2 | `travel_to` на соседнюю карту (`prontera` → `prt_fild08`) | 5 | `confirmed ARRIVED`, `loadmap` свидетеля |
| T3 | `travel_to` через 2–3 перехода (`prontera` → `payon` или `izlude`, по набору карт сервера) | 5 | `confirmed ARRIVED` |
| T4 | Цель — непроходимая клетка или несуществующая карта | 2 | `rejected UNWALKABLE` или `failed NO_ROUTE` с причиной |
| T5 | Дедлайн заведомо меньше пути | 1 | `timed_out` / `failed TIMEOUT`, тело в `IDLE_SAFE` |
| T6 | `cancel` посреди маршрута | 1 | `cancelled`, тело стоит в `IDLE_SAFE` |
| T7 | Убить оркестратор посреди маршрута, поднять через 30 с | 2 | `unknown` → сверка → `confirmed` (если дошёл) или `failed`; без повторной отправки |
| T8 | Убить OpenKore посреди маршрута, поднять | 1 | новый `body_epoch`; действие `unknown` → `failed` (по свидетелю не дошёл); без двойной отправки |
| T9 | Отправить `skill_start` с тем же `idem_key` дважды | 2 | второй — `rejected DUPLICATE`, маршрут один |
| T10 | `whisper` онлайн-человеку / несуществующему или офлайн-имени | 3 / 1 | `confirmed` (`SENT` + `chatlog`) / `failed OFFLINE` |
| T11 | E-stop файлом во время маршрута | 1 | остановка ≤ 2 с, `IDLE_SAFE`, событие в журнале |
| T12 | Контрольная сумма `config.txt` до и после всех тестов | 1 | одинаковая |
| T13 | Маршрут через поле с агрессивными монстрами (по уровню) | 2 | рефлексы отбиваются; итог `ARRIVED` или код (`DIED`, `STUCK`); зависаний нет |

## 5. Что проверить попутно (неизвестные)

- **U1:** RSS и CPU OpenKore за 30 мин.
- **U2:** RSS rAthena и MariaDB.
- **U3:** работает ли изменение `%config` в памяти без записи файла.
- **U4:** задержка от события до строки в `resident_events`.
- **U9:** фактическая семантика `log_chat`.

## 6. Критерии приёмки

1. T1–T3: не меньше 13 из 15 `confirmed`. **Ни одного ложного `confirmed`**, то есть случая, когда свидетель говорит обратное.
2. T4–T13 — все пройдены по ожидаемому итогу.
3. У каждого неуспеха есть код и человекочитаемая причина.
4. Возраст телеметрии p95 ≤ 2 с. Задержка приёма свидетельства p95 ≤ 3 с.
5. Доля `unknown`, не разрешённых сверкой, ≤ 10%.
6. Вмешательств оператора — не больше 1 на весь прогон, не считая команд CLI, которые и есть тест.
7. Пакет доказательств собран (§7).

## 7. Пакет доказательств

- `actions.jsonl` и `action_transitions.jsonl` за прогон;
- `witness_events.jsonl` — выгрузка свидетеля за тот же интервал;
- `metrics.md`: подтверждения по T, доля `unknown`, задержки p50/p95, RSS/CPU;
- `trace_*.txt`: трассы всех неуспехов и 3 случайных успехов;
- `operator_log.jsonl` и выборка `atcommandlog` по char_id жителя (должна быть пустой);
- контрольные суммы `config.txt` до и после.

## 8. Условия остановки и откат

- **Немедленная остановка:**
  - ложный `confirmed`;
  - запись в `config.txt`;
  - двойной маршрут по одному `idem_key`;
  - заметная деградация map-server.
- **Откат:**
  - `residents safe-stop` → остановить тело и оркестратор;
  - убрать загрузку `witness.txt` и import-конфиг журналов, перезапустить map-server в окно владельца;
  - схему `residents` и журнал оркестратора **сохранить** для разбора.

## 9. Бюджет объёма (сигнал остановиться)

Ориентир, а не норма:

- плагин `residentBody` — 500–800 строк Perl;
- скрипт свидетеля — до 200 строк;
- ядро оркестратора — 1,5–2,5 тыс. строк Python плюс тесты разбора протокола и жизненного цикла.

Если объём уходит больше чем в 1,5 раза выше, остановиться и пересмотреть: задача расползается. Это прямой урок live_ro: 29 тыс. строк мозга до первого подтверждённого цикла.

## 10. Вопросы до старта

См. RISKS.md §6. Для этапов 0–1 обязательны пункты 1 (сервер и версии), 2 (разрешение на скрипт, схему и конфиг журналов), 8 (доступ разработчика к журналам стенда) и U6 (состояние персонажей live_ro).

## 11. Definition of Done

Критерии §6 выполнены на стенде владельца. Пакет доказательств лежит в репозитории проекта: без секретов, сырых логов игры и БД. Владелец подписал приёмку этапа 1. Только после этого начинается этап 2 из ROADMAP.md.
