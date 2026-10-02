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
