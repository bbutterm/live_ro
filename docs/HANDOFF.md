# Handoff: один исполнитель, продолжать веху A

## Принято
См. STATUS.md и QA_02.md: обычный ResidentA group0, registry negative/positive, server-observed movement и relog; null-session crash RED→GREEN; witness map-restart persistence.

## Следующие gates
1. Включить нужный picklog, провести настоящую покупку у NPC обычным жителем. Разделять стартовый grant средств/fixtures и факт покупки; SQL-update inventory/zeny не покупка.
2. По отдельным game tests: kill/die/loadmap и нужные loadevent mapflags. Не менять ядро ради witness; единственный core guard уже сохранён и обоснован crash-repro.
3. Reader: SQLite store + cursor атомарно, restart, source identity/reset, ограниченный reconnect и timezone. stdout ACK не durable delivery.
4. Полная bounded веха-A приёмка и свежий ресурсный замер; затем простой исполнитель без LLM, не сейчас.

## Runtime
/root/ragnarok/repos/rathena и /root/ragnarok/repos/openkore; canonical source /root/ragnarok/repos/live_ro. Последние ручные clients: tester proc_58b8d09f383b, resident proc_d2df661721c0. Проверять identity/live-state перед действиями. Регистрация процессов run/PROCESSES.md. Char был пересобран и запущен после guard; map был перезапущен только для autoload test. Login/Hermes/чужие службы не перезапускались. Credentials в protected runtime profiles, не в Git.

Разрешены bounded дальнейшие шаги/проверки без повторного подтверждения. Не запускать другие модели/Claude или платные LLM, не открывать порты. Сырые доказательства не публиковать. Отсутствие дальнейших тестов не закрывать green source suite.
