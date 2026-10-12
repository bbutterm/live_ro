# STATUS
Ночная ветка action-interface: staged, в live не установлена.
Каталог: 21 контракт; heal и party_invite явно unavailable.
Hunt_solo: после дороги повтор eligibility по новому status.
Stale/dead/перевес/недостаточные ресурсы запрещают native hunt.
Действующий допуск сохраняет solo=true без support_target профиля.
Неудачная дорога не вызывает hunt; native witness/legacy hunt сохранены.
RED: night-solo-route-red.log — 5 новых subtest FAIL ловят старый snapshot.
GREEN: env -u PYTHONHOME -u PYTHONPATH PATH=/usr/bin:/bin make check.
Итог: 570 тестов, прежние 4 FAIL и 1 ERROR; новая route-регрессия проходит.
Компиляция с настоящим OpenKore проходит без skip.
Party/support/economic post-route и scoped whisper guards сохранены.
Не подтверждены: live gameplay, Heal, native invite/accept, ordinary-player follow witness.
Службы, бюджеты, SQL/GM, runtime и live checkout не менялись; push не выполнялся.
Следующее: strict schema/review ownership; пять baseline failures остаются.
