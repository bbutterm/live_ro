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

---

## Задание №10: «запустили — живут сами» (быстрая проверка)

**Ветка:** `claude/brain-routine` (включает №7–9)
**Commit:** указан в сообщении разработчика (последний commit ветки, менявший этот файл).
Без пассивного наблюдения дольше указанного. login/char/map не перезапускать.

### Что изменилось
- `scripts/lab up | down | report`: одна команда запуска; сторож раз в 30 с поднимает упавший мозг/бот
  (не больше 5 раз за 30 мин на процесс, дальше — запись в `logs/supervisor.log` и ожидание).
- Экономика (оба профиля): продажа лута у Tool Dealer `prt_in 126,76` при рюкзаке ≥ 48%, зелья не продаются;
  докупка Red Potion 10–40 шт. при зени > 3000.
- Правила жизни без LLM: застрял (5 мин без движения и боя) → `ai clear` + шаг; 3 смерти за 30 мин → отдых
  и карта полегче; дневник дня; житель рядом → воспоминание.

### Шаг 0 — код и запуск одной командой (3 мин)
```sh
cd /opt/ro-bot-lab/src/live_ro-qa; export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive && git rev-parse HEAD
python3 scripts/check.py
(cd brain && python3 -m unittest tests.test_brain tests.test_rules tests.test_typesafe tests.test_limits tests.test_plans tests.test_routine)  # OK, 51
scripts/lab stop live all
scripts/lab up && sleep 90 && scripts/lab report
```
Ожидание: `report` показывает обоих жителей (класс, уровень, карта, HP/SP, зени, вес, распорядок), строку сторожа.

### Шаг 1 — сторож (2 мин)
```sh
kill -9 $(cat $LAB_ROOT/run/pids/bot02.pid); sleep 45
scripts/lab status | grep -E 'bot02|supervisor'; tail -n 3 $LAB_ROOT/logs/supervisor.log
```
Ожидание: `bot02: не запущен — перезапускаю (1/5 за окно)`, bot02 снова запущен и входит в игру.

### Шаг 2 — экономика (до 10 мин, по возможности)
```sh
grep -nE 'sellAuto|Selling|buyAuto|Buying|Calculating auto-sell' $LAB_ROOT/logs/bot0*/console.log | tail -6
scripts/lab report | grep -E '^== '
```
Ожидание: при весе ≥ 48% бот идёт в `prt_in` к 126,76 и продаёт лут; при < 10 Red Potion и зени > 3000 — покупает.
Если за 10 минут вес не дошёл до 48% — «не наблюдалось» и текущий вес из `report`.

### Шаг 3 — сводка и дневник (1 мин)
```sh
scripts/lab report
grep -h '"event": "routine_' $LAB_ROOT/state/bot0*/decisions.jsonl | tail -4
```
Дневник появится после полуночи (UTC+3) — проверить в следующем отчёте строкой `помнит: Дневник ...`.

### Шаг 4 — down (1 мин)
```sh
scripts/lab down && scripts/lab status
```
Ожидание: жители и сторож остановлены, login/char/map работают.
После проверки: `scripts/lab up` — оставить жить.

### Риски
- Сторож перезапускает и бота, остановленного вручную через `stop bot01`. Чтобы остановить надолго — `scripts/lab down`.
- Продажа: `all 0 0 1` продаёт любой неэкипированный лут (кроме перечисленных зелий/крыльев с минимумом).
- Автозапуск после перезагрузки VPS — строка `@reboot` в README; ставить только с разрешения владельца.

### Откат
`scripts/lab down`, `git checkout --detach cbeb2a9df4801abd0dc5c5865b913ff63333766b`, `scripts/lab start live all`.

### Что прислать
Вывод шагов 0–4 в `docs/qa/HERMES-<sha7>.md`.

## Задание №11: склад и взаимопомощь жителей

**Ветка:** `claude/brain-routine` (включает №7–10). **Commit:** из сообщения разработчика.
Можно совместить с №10. Без пассивного наблюдения дольше указанного. login/char/map не перезапускать.
БД не трогать.

### Что изменилось
- Плагин `economy` (добавлен в `loadPlugins_list` обоих профилей): карты и руда — на склад Kafra
  `prontera 146,89` (`storageAuto 1`, `minStorageZeny 100`), а не на продажу.
- Сделки: `dealAuto 3`, `dealAuto_names` = другой житель (bot01 → Vera, bot02 → Arkady); чужим — отказ.
- Мозг: в городе житель просит у жителя рядом Red Potion (< 5) или зени (< 500); второй отдаёт сделкой,
  если у него остаётся запас. Команда проверки: `scripts/lab gift BOT ITEM N`.
- В state тела: `items` (зелья/крылья по ID), `vend`, `give`; `report` показывает строку «хозяйство».

### Шаг 0 — код (3 мин)
```sh
cd /opt/ro-bot-lab/src/live_ro-qa; export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive && git rev-parse HEAD
python3 scripts/check.py
(cd brain && python3 -m unittest tests.test_brain tests.test_rules tests.test_typesafe tests.test_limits tests.test_plans tests.test_routine tests.test_economy)  # OK, 62
perl -Ibots/tests/stubs bots/tests/economy.t | tail -1    # 1..30
scripts/lab down; scripts/lab up && sleep 90
grep -h 'economy' $LAB_ROOT/logs/bot0*/console.log | head -4; scripts/lab report
```
Ожидание: плагин загружен (нет `Unable to load plugin economy`), в `report` строка «хозяйство: красных зелий N».

### Шаг 1 — оба в город (до 5 мин)
```sh
scripts/lab routine bot01 rest; scripts/lab routine bot02 rest
# подождать, пока report покажет обоих на prontera у 156,185 (до 5 мин)
scripts/lab report | grep -E '^== '
```

### Шаг 2 — передача (2 мин)
```sh
scripts/lab gift bot02 501 3; sleep 60
grep -h '"type": "economy"' $LAB_ROOT/state/bot0*/decisions.jsonl | tail -4
grep -hE 'economy|Deal|deal' $LAB_ROOT/logs/bot0*/console.log | tail -12
scripts/lab report | grep -E '^== |хозяйство'
```
Ожидание: Vera `gift_asked`; Arkady `[economy] передал Vera: предмет 501 x 3`, `Deal Complete`, `gift_given`;
Vera `gift_received` («по данным игры»). Если у Arkady меньше 23 Red Potion — отказ «самому мало»
(это тоже правильный исход; тогда повторить `scripts/lab gift bot01 501 3` в обратную сторону или
`gift bot02 z 100` для зени: у отдающего должно остаться ≥ 15000).

### Шаг 3 — склад (по возможности, без ожидания)
```sh
grep -hE 'Auto-storaging|storage|Kafra|Stored' $LAB_ROOT/logs/bot0*/console.log | tail -6
```
Ожидание: только если в рюкзаке есть карта/руда и вес ≥ 48% — поход к Kafra 146,89. Не было — «не наблюдалось».

### Шаг 4 — вернуть жизнь
```sh
scripts/lab routine bot01 hunt; scripts/lab routine bot02 hunt
```

### Риски
- Сделка: rAthena требует ≤ 2 клеток; тело подходит само, таймаут 40 с. Неудача пишется как `gift_failed` с причиной.
- Склад: при зени < 100 OpenKore на склад не идёт — карты остаются в рюкзаке (не продаются).
- `gift` — проверочная команда; правила отдающего (запас, лимит в сутки) действуют.

### Откат
`scripts/lab down`, `git checkout --detach f978d341c8394c77b23108a9a77823870c914c42`, `scripts/lab up`.

### Что прислать
Вывод шагов 0–3 в `docs/qa/HERMES-<sha7>.md`.

---

## План дальнейшей автономности (поручение владельца)

См. [`AUTONOMY_BACKLOG.md`](AUTONOMY_BACKLOG.md): 120 задач AUT-001–AUT-120, приоритеты, критерии готовности, порядок вертикальных срезов и ворота автономности.

Это backlog разработки и проверки, **не разрешение немедленно запускать/обновлять runtime**, менять лимиты API, включать cron/autostart или перезапускать сервер. Существующие реализации №7–10 сначала сопоставить с задачами и довести, не переписывать заново. Перед каждым выпуском подготовить короткий проверяемый HANDOFF для Hermes; отмечать код, локальные проверки и реальные VPS-доказательства раздельно.

## Задание №12: фаза A — «умер → респаун → лечение → безопасная задача»

**Ветка:** `claude/brain-routine` (включает №10, №11, отчёт QA №9 и backlog). **Commit:** из сообщения разработчика.
Сопоставление с backlog: [`AUTONOMY_STATUS.md`](AUTONOMY_STATUS.md). Можно совместить с №10/№11.
login/char/map не перезапускать, БД не трогать, GM/SQL-лечение и ручной `respawn` не использовать —
цель проверки именно в том, чтобы обошлось без них. Без пассивного наблюдения дольше указанного.

### Что изменилось
- `pause` больше не `ai manual`: бот только перестаёт искать новых монстров, но отбивается и пьёт зелья.
- Мёртв и AI не в auto > 3 с → тело само включает `ai auto` (OpenKore делает респаун только в auto).
- После любой смерти: город, отдых; на охоту — только при HP ≥ 80% (и по расписанию, и по `routine hunt`).
- На охоте HP < 25% и нет зелий 20 с → город. Зелья по ID: `useSelf_item 569, 501, 502, 503, 504`.
- Застревание не срабатывает, пока сидит/торгует/дерётся. Команды `routine/plan/gift` ждут первого состояния тела
  (не дольше 10 мин). Цель в памяти следует за распорядком.
- `stop botNN|brain|live` — сторож больше не поднимает остановленное; `start`/`up` возвращают под надзор.
- Не продаются (идут на склад): карты, руда, оружие/броня, которых нет в списке продажи `items_control.txt`.
- `scripts/lab doctor`: раздел «карты мира» — включены ли в rAthena все карты жителей (охота, город, `prt_in`, склад).

### Шаг 0 — код и проверки (4 мин)
```sh
cd /opt/ro-bot-lab/src/live_ro-qa; export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive && git rev-parse HEAD
python3 scripts/check.py
(cd brain && python3 -m unittest tests.test_brain tests.test_rules tests.test_typesafe tests.test_limits tests.test_plans tests.test_routine tests.test_economy tests.test_inbox)  # OK, 68
for t in bots/tests/*.t; do perl -Ibots/tests/stubs $t | tail -1; done   # 1..10, 1..29, 1..33
scripts/lab doctor | sed -n '/карты мира/,/OpenKore/p'
```
Ожидание: все `[ok]` в «карты мира». Если `[!!] prt_in` — продажа и закупка невозможны: прислать вывод и
`grep -n prt_in $RATHENA_DIR/conf/maps_athena.conf $RATHENA_DIR/conf/import/*.txt`, остальное не делать.

### Шаг 1 — запуск и команда до готовности тела (2 мин)
```sh
scripts/lab down; scripts/lab up; scripts/lab routine bot02 rest   # сразу, до входа в игру
sleep 90; grep -h '"type": "operator"' $LAB_ROOT/state/bot02/decisions.jsonl | tail -2
scripts/lab routine bot02 show
```
Ожидание: ровно одна запись `operator` с `"result": "ok"` после входа, `mode: town`.

### Шаг 2 — пауза не ai manual (2 мин)
```sh
grep -h 'решение мозга -> ' $LAB_ROOT/logs/bot0*/console.log | grep -E 'attackAuto 1|ai manual' | tail -3
```
Ожидание: если модель ставила паузу — `conf attackAuto 1; conf route_randomWalk 0`, строк `ai manual` от новой
версии нет. Не ставила — «не наблюдалось» (специально не вызывать).

### Шаг 3 — смерть и самостоятельное возвращение (по факту, без ожидания)
Только если за время проверки кто-то погиб естественно (не убивать специально):
```sh
grep -hE 'You died|Sending respawn|ai auto для респауна|Auto-storaging due to death' $LAB_ROOT/logs/bot0*/console.log | tail -6
grep -h '"event": "routine_(recover|wait_hp|hunt)"' $LAB_ROOT/state/bot0*/decisions.jsonl | tail -4
scripts/lab report | grep -E '^== '
```
Ожидание: респаун без человека; `routine_recover`; на охоту — только после HP ≥ 80% (`routine_wait_hp`, если ждал).
Если Arkady уже мёртв при старте — это и есть проверка: через 3–10 с `Sending respawn` без ручной команды.

### Шаг 4 — сторож уважает остановку (2 мин)
```sh
scripts/lab stop bot02; sleep 45; scripts/lab status | grep -E 'bot02'
scripts/lab start live bot02; sleep 45; scripts/lab status | grep -E 'bot02'
```
Ожидание: после `stop` — «остановлен владельцем — сторож не поднимает»; после `start` — запущен.

### Риски
- `pause` больше не замораживает бота: если раньше пауза использовалась, чтобы бот стоял, теперь он отбивается.
- Новое правило «HP ≥ 80% перед охотой» может удлинить отдых, если вес ≥ 50% (RO не восстанавливает HP) и нет зелий:
  тогда в `report` бот долго в городе — прислать вес и `routine_wait_hp`.
- Неперечисленные оружие/броня копятся на складе вместо продажи (при `minStorageZeny 100`).

### Откат
`scripts/lab down`, `git checkout --detach 490156a0c96a4a08c5588bcb16c781b56967d11c`, `scripts/lab up`.

### Что прислать
Вывод шагов 0–4 в `docs/qa/HERMES-<sha7>.md`; для каждого AUT из «Что изменилось» — «подтверждено / не наблюдалось / провал».

## Задание №13: весь пакет автономности — группа, выживание, состояния, бюджет; короткая миссия пары

**Ветка:** `claude/brain-routine` (включает №10–12). **Commit:** из сообщения разработчика.
Что сделано и что нет — по каждому AUT: [`AUTONOMY_STATUS.md`](AUTONOMY_STATUS.md). Это проверка «код → игра»;
ничего не считать закрытым по ack или репликам. Можно выполнять вместо отдельных №10–12 (их шаги вошли сюда).
login/char/map не перезапускать, БД не трогать, SQL/GM-лечение, ручной `respawn` и телепорт админом не использовать.
Без пассивного наблюдения дольше указанного; если шаг не наблюдается за его время — «не наблюдалось» и дальше.

### Что нового (кратко)
- Плагин `survival` (в `loadPlugins_list`): прогноз урона, экстренное зелье по ID, Butterfly Wing при HP < 15%
  без зелий, подъём с отдыха под ударами. Эффекты (яд и т. п.) — Green Potion/Panacea.
- Группа: приглашение `LR_<житель>` плагин принимает сразу (раньше `partyAuto 1` успевал отказать — `party: null`
  в QA №9). Лидер Arkady; Vera охотится и отдыхает в его темпе, идёт за ним; лидер ждёт отставшую ≤ 3 мин.
  Лечение подтверждается пакетом сервера: событие `heal_confirmed` с числом HP.
- Состояния жителя (`status` в decisions и report), арбитр движения, переподключение, отбрасывание устаревших
  ответов модели, лестница застревания, оповещения `scripts/lab alerts`, общий бюджет всех жителей,
  выбор карты по опыту, разбор смерти по фактам, `BRAIN_DISABLE`.
- Профили: `residents`, `survival_*`, `useSelf_item` эффектов, `pickupitems.txt` (не подбирать тяжёлое дешёвое).

### Шаг 0 — код и проверки (5 мин)
```sh
cd /opt/ro-bot-lab/src/live_ro-qa; export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive && git rev-parse HEAD
python3 scripts/check.py
(cd brain && python3 -m unittest tests.test_brain tests.test_rules tests.test_typesafe tests.test_limits tests.test_plans \
  tests.test_routine tests.test_economy tests.test_inbox tests.test_party tests.test_postmortem tests.test_lifecycle \
  tests.test_reliability tests.test_maps)                       # OK, 101
for t in bots/tests/*.t; do perl -Ibots/tests/stubs $t | tail -1; done   # 1..22, 1..29, 1..33, 1..39
scripts/lab doctor | sed -n '/карты мира/,/OpenKore/p'          # все [ok]; если [!!] prt_in — прислать и остановиться
grep -c BRAIN_GLOBAL $LAB_ROOT/secrets/live_ro.env || echo "нет — действуют значения по умолчанию (600 вызовов, \$2 на всех)"
scripts/lab down; scripts/lab up && sleep 120
grep -hE 'Unable to load|survival|economy' $LAB_ROOT/logs/bot0*/console.log | head -6; scripts/lab report
```
Ожидание: плагины загружены (нет `Unable to load plugin`), в `report` строки «состояние», «хозяйство», «группа».

### Шаг 1 — группа и подтверждённое лечение (до 10 мин) — главный пункт
```sh
grep -h '"type": "party"' $LAB_ROOT/state/bot0*/decisions.jsonl | tail -6
grep -hE 'party|Party|приглашение' $LAB_ROOT/logs/bot0*/console.log | tail -8
scripts/lab report | grep -E '^== |группа'
# дальше — пока оба на охоте вместе (распорядок/лидер), до 10 мин:
grep -h 'heal_confirmed' $LAB_ROOT/state/bot0*/decisions.jsonl | tail -3
```
Ожидание: `party_create` у Arkady, у Vera `приглашение в группу жителя LR_Arkady — принимаю`, у обоих
`party_confirmed` («подтверждена сервером»). Vera следует режиму Arkady (`routine_party_hunt`/`routine_party_town`) и идёт
за ним (`follow Arkady`). Лечение — `heal_confirmed` `"from": "Vera", "to": "Arkady", "amount": N` (N > 0).
Если за 10 мин Arkady не получал урон ниже порога Heal — «лечение не наблюдалось», указать HP Arkady из report.

### Шаг 2 — выживание и смерть (по факту, без провокации)
```sh
grep -hE '\[survival\]|You died|Sending respawn|ai auto для респауна' $LAB_ROOT/logs/bot0*/console.log | tail -8
grep -h '"type": "postmortem"' $LAB_ROOT/state/bot0*/decisions.jsonl | tail -2
grep -h '"event": "routine_(recover|wait_hp|escape)"' $LAB_ROOT/state/bot0*/decisions.jsonl | tail -4
```
Ожидание: если был опасный бой — `[survival] ... пью`; смерть — респаун без человека, `death_report` с монстрами и
«неизвестно», `routine_recover`, на охоту только при HP ≥ 80%. Ничего не случилось — «не наблюдалось».

### Шаг 3 — состояния, оповещения, команды (3 мин)
```sh
grep -h '"type": "status"' $LAB_ROOT/state/bot01/decisions.jsonl | tail -5
scripts/lab alerts
scripts/lab stop bot02; sleep 45; scripts/lab status | grep bot02          # «остановлен владельцем»
scripts/lab start live bot02; scripts/lab routine bot02 rest                 # команда до входа в игру
sleep 90; grep -h '"type": "operator"' $LAB_ROOT/state/bot02/decisions.jsonl | tail -1
```
Ожидание: переходы состояний с причинами; оповещений нет или только реальные; ровно одна команда `ok`.

### Шаг 4 — короткая миссия пары (AUT-116, по возможности, до 30 мин)
Без вмешательства, только наблюдение итогов в начале и в конце:
`scripts/lab report` → (через 30 мин) `scripts/lab report`, плюс
```sh
for k in kill died heal_confirmed party_confirmed gift_given gift_received death_report map_banned; do
  printf '%s: ' $k; grep -h "\"$k\"" $LAB_ROOT/state/bot0*/decisions.jsonl | wc -l; done
grep -hE 'Selling|Auto-storaging|Buying' $LAB_ROOT/logs/bot0*/console.log | tail -4
```
Цель: пара охотится вместе, Vera лечит Arkady (сервер), при перегрузе — продажа/склад, при нехватке зелий — закупка или
обмен, затем снова охота. Записать, какие звенья наблюдались, а какие нет; ручные вмешательства — перечислить (их не должно быть).

### Риски
- Группа: если `party_confirmed` не появляется — прислать строки `party` из console.log: возможно, сервер отвечает иначе.
- `survival` пьёт зелья раз в секунду при опасности — расход зелий выше прежнего; зелий нет — крыло (если есть) или danger.
- Vera следует за Arkady: её личная норма охоты подчинена лидеру, пока группа подтверждена.
- `pickupitems.txt`: 142 тяжёлых дешёвых предмета не подбираются — доход от них уходит, зато нет перегруза.
- Отключить новинку без отката: `BRAIN_DISABLE=party` (или `economy`) в env, `scripts/lab stop brain && scripts/lab start brain`.

### Откат
`scripts/lab down`, `git checkout --detach d2032a7f046dacf42129fb0e56ddd2f6fec88e7a` (фаза A) или
`490156a0c96a4a08c5588bcb16c781b56967d11c` (до фазы A), `scripts/lab up`. Память жителей совместима: копия до миграции —
`$LAB_ROOT/state/<bot>/memory.sqlite.bak-v1`.

### Что прислать
`docs/qa/HERMES-<sha7>.md`: вывод шагов 0–4 и таблица «AUT-ID — подтверждено / не наблюдалось / провал» для пунктов
со статусом «код» в AUTONOMY_STATUS, которые затронуты наблюдениями.

## Задание №14: органичный мир — сон, мотивы, город, речь без LLM, атлас, карьера; исправления ревизии

**Ветка:** `claude/brain-routine`. **Commit:** из сообщения разработчика. Заменяет №13 (все его шаги актуальны, ниже — что
добавить). Статусы: [`AUTONOMY_STATUS.md`](AUTONOMY_STATUS.md) и раздел «Статус» в [`ORGANIC_BACKLOG.md`](ORGANIC_BACKLOG.md).
Правила те же: login/char/map не перезапускать, БД не трогать, без SQL/GM/ручного respawn, без провокаций.
**Важно:** в env должно быть `LAB_BOTS="bot01 bot02"` — мозг теперь считает жителями только запущенных.

### Что нового и что проверить в первую очередь
1. **Исправление сидения (ORG-001, главный тупик QA №9).** Команда `sit` OpenKore блокировала продажу, склад и движение
   (`sitAuto_forcedBySitCommand`). Теперь бот садится сам (`sitAuto_idle`), а перед движением встаёт.
   Проверка: в городе при весе ≥ 48% — `Selling`/`Auto-storaging` в console.log; встреча из сидения — бот идёт к точке.
2. **Прокачка навыков (все профили).** raiseSkill отключался на навыке с невыполненным требованием; у Vera Blessing стоял до
   Divine Protection 5. Проверка: `grep -h 'raiseSkill\|Auto-adding skill' console.log | tail`; нет строки
   «prerequisite not reached; disabling skillsAddAuto»; при свободных очках навыки растут.
3. **Сон (ORG-012).** Arkady засыпает около 02:30, Vera около 23:30 (время мира UTC+3), 6.5–8.5 ч: распорядок уводит в город,
   затем `relog <сек>`. Проверка (если окно сна попадает в проверку): `routine_sleep`/`routine_wake` в decisions,
   `Relogging in N seconds` в console.log, `scripts/lab report` — состояние SLEEPING; персонаж офлайн на сервере.
   Если окно не попадает — «не наблюдалось» (не ждать).
4. **Город (social.py).** Прогулки между фонтаном, Kafra, торговцами и собором; разговор Arkady и Vera при встрече
   (шёпот с меткой `[chat:...]`, эмоции `e ...`); реакции на уровень/лечение. Проверка за 30 мин отдыха:
   `grep -h '"source": "social"\|"type": "social"' decisions.jsonl | tail`; в console.log шёпоты обоих и `e wav`/`e lv`.
5. **Мотивы и характер.** `scripts/lab report` — строка «мотивы сейчас»; выбор карты охоты — `routine_map_choice` с причиной.
6. **Поездка по делам (ORG-018).** Вес ≥ 40% или < 10 зелий в городе — `routine_service`, затем `autosell`/`autostorage`.
7. **Атлас и рост.** Arkady (lv ~41) на полях 1–6 ур.: ожидается `routine_grow_advice` и оповещение `growth` с советом
   (gef_fild02, prt_fild10, gef_fild09). Само освоение карт — только при `auto_hunt_maps: true` в goals.json и после
   `scripts/lab doctor` (карты включены на сервере) — по решению владельца.
8. **Карьера.** `report`/промпт: цель «путь Swordsman -> Knight ...». Сам квест профессии выключен (`auto_job_change: false`).
9. **Хроника мира:** `scripts/lab chronicle` — один день всех жителей.

### Шаг 0 — код и проверки (5 мин)
```sh
cd /opt/ro-bot-lab/src/live_ro-qa; export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive && git rev-parse HEAD
grep -n '^LAB_BOTS' $LAB_ROOT/secrets/live_ro.env          # должно быть "bot01 bot02"
python3 scripts/check.py
(cd brain && python3 -m unittest discover -s tests)          # OK, 179 (PyYAML нужен для 2 тестов сверки, иначе skipped)
for t in bots/tests/*.t; do perl -Ibots/tests/stubs $t | tail -1; done   # 1..40 1..29 1..35 1..28 1..39
python3 scripts/check_skill_lists.py                          # порядок навыков OK
scripts/lab doctor | sed -n '/карты мира/,/OpenKore/p'
scripts/lab down; scripts/lab up && sleep 120; scripts/lab report
```

### Шаги 1–4 — как в №13 (группа и Heal, выживание, состояния и команды, миссия пары), плюс пункты 1–9 выше
Для каждого пункта 1–9: «подтверждено / не наблюдалось / провал» с выдержкой из console.log/decisions.jsonl.

### Риски
- Сон: `relog` разрывает соединение без пакета выхода — если сервер держит сессию, при входе возможен «still recognizes»;
  OpenKore повторит вход сам. После трёх неудач уснуть — оповещение `sleep`.
- Город: эмоции и шёпоты видны другим игрокам рядом. Отключить: `BRAIN_DISABLE=social`.
- Изменённые списки навыков: при следующем уровне профессии бот может выучить Divine Protection/Provoke (раньше — нет).
- Выключить новинки без отката: `BRAIN_DISABLE=social,party,economy,career,routine` (любые), `scripts/lab stop brain && scripts/lab start brain`.

### Откат
`scripts/lab down`, `git checkout --detach bd2ef50720c9dcbdce65a3fea97df85d3ed1c857` (задание №13) или
`d2032a7f046dacf42129fb0e56ddd2f6fec88e7a`, `scripts/lab up`. Память: копии `memory.sqlite.bak-v1`.

### Что прислать
`docs/qa/HERMES-<sha7>.md`: вывод шага 0, итоги пунктов 1–9 и шагов №13, `scripts/lab chronicle` за день проверки,
список ручных вмешательств (цель — 0).


## Задание №15: органичный мир — занятия, связи, группы, события мира, экономика жителей, реестр

**Ветка:** `claude/brain-routine`. **Commit:** из сообщения разработчика. Заменяет №14 (его пункты 1–9 актуальны, ниже —
что добавить). Статусы: раздел «Статус» в [`ORGANIC_BACKLOG.md`](ORGANIC_BACKLOG.md). Правила те же: login/char/map не
перезапускать, БД не трогать, без SQL/GM/ручного respawn. В env по-прежнему `LAB_BOTS="bot01 bot02"`.
**Новых жителей не создавать** (Bram, Ilsa, Rook, Odette лежат в реестре как неактивные) — рождение только по решению
владельца и с бэкапом БД (`docs/POPULATION.md`).

### Что нового и что проверить
1. **Занятия (activity.py).** Выбор занятия по мотивам, инерции и шуму: `grep -h '"type": "activity"' decisions.jsonl | tail`.
   Ожидается смена занятий за 2 ч (охота, отдых, прогулка, дела), не одно и то же.
2. **Связи (bonds.py).** После `meeting_confirmed` — `bonds_hunt_together` или `bonds_sit_together`, через 15 мин
   `bonds_together_done/lost`. При отношении ≥ 3 — `friend request` в console.log и Vera/Arkady в списке друзей (`friend`).
3. **Группа ≤ 3 (party.py).** С двумя жителями поведение как в №13; проверить, что приглашение и Heal работают.
4. **События мира.** Файл `state/shared/world.sqlite` создаётся; `scripts/lab chronicle` — раздел «События мира».
   Объявления сервера (если будут) — события `event`. Цели недели: строка в `report` / промпте.
5. **Экономика жителей (docs/ECONOMY.md).** Шёпоты с меткой `[offer:...]`; сделка: обе стороны `deal`, в console.log
   у обоих `Deal complete`; события `trade_sold`/`trade_bought` или `trade_unverified`. В `report` — строка
   «экономика за сутки», в хронике — строка «экономика:». Почта: `mail_check` раз в сутки; подарок почтой — только если
   друг не виден. **Риск:** RODEX-пакеты для 20180620 не проверялись — при ошибках в console.log выключить
   `"market": {"enabled": false}` в goals.json и прислать выдержку.
6. **Реестр (docs/POPULATION.md).** `scripts/lab roster` — расхождений нет; `scripts/lab doctor` — раздел «жители».
   Рендер конфигов должен совпасть с нынешним (кроме логина/пароля): `diff` старого и нового control/config.txt.
7. **Реплей.** Для разбора — включить `BRAIN_RECORD=1` на один прогон, прислать размер `state/<bot>/replay.jsonl`
   (сам файл не присылать, в нём реплики).

### Шаг 0 — код и проверки
```sh
cd /opt/ro-bot-lab/src/live_ro-qa; export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive && git rev-parse HEAD
python3 scripts/check.py
(cd brain && python3 -m unittest discover -s tests)          # OK, 254 (PyYAML нужен для тестов сверки, иначе skipped)
for t in bots/tests/*.t; do perl -Ibots/tests/stubs $t | tail -1; done   # auto_create 34, brain_bridge 49, combat 29, economy 102, job_change 28, survival 39
python3 scripts/check_skill_lists.py
scripts/lab roster; scripts/lab doctor | sed -n '/жители/,/^==/p'
scripts/lab down; scripts/lab up && sleep 120; scripts/lab report
```

### Риски
- Торговля: при `dealAuto 3` обе стороны подтверждают автоматически; отмена `deal no` при несоответствии не проверялась
  в игре. Выключить: `"market": {"enabled": false}`.
- Новичок от autoCreate появится в `iz_int` (учебный полигон), выйти оттуда бот не умеет — до решения владельца
  (`start_point` в conf/import или вывод через tmux) жителей не рождать.
- Выключить новинки без отката: `BRAIN_DISABLE=social,party,economy,career,routine,world_bus` (любые).

### Откат
`scripts/lab down`, `git checkout --detach <commit задания №14>`, `scripts/lab up`. Память: копии `memory.sqlite.bak-v1`.

### Что прислать
`docs/qa/HERMES-<sha7>.md`: вывод шага 0, итоги пунктов 1–7 и пунктов №14, `scripts/lab chronicle` за день,
ручные вмешательства (цель — 0).

## Задание №16: общество, питомцы, смены и ресурсы, исправления ревизии стыков

**Ветка:** `claude/brain-routine`. **Commit:** из сообщения разработчика. Заменяет №15 (его пункты 1–7 актуальны).
Правила те же: login/char/map не перезапускать, БД не трогать, без SQL/GM/ручного respawn. `LAB_BOTS="bot01 bot02"`.
Новых жителей не рождать, `start_point` не включать, `auto_job_change` не трогать — это решения владельца
(docs/POPULATION.md §6).

### Важно до запуска
- **Обновлять мост и мозг вместе:** номер эмоции теперь в поле `emotion` (раньше уходил в `id`, занятый ack, — эмоции,
  скорее всего, не работали). Проверка: `scripts/lab doctor` — плагины `pets` и все прежние загружены.
- Профили: в `sys.txt` добавлен плагин `pets`; в `config.txt` — блок `buyAuto Pet Food` (`disabled 1`); в конце
  `items_control.txt` — раздел питомцев (яйца, инкубатор, Pet Food не продаются).

### Что проверить
1. **Эмоции (ORG-022).** В console.log обоих: `e wav` при встрече, `e thx`/`e ok` после передачи. Нет — выдержку
   `decisions.jsonl` с `"action": "emote"` и ack.
2. **Вывеска (ORG-026).** В городе `chat create "..."` в console.log и комната видна другому (`chat list` у второго
   бота — только чтение). Перед уходом на охоту — `chat leave`. `society_room_failed` — прислать (возможно, рядом NPC).
3. **Ссоры (ORG-027).** Специально не провоцировать; если в `decisions.jsonl` есть `society_quarrel`/`society_reconciled` —
   прислать контекст.
4. **Питомцы (ORG-051, docs/PETS.md).** Скорее всего «не наблюдалось» (нужен предмет приручения в рюкзаке).
   Если в decisions есть `pet_tame`/`pet_hatch` — прислать выдержку console.log (`Attempting to capture pet`,
   `Pet capture success/failed`). Ошибки пакетов — выключить `BRAIN_DISABLE=pets` и прислать.
5. **Ресурсы (ORG-047).** `scripts/lab resources` — прислать вывод целиком (RSS/CPU тел и мозгов, «сколько ещё влезет»).
   Это главный замер для решения о числе жителей.
6. **Смены (ORG-044).** Не включать `LAB_MAX_ONLINE` без решения владельца; только `scripts/lab doctor` — раздел смен.
8. **Жизнь группы (ORG-053, docs/SOCIETY.md).** В console.log: шёпот `[crew:pref:...]` от Vera лидеру, фразы
   в чате группы (`[Party]` у обоих), `crew_group_choice` в decisions лидера перед сессией, прогулка Vera за Arkady
   в городе (`crew_walk`, `follow`). Слишком шумно — `BRAIN_DISABLE=crew`.
7. **Ревизия стыков.** Сон, квест и поход к NPC теперь ждут конца сделки/почты; при отказе «занят» — запись в decisions.
   Если житель долго не засыпает в своё окно — прислать `routine_sleep`/`sleep_blocker` из decisions.

### Шаг 0
```sh
cd /opt/ro-bot-lab/src/live_ro-qa; export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive && git rev-parse HEAD
python3 scripts/check.py
(cd brain && python3 -m unittest discover -s tests)          # OK, 352 (без PyYAML/upstream часть skipped)
for t in bots/tests/*.t; do perl -Ibots/tests/stubs $t | tail -1; done
# auto_create 34, brain_bridge 70, combat 29, economy 102, job_change 34, pets 32, survival 39
scripts/lab doctor; scripts/lab resources
scripts/lab down; scripts/lab up && sleep 120; scripts/lab report
```

### Откат
`scripts/lab down`, `git checkout --detach <commit задания №15>`, `scripts/lab up`. Профили откатываются вместе с кодом.

### Что прислать
`docs/qa/HERMES-<sha7>.md`: шаг 0, пункты 1–8 этого задания и 1–7 задания №15, `scripts/lab resources`,
`scripts/lab chronicle` за день, ручные вмешательства (цель — 0).

## Задание №17: первый прогон ночного кода — этап 0 из docs/ROLLOUT.md

**Ветка:** `claude/brain-routine`. **Commit:** из сообщения разработчика. Заменяет №16 по выкладке; проверяемые пункты
№14–16 остаются (они и есть этап 0). Полный план включения — [`ROLLOUT.md`](ROLLOUT.md), этапы 0–7. За ночь добавлено
~50 модулей мозга и плагины; **в игре ничего не проверено** — поэтому первый прогон почти всё новое выключает.
Правила те же: login/char/map не перезапускать, без SQL/GM/ручного respawn, БД не трогать, `start_point`, гильдию,
ивенты, свадьбу, спарринг, `auto_job_change`, `auto_hunt_maps` не включать (решения владельца).

### Что изменилось для тела и оператора (даже на этапе 0)
- **Мост и мозг — вместе.** Новые плагины в `sys.txt`: `pets`, `refine`, `spar`, `buyer` (все спят без команды мозга).
  `scripts/lab doctor` должен видеть их без ошибок загрузки.
- **Таблицы переходов renewal** — `bots/common/tables/portals.txt` через `!include` в профиле, `field_*` в `servers.txt`
  (izlude, полигон, Морокко через Kafra). `portalRecord` — как в профиле (решение владельца, см. ROLLOUT).
- **Точка отдыха Пронтеры перенесена** с фонтана (156,185) в западный квартал **106,188** (`goals.json routine.town`,
  риск «все у фонтана»). Проверить маршрут: `Calculating route` без `Unable to calculate`.
- **Память мозга: миграция схемы v3** (индекс) — при первом старте мозг сам делает `memory.sqlite.bak-v2`. Перед стартом —
  `cp -a $LAB_ROOT/state $LAB_ROOT/state.before-rollout`.
- **Разогрев после входа в игру:** социальные и хобби-модули молчат первые 1–2 мин после подключения тела (WARMUP),
  сигналы группы `[party:hunt|town:]` — при смене режима и раз в 30 мин (было 5 мин), шёпоты офлайн-жителям не шлются.
- **Команды оператора** (`scripts/lab plan/ask`, сторож) теперь не теряются: мозг забирает inbox атомарным rename, задержка ~1 с.
- `items_control`: раздел питомцев (яйца, инкубатор, Pet Food не продаются); `config.txt`: выключенные блоки `buyAuto`
  (Pet Food, Empty Bottle).

### Шаг 0 — код, проверки, строка этапа 0
```sh
cd /opt/ro-bot-lab/src/live_ro-qa; export LAB_ROOT=/opt/ro-bot-lab
git fetch origin && git checkout --detach <COMMIT> && git submodule update --init --recursive && git rev-parse HEAD
python3 scripts/check.py && python3 scripts/check_skill_lists.py
(cd brain && python3 -m unittest discover -s tests)          # OK, ~1140 (часть skipped без PyYAML/upstream)
for t in bots/tests/*.t; do perl -Ibots/tests/stubs $t | tail -1; done   # 12 файлов, без not ok
scripts/lab down; cp -a $LAB_ROOT/state $LAB_ROOT/state.before-rollout
# в secrets/live_ro.env:
BRAIN_DISABLE=home,explore,strangers,healer,gaze,tradition,rivalry,gossip,episodes,mentor,director,orders,market_day,savings,dream,mood,calendar,crowd,habits,interests,wealth,wed,fest,attention,drift,scars,flaw,treat
scripts/lab modules bot01        # должно совпасть со строкой: выключенные — «BRAIN_DISABLE»
scripts/lab doctor; scripts/lab resources
scripts/lab up && sleep 120; scripts/lab report
```

### Что проверить (этап 0, сутки)
1. Пункты №16 (1–8) и №15 (1–7) — база: распорядок, сон, город, группа и crew, питомцы, вывески, эмоции.
2. Плагины загружены, ошибок в `console.log` нет; продажа не уносит зелья, крылья, предметы Sir Andrew, раздел питомцев.
3. Маршруты: Tool Dealer `prt_in 126,76`, Kafra `prontera 146,89`, точка отдыха 106,188, карты охоты.
4. Пассивные модули молчат при первом запуске: `collection`, `places`, `bestiary`, `achieve` (если пришёл список
   достижений — строки «Achievement» в console.log), `memoir`.
5. Новые инструменты: `scripts/lab organic all` (метрики M1–M22, пороги — гипотеза), `scripts/lab dashboard`,
   `scripts/lab census`, `scripts/lab episode`.

**Прошло:** ручных вмешательств 0, Traceback 0, оба жителя сутки живут (охота → город → сон → охота). **Откат:** ошибки
плагинов/маршрутов — `git checkout --detach <commit задания №16>`, `cp -a state.before-rollout state` (память v3
совместима и со старым кодом). Дальше — этап 1 по ROLLOUT, только после отчёта.

### Что прислать
`docs/qa/HERMES-<sha7>.md`: шаг 0 (вывод), `scripts/lab modules bot01`, пункты 1–5, `scripts/lab organic all`,
`scripts/lab resources`, `scripts/lab chronicle` за сутки, ручные вмешательства (цель — 0).
