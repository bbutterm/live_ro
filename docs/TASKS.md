# Очередь независимых задач

- [#1 — интегратор: tester и runtime-ready](https://github.com/bbutterm/live_ro/issues/1). Только владелец стенда; внешний worker не управляет VPS.
- [#2 — NPC-свидетель и миграция](https://github.com/bbutterm/live_ro/issues/2). `server/witness/`, `server/schema/`, `tests/witness/`.
- [#3 — SELECT-only reader](https://github.com/bbutterm/live_ro/issues/3). `packages/witness_reader/`, `tests/witness_reader/`.
- [#4 — offline evidence analyzer](https://github.com/bbutterm/live_ro/issues/4). `packages/evidence/`, `tests/evidence/`.
- [#5 — независимая QA диагностика](https://github.com/bbutterm/live_ro/issues/5). `checks/qa/`, `tests/qa/`.

Issues 2–5 можно разрабатывать параллельно в разных worktrees, без реального VPS. Не более одного writer на issue. Согласованный контракт — `contracts/witness-v1.md`; его owner интегратор. Реальные rollout и acceptance последовательно: tester → witness → reader → evidence. Создание этих issues не запускает нейронки автоматически и не означает готовности функций.
