# Статус — 2026-10-07

## A: технически принята
8/8 gates, 16/16 tests на её срезе; native gameplay, SELECT-only eyes, SQLite5080/5080/restart0. См. QA_A.md. Личный графический смотр владельца отдельно не выполнен.

## B: первый рабочий срез, НЕ полная приёмка
Явное разрешение владельца: следующие вехи самостоятельно, без вопросов, только отчёты; один исполнитель Hermes, без Claude/других coding models.
- Новый residentBody → Unix socket0600 → durable SQLite WAL telemetry. Group0 ResidentA реально вошёл в iz_int01, HP40/40.
- 169 telemetry за169.83s; интервал p95 1.022s, delivery lag p95 .931ms. `calcPosition` и `dest` различаются на реальном движении.
- move18,26→24,25 выполнен обычным OpenKore CLI; witness5084 и body24,25, delta0. Это НЕ T1/travel_to через новый оркестратор.
- Новый epoch после обычного restart; configSHA до/после совпал. 20/20 unit/source tests PASS. Подробности/ограничения: QA_B_01.md.

Остаток B: полный telemetry contract, StateStore/divergence, action journal/SkillExecutor/CLI, travel_to/whisper, idempotency, cancel/ESTOP/watchdog, recovery и T1–T13, 30мин metrics, Пронтера/межкарточные маршруты, графический смотр/видео. C и LLM не начаты. Автономного цикла/cron/dispatcher нет.

Секреты/профили/БД/сырые логи вне Git. Стенд /root/ragnarok, pins/PACKETVER неизменны. При рестарте сначала проверить live процессы; register — snapshot, не доказательство текущей игры.
