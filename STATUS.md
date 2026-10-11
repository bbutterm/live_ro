# STATUS
Цель: жители живут сами, LLM — их мозг. Как включить: docs/LLM_BRAIN.md. В игре ещё не принято.
Мозг resident_world: осмотреться → спросить LLM «что дальше» → одно действие меню → свидетель.
Токен: RO_WORLD_LLM_API_KEY в .env; проверка `tools/ro doctor`; смена токена без перезапуска.
Деньги по факту ответа (llm-spend.json), лимит daily_llm_usd, без резервов: E04 и ночь «без API» устранены.
429/402/401/сбой/дневной лимит: общая пауза llm-gate.json, привычки, затем LLM снова сама.
E05 перевес: тело продаёт добычу по типу предмета; SOLD подтверждается picklog сервера.
V1: причина E01 (лишний nameID в кадре монстра) и E06 (cooldown на interrupted) исправлены.
V1 остаётся экспериментальным путём под lease; основной путь — resident_world.
make check: 507 тестов, 5 раундов ревью; 3 старых падают и на main вне VPS (пути /root/ragnarok, OpenKore).
Сквозной тест: настоящий brain + gateway, тело по протоколу плагина, 429 посередине — OK.
Правка плагина проверена на заглушках OpenKore; на VPS её проверит test_perl_compile_against_actual_openkore.
Не проверено вживую: первый запуск — шаги 1–3 в docs/LLM_BRAIN.md, затем `tools/ro status`.
Прошлый checkpoint V1: docs/RESULT_BEHAVIOR_V1.md, ошибки и причины: docs/ERRORS_BEHAVIOR_V1.md.
