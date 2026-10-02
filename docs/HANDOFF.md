# Передача работы: разработчик ↔ Hermes

## Роли
- **Разработчик** пишет код в GitHub, пушит и передаёт ветку, точный commit и задание на проверку.
  SSH на VPS у него нет и не нужен.
- **Hermes** — оператор VPS и runtime QA. Он проверяет именно этот commit и возвращает
  фактический вывод и ошибки.
- **Git** — общий журнал: статус (`docs/STATUS.md`) и задания (этот файл). Секретов здесь нет.

## Правила
1. Существующие БД (`ro_bot_lab`, `ro_bot_lab_logs`), персонаж Arkady и работающий
   стек не пересоздаются и не заменяются без конкретной причины, согласованной с владельцем.
2. В задании всегда указаны: что изменилось, точные команды, какие процессы
   перезапустить, ожидаемый результат, риски для БД и откат.
3. Ожидаемый результат — не факт. В `STATUS.md` попадает только то, что Hermes прислал.
4. В отчётах и в Git нет паролей. Пароли и логины только в `secrets/live_ro.env` на VPS.

## Формат отчёта Hermes
```
commit: <полный sha>
шаг N: <команда>
вывод: <как есть, без паролей>
итог: OK / FAIL / не выполнялся (почему)
```

---

## Задание №1: управление существующей лабораторией и fallback выживания

**Результат:** PARTIAL PASS, `docs/qa/HERMES-56bdba0.md`. Исправления — в задании №2.

**Ветка:** `claude/stage1-reproducible-delivery`
**Commit:** указан в сообщении разработчика. Это последний commit ветки, менявший этот файл:
`git log -1 --format=%H origin/claude/stage1-reproducible-delivery -- docs/HANDOFF.md`.

### Что изменилось
- `scripts/lab`: режим **existing**. В нём `doctor`, `status`, `db-backup`, `start/stop`
  работают с уже собранным rAthena (`RATHENA_DIR`). `conf/import` не перезаписывается,
  `deploy/activate/db-init` запрещены, запуск от root отклоняется. Запущенные вручную процессы
  находятся без pid-файлов.
- Реквизиты БД читаются из существующего `conf/import/inter_conf.txt`, лог-БД учитывается отдельно.
- Логины и пароли проверяются: не длиннее 23 печатных ASCII-символов без пробелов.
- `bots/bot01/tables/servers.txt`: `addTableFolders kRO/RagexeRE_2018_06_21a;kRO;translated/kRO_english`.
- Fallback выживания bot01: телепорт выключен, плагин `lowHpGuard` (новые цели не выбираются
  при HP < 40% до 90%, отдых без randomWalk), зелья при HP < 50%.
- Env-файл переименован: `secrets/live_ro.env`. Существующие файлы в `secrets/` не трогаются.

### Какие процессы перезапускать
- **login/char/map — не перезапускать.**
- **bot01 — один перезапуск** в шаге 7, чтобы применить профиль из Git.

### Шаги (от `ro-lab`)
```sh
# 1. Отдельный checkout, не поверх runtime
sudo -u ro-lab -H bash
export LAB_ROOT=/opt/ro-bot-lab
mkdir -p $LAB_ROOT/src && cd $LAB_ROOT/src
[ -d live_ro ] || git clone https://github.com/bbutterm/live_ro.git
cd live_ro && git fetch origin
git checkout --detach <COMMIT> && git submodule update --init --recursive
git rev-parse HEAD

# 2. Статическая проверка
python3 scripts/check.py

# 3. Каталоги и env-файл (создаётся только недостающее)
ls -la $LAB_ROOT $LAB_ROOT/secrets      # до
scripts/lab prepare
ls -la $LAB_ROOT $LAB_ROOT/secrets      # после

# 4. Заполнить $LAB_ROOT/secrets/live_ro.env (права 600):
#    RATHENA_DIR=<каталог с login-server и conf/>
#    OPENKORE_DIR=<каталог OpenKore с XSTools.so>
#    DB_PASS=            (пусто: читается из conf/import/inter_conf.txt)
#    BOT01_USER / BOT01_PASS = аккаунт Arkady, BOT01_CHAR_SLOT = слот Arkady
#    BOT01_CONTROL_DIR=  (пусто)

# 5. Только чтение
scripts/lab doctor
scripts/lab status

# 6. Бэкап обеих БД
scripts/lab db-backup
ls -la $LAB_ROOT/backups

# 7. Перезапуск только бота с профилем из Git
cp -a <текущий control-каталог bot01> $LAB_ROOT/backups/bot01-control-before-$(date +%F)
scripts/lab stop bot01      # если бот запущен не ro-lab, остановить его вручную
scripts/lab start bot01
tmux attach -t live_ro_bot01    # смотреть вход; выход Ctrl-b d

# 8. Наблюдение 20-30 минут, затем
grep -c '\[lowHpGuard\]' $LAB_ROOT/logs/bot01/console.log
grep -E '\[lowHpGuard\]|Teleport|teleport|You have died|died' $LAB_ROOT/logs/bot01/console.log | tail -40
grep -iE 'lowHpGuard|plugin' $LAB_ROOT/logs/bot01/console.log | head
scripts/lab status
```

### Ожидаемый результат (не проверен)
| Шаг | Ожидание |
|---|---|
| 2 | `Repository checks OK; runtime not tested` |
| 3 | создан только `secrets/live_ro.env` и недостающие `run/`, `state/`, `backups/`, `logs/rathena`; существующие файлы не изменены |
| 5 | `doctor`: все `[OK]`, кроме возможных `[INFO]`. Строка `bot01: Arkady slot N lv .. map prt_fild08`. login/char/map найдены как «запущен не через lab», порты открыты |
| 6 | два непустых файла `ro_bot_lab-*.sql.gz` и `ro_bot_lab_logs-*.sql.gz` |
| 7 | Arkady входит на `prt_fild08`, в консоли загружен плагин `lowHpGuard` |
| 8 | нет строк `Teleporting due to insufficient HP`. При HP < 40% появляется `[lowHpGuard] ... новые цели не выбираю`, затем `защита снята` при 90%. Бот продолжает бой и набор опыта |

Если HP за время наблюдения не опускался ниже 40%, так и напишите: это «не наблюдалось», а не «работает».

### Риски
- **БД:** схема и данные не меняются. `db-backup` читает БД. На MyISAM-таблицах rAthena
  дамп ставит короткие блокировки чтения, и map-server может на несколько секунд задержать сохранение.
- **Бот:** профиль из Git может отличаться от рабочего (например, отсутствующие у вас локальные правки).
  Проверьте diff: `diff -r <текущий control> $LAB_ROOT/run/bots/bot01/control`. Различия по
  `username/password/char` ожидаемы, о прочих сообщите.
- **Серверы:** в этом задании `stop server` и `start` не выполняются.

### Откат
- Бот: `scripts/lab stop bot01`, затем запустить прежним способом из сохранённого control.
  Либо задать `BOT01_CONTROL_DIR=<сохранённая копия>` и выполнить `scripts/lab start bot01`.
- Код: `git checkout --detach <предыдущий commit>` в checkout из шага 1. Runtime не затронут.
- БД: восстановление из `backups/` только при реальной необходимости, вручную, при остановленном стеке.

### Что прислать
Вывод шагов 1–8 по формату выше. Плюс `diff -r` из раздела «Риски» без паролей и любые ошибки дословно.

---

## Задание №2: исправления по отчёту №1 и завершение наблюдения

**Результат:** PARTIAL PASS, `docs/qa/HERMES-9b8157a.md`. Локаль, TERM и бэкап проверены, stale-session осталась. Исправление — в задании №3.

**Ветка:** `claude/stage1-reproducible-delivery`
**Commit:** указан в сообщении разработчика (последний commit ветки, менявший этот файл).

### Что изменилось
1. Проверка логинов/паролей больше не зависит от локали: внутри функции `LC_ALL=C`,
   класс `[[:graph:]]` вместо диапазона. `LC_ALL=C` перед командами больше не нужен.
2. Новый плагин `bots/plugins/gracefulStop`: SIGTERM/SIGINT → штатный `quit` с пакетом выхода.
   Добавлен в `loadPlugins_list`.
3. Фоновый запуск бота задаёт `TERM=dumb`.
4. `db-backup`: `--lock-tables` вместо `--single-transaction` (таблицы MyISAM), проверка
   `gzip -t` и маркера `Dump completed`.

### Какие процессы перезапускать
- login/char/map — **не перезапускать**.
- bot01 — **один перезапуск** (шаг 4), чтобы загрузить `gracefulStop`.

### Шаги (от `ro-lab`, в checkout из задания №1, **без** `LC_ALL=C`)
```sh
cd /opt/ro-bot-lab/src/live_ro-qa
export LAB_ROOT=/opt/ro-bot-lab
locale | head -3                      # для отчёта: текущая локаль
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive
git rev-parse HEAD

# 1. Статика и проверка без LC_ALL=C
python3 scripts/check.py
scripts/lab doctor

# 2. Бэкап
scripts/lab db-backup

# 3. Отметка времени перед перезапуском
date -u +%FT%TZ

# 4. Перезапуск только бота
scripts/lab stop bot01
grep -n 'gracefulStop' $LAB_ROOT/logs/bot01/console.log | tail -3   # у старого бота плагина не было: ожидается пусто
scripts/lab start bot01
sleep 60
grep -nE 'still recognizes|gracefulStop|TERM' $LAB_ROOT/logs/bot01/console.log | tail -10

# 5. Проверка корректного выхода: второй перезапуск подряд
scripts/lab stop bot01
grep -n '\[gracefulStop\]' $LAB_ROOT/logs/bot01/console.log | tail -2
scripts/lab start bot01
sleep 60
grep -n 'still recognizes' $LAB_ROOT/logs/bot01/console.log | tail -3

# 6. Наблюдение 30 минут после шага 5, затем
grep -E '\[lowHpGuard\]' $LAB_ROOT/logs/bot01/console.log | tail -20
grep -cE 'Teleporting due to insufficient HP' $LAB_ROOT/logs/bot01/console.log
grep -iE 'You have died|died' $LAB_ROOT/logs/bot01/console.log | tail -5
scripts/lab status
```
Для шага 6 по каждому срабатыванию защиты важно увидеть, начинал ли бот бой с новой целью
между `новые цели не выбираю` и `защита снята`. Если удобнее, пришлите фрагмент лога
вокруг одного такого периода (±40 строк).

### Ожидаемый результат (не проверен)
| Шаг | Ожидание |
|---|---|
| 1 | `doctor` без `LC_ALL=C`: `BOT01_USER/PASS: формат допустим`, `doctor: ошибок нет` |
| 2 | два архива, строка `gzip и маркер завершения OK` для каждого |
| 4 | бот входит; нет предупреждений про `TERM` |
| 5 | при stop в логе `[gracefulStop] получен SIGTERM: выхожу из игры`; после start **нет** `still recognizes your last connection` |
| 6 | пары `новые цели не выбираю` → `защита снята`; между ними нет атаки новых целей; телепортов 0 |

Если HP за 30 минут не опускался ниже 40%, напишите «не наблюдалось».

### Риски
- БД: схема и данные не меняются. Во время `db-backup` таблицы на секунды блокируются
  на запись, map-server может задержать сохранение персонажа.
- Если `gracefulStop` не сработает, `stop` ждёт 60 с и сообщает об ошибке. Тогда
  `scripts/lab stop bot01 --force`, и пришлите вывод.

### Откат
- Код: `git checkout --detach 56bdba090258ac96e6ff9c1b9578ce0b5c914d5e`, затем `scripts/lab stop bot01` и `scripts/lab start bot01`.
- Бот по-старому: `BOT01_CONTROL_DIR=$LAB_ROOT/backups/bot01-control-before-56bdba0` в env и `scripts/lab start bot01`.

### Что прислать
Вывод шагов 1–6 (без паролей) по формату отчёта, в файле `docs/qa/HERMES-<sha7>.md`.

---

## Задание №3: подтверждённый выход бота (короткий тест)

**Ветка:** `claude/stage1-reproducible-delivery`
**Commit:** указан в сообщении разработчика (последний commit ветки, менявший этот файл).
**Без 30-минутного наблюдения.** Каждое ожидание ограничено 60 с.

### Причина stale-session (разбор исходников rAthena)
- `src/map/clif.cpp`, `clif_parse_QuitGame`: если персонаж был в бою меньше `prevent_logout`
  (10000 мс, `conf/battle/player.conf`; триггеры 14 = атака, умение, получение урона),
  сервер отвечает на запрос выхода отказом (`018B` fail=1).
- `clif_quitsave`: если при этом закрыть соединение, персонаж остаётся в мире ещё 10 с
  (`clif_delayquit`). Login-сервер в это время отвечает на вход кодом 8, отсюда
  «still recognizes your last connection».
- gracefulStop v1 только поднимал `quit` и закрывал сокет. Бот был в бою, поэтому сервер
  выход не принимал. Строка `[gracefulStop]` не доказывала выход.

### Что изменилось
1. gracefulStop v2: по сигналу AI → manual, каждые 2 с запрос выхода, выход только после
   `018B` fail=0. Без подтверждения за `gracefulStop_timeout` (40 с) — выход с предупреждением.
2. `lab stop bot01`: после завершения процесса ждёт `char.online=0` (SELECT, до 30 с) и пишет время.
3. `lab start bot01`: если персонаж ещё online, ждёт снятия сессии до 60 с (опрос каждые 2 с),
   потом запускает. OpenKore при коде 8 сам повторяет вход.
4. Строки `=== live_ro START|STOP bot01 <UTC> commit <sha> ===` в `console.log` делят лог на сегменты.
5. Ни одна команда не пишет в БД: online-флаги выставляет только rAthena.

### Какие процессы перезапускать
- login/char/map — **не перезапускать**.
- bot01 — перезапуски в шагах 3 и 4.

### Шаги (от `ro-lab`, без `LC_ALL=C`)
```sh
cd /opt/ro-bot-lab/src/live_ro-qa
export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive
git rev-parse HEAD
L=$LAB_ROOT/logs/bot01/console.log

# 1. Статика
python3 scripts/check.py && scripts/lab doctor

# 2. Первый перезапуск: у текущего процесса старый gracefulStop v1 — результат не оценивается
scripts/lab stop bot01 ; scripts/lab start bot01
sleep 45

# 3. Попытка А: stop во время боя
date -u +%T; scripts/lab stop bot01; date -u +%T
awk '/=== live_ro START/{seg=""} {seg=seg $0 "\n"} END{printf "%s", seg}' $L | grep -E 'gracefulStop|live_ro STOP'
scripts/lab start bot01; date -u +%T
sleep 45
awk '/=== live_ro START/{seg=""} {seg=seg $0 "\n"} END{printf "%s", seg}' $L | grep -nE 'still recognizes|Map Change|You are now attacking' | head

# 4. Попытка Б: то же ещё раз
date -u +%T; scripts/lab stop bot01; date -u +%T
awk '/=== live_ro START/{seg=""} {seg=seg $0 "\n"} END{printf "%s", seg}' $L | grep -E 'gracefulStop|live_ro STOP'
scripts/lab start bot01; date -u +%T
sleep 45
awk '/=== live_ro START/{seg=""} {seg=seg $0 "\n"} END{printf "%s", seg}' $L | grep -nE 'still recognizes|Map Change|You are now attacking' | head

# 5. Серверная сторона
grep -E "Arkady.*logged off|logged off" $LAB_ROOT/logs/rathena/map.log 2>/dev/null | tail -4   # если map пишет туда; иначе — лог вашего map-server
scripts/lab status
```
Если map-server пишет консоль не в `$LAB_ROOT/logs/rathena/map.log`, возьмите строки
`logged off` из его фактического лога.

### Ожидаемый результат (не проверен)
| Шаг | Ожидание |
|---|---|
| 3, 4 stop | в сегменте `[gracefulStop] сервер подтвердил выход через N с (отказов: K)`; `lab` пишет `char.online=0`, время ≤ 30 с |
| 3, 4 start | в новом сегменте **нет** `still recognizes your last connection`, есть `Map Change` и затем `You are now attacking` |
| 5 | на каждую остановку строка `Character 'Arkady' logged off` в логе map-server. Она печатается при закрытии соединения, даже если сервер ещё держит персонажа. Доказательство снятия — `char.online=0` из вывода `lab stop` |

Возможный честный исход: монстр бьёт бота всё время → `не подтвердил выход за 40 с`.
Тогда сервер держит персонажа до 10 с, а `start` ждёт `online=0`. Отметьте это как отдельный
случай с выводом, это не провал теста.

### Риски
- БД: только чтение (`SELECT online`). Схема и данные не меняются.
- Остановка бота занимает до 40 с (ожидание подтверждения) + до 30 с проверки.
- Если `stop` упрётся в 60 с ожидания процесса: `scripts/lab stop bot01 --force` и вывод в отчёт.

### Откат
- `git checkout --detach 9b8157a87273c614bbe170608c1b8fb3e85195ac`, затем `scripts/lab stop bot01` и `scripts/lab start bot01`.

### Что прислать
Вывод шагов 1–5 и время каждой попытки (stop → online=0 → start → Map Change), в `docs/qa/HERMES-<sha7>.md`.

---

## Задание №4: запуск мозга bot01 (OpenRouter) — «живой» Arkady

**Заменено заданием №5.** Платные вызовы только после согласования модели и бюджета; №4 не выполнять.

**Ветка:** `claude/stage1-reproducible-delivery`
**Commit:** указан в сообщении разработчика (последний commit ветки, менявший этот файл).
Включает задание №3 (gracefulStop v2). Отдельно его прогонять не нужно: шаг 4 проверяет выход тоже.

### Что изменилось
- `brain/live_brain` — мозг на Python (только стандартная библиотека): память SQLite в
  `$LAB_ROOT/state/bot01/`, решения через OpenRouter с суточным лимитом, таймаутом и работой без LLM.
- Плагин `bots/plugins/brainBridge`: отправляет мозгу состояние и события, исполняет только
  `say`, `whisper`, `set_hunt_map` (карты из `brain/personas/bot01.json`), `pause`, `resume`.
- `scripts/lab`: `brain-check`, `start live|brain`, `stop live|brain`, мозг в `status`.
- Исправлено: `status`/`stop` молча обрывались, если находили больше одного подходящего процесса.

### Предварительно (владелец или агент с ключом)
В `/opt/ro-bot-lab/secrets/live_ro.env` (права 600) добавить строки:
```
OPENROUTER_API_KEY=<ключ OpenRouter>
OPENROUTER_MODEL=deepseek/deepseek-chat
BRAIN_DAILY_LIMIT=300
```
Ключ — только в этот файл. В чат, отчёт и Git его не вставлять.
Модель сверить с каталогом OpenRouter. Лимит расходов поставить и в кабинете OpenRouter.

### Какие процессы перезапускать
- login/char/map — **не перезапускать**.
- bot01 — один перезапуск (чтобы загрузить brainBridge). Мозг — новый процесс.

### Команда запуска (от `ro-lab`)
```sh
sudo -u ro-lab -H bash -lc '
  cd /opt/ro-bot-lab/src/live_ro-qa &&
  git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive &&
  export LAB_ROOT=/opt/ro-bot-lab &&
  python3 scripts/check.py &&
  (cd brain && python3 -m unittest tests.test_brain) &&
  scripts/lab brain-check &&
  scripts/lab stop bot01 &&
  scripts/lab start live &&
  scripts/lab status'
```

### Проверка (через 10–15 минут)
```sh
export LAB_ROOT=/opt/ro-bot-lab
tail -n 30 $LAB_ROOT/logs/bot01/brain.log
tail -n 10 $LAB_ROOT/state/bot01/decisions.jsonl
grep -n 'brainBridge' $LAB_ROOT/logs/bot01/console.log | tail -10
# Для чата: с любого другого аккаунта написать Arkady в личку, через 15-30 с:
tail -n 4 $LAB_ROOT/state/bot01/decisions.jsonl
# Память после перезапуска мозга:
scripts/lab stop brain && scripts/lab start brain && sleep 3 && grep 'память:' $LAB_ROOT/logs/bot01/brain.log | tail -2
```

### Ожидаемый результат (не проверен)
| Проверка | Ожидание |
|---|---|
| unittest | `OK` (3 теста) |
| `brain-check` | `CHECK OK за N с ... <приветствие от Arkady>` |
| `start live` | бот входит; в console.log `[brainBridge] подключён к мозгу`; в brain.log `тело на связи: Arkady` |
| decisions.jsonl | строка `decision` с `thought` и `goal`; для каждого действия строка `ack` с `"ok": true` и командой OpenKore |
| console.log | `[brainBridge] решение мозга -> ...` с той же командой |
| личка | решение с поводом `<имя> пишет мне в личку` и ответ `whisper`/`say`; ответ виден в игре |
| перезапуск мозга | `память: N воспоминаний`, N > 0 |

Решение **и** его исполнение (`ack ok` + строка в console.log, а для чата — ответ, видимый
в игре) считаются доказательством. Одна строка `decision` — нет.

### Риски
- Деньги: не больше `BRAIN_DAILY_LIMIT` запросов за 24 ч. По умолчанию ~300 коротких запросов.
- Поведение: модель может сменить карту охоты только на карты из `hunt_maps`; пауза (`pause`) останавливает
  охоту до `resume`. Если бот «завис» на паузе: `scripts/lab stop brain`, затем в консоли бота `ai auto`.
- БД игры не меняется. Память мозга — отдельный файл `state/bot01/memory.sqlite`.

### Откат
- Только мозг: `scripts/lab stop brain`. Бот продолжает играть сам: без мозга плагин молча ждёт.
- Полностью: `git checkout --detach d24e12841f2d286b46c69c710b3e22cec391d9db`, `scripts/lab stop bot01`, `scripts/lab start bot01`.

### Что прислать
Вывод команды запуска и проверок (без ключа), последние строки decisions.jsonl,
`docs/qa/HERMES-<sha7>.md`.

---

## Задание №5: первый результат без LLM — событие из игры → правило → команда в игре

**Входит в задание №6 как этап A.** Отдельно не выполнять.

**Ветка:** `claude/brain-coordinator`
**Commit:** указан в сообщении разработчика (последний commit ветки, менявший этот файл).
Включает gracefulStop v2 (задание №3) и мозг из задания №4. **Платных вызовов нет.**

### Что изменилось
- `BRAIN_LLM=off` по умолчанию: даже с ключом в env к OpenRouter не уходит ни одного запроса.
- Decision gate на правилах:
  - при входе в игру Arkady один раз здоровается в общем чате (не чаще раза в 6 ч);
  - на личное сообщение `!status` отвечает шёпотом: HP, SP, уровень, карта, занятие.
- SafetyPolicy проверяет каждое действие без LLM.
- Мост передаёт событие боя (`attack`) и текущее занятие тела (`activity`).
- `start live` запускает сначала мозг, потом бота.

### Какие процессы перезапускать
- login/char/map — **не перезапускать**.
- bot01 — один перезапуск (загрузить brainBridge). Мозг — новый процесс.

### Шаги (от `ro-lab`)
```sh
cd /opt/ro-bot-lab/src/live_ro-qa
export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive
git rev-parse HEAD
grep -c '^BRAIN_LLM=openrouter' $LAB_ROOT/secrets/live_ro.env   # ожидается 0; если 1 — поставить BRAIN_LLM=off

# 1. Статика и тесты (без сети)
python3 scripts/check.py
(cd brain && python3 -m unittest tests.test_brain tests.test_rules)

# 2. Доказательство, что платный вызов не делается
scripts/lab brain-check; echo "код=$?"

# 3. Запуск: мозг, затем бот
scripts/lab stop bot01
scripts/lab start live
sleep 90
scripts/lab status

# 4. Результат
grep -nE '\[brainBridge\]' $LAB_ROOT/logs/bot01/console.log | tail -5
grep -n 'снова на поле' $LAB_ROOT/logs/bot01/console.log | tail -3
tail -n 20 $LAB_ROOT/logs/bot01/brain.log
tail -n 6 $LAB_ROOT/state/bot01/decisions.jsonl
python3 -c "import sqlite3;d=sqlite3.connect('$LAB_ROOT/state/bot01/memory.sqlite');print(d.execute('select kind,count(*) from events group by kind').fetchall())"

# 5. (если есть второй аккаунт) написать Arkady в личку: !status — через 1-5 с ответ
tail -n 2 $LAB_ROOT/state/bot01/decisions.jsonl
```

### Ожидаемый результат (не проверен)
| Шаг | Ожидание |
|---|---|
| 1 | `Repository checks OK`; unittest `OK` (16 тестов) |
| 2 | `CHECK SKIP: BRAIN_LLM=off, платные вызовы не включены...`, `код=2` |
| 3 | `status`: bot01 и brain запущены |
| 4 console.log | `[brainBridge] подключён к мозгу`, затем `[brainBridge] решение мозга -> c Arkady снова на поле...`, и строка чата сервера с этим текстом |
| 4 decisions.jsonl | `"source": "rule"` с `say`, затем `"type": "ack" ... "ok": true` |
| 4 brain.log | `LLM ВЫКЛЮЧЕНА (BRAIN_LLM=off...)`, `тело на связи: Arkady`, `событие in_game` |
| 4 memory | есть `attack`, `kill`, `loot` (реальные события из игры) |
| 5 | `whisper` со статусом и `ack ok`; ответ виден в игре на втором аккаунте |

Доказательство первого результата: `ack ok` в decisions.jsonl **и** строка `[brainBridge] решение мозга ->`
в console.log **и** сообщение в чате игры. Одна строка `decision` — не доказательство.
Если приветствие уже было меньше 6 часов назад (повторный запуск), используйте шаг 5.

### Риски
- БД игры не меняется. Память мозга: отдельный `state/bot01/memory.sqlite`.
- Arkady пишет одну строку в общий чат при входе.
- Если мозг упал, бот играет сам: плагин переподключается раз в 5 с.

### Откат
- Только мозг: `scripts/lab stop brain`.
- Полностью: `git checkout --detach d24e12841f2d286b46c69c710b3e22cec391d9db`, `scripts/lab stop bot01`, `scripts/lab start bot01`.

### Что прислать
Вывод шагов 1–5 (без ключа), в `docs/qa/HERMES-<sha7>.md`.

---

## Задание №6: DeepSeek + JEV + два бота (Arkady и Mirela) и их общение

**Ветка:** `claude/brain-coordinator`
**Commit:** указан в сообщении разработчика (последний commit ветки, менявший этот файл).
Владелец согласовал включение DeepSeek через OpenRouter и JEV. Лимиты ниже — потолок расходов.

Этапы идут по порядку. **Этап провален — остановиться и прислать вывод**, дальше не идти.
login/char/map **не перезапускать** ни на одном этапе.

### Что изменилось (относительно задания №5)
- JEV — быстрый gate (`BRAIN_GATE=jev`, OpenAI-совместимый API `JEV_*`). Для лички, обращений,
  смерти и уровня решает за секунды: ответить короткой фразой (say/whisper), позвать DeepSeek или
  промолчать. Сбой JEV — решают правила.
- DeepSeek через OpenRouter (`BRAIN_LLM=openrouter`) — продуманные решения и разговоры.
- Два бота: bot01 = Arkady, bot02 = **Mirela** (персонаж создаётся с этим именем). У каждого свой
  мозг (`brain-bot01`, `brain-bot02`) и своя память `state/<bot>/memory.sqlite`.
- Общение жителей: личка между ботами, не больше 6 ответов другому боту в час, повод заговорить —
  раз в 30 минут. Всё проходит SafetyPolicy (лимиты чата).
- `scripts/lab db-add-account bot02`: создаёт игровой аккаунт (нужен бэкап за 24 ч).

### Этап A — код и первый результат без платных вызовов
```sh
cd /opt/ro-bot-lab/src/live_ro-qa
export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive
git rev-parse HEAD
python3 scripts/check.py
(cd brain && python3 -m unittest tests.test_brain tests.test_rules)    # ожидается OK (22 теста)
grep -E '^(BRAIN_LLM|BRAIN_GATE)=' $LAB_ROOT/secrets/live_ro.env          # должно быть off / rules (или нет строк)
scripts/lab stop bot01
scripts/lab start live bot01
sleep 90
grep -nE '\[brainBridge\]' $LAB_ROOT/logs/bot01/console.log | tail -5
tail -n 4 $LAB_ROOT/state/bot01/decisions.jsonl
```
Ожидание: `[brainBridge] решение мозга -> c Arkady снова на поле...`, в decisions — `"source": "rule"` и
`ack ok`. Приветствие было меньше 6 ч назад — тогда с любого аккаунта написать Arkady `!status`.

### Этап B — второй бот Mirela
В `$LAB_ROOT/secrets/live_ro.env` (права 600) добавить/исправить:
```
LAB_BOTS=bot01 bot02
BOT02_USER=<логин, 1-23 ASCII>
BOT02_PASS=<пароль, 1-23 ASCII>
BOT02_SEX=F
BOT02_RUNNER=tmux
```
```sh
scripts/lab db-backup
scripts/lab db-add-account bot02          # ожидается "аккаунт bot02 создан: account_id N"
scripts/lab start live bot02
tmux attach -t live_ro_bot02
```
В консоли OpenKore создать персонажа **с именем `Mirela`** (слот 0, параметры по умолчанию),
дождаться входа на карту. Выйти из tmux: `Ctrl-b d`. Если имя занято — остановиться и сообщить.
```sh
scripts/lab doctor | grep -E 'bot0[12]:'    # ожидается: bot02: Mirela slot 0 ...
scripts/lab status
```

### Этап C — DeepSeek (платно, с лимитом)
В env:
```
BRAIN_LLM=openrouter
OPENROUTER_MODEL=deepseek/deepseek-chat     # сверить точное имя в каталоге openrouter.ai/models
BRAIN_DAILY_LIMIT=150                       # на каждого бота: всего ≤ 300 запросов в сутки
BRAIN_DECIDE_INTERVAL=300
```
```sh
scripts/lab brain-check bot01         # ожидается CHECK OK ... приветствие Arkady
scripts/lab stop brain all && scripts/lab start brain all
```

### Этап D — JEV
В env (значения JEV даёт владелец; ключ только здесь):
```
BRAIN_GATE=jev
JEV_API_BASE=<базовый URL, к нему добавляется /chat/completions>
JEV_API_KEY=<ключ>
JEV_MODEL=<имя модели>
JEV_DAILY_LIMIT=2000
```
```sh
scripts/lab brain-check-jev bot01     # ожидается CHECK OK (JEV ...) {...}
```
Если `CHECK FAIL` с HTTP 404/400 или ответ не в формате chat/completions — API JEV не OpenAI-совместимый:
вернуть `BRAIN_GATE=rules`, прислать текст ошибки и ссылку на документацию JEV, этап E выполнять без JEV.
```sh
scripts/lab stop brain all && scripts/lab start brain all
scripts/lab status
```

### Этап E — наблюдение 20 минут: жизнь и общение
```sh
export LAB_ROOT=/opt/ro-bot-lab
for b in bot01 bot02; do echo "== $b"; tail -n 15 $LAB_ROOT/logs/$b/brain.log; done
for b in bot01 bot02; do echo "== $b"; grep -E '"type": "(decision|jev|ack)"' $LAB_ROOT/state/$b/decisions.jsonl | tail -n 8; done
grep -hE '\(From: (Arkady|Mirela)\)|\(To: (Arkady|Mirela)\)' $LAB_ROOT/logs/bot0*/console.log | tail -n 10
for b in bot01 bot02; do python3 -c "import sqlite3,time;d=sqlite3.connect('$LAB_ROOT/state/$b/memory.sqlite');print('$b',d.execute('select provider,count(*),sum(ok) from llm_calls where ts>?',(time.time()-86400,)).fetchall())"; done
```

### Ожидаемый результат (не проверен)
| Этап | Ожидание |
|---|---|
| A | первый результат без LLM: правило → команда в игре, `ack ok` |
| B | Mirela создана, входит на `prt_fild08`, `doctor` показывает её |
| C | `CHECK OK` от DeepSeek |
| D | `CHECK OK (JEV ...)`, в brain.log `gate rules+jev(...)` |
| E | в decisions.jsonl обоих ботов есть `source: llm` и/или `jev` с `whisper` друг другу и `ack ok`; в console.log строки `(From: Mirela)` у Arkady и `(From: Arkady)` у Mirela; вызовов DeepSeek ≤ лимита |

Доказательство общения: шёпот одного бота (`ack ok` + `(To: X)` в его console.log) **и** получение другим
(`(From: Y)` в его console.log + событие и решение в его decisions.jsonl). Только строки `decision` — не доказательство.

### Риски
- Расходы: DeepSeek ≤ 150 запросов в сутки на бота, JEV ≤ 2000. Дополнительно поставьте лимит ключа в кабинете OpenRouter.
- БД игры: только новый аккаунт bot02 (этап B), после бэкапа. Персонаж создаёт сам сервер при входе.
- Чат: Arkady и Mirela пишут в общий чат при входе и по решению модели (не больше 3 сообщений за 10 минут на бота).

### Откат
- Выключить платные вызовы: `BRAIN_LLM=off`, `BRAIN_GATE=rules`, затем `scripts/lab stop brain all && scripts/lab start brain all`.
- Убрать второго бота: `scripts/lab stop live bot02`, `LAB_BOTS=bot01`. Аккаунт остаётся в БД (удалять только по решению владельца).
- Код: `scripts/lab stop live all` (серверы не трогает; **не** `stop all`), затем `git checkout --detach d24e12841f2d286b46c69c710b3e22cec391d9db` и `scripts/lab start bot01`.

### Что прислать
Вывод этапов A–E (без ключей и паролей) в `docs/qa/HERMES-<sha7>.md`; на каком этапе остановились, если остановились.

---

## Задание №7: доставка, класс/пол, группа/следование/лечение, лимиты — быстрая проверка

**Ветка:** `claude/brain-v2` (поверх `hermes/live-jev-verified` 4feb7a8)
**Commit:** указан в сообщении разработчика (последний commit ветки, менявший этот файл).
**Без 20–30-минутного наблюдения.** Каждый шаг ограничен временем, указанным в нём. login/char/map не трогать.
Этап провален — остановиться и прислать вывод.

### Что изменилось
1. JEV: `JEV_PROVIDER=typesafe|openai` вместо сравнения URL (без переменной — по адресу, текущий env работает).
   Лимит JEV резервируется до вызова (гонка параллельных событий закрыта).
2. Деньги: `BRAIN_DAILY_USD_LIMIT` по `usage.cost` OpenRouter (учёт по отчётам API, не лимит аккаунта).
   Промпт не длиннее `BRAIN_MAX_PROMPT_CHARS` (8000).
3. Доставка: `delivery` в decisions.jsonl — ответ сервера на шёпот (0 доставлено / 1 не в сети / 2 игнор / 3 не принимает)
   и эхо общего чата; без ответа 15 с — `timeout`. В console.log: `[brainBridge] сервер подтвердил: ...` / `не доставлено (...)`.
4. Класс/пол/уровень бота и игроков рядом — из игры; модели запрещено угадывать.
5. Действия party_create (имя всегда `LR_<имя>`), party_invite/follow (только жители), party_accept (правило: только
   группа жителя), party_leave, unfollow. Vera: `partySkill AL_HEAL` (участники группы HP < 60%) и самолечение
   HP < 50% — правила OpenKore, без LLM.

### Шаг 0 — код (2 мин)
```sh
cd /opt/ro-bot-lab/src/live_ro-qa
export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive
git rev-parse HEAD
python3 scripts/check.py
(cd brain && python3 -m unittest tests.test_brain tests.test_rules tests.test_typesafe tests.test_limits)   # OK, 35 тестов
```
В env добавить (если нет): `BRAIN_DAILY_USD_LIMIT=0.5` и, по желанию, `JEV_PROVIDER=typesafe`.
```sh
scripts/lab brain-check-jev bot01     # CHECK OK (JEV ...) — transport typesafe
scripts/lab stop live all && scripts/lab start live all     # серверы не трогает
sleep 60 && scripts/lab status
```

### Шаг 1 — доставка шёпота (3 мин)
В консоли Vera (`tmux attach -t live_ro_bot02`) ввести: `pm "Arkady" !status`, затем `Ctrl-b d`.
```sh
sleep 20
grep -E '"type": "(decision|ack|delivery)"' $LAB_ROOT/state/bot01/decisions.jsonl | tail -n 3
grep -nE 'сервер подтвердил|не доставлено' $LAB_ROOT/logs/bot01/console.log | tail -n 3
grep -n '(From: Arkady)' $LAB_ROOT/logs/bot02/console.log | tail -n 2
```
Ожидание: у Arkady `decision` (правило статуса) → `ack ok` → `delivery ok, code 0`; у Vera строка `(From: Arkady) : HP ...`.

### Шаг 2 — класс и пол (1 мин)
```sh
python3 -c "import sqlite3;d=sqlite3.connect('$LAB_ROOT/state/bot01/memory.sqlite');print(d.execute(\"select value from kv where key='known_players'\").fetchone())"
```
Ожидание: `Vera` с `Acolyte`, `Female` (если Vera была в зоне видимости Arkady). Нет записи — так и написать.

### Шаг 3 — группа и следование (5 мин)
В консоли Vera: `party create "LR_Vera"`, затем `party request "Arkady"`, `Ctrl-b d`.
```sh
sleep 15
grep -E 'party_invite|party_accept' $LAB_ROOT/state/bot01/decisions.jsonl | tail -n 3
grep -niE 'party|группу' $LAB_ROOT/logs/bot01/console.log | tail -n 5
```
Ожидание: у Arkady правило «приглашение в группу жителя Vera» → `party join 1` → `ack ok`; в консоли — вступление в группу.
Затем в консоли Vera: `follow Arkady`, `Ctrl-b d`; через 60 с в её console.log нет ошибок follow и она рядом с Arkady.

### Шаг 4 — лечение (до 10 мин, затем остановиться)
```sh
timeout 600 sh -c "until grep -qiE 'Heal' $LAB_ROOT/logs/bot02/console.log; do sleep 15; done"; echo rc=$?
grep -niE 'Heal' $LAB_ROOT/logs/bot02/console.log | tail -n 5
```
Ожидание: Vera применяет Heal к Arkady, когда его HP < 60%. `rc=124` — за 10 минут HP не опускался или навыка нет:
это «не наблюдалось», указать уровень навыка Heal у Vera (`skills` в её консоли).

### Шаг 5 — расходы (1 мин)
```sh
for b in bot01 bot02; do python3 -c "import sqlite3,time;d=sqlite3.connect('$LAB_ROOT/state/$b/memory.sqlite');print('$b',d.execute('select provider,count(*),sum(ok),round(coalesce(sum(cost),0),6) from llm_calls where ts>? group by provider',(time.time()-86400,)).fetchall())"; done
```

### Риски
- Группа: Arkady вступает только в `LR_<житель>`. Общий опыт не включается (partyAutoShare 0).
- Следование отвлекает Vera от своей охоты; `follow stop` в её консоли или действие мозга `unfollow` отменяет.
- Расходы: лимиты запросов как раньше + `BRAIN_DAILY_USD_LIMIT` на бота.

### Откат
`scripts/lab stop live all` (серверы не трогает), `git checkout --detach 4feb7a815ab14f0e4a9393337134e15427bf2154`, `scripts/lab start live all`.
Группа: `party leave` в консоли бота. БД игры и память не удалять.

### Что прислать
Вывод шагов 0–5 в `docs/qa/HERMES-<sha7>.md`.

---

## Задание №8: реальная встреча Arkady и Vera — решение → движение → встреча → память → перезапуск

**Ветка:** `claude/brain-v2` (включает задание №7)
**Commit:** указан в сообщении разработчика (последний commit ветки, менявший этот файл).
Без пассивного наблюдения: у каждого шага есть предел времени. login/char/map не трогать.
Шаг провален — остановиться и прислать вывод.

### Что изменилось
- Исполнитель плана встречи (правила, без LLM на тик): предложение и ответ — шёпотом с меткой
  `[meet:<id>:<map>:<x>:<y>]` / `[meet:<id>:ok]`. Точка — позиция предлагающего. Движение — OpenKore
  (`lockMap_x/y`, держаться в радиусе 2, отбиваться). «Дошёл» и «встретился» проверяются по позиции и списку
  игроков рядом из игры. План и его история — в `state/<bot>/memory.sqlite` (таблица `plans`).
- `scripts/lab plan BOT meet ИМЯ|cancel|show`.

### Полезные однострочники
```sh
export LAB_ROOT=/opt/ro-bot-lab; cd /opt/ro-bot-lab/src/live_ro-qa
pos() { python3 -c "import sqlite3,json;d=sqlite3.connect('$LAB_ROOT/state/$1/memory.sqlite');s=json.loads(d.execute(\"select value from kv where key='last_state'\").fetchone()[0]);print('$1',s.get('map'),s.get('x'),s.get('y'),'точка',s.get('lock_x'),s.get('lock_y'))"; }
mem() { python3 -c "import sqlite3;d=sqlite3.connect('$LAB_ROOT/state/$1/memory.sqlite');[print('$1',r[0]) for r in d.execute(\"select text from memories where text like '%встрет%' or text like '%Встреча%' order by id desc limit 6\")]"; }
```

### Шаг 0 — код и перезапуск (3 мин)
```sh
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive && git rev-parse HEAD
python3 scripts/check.py
(cd brain && python3 -m unittest tests.test_brain tests.test_rules tests.test_typesafe tests.test_limits tests.test_plans)  # OK, 39
# если Vera следует за Arkady после №7: в её консоли `follow stop`
scripts/lab stop live all && scripts/lab start live all && sleep 60 && scripts/lab status
pos bot01; pos bot02        # обе на prt_fild08; записать расстояние
```

### Шаг 1 — решение и согласие (1 мин)
```sh
scripts/lab plan bot02 meet Arkady
sleep 60
scripts/lab plan bot02 show | head -2
scripts/lab plan bot01 show | head -2
grep -nE 'meet:|сервер подтвердил' $LAB_ROOT/logs/bot02/console.log | tail -3
grep -nE 'meet:|conf lockMap_x' $LAB_ROOT/logs/bot01/console.log | tail -4
```
Ожидание: у Vera план `planned/awaiting_answer` → `executing`; у Arkady `executing/moving` (принято моделью
или правилом через 45 с); в консоли Vera — шёпот с `[meet:...]` и `сервер подтвердил`, у Arkady — `[meet:...:ok]` и `conf lockMap_x`.

### Шаг 2 — движение и встреча (до 9 мин)
```sh
for i in $(seq 1 18); do pos bot01; pos bot02; s1=$(scripts/lab plan bot01 show | head -1); s2=$(scripts/lab plan bot02 show | head -1)
  echo "$s1" | grep -q '"status": "completed"' && echo "$s2" | grep -q '"status": "completed"' && break; sleep 30; done
scripts/lab plan bot01 show | head -1; scripts/lab plan bot02 show | head -1
mem bot01; mem bot02
```
Ожидание: координаты Arkady приближаются к точке Vera; оба плана `completed` с `result: встреча, расстояние N`;
в памяти у обоих «Я встретился с ...» (отдельно от «предложил»/«согласился»); после встречи точка снята (`точка None None`)
и охота продолжается. `failed` — прислать `history` плана целиком.

### Шаг 3 — перезапуск посреди плана (до 9 мин)
```sh
scripts/lab plan bot02 meet Arkady; sleep 70
scripts/lab plan bot01 show | head -1                 # ожидается executing/moving или waiting
scripts/lab stop brain bot01; sleep 10; scripts/lab start brain bot01; sleep 15
grep '"event": "reconciled"' $LAB_ROOT/state/bot01/decisions.jsonl | tail -1
# дождаться завершения как в шаге 2 (тот же цикл)
```
Ожидание: `reconciled` с `point_set: true` (точка в OpenKore осталась, команда не повторялась) и затем `completed`
у обоих. Если в шаге 3 план уже успел завершиться до перезапуска — повторить с `sleep 30` вместо 70.

### Шаг 4 — память после перезапуска (1 мин)
```sh
scripts/lab stop brain all && scripts/lab start brain all && sleep 5
grep 'память:' $LAB_ROOT/logs/bot01/brain.log | tail -1; mem bot01; scripts/lab plan bot01 show | head -3
```
Ожидание: две встречи в памяти и в `plans` со статусом `completed`; число воспоминаний не уменьшилось.

### Не является доказательством
Реплика «давай встретимся», `ack` команды или `delivery` шёпота. Доказательство — `completed` у обоих планов,
позиции из `pos` у точки и запись «Я встретился» в памяти обоих.

### Риски
- Во время плана бот держится у точки (радиус 2), а не охотится по всему полю; после встречи/провала точка снимается.
- Если точка оказалась у монстров — бой; HP-правила и lowHpGuard работают как раньше.
- Денег: LLM только при решении принять/ответить, лимиты прежние; исполнитель плана LLM не вызывает.

### Откат
`scripts/lab plan bot01 cancel; scripts/lab plan bot02 cancel` (снимает точку), затем
`scripts/lab stop live all`, `git checkout --detach 206a7a1a4f4bb678445c06471f21c050721e211a`, `scripts/lab start live all`.

### Что прислать
Вывод шагов 0–4 и `history` обоих планов в `docs/qa/HERMES-<sha7>.md`.

---

## Задание №9: распорядок дня и боевые профили — быстрая проверка

**Ветка:** `claude/brain-routine` (поверх `claude/brain-v2`, включает задания №7–8)
**Commit:** указан в сообщении разработчика (последний commit ветки, менявший этот файл).
Каждый шаг ограничен по времени, без пассивного наблюдения. login/char/map не трогать.

### Что изменилось
- `brain/world/goals.json`: глобальные цели и распорядок — норма охоты 4–5 ч/сутки, сессии 60–100 мин,
  перерывы 30–90 мин в `prontera 156,185` (дойти, сесть, общаться). Исполнитель — правила без LLM.
- `bots/combat/classes.json` + плагин `combatProfile`: боевые настройки по профессии для 24 классов
  (только изученные навыки). Ручные блоки лечения Vera заменены профилем Acolyte.
- `scripts/lab routine BOT rest|hunt|show` — досрочный переход для проверки.

### Шаг 0 — код и перезапуск (3 мин)
```sh
cd /opt/ro-bot-lab/src/live_ro-qa; export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive && git rev-parse HEAD
python3 scripts/check.py
(cd brain && python3 -m unittest tests.test_brain tests.test_rules tests.test_typesafe tests.test_limits tests.test_plans tests.test_routine)  # OK, 48
perl -Ibots/tests/stubs bots/tests/combat_profile.t | tail -1      # 1..29, без "not ok"
scripts/lab stop live all && scripts/lab start live all && sleep 90 && scripts/lab status
```

### Шаг 1 — боевые профили (2 мин)
```sh
grep -h '\[combatProfile\]' $LAB_ROOT/logs/bot0*/console.log | tail -4
```
Ожидание: Arkady — `Swordsman (Swordsman)`, атака из изученных (`SM_BASH`/`SM_MAGNUM`), Vera — `Acolyte (Acolyte)`,
группе `AL_HEAL`, себе `AL_HEAL`; в «не изучено» — то, чего у них нет. Если профессия в игре другая (например,
уже Knight) — так и написать. В консоли нет ошибок про `attackSkillSlot`/`partySkill`.

### Шаг 2 — распорядок: охота (1 мин)
```sh
scripts/lab routine bot01 show; scripts/lab routine bot02 show
```
Ожидание: `mode: hunt`, `budget` 14400–18000 (с), `hunted` растёт между двумя вызовами с паузой 60 с.

### Шаг 3 — отдых в городе (до 10 мин)
```sh
scripts/lab routine bot01 rest
for i in $(seq 1 20); do python3 -c "import sqlite3,json;d=sqlite3.connect('$LAB_ROOT/state/bot01/memory.sqlite');s=json.loads(d.execute(\"select value from kv where key='last_state'\").fetchone()[0]);print(s.get('map'),s.get('x'),s.get('y'),s.get('lock_map'),s.get('lock_x'),s.get('activity'))"; sleep 30; done
grep '"type": "routine"' $LAB_ROOT/state/bot01/decisions.jsonl | tail -3
grep -nE 'conf lockMap prontera|\bsit\b|Invalid coordinates|unwalkable' $LAB_ROOT/logs/bot01/console.log | tail -5
```
Ожидание: Arkady идёт в `prontera`, доходит до ~156,185, в журнале `routine_town` → `routine_arrived`, команда `sit`.
Если в консоли `Invalid coordinates`/`unwalkable` — точка в городе непроходима: прислать вывод (поправлю `goals.json`).

### Шаг 4 — перезапуск мозга в городе (1 мин)
```sh
scripts/lab stop brain bot01; scripts/lab start brain bot01; sleep 70
scripts/lab routine bot01 show
grep '"source": "routine"' $LAB_ROOT/state/bot01/decisions.jsonl | tail -2
```
Ожидание: режим `town` сохранён; новых команд распорядка после перезапуска нет (настройка OpenKore совпадает).

### Шаг 5 — обратно на охоту (до 10 мин)
```sh
scripts/lab routine bot01 hunt; sleep 60
grep '"type": "routine"' $LAB_ROOT/state/bot01/decisions.jsonl | tail -1
grep -nE 'conf lockMap prt_fild08|stand' $LAB_ROOT/logs/bot01/console.log | tail -3
```
Ожидание: `routine_hunt`, `conf lockMap prt_fild08`, `lockMap_x none`, `stand`; Arkady уходит на поле и снова дерётся.

### Риски
- Путь prt_fild08 ↔ prontera прокладывает OpenKore по таблицам порталов; в городе бот не атакует (`attackAuto_notInTown`).
- Профиль может изменить дистанцию/слоты навыков бота; откат — ниже.
- LLM-расходы не меняются: распорядок и бой — правила.

### Откат
`scripts/lab routine bot01 hunt`, `scripts/lab stop live all`,
`git checkout --detach 00579291aa407080beccf3d432f09fecffac7dfa`, `scripts/lab start live all`.

### Что прислать
Вывод шагов 0–5 в `docs/qa/HERMES-<sha7>.md`.
