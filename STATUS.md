# STATUS
Цель: docs/RESIDENT_BEHAVIOR_V1.md; полная игровая приёмка не завершена.
Результат: docs/RESULT_BEHAVIOR_V1.md; ошибки: docs/ERRORS_BEHAVIOR_V1.md.
B2: reducer/store/outbox/contracts/replay реализованы; финальная полнота открыта.
B3: canary ResidentA вернулся в released после ContractError(invalid_keys).
B4: DeepSeek V3.2/Controller/chat реализованы; успешный backend chat получен.
В canary: 437 evaluation, 1 paid decision, invalid_response; source=fallback.
Маршрут accepted; свидетель подтвердил prt_fild08, но итог failed/interrupted.
Игровой ответ игроку и успешное модельное действие не доказаны.
Речь изолирована от motor; chat cadence/RNG и strict chat JSON исправлены.
Штатный cap 2 USD; разрешённый временный cap 3 недействителен после released.
Неизвестные резервы не удалены; ledger/секреты/runtime остаются вне Git.
Последний make check: 433 теста OK; git-checkpoint-check.log.
ro status: все трое онлайн, безопасная пауза из-за перевеса; мир не остановлен.
По поручению владельца сохраняется Git-checkpoint, без расширения rollout.
