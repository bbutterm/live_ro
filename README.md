# live_ro — связующий репозиторий

Код редактируется через GitHub, выполнение — на VPS. Репозиторий приватный.

## Структура
- `upstream/rathena`, `upstream/openkore`: git submodules, закреплённые версии.
- `server/`: наши настройки и патчи, без секретов.
  - `server/build.conf` — PACKETVER и флаги сборки;
  - `server/conf/import-tmpl/` — шаблоны конфигов rAthena с плейсхолдерами `@@...@@`;
  - `server/patches/` — патчи исходников upstream.
- `bots/bot01/`: профиль бота, поля авторизации пустые.
- `brain/`: место для AI-координатора. **Пока не реализован.**
- `scripts/lab`: подготовка, сборка, релизы, запуск/остановка, БД.
- `scripts/check.py`: статическая проверка (пины, PACKETVER, секреты).
- `lab.env.example`: список локальных секретов (сам `lab.env` — вне Git).
- `docs/`: `SETUP.md`, `RELEASES.md`, `SUBMODULES.md`, `VPS.md`.

```sh
git clone --recurse-submodules https://github.com/bbutterm/live_ro.git
cd live_ro
python3 scripts/check.py
scripts/lab prepare          # затем заполнить $LAB_ROOT/secrets/lab.env
scripts/lab deploy HEAD && scripts/lab activate <sha12>
scripts/lab db-init          # только для пустой БД
scripts/lab start && scripts/lab start bot01
```
Подробно: `docs/SETUP.md`. Обновление и откат: `docs/RELEASES.md`.

## Важное
Push не деплоит, не перезапускает сервер и не меняет базы. Обновление делается вручную
через `scripts/lab deploy/activate`, откат — через `scripts/lab rollback`.

rAthena ранее собирался вручную с PACKETVER 20180620, без LTO. Сборка через
`scripts/lab`, полный запуск мира, вход OpenKore и AI-поведение **ещё не подтверждены**.

Секреты, игровые базы, память персонажей, логи и бинарники не коммитить.
Изменения исходников upstream — только патчами или fork (`docs/SUBMODULES.md`).
См. также `AGENTS.md`.
