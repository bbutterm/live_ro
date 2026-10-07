# B-01 — реальное тело и телеметрия, веха B ещё не принята

2026-10-07. Владелец разрешил следующие вехи самостоятельно, без вопросов, только отчёты. Один исполнитель Hermes, без Claude/других coding models. Исходные B gates — STEPS.md и IMPLEMENTATION_BRIEF.md; графический смотр не подменяется JSON.

## Что реально работает
- Новый `plugins/residentBody/residentBody.pl`; не перенос старого brainBridge/мозга. Только наблюдение и IDLE_SAFE memory-only настройки.
- Вход обычного ResidentA: char150001, group0; **iz_int01**, пока не Пронтера. SQL не создавал/перемещал персонажа; GM-команд над ним нет.
- Unix BodyGateway: socket0600, один peer того же OS UID, строгая hello identity, UUID/seq/proto validation. JSONL-фреймы durable в отдельном SQLite WAL. Это collector v0, не полный action journal.
- Телеметрия: карта, `pos` через `Utils::calcPosition`, отдельно `dest`, HP/SP, вес, зени, mode. На первой сессии записаны **169 telemetry + hello** за **169,83 с**.
- Фактический интервал p95 **1,022 с**, задержка доставки кадра p95 **0,931 мс**. Это НЕ измерение возраста состояния StateStore или p95 SQL-свидетеля: они ещё не реализованы.
- Операторской командой OpenKore `move 24 25` житель прошёл из 18,26 в 24,25. Witness **№5084**, iz_int01,24,25; body также24,25, расхождение **0 клеток**. Это реальная сверка снимков, не `travel_to` через оркестратор и не T1.
- Во время реального движения получен кадр: `pos=20,26`, `dest=24,25`. Конечная цель не выдаётся за текущую позицию.
- Повторный обычный запуск дал новый body_epoch; новый hello/telemetry успешно записались в тот же SQLite без коллизии старого epoch. Это не T8 посреди маршрута.
- `config.txt` исходного и B-профиля byte-identical, SHA256 до/после совпал. Изменён только `sys.txt` в отдельной приватной runtime-копии: загрузка residentBody, остальные плагины не запускаются.
- **20/20 tests PASS**; RED→GREEN для session, Unix socket/journal, actual Perl telemetry builder, malformed UUID, bind-race cleanup (не удаляет чужой endpoint). Unit mocks OpenKore используются только в unit harness, не как игровое доказательство. Actual Perl compile с src+src/deps PASS.

## Ошибки/ограничения
- Первый запуск вышел exit0 до входа: в CLI забыта fallback папка `tables`. Исправлен путь `--tables=/root/ragnarok/run/B/resident/tables:tables`; пины/ядро не изменены. Exit0 не объявлялся успехом.
- Console/Simple.pm предупреждал о TERM; повторная проба получила TERM=dumb, без core patch.
- Пробы ограничены timeout. Первая рабочая body сессия завершилась **exit124** по заданному timeout210; это завершение лабораторного процесса, не итог игрового действия.
- Протокол пока только hello/telemetry; party/supplies/nearby и навыки ещё не реализованы. Нет SkillExecutor/CLI, travel_to/whisper, outcomes/trace, action ledger/idempotency, cancel/ESTOP/watchdog60s, action recovery.
- Не закрыты T1–T13, 30-мин RSS/CPU, Пронтера/межкарточные маршруты, личный графический смотр, видео и полная приёмка B. В C не переходили, LLM не добавляли.

## Воспроизведение
На авторизованном изолированном VPS с приватной B runtime-копией профиля:
```
python3 -m unittest discover -s tests -q
python3 -m packages.body_gateway.gateway --socket /root/ragnarok/run/B/body.sock --store /root/ragnarok/evidence/B/body.sqlite --resident resident_a --char-id 150001 --duration 240
```
В другом tracked PTY, cwd repos/openkore:
```
TERM=dumb RESIDENT_SOCKET=/root/ragnarok/run/B/body.sock RESIDENT_ID=resident_a timeout 210 perl openkore.pl --control=/root/ragnarok/run/B/resident/control --tables=/root/ragnarok/run/B/resident/tables:tables --plugins=/root/ragnarok/repos/live_ro/plugins --logs=/root/ragnarok/logs/B-resident --ai=auto
```
Не пытаться запустить второй body на той же учётной записи. При занятом socket gateway отказывается; после аварии сначала проверить owning process, не удалять endpoint слепо. Существующая SQLite schema относится к collector v0; не выдавать её за финальную schema actions.

Raw доказательства вне Git: evidence/B/{body.sqlite,gateway-live.jsonl,position-comparison.json,metrics-first.json,restart-proof.json,config-before.sha256,config-after.sha256}, RED outputs, logs/B-resident. Пароли/профили/БД в Git не экспортируются.
