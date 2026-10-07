# Проверенный срез

**Веха A: техническая приёмка PASS, 8/8 gates (включая 1б).** См. QA_A.md и ресурсные замеры RISKS.md §2.1а. Графический смотр владельца отдельно не принят.

- Реальные серверные действия ordinary ResidentA и native Tester: покупка, движение, relog, kill/die/loadmap.
- SELECT-only eyes, события в порядке ID, wait-event 0/1/2, UTC-представление для фиксированного timezone стенда.
- Durable SQLite inbox/cursor, restart/dedup/rollback/retry; явная generation и защита от MAX<cursor. Automatic high-water reset detection и consumer exactly-once не обещаны.
- Позиционная проба 5 с с порогом >3 клетки; stationary test без новых строк.
- Persistent witness/maps/logging реально загружены после restart.

Работа в B/C/LLM не начата. Графический вход и личный смотр нельзя заменить автоматическими проверками. Отдельные 30-минутный CPU benchmark, skill PM, телеметрия/действия/ESTOP относятся к следующим вехам.
