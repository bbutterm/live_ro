# Проверенный срез: tester → witness → reader

Факты завершённого bounded прогона, не live-monitor и не принятие всей вехи A.

## Подтверждено игрой
- Tester создан обычным клиентским протоколом: char_id 150000, novice, base level 1. Это привилегированный операторский тестер (group99), не обычный автономный житель.
- Map-server подтвердил вход. OpenKore правильно читает имя и уровень после charBlockSize=155 (175 соответствует значительно более новым клиентам).
- Свидетель v0 загружен настоящим NPC-loader, записывает только registry-enabled персонажей. Таймер использует name lookups без attachrid.
- Серверные pos: iz_int01 (20,26) → (22,26), это не только ACK движения.
- Настоящие события: id34 logout, id35 login после relog; оба в iz_int01 (22,26).
- Reader получил 71 реальное событие при первом чтении после id25, включая logout/login. Время оставлено сырым временем DB, без притворной UTC-нормализации.
- Reader имеет отдельного пользователя с SELECT только на resident_events. Пробный DELETE WHERE 1=0 отклонён.
- 7 unit/source tests PASS; bounded acceptance-check 4/4 PASS. Сырые доказательства только `/root/ragnarok/evidence/01b`.

## Runtime и исходники
Сервисам map/char/login оставлены прежние loopback-порты. Witness подключён через npc/scripts_custom.conf, diff подключения сохранён в server/patches/witness-autoload.patch. Автозагрузку после нового restart пока не проверяли; hot-load и relog проверены. SQL-схема residents создана отдельно; минимальные grants server user: registry SELECT, events INSERT. Локальные reader credentials 0600, не в Git.

## Обнаруженные дефекты/ограничения
- При создании персонажа через долго простаивавшую char-session сервер получил SIGSEGV. После свежего подключения создание прошло. Причина серверного crash не установлена, исправление не заявляется. Сохранён crash-log; требуется отдельный bounded repro/hardening.
- checks/01 всё ещё недостаточен: listeners не означают interserver auth. 01b проверяет результат конкретного прогона, не универсальную health-check.
- Нет обычного жителя group0, проверки покупки/picklog, kill/die/loadmap acceptance, позиционных отрицательных проверок незарегистрированного персонажа.
- Reader v0 не сохраняет cursor транзакционно в SQLite, не нормализует timezone и не умеет reconnect/backoff. Issue #3 не закрыт.
- loadmap handler имеется, но зависит от loadevent mapflag и пока не принят тестом. Нет kill/die/level handlers.
- Нет игрового графического клиента/видео-проверки, body-плагина, LLM или автономии.
- Замер: login RSS 9476 KB, char 11804 KB, map 450216 KB, tester Perl 136028 KB, MariaDB 25440 KB; available RAM 1378 MB, swap used 1306 MB. Это разовый замер, не ёмкость стенда.

Upstream pins и непереносимые пути неизменны. Kernel не патчили. Веха A **частично выполнена**.
