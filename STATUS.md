# STATUS
12.10: приёмка v1 — на новом мире (новый VPS, новички, без GM); старый мир выключен.
План по этапам: docs/PLAN_V1.md. Этап 1 (main зелёный, один мир в git) — сделан.
Чат всегда отвечает игроку; «мой план» модели не стирается после шага.
config/world.yaml — база нового мира (3 жителя); сервер — /var/lib/ro-world/world.local.json.
ro status первой строкой показывает версию запущенного кода.
Каталог действий Hermes влит (docs/ACTION_INTERFACE.md); в меню LLM расширения — после v1.
make check: зелёный; 3 теста стенда VPS пропускаются вне VPS.
Следующее: этап 2 — UTC в БД, ro doctor world/ro accept, развёртывание нового мира.
Развёртывание: docs/DEPLOY_NEW_WORLD.md (с последнего зелёного main).
Вживую на новом мире ещё ничего не принято: готово = свидетель + ro status.
Правило main: fetch → merge origin/main → make check → push (docs/PLAN_V1.md).
