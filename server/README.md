# Серверные изменения

- `build.conf` — PACKETVER, флаги configure, цели make. Секретов нет.
- `conf/import-tmpl/*.txt` — шаблоны `conf/import/*.txt` rAthena. Плейсхолдеры
  `@@ИМЯ@@` подставляются из `$LAB_ROOT/secrets/lab.env` при `scripts/lab start`.
  Пароли и логины пишутся только плейсхолдерами (`scripts/check.py` это проверяет).
  Несекретные настройки (рейты и т.п.) — новый файл, например `battle_conf.txt`.
- `patches/rathena|openkore/*.patch` — патчи исходников, см. `docs/SUBMODULES.md`.
- `npc/` — NPC (подключение в сборку ещё не реализовано).
