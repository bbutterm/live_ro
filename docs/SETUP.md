# Подготовка и управление лабораторией

Роли: разработчик пишет код в GitHub, **Hermes** (оператор VPS) проверяет конкретный
commit на VPS и возвращает фактический результат. Порядок обмена описан в `docs/HANDOFF.md`, текущий статус — в `docs/STATUS.md`.

`scripts/lab` работает в одном из двух режимов. Режим задаётся в env-файле.

| | existing (VPS сейчас) | release (новая установка) |
|---|---|---|
| rAthena/OpenKore | уже собранные каталоги оператора (`RATHENA_DIR`, `OPENKORE_DIR`) | `releases/<sha12>` из `deploy` |
| conf/import rAthena | **не трогается** | рендерится из шаблонов |
| БД | существующая; реквизиты читаются из её `inter_conf.txt` | пустая, `db-init` |
| deploy/activate/rollback/db-init | **запрещены** | доступны |
| профиль бота, плагины | из checkout, где лежит `scripts/lab` | из активной версии |

Общие правила:
- запускать **от пользователя-владельца `LAB_ROOT`** (на VPS — `ro-lab`); от root скрипт откажется;
- env-файл: `$LAB_ROOT/secrets/live_ro.env`, права 600, вне Git (можно переопределить через `LAB_ENV`);
- логины и пароли rAthena (`INTER_*`, `BOT*_USER/PASS`): 1–23 печатных ASCII-символа
  без пробелов, иначе команда откажется;
- никаких миграций БД и автоматических перезапусков.

## Режим existing: действующая лаборатория `/opt/ro-bot-lab`

```sh
sudo -u ro-lab -H bash
cd /opt/ro-bot-lab/src/live_ro     # checkout оператора (путь — на выбор Hermes)
git fetch origin && git checkout --detach <commit> && git submodule update --init --recursive
export LAB_ROOT=/opt/ro-bot-lab
scripts/lab prepare                # создаст только недостающее: secrets/live_ro.env, run/, backups/
```
Затем заполнить `$LAB_ROOT/secrets/live_ro.env`:
- `RATHENA_DIR` — каталог с `login-server` и `conf/`;
- `OPENKORE_DIR` — каталог OpenKore с собранным `src/auto/XSTools/XSTools.so`;
- `DB_PASS` оставить пустым: реквизиты будут прочитаны из `$RATHENA_DIR/conf/import/inter_conf.txt`;
- `BOT01_USER`, `BOT01_PASS` — аккаунт Arkady; `BOT01_CHAR_SLOT` — слот Arkady, если не 0;
- `BOT01_CONTROL_DIR` — оставить пустым, чтобы применялся профиль из Git с fallback выживания.
  Если указать текущий control-каталог, бот запустится как раньше, без изменений из Git.

Команды:
```sh
scripts/lab doctor        # только чтение: файлы, права, БД (SELECT), процессы
scripts/lab status        # процессы и порты; найдёт и запущенные вручную
scripts/lab db-backup     # дамп основной и лог-БД в $LAB_ROOT/backups/
scripts/lab stop bot01    # SIGTERM, ждёт до 60 с
scripts/lab start bot01   # runtime-копия профиля -> tmux-сессия live_ro_bot01
scripts/lab stop server   # map -> char -> login
scripts/lab start         # login -> char -> map, ждёт каждый порт
```
- `start` не трогает уже запущенный процесс. Если порт занят чем-то, что не опознано
  как сервер из `RATHENA_DIR`, команда отказывается работать.
- `stop` останавливает только процессы текущего пользователя. Процесс другого
  пользователя (например, root) скрипт не трогает и сообщает об этом.
- Процесс, запущенный вручную, опознаётся по имени и рабочему каталогу (сервер)
  или как единственный `openkore.pl` (бот, пометка `без-метки-профиля`).
- Бот по умолчанию запускается в tmux (`tmux attach -t live_ro_bot01`, выход — `Ctrl-b d`),
  консольный вывод дублируется в `$LAB_ROOT/logs/bot01/console.log`.
  `BOT_RUNNER=background` запускает бота без tmux (`Console::Simple`), этот режим не проверен.

## Режим release: установка с нуля

```sh
git clone --recurse-submodules https://github.com/bbutterm/live_ro.git && cd live_ro
python3 scripts/check.py
scripts/lab prepare && $EDITOR "$LAB_ROOT/secrets/live_ro.env"   # RATHENA_DIR пустой
```
Пустую БД создаёт администратор MariaDB:
```sql
CREATE DATABASE ragnarok CHARACTER SET utf8mb4;
CREATE DATABASE ragnarok_log CHARACTER SET utf8mb4;
CREATE USER 'ragnarok'@'127.0.0.1' IDENTIFIED BY '<DB_PASS>';
GRANT ALL ON ragnarok.* TO 'ragnarok'@'127.0.0.1';
GRANT ALL ON ragnarok_log.* TO 'ragnarok'@'127.0.0.1';
```
```sh
scripts/lab deploy HEAD && scripts/lab activate <sha12>   # см. docs/RELEASES.md
scripts/lab db-init        # только пустые БД: схема, inter-server, аккаунт bot01
scripts/lab start && scripts/lab start bot01
```
Персонажа `db-init` не создаёт. Его создают один раз через консоль OpenKore в tmux.
Зависимости сборки (Debian/Ubuntu): `build-essential git python3 perl libperl-dev
libmariadb-dev libmariadb-dev-compat zlib1g-dev libpcre3-dev mariadb-client tmux`.

## Где что лежит

| Что | Где | В Git? |
|---|---|---|
| Код, шаблоны, профили, плагины ботов | этот репозиторий | да |
| Пароли, реквизиты, пути runtime | `$LAB_ROOT/secrets/live_ro.env` | **нет** |
| Runtime-копия профиля бота | `$LAB_ROOT/run/bots/<bot>/control` (пересоздаётся при start) | нет |
| Мир (аккаунты, персонажи) | MariaDB | нет |
| Память ботов (этап 3) | `$LAB_ROOT/state/` | нет |
| Логи / бэкапы | `$LAB_ROOT/logs/`, `$LAB_ROOT/backups/` | нет |

## Fallback выживания bot01
Плагин `bots/plugins/lowHpGuard` и правки `bots/bot01/control/config.txt`:
- все триггеры телепорта (`teleportAuto_hp/deadly/maxDmg/atkMiss/dropTargetEngaged`)
  выключены. Без навыка и крыльев они только сбрасывали очередь AI и бросали текущий бой;
- при HP < `lowHpGuard_lower` (40%) бот не выбирает новые цели и отбивается только
  от уже атакующих монстров. Пока HP не поднимется до `lowHpGuard_upper` (90%), бот:
  отдыхает сидя, если позволяет Basic Skill ≥ 3, иначе стоит на месте без randomWalk;
- `useSelf_item` пьёт зелья новичка, красные, оранжевые, жёлтые и белые при HP < 50%, если они есть в инвентаре.
