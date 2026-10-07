# Handoff — техническая веха A принята

Читайте STATUS.md, QA_B_01.md, QA_A.md, QA_02.md, QA_03.md. Исходный checklist A в docs/ro_residents_report/CHECKS.md: 8/8 gates PASS. Графический смотр владельца остаётся отдельным, не выдавать за выполненный. Владелец после этого явно разрешил следующие вехи самостоятельно, без вопросов, только отчёты. B-01 реализована: см. QA_B_01.md, plugins/residentBody, packages/body_gateway. Полная B/T1–T13 НЕ принята; C/LLM не запускались.

Один исполнитель, без Claude/других coding models. Стенд /root/ragnarok, приватные profiles/credentials/evidence вне Git. Обычный ResidentA 150001/group0, Tester 150000/group99. Не создавать их SQL-вставками и не подменять игровые результаты.

- Native Tester proof: prontera №5062, покупка picklog №4/S/501 в prt_in, field Poring kill №5069, die №5070 (@die только Tester), loadmap №5071, relog №5073/5074. Границы зафиксированы в QA_A; для fresh-game нужны новые.
- tmux server -L ro-residents, target tester; tools/tester.sh посылает только literal-команду в уже запущенную сессию.
- tools/events.py/wait_event.py/sql.sh читают приватный reader.cnf; ONLY SELECT. stdout — диагностический, не durable ACK.
- Witness source server/witness: residents_witness.txt, maps.txt, log_conf.txt. Persistent runtime includes witness + residents_maps, log import; runtime успешно restarted. QA fixture не в автозагрузке. Position 5sec/>3 cells.
- DB-time SYSTEM/Europe/Amsterdam; eyes сохраняют ts и добавляют ts_utc. Cursor ordering по ID. После source reset/restore/timezone change новая --source-id; MAX<cursor fails closed. High-water reset/DST/consumer semantics — границы v0, не выдавать за веху D.
- SQLite доказательства /root/ragnarok/evidence/03, комбинированные /evidence/A, ресурсы /evidence/07. Ресурсы A: rAthena457.1MiB, OpenKore143.9MiB, available1225MiB, swap1315MiB; 20 тел не приняты.
- QA-клиенты завершаются после приёмки; не предполагать online или оставлять автономные циклы. Проверять live processes вместо старых PID; run/PROCESSES.md — snapshot.
- Принятый отдельный null-session char patch см. QA_02. Новых core/pin/protocol изменений нет. Не поднимать старый live_ro runtime.
