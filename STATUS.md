# STATUS
Ночная ветка action-interface: staged, в live не установлена.
Каталог: 21 контракт; heal и party_invite явно unavailable.
find_party проверяет числовой age 0–4 с также при polling до подтверждения membership.
RED: night-party-age-red.log — bool/negative приняли, string вызвал TypeError, NaN прошёл к witness.
GREEN: env -u PYTHONHOME -u PYTHONPATH PATH=/usr/bin:/bin make check.
Итог: 566 тестов, 4 FAIL и 1 ERROR — прежний набор; новая polling-регрессия проходит.
Компиляция с настоящим OpenKore проходит без skip.
Не подтверждены: live gameplay, отдельный Heal, native invite/accept, ordinary-player follow witness.
Службы, бюджеты, SQL/GM, runtime и live checkout не менялись; push не выполнялся.
Следующее: whisper/free-control targeting и strict schema; пять baseline failures остаются.
