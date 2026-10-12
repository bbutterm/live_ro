# STATUS
Ночная ветка action-interface: staged, в live не установлена.
Каталог: 21 контракт; heal и party_invite явно unavailable.
Support/hunt_party: после подтверждённого маршрута новый status и повтор eligibility до hunt.
Потеря party, stale body или learned Heal останавливает dispatch; неудачная дорога не запускает hunt.
RED: night-party-route-red.log — executor использовал допуск до маршрута.
GREEN: env -u PYTHONHOME -u PYTHONPATH PATH=/usr/bin:/bin make check.
Итог: 568 тестов, 4 FAIL и 1 ERROR — прежний набор; новая route-регрессия проходит.
Компиляция с настоящим OpenKore проходит без skip.
Native_control whisper сохраняет независимый scoped inbox/target guard.
Не подтверждены: live gameplay, отдельный Heal, native invite/accept, ordinary-player follow witness.
Службы, бюджеты, SQL/GM, runtime и live checkout не менялись; push не выполнялся.
Следующее: strict schema/review ownership; пять baseline failures остаются.
