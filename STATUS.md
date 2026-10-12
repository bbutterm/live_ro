# STATUS
Ночная ветка action-interface: staged, в live не установлена.
Каталог: 21 контракт; heal и party_invite явно unavailable.
Sell_loot/sell_equipment/buy_potions: после дороги повтор eligibility по новому status.
Stale/dead/исчезнувший лут/достигнутый stock запрещают economic dispatch.
Неудачная дорога не вызывает торговлю; native whitelist/witness/no blind retry сохранены.
RED: night-trade-route-red.log — 12 новых subtest FAIL ловят старый snapshot.
GREEN: env -u PYTHONHOME -u PYTHONPATH PATH=/usr/bin:/bin make check.
Итог: 569 тестов, прежние 4 FAIL и 1 ERROR; новая route-регрессия проходит.
Компиляция с настоящим OpenKore проходит без skip.
Support/party post-route и scoped whisper guards сохранены.
Не подтверждены: live gameplay, Heal, native invite/accept, ordinary-player follow witness.
Службы, бюджеты, SQL/GM, runtime и live checkout не менялись; push не выполнялся.
Следующее: strict schema/review ownership; пять baseline failures остаются.
