# VPS и обновления

Текущий runtime: `/opt/ro-bot-lab`, отдельный системный пользователь `ro-lab`.
Вне Git: `secrets/`, `state/`, `logs/`, MariaDB и локальные конфиги с авторизацией.

## Процедура
- Подготовка и запуск: `docs/SETUP.md`.
- Обновление до commit и откат: `docs/RELEASES.md` (`scripts/lab deploy/activate/rollback`).
- Работа с submodules: `docs/SUBMODULES.md`.

Все команды выполняются от `ro-lab`. Checkout оператора (например
`$LAB_ROOT/src`) отделён от `releases/`, поэтому живая сборка никогда не перезаписывается.

## Доставка
Автоматического apply/restart после push нет, и это намеренно. Цикл ручной:
`git fetch` → `lab deploy <commit>` → `lab db-backup` → `lab stop` → `lab activate` →
`lab start` → проверка login/char/map и входа бота.
Миграции БД отдельно, вручную, после бэкапа.

## Переход с текущей раскладки
Раскладка `/opt/ro-bot-lab`, сделанная до `scripts/lab`, не описана в репозитории.
Перед переходом оператор должен:
1. Сделать бэкап MariaDB и скопировать текущие локальные конфиги rAthena.
2. Перенести значения паролей БД и inter-server в `lab.env` (`scripts/lab prepare`).
   Если учётка inter-server в таблице `login` уже не `s1/p1`, указать фактическую.
   `db-init` на непустой БД не запускать: он откажется.
3. Для первого прогона использовать отдельный `LAB_ROOT` (например, `/opt/live_ro`)
   и остановить старые процессы: порты общие.

## Секреты бота
В Git лежит `config.txt` без username/password. `scripts/lab start bot01` делает
runtime-копию в `$LAB_ROOT/run/bots/bot01/control` и подставляет `BOT01_USER/PASS`
из `lab.env`. Серверные SQL/inter-server пароли тоже только в `lab.env`.

## Текущее состояние
Ранее вручную подтверждена сборка rAthena. Сборка через `scripts/lab`, полный запуск мира и
вход бота не подтверждены. Brain не реализован. Репозиторий — исходная точка
совместной разработки, а не заявление о готовом AI-мире.
