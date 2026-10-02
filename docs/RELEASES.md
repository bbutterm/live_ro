# Обновление и откат кода

Принцип: **код меняется, данные остаются.** Каждая версия собирается в отдельный
каталог и не трогает живую сборку, БД, `state/` и `logs/`. Push в GitHub ничего
не деплоит. Миграций и рестартов скрипты сами не делают.

## Обновление до выбранного commit

```sh
cd <checkout оператора>            # например $LAB_ROOT/src, отдельный от releases
git fetch origin
git submodule update --init --recursive   # нужно, если в commit поменялись gitlinks
scripts/lab deploy <commit>        # сборка в releases/<sha12>, current не меняется
scripts/lab db-backup              # бэкап перед переключением (обязателен, если есть миграции)
scripts/lab stop
scripts/lab activate <sha12>       # current -> новая, previous -> старая
scripts/lab start
scripts/lab start bot01
```

Что проверяет `deploy`:
1. Checkout оператора чист (`git status --porcelain --ignore-submodules=none`),
   включая содержимое submodules. Если есть хоть одно изменение или неотслеживаемый файл, deploy отказывается.
2. Commit есть локально. Release-каталог клонируется из локального checkout,
   объекты submodules тоже берутся локально, без скачивания upstream заново.
3. В новом каталоге submodules = gitlink commit'а = таблица `PINS` в
   `scripts/check.py`, внутри них нет изменений, PACKETVER совпадает с профилями ботов,
   в файлах нет секретов.
4. Накладываются `server/patches/*/*.patch`; если патч не встаёт, deploy останавливается.
5. Сборка rAthena (`PACKETVER` и флаги из `server/build.conf`) и OpenKore XSTools.
6. Записывается `RELEASE` (commit, время, upstream-коммиты), и только затем
   `*.partial` переименовывается в `releases/<sha12>`. Прерванная сборка не оставляет
   «полуготовую» версию.
7. Если upstream-версии отличаются от current, deploy предупреждает: надо проверить
   `sql-files/upgrades`.

Версии неизменяемы: повторный deploy того же commit отказывается. Битую
неактивную версию удаляют вручную (`rm -rf releases/<sha12>`), но **никогда** не
`current` и не `previous`.

## Откат

```sh
scripts/lab stop
scripts/lab rollback               # current <-> previous
scripts/lab start
```
Или `scripts/lab activate <любой sha12>` из `scripts/lab releases`.
`activate` и `rollback` отказываются работать при запущенном стеке.

## Базы данных и память
- MariaDB, `state/`, `logs/`, `backups/`, `secrets/` лежат вне `releases/` и при
  activate/rollback не меняются.
- Откат кода **не откатывает схему БД.** Если новая версия требовала миграции,
  перед откатом решите, нужна ли обратная миграция или восстановление из
  `backups/` (вручную, с остановленным стеком):
  `gunzip -c backups/<файл>.sql.gz | mariadb --defaults-extra-file=... ragnarok`.
- Изменение схемы оформляется отдельной миграцией с ревью. Автоматически миграции
  не применяются ни при deploy, ни при start.
