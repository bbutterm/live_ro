# live_ro — связующий репозиторий

Код редактируется через GitHub, выполнение — на VPS. Репозиторий приватный.

## Структура
- `upstream/rathena`, `upstream/openkore`: git submodules, закреплённые версии.
- `server/`: наши настройки/NPC и патчи, без секретов.
- `bots/bot01/`: экспорт текущего профиля, поля авторизации очищены.
- `brain/`: место для AI-координатора. **Пока не реализован.**
- `scripts/`: безопасная проверка версий и отдельная сборка.
- `docs/`: контракт работы другой нейронки и VPS.

```sh
git clone --recurse-submodules https://github.com/bbutterm/live_ro.git
cd live_ro
python3 scripts/check.py
bash scripts/build-server.sh
```
Сборка требует Linux, g++, make, autoconf и dev-библиотеки MariaDB/zlib.

## Важное
Рабочая лаборатория сейчас `/opt/ro-bot-lab`. Этот репозиторий **ещё не является автоматическим деплоем** в неё. Push не перезапускает сервер и не меняет базы.

rAthena собран с PACKETVER 20180620, без LTO. Вход OpenKore и AI-поведение ещё не подтверждены.

Секреты, игровые базы, память персонажей, логи и бинарники не коммитить.
См. `docs/VPS.md` и `AGENTS.md`.
