# live_ro — связующий репозиторий

Код редактируется через GitHub, выполнение — на VPS. Репозиторий приватный.

## Структура
- `upstream/rathena`, `upstream/openkore`: git submodules, закреплённые версии.
- `server/`: наши настройки и патчи, без секретов.
  - `server/build.conf` — PACKETVER и флаги сборки;
  - `server/conf/import-tmpl/` — шаблоны конфигов rAthena с плейсхолдерами `@@...@@`;
  - `server/patches/` — патчи исходников upstream.
- `bots/bot01/`: профиль бота, поля авторизации пустые.
- `brain/`: мозг бота `live_brain` (OpenRouter, память SQLite), см. `brain/README.md`.
- `scripts/lab`: подготовка, сборка, релизы, запуск/остановка, БД.
- `scripts/check.py`: статическая проверка (пины, PACKETVER, секреты).
- `live_ro.env.example`: список локальных секретов (сам `live_ro.env` — вне Git).
- `bots/plugins/`: плагины OpenKore: `lowHpGuard` (выживание), `gracefulStop` (выход из игры), `brainBridge` (связь с мозгом).
- `docs/`: `STATUS.md` (что проверено и где), `HANDOFF.md` (задания для оператора VPS),
  `SETUP.md`, `RELEASES.md`, `SUBMODULES.md`, `VPS.md`.

```sh
git clone --recurse-submodules https://github.com/bbutterm/live_ro.git
cd live_ro
python3 scripts/check.py
scripts/lab prepare          # затем заполнить $LAB_ROOT/secrets/live_ro.env
scripts/lab deploy HEAD && scripts/lab activate <sha12>
scripts/lab db-init          # только для пустой БД
scripts/lab start && scripts/lab start bot01
```
Подробно: `docs/SETUP.md`. Обновление и откат: `docs/RELEASES.md`.

## Важное
Push не деплоит, не перезапускает сервер и не меняет базы. Обновление делается вручную
через `scripts/lab deploy/activate`, откат — через `scripts/lab rollback`.

Текущее состояние смотрите в `docs/STATUS.md`. Там отдельно указано, что реализовано в коде,
что проверено локально и что проверено оператором (Hermes) на VPS. На VPS действует существующая
лаборатория, её БД и персонаж не пересоздаются. Мозг реализован в коде, на VPS ещё не проверен.

Секреты, игровые базы, память персонажей, логи и бинарники не коммитить.
Изменения исходников upstream — только патчами или fork (`docs/SUBMODULES.md`).
См. также `AGENTS.md`.
