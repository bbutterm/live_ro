# Подготовка и запуск лаборатории

Эти шаги описывают, как поднять лабораторию с нуля. Выполнять их надо на своей машине или на VPS с разрешения владельца.
**Статус:** скрипты прошли статическую проверку и тест логики в песочнице.
Сборка rAthena/OpenKore через `scripts/lab`, запуск серверов и вход бота **ещё не проверены**:
это этап 2.

## 0. Что где лежит

| Что | Где | В Git? |
|---|---|---|
| Код, шаблоны конфигов, профили ботов | этот репозиторий | да |
| Закреплённые rAthena/OpenKore | `upstream/*` (submodules) | только ссылка |
| Пароли БД, inter-server, аккаунты ботов | `$LAB_ROOT/secrets/lab.env` | **нет** |
| Собранные версии | `$LAB_ROOT/releases/<sha12>/` | нет |
| Сгенерированные конфиги с паролями | `<release>/upstream/rathena/conf/import/`, `$LAB_ROOT/run/` | нет |
| Мир (персонажи, предметы) | MariaDB | нет |
| Память ботов (этап 3) | `$LAB_ROOT/state/` | нет |
| Логи / бэкапы | `$LAB_ROOT/logs/`, `$LAB_ROOT/backups/` | нет |

`LAB_ROOT` по умолчанию `/opt/ro-bot-lab`. Для локальной разработки: `export LAB_ROOT=$HOME/ro-lab`.

> На действующем VPS `/opt/ro-bot-lab` уже содержит лабораторию с неизвестной
> раскладкой. Для первого прогона возьмите отдельный `LAB_ROOT` (например,
> `/opt/live_ro`) и ту же MariaDB. `prepare` не перезаписывает существующие файлы,
> но порты 6900/6121/5121 общие: старый стек перед запуском нового надо остановить.

## 1. Клонирование

```sh
git clone --recurse-submodules https://github.com/bbutterm/live_ro.git
cd live_ro
python3 scripts/check.py        # пины submodules, PACKETVER, поиск секретов
```

## 2. Окружение и секреты

```sh
scripts/lab prepare             # проверяет зависимости, создаёт каталоги и secrets/lab.env
$EDITOR "$LAB_ROOT/secrets/lab.env"   # заменить все CHANGE_ME
```
`prepare` ничего не устанавливает, только печатает команду `apt-get` для недостающих пакетов.

Пустую базу и пользователя создаёт администратор MariaDB (это делается один раз):
```sql
CREATE DATABASE ragnarok CHARACTER SET utf8mb4;
CREATE USER 'ragnarok'@'127.0.0.1' IDENTIFIED BY '<DB_PASS из lab.env>';
GRANT ALL ON ragnarok.* TO 'ragnarok'@'127.0.0.1';
```

## 3. Сборка версии и активация

```sh
scripts/lab deploy HEAD         # отдельный releases/<sha12>, долгая сборка (MAKE_JOBS=1)
scripts/lab activate <sha12>
```
Подробности и откат — в `docs/RELEASES.md`.

## 4. Первичная БД

```sh
scripts/lab db-init
```
Работает **только на пустой БД**, иначе отказывается. Загружает `main.sql` и `logs.sql`
из активной версии. Меняет upstream-учётку `s1/p1` на `INTER_USER/INTER_PASS` и создаёт
аккаунт `BOT01_USER`. Персонажа не создаёт: это задача этапа 2.

## 5. Запуск, проверка, остановка

```sh
scripts/lab start               # login -> char -> map, ждёт открытия каждого порта
scripts/lab start bot01         # OpenKore с runtime-копией профиля
scripts/lab status              # процессы и порты
scripts/lab stop                # бот, затем map -> char -> login (SIGTERM, ждёт 60 с)
scripts/lab stop all --force    # SIGKILL, если процесс завис (риск потери несохранённого)
```
- Перед каждым запуском шаблоны `server/conf/import-tmpl/*.txt` рендерятся заново
  с данными из `lab.env`. Сменили пароль в `lab.env` — достаточно перезапуска.
- По умолчанию серверы слушают только `127.0.0.1` (`BIND_IP`), PIN-код выключен
  (так же, как `pinCode 0` в профиле OpenKore).
- `bots/bot01/control` копируется в `$LAB_ROOT/run/bots/bot01/control`, после чего
  в `config.txt` подставляются `username/password`. Копия пересоздаётся при каждом
  старте, поэтому изменения, сделанные из консоли OpenKore, не сохраняются: их надо вносить в Git.
- Логи: `$LAB_ROOT/logs/rathena/*.log`, `$LAB_ROOT/logs/bot01/`. При старте лог,
  выросший больше `LOG_MAX_MB`, переименовывается в `.1`. Полноценное ограничение
  роста логов работающего процесса — задача этапа 2.
- `status` показывает процессы и порты. Это **не** доказательство, что бот вошёл и играет.

## Известные ограничения этапа 1
- OpenKore запускается в фоне с `--interface=Console::Simple` и stdin из `/dev/null`;
  поведение без терминала проверим на этапе 2 (запасной вариант — tmux).
- Процессы управляются pid-файлами, не systemd: падение не перезапускается автоматически.
- NPC из `server/npc/` в сборку пока не подключаются.
