# STATUS
Ночная ветка action-interface: staged, в live не установлена.
Каталог: 21 контракт; heal и party_invite явно unavailable.
Новый допуск: status.age должен быть числом 0–4 с; иначе STALE_BODY до native dispatch.
RED: night-age-red.log воспроизвёл принятие неизвестного возраста; полный прогон прерван по 180 с.
GREEN: env -u PYTHONHOME -u PYTHONPATH PATH=/usr/bin:/bin make check.
Итог: 565 тестов, 4 FAIL и 1 ERROR — прежний набор; новая проверка возраста проходит.
Компиляция с настоящим OpenKore проходит без skip.
Не подтверждены: live gameplay, отдельный Heal, native invite/accept, ordinary-player follow witness.
Службы, бюджеты, SQL/GM, runtime и live checkout не менялись; push не выполнялся.
Следующее: проверить whisper/free-control targeting и strict schema; пять baseline failures остаются.
