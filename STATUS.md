# STATUS
Ночная ветка action-interface: staged, в live не установлена.
Каталог: 21 контракт; heal и party_invite явно unavailable.
Find_party: собственное имя исключается даже при ошибочном operator opt-in.
Self-only список: NO_CONSENTING_CANDIDATES, без native whisper.
Настоящие consenting player, ranking, membership/witness сохранены.
Тест охватывает identity по resident_id/имени, каталог и executor.
RED: night-party-self-red.log — 3 новых FAIL показывают self-кандидата.
GREEN: env -u PYTHONHOME -u PYTHONPATH PATH=/usr/bin:/bin make check.
Итог: 571 тест, прежние 4 FAIL и 1 ERROR; новый тест проходит.
Компиляция с настоящим OpenKore проходит без skip.
Post-route solo/party/economy и scoped whisper guards сохранены.
Не подтверждены: live gameplay, Heal, native invite/accept, ordinary-player follow witness.
Службы, бюджеты, SQL/GM, runtime и live checkout не менялись; push не выполнялся.
Следующее: strict schema/review ownership; пять baseline failures остаются.
