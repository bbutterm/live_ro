# STATUS
Ночная ветка action-interface: staged, в live не установлена.
Каталог: 21 контракт; heal и party_invite явно unavailable.
Общий run_native проверяет task.expires после terminal polling RPC.
Позднее confirmed: timeout цели; фактический native done сохранён.
Нет отмены terminal операции/повторного dispatch/слепого повтора экономики.
Тест: travel и sale, до срока/на границе/после срока.
RED: night-native-deadline-red.log — 4 новых FAIL: позднее done.
GREEN: env -u PYTHONHOME -u PYTHONPATH PATH=/usr/bin:/bin make check.
Итог: 573 теста, прежние 4 FAIL и 1 ERROR; новый тест проходит.
В RED ещё падал supervisor descendant; в GREEN проходит, flaky остаётся.
Компиляция с настоящим OpenKore проходит без skip.
Post-route solo/party/economy, scoped whisper и find_party guards сохранены.
Не подтверждены: live gameplay, Heal, invite/accept, ordinary-player witness.
Службы/бюджеты/SQL/GM/runtime/live checkout неизменны; push не выполнялся.
Следующее: strict schema/ownership review; baseline failures и flaky открыты.
