# Текущий проверенный срез

Срез tester→witness→reader опубликован в f1e2bac; новые проверки описаны в QA_02.md. Это не принятие всей вехи A и не live-monitor.

## Принято
- Tester150000 group99: создание протоколом, вход и движение20,26→22,26; серверные logout/login; SELECT-only reader. 7 source/unit tests PASS.
- ResidentA150001 group0: создан протоколом, вошёл; до registry COUNT events=0; после registry серверные pos20,26→21,25→24,25; logout/login24,25. Отдельный ручной профиль, без LLM/автоохоты.
- Crash воспроизведён под gdb, локализован до char_packet_db.handle(fd,*sd) при nullptr. Отдельный wire-repro RED до patch, GREEN после. Пересобран только char. Исходный upstream pin тот же, **минимальный core patch сохранён отдельно**.
- Witness автозагрузился после настоящего map-server restart: новый лог содержит имя скрипта и Map Server online; появились свежие pos ResidentA с id выше дорестартового baseline. Клиент переподключился.
- Reader получил реальные события обычного жителя в evidence/01b/resident-reader.jsonl. Пользователь имеет только SELECT; DELETE probe отклонён.

## Не принято
Покупка/picklog, kill/die/loadmap. Наличие loadmap handler само по себе не тест, нужны loadevent mapflags. Durable cursor, reconnect/backoff, source reset и нормализация timezone reader ещё отсутствуют. Графический клиент, видео и автономия не проверены. Нет оркестратора или LLM.

## Evidence и воспроизводимость
Сырые логи/DB/credentials только на VPS. evidence/01b: char-crash.log, unauth-red.txt, unauth-green.txt, resident-unregistered.txt, resident-reader.jsonl, autoload.json; debugger stack в logs/char-gdb.log.

Source содержит server/patches/char-null-session.patch и witness-autoload.patch. Submodule SHA без этих patches не эквивалент deployed runtime. См. server/patches/README.md. Универсального installer нет. Скрипты provisioning одноразовые и намеренно отказывают при существующих аккаунтах/схемах.

Веха A частично выполнена. Один исполнитель, внешние модели не запускались; issues не закрыты.
