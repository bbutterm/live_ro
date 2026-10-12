# STATUS
Ночная ветка action-interface: staged, в live не установлена.
Каталог: 21 контракт; heal и party_invite явно unavailable.
Find_party соблюдает исходный task.expires до и после polling RPC.
Поздняя membership: timeout, без native_party_confirmation/повтора whisper.
Пауза ограничена остатком срока; consent/native witness не ослаблены.
Тест: expiry после offer, во время poll, подтверждение внутри срока.
RED: night-party-deadline-red.log — 2 новых FAIL: позднее done.
GREEN: env -u PYTHONHOME -u PYTHONPATH PATH=/usr/bin:/bin make check.
Итог: 572 теста, прежние 4 FAIL и 1 ERROR; новый тест проходит.
Компиляция с настоящим OpenKore проходит без skip.
Post-route solo/party/economy и scoped whisper/self guards сохранены.
Не подтверждены: live gameplay, Heal, native invite/accept, ordinary-player follow witness.
Службы, бюджеты, SQL/GM, runtime и live checkout не менялись; push не выполнялся.
Следующее: strict schema/review ownership; пять baseline failures остаются.
