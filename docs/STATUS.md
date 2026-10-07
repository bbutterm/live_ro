# Проверенный срез

Один исполнитель. Предыдущие этапы опубликованы в 55f55eb; текущий следующий срез — QA_03.md. Это не приёмка всей вехи A.

## Принято
- Tester и обычный ResidentA(group0): протокольное создание/вход/движение/relog, registry negative/positive. SELECT-only reader и denied-write probe — предыдущий этап QA_02.
- Null-session crash RED→GREEN; отдельный core guard, char пересобран. Witness autoload после настоящего map restart принят ранее.
- QA_03: покупка одной банки501 за50 зени, persisted450 зени/1 банка; серверные kill3328(Poring1002), loadmap3402(guild_vs1), die3431. Стартовый grant500 — fixture, не игровой заработок. SQL не подменял игровые действия.
- Исправлен witness INSERT: a1/a2 по установленной схеме; записи после исправления восстановлены и бой повторён.
- Reader durable SQLite inbox + атомарный cursor, отдельные CLI restarts без replay, реальные боевые события в inbox. Ограниченные retries, реальное восстановление недоступного defaults file; cursor сохраняется при исчерпании retries.

## Не принято
Полная объединённая веха-A приёмка, PM/прочие gates исходного контракта, automatic epoch/reset detection, timezone normalization, downstream ACK/consumer, sustained resource/retention limits. loadevent проверен только на двух QA-картах. 1Hz position probe остаётся диагностическим: перед постоянным населением нужен change detection/heartbeat. Графический клиент, автономия, оркестратор и LLM не запущены.

## Evidence
QA_02.md и QA_03.md — описание source/runtime gates. Raw logs/defaults/SQLite/JSONL только локально. Скрипты provisioning — одноразовые; fixture03 загружается вручную, не startup. Core patch и autoload patch сохраняются отдельно от upstream pin. GitHub CI workflow не включён из-за ранее выявленных прав; local tests не выдаются за CI.
