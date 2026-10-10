# Resident Behavior V1 — сохранённый результат

Срез: 2026-10-10, 19:39 UTC. Это checkpoint реализации, не акт полной игровой приёмки.

## Что сохраняется

- SPEC `RESIDENT_BEHAVIOR_V1.md`, конфигурация поведения и описание реализации.
- Reducer, durable state/store/outbox, контракты, utility/arbitration, replay, adapter/motor.
- Lease-bound live Controller, native ownership/fencing, адресный cutover и drain/rollback.
- OpenRouter-клиент с закреплённым `deepseek/deepseek-v3.2`, общим денежным журналом и подтверждением стоимости по receipt.
- Чат при активном lease: durable очередь → Controller.chat → общий outbox. Legacy cancel/dispatch не должен выполняться параллельно этой ветке.
- Речь не занимает motor slot; её отмена не отправляет глобальный safe_stop и не ждёт остановки движения/торговли.
- Чат не запускает автономный planning tick и не сбрасывает его cadence/RNG.
- Обещание движения формируется только после accepted; unsupported meet не подтверждается.
- Chat-модель ограничена одобренными репликами. Это НЕ свободный содержательный диалог.
- Strict schema для provider chat запрещает лишнее поле `say`; обычные decision-запросы пока остаются JSON-mode.

## Проверки

Последний завершённый canonical gate: `make check`, 433 теста, OK.
Лог вне Git: `/var/lib/ro-world/strict-chat-green.log`.
Финальный повторный `make check`: 433 теста, OK; `/var/lib/ro-world/git-checkpoint-check.log`.

Тесты включают речь параллельно движению/compound service, timeout/interrupt/stale/dead,
at-most-once chat claim, общий клиент/бюджет, безопасность движения и сохранение cadence.
Это регрессии, а не доказательство полноты §7 SPEC.

## Что действительно произошло в runtime

### Реальный отдельный запрос провайдеру

После strict chat schema получен допустимый ответ DeepSeek:
- request_sent: true;
- fallback_reason: null;
- стоимость ответа: 0.000187 USD;
- выбранная публичная реплика: «Привет.».

Этот вызов проверял backend отдельно; он не доставлял ответ реальному игроку в игре.

### Адресный canary ResidentA

- Lease начат 19:22:39 UTC, исходный deadline 19:42:39 UTC.
- Загруженный native `behavior_v1=true` проверен; мир и остальные жители не выключались.
- LLM opt-in связан с токеном lease; для загрузки клиента/исправленного prompt обновлялся только idle brain.
- По прямому разрешению владельца временный cap 3 USD привязан к одному жителю, token digest и исходному deadline; старые резервы не удалены.
- В журнале этого lease: 437 evaluation, одна настоящая decision-попытка LLM, ноль `chat_outcome`.
- В 19:35:17 UTC настоящий запрос стоил 0.00012026 USD, но ответ не прошёл validation (`invalid_response`).
- Поэтому принятое движение `walk_to(poring_edge)` имеет source=fallback, НЕ source=llm.
- Native принял маршрут. Свидетель во время него подтвердил переход в `prt_fild08` и серверную позицию (170, 375).
- Затем runner получил `ContractError: invalid_keys`, перешёл в drain; маршрут завершён как failed/interrupted.
- В 19:37:08 UTC lease перешёл в released. Это ранний возврат после ошибки, не успешное завершение canary.

## Состояние на момент сохранения

`ro status`: все трое онлайн в prontera; безопасная пауза из-за перевеса без разрешённой добычи для продажи.
Lease ResidentA released: проверка ИИ больше не активна, несмотря на оставшееся время исходного deadline.
Временный cap недействителен при released; штатный cap остаётся 2 USD.

## Чего НЕ доказали

- Принятый именно модельный выбор → завершённый серверный результат.
- Новый чат → содержательный LLM-ответ реальному игроку → выполнение просьбы.
- Полную приёмку SPEC §7, длительное наблюдение и расширение на остальных жителей.

Все известные ошибки и ограничения вынесены в `ERRORS_BEHAVIOR_V1.md`.
Runtime журналы, SQLite, opt-in/lease/override, credentials и секреты в Git не включаются.
