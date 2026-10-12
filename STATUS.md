# STATUS
Ночная ветка action-interface: staged, в live не установлена.
Каталог: 21 контракт; heal и party_invite явно unavailable.
Native_control whisper: nearby/enabled residents/собственные последние16 входящих за30мин; не себе.
Executor независимо читает inbox read-only, caller _known/чужая история не разрешают отправку.
RED: night-whisper-red.log — свободный шёпот принял self/обошёл модельный target guard.
GREEN: env -u PYTHONHOME -u PYTHONPATH PATH=/usr/bin:/bin make check.
Итог: 567 тестов, 4 FAIL и 1 ERROR — прежний набор; новая targeting-регрессия проходит.
Компиляция с настоящим OpenKore проходит без skip.
Не подтверждены: live gameplay, отдельный Heal, native invite/accept, ordinary-player follow witness.
Службы, бюджеты, SQL/GM, runtime и live checkout не менялись; push не выполнялся.
Следующее: strict schema/review ownership; пять baseline failures остаются.
