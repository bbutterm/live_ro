# Серверные изменения

- `build.conf` — PACKETVER, флаги configure, цели make. Секретов нет.
- `conf/import-tmpl/*.txt` — шаблоны `conf/import/*.txt` rAthena. Плейсхолдеры
  `@@ИМЯ@@` подставляются из `$LAB_ROOT/secrets/live_ro.env` при `scripts/lab start`.
  Пароли и логины пишутся только плейсхолдерами (`scripts/check.py` это проверяет).
  Несекретные настройки (рейты и т.п.) — новый файл, например `battle_conf.txt`.
- `conf/optional/*.txt` — готовые, но **не включённые** переопределения: их не рендерит `scripts/lab`, включает
  владелец по инструкции в самом файле. Сейчас: `char_start_point.txt` — новичок появляется в Пронтере, а не в
  учебном полигоне iz_int (docs/POPULATION.md, «Первые шаги новичка»); `guild_no_emperium.txt` — гильдию можно
  основать без Emperium (docs/GUILD.md).
- `patches/rathena|openkore/*.patch` — патчи исходников, см. `docs/SUBMODULES.md`.
- `npc/` — NPC (подключение в сборку ещё не реализовано).
