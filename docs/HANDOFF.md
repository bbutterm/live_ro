# Handoff: продолжать самостоятельно, без внешних coding agents

## Принято в этом проходе
Tester создан, вошёл и прошёл iz_int01 (20,26) → (22,26). Серверный witness пишет реальные pos/logout/login; SELECT-only reader их читает. См. STATUS.md. Это один сквозной срез, не вся веха A.

## Текущий контекст на VPS
Репозиторий /root/ragnarok/repos/live_ro. Runtime rAthena /root/ragnarok/repos/rathena, OpenKore /root/ragnarok/repos/openkore. Тестер последний tracked handle proc_58b8d09f383b, PID112761; не использовать PID без проверки identity/cwd. Разрешены bounded дальнейшие проверки вехи A без повторных подтверждений. Не запускать другие модели/Claude. Secrets в run/private и tester profile, не в Git.

## Следующие gates
1. Разобрать stale char-session SIGSEGV по сохранённому evidence/01b/char-crash.log; не объявлять сервер надёжным по удачному свежему входу. После двух неудачных scoped попыток остановиться с причиной.
2. Создать обычного персонажа-жителя (group0) нормальным клиентским протоколом. Отдельно подтвердить, что незарегистрированный char не пишет события, и что регистрируемый пишет.
3. Включить нужные серверные picklog, проверить реальную покупку. SQL-правки инвентаря/позиции не доказательство покупки/перемещения.
4. Дорастить witness kill/die/loadmap по отдельным тестам и mapflag semantics. Не менять ядро; autoload config patch уже сохранён, restart persistence ещё проверить.
5. Затем reader cursor/restart и отрицательные проверки, уточнение a1/a2/source reset/timezone. Завершить веху A ресурсным замером и честным demonstration.

Не закрывать issues #1–#3 по текущему частичному результату. Никаких paid LLM, будущих вех или открытых портов. Сервер и чат Hermes не выключать при scoped остановке tester.
