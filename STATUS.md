# STATUS
12.10: приёмка v1 — на новом мире (новый VPS, новички, без GM); старый мир выключен.
План: docs/PLAN_V1.md. Этап 1 сделан; код этапа 2 (2.1–2.10) сделан и проверен на стенде.
Развёртывание одной командой: python3 tools/deploy_world.py (docs/DEPLOY_NEW_WORLD.md).
Стенд (rAthena+OpenKore, заглушка LLM): решения LLM → done по свидетелю, чат ≤1 с без остановки охоты.
Follow за игроком: 99% замеров рядом; гость временно вносится в registry узким пользователем.
БД свидетеля в UTC, время фактов — UNIX_TIMESTAMP из БД; DST не сдвигает факты.
Приёмка: ro doctor world (сейчас) и ro accept --window 6h|24h (критерии ТЗ §1).
Контекст ≤1536 знаков, меню v1 из 9 действий; траты распределяются до полуночи UTC.
Журнал шлюза очищается раз в час; логи ротируются; суточный бэкап в /var/backups/ro-world.
make check зелёный. Каталог действий Hermes — для оператора; в меню LLM через llm.menu.
12.10 сервер: --adopt прошёл; 3 жителя онлайн, решения от LLM; doctor world PASS; accept 30m без FAIL по аптайму и тратам.
Следующее: ro accept --window 6h на сервере, затем пассивная ro accept 24h.
Правило main: fetch → merge origin/main → make check → push (AGENTS.md п.16).
