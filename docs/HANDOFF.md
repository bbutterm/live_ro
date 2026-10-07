# Handoff: finish bounded milestone-A acceptance

Read STATUS.md, QA_02.md and QA_03.md. One executor; no external coding models, no old bots, no paid LLM. Isolated rAthena/OpenKore runtime /root/ragnarok; source /root/ragnarok/repos/live_ro. Loopback ports; shared DB/other services untouched.

## Accepted new step
Normal ResidentA group0 performed actual shop purchase, kill, map travel and death with server records. Initial money/knife and mobs were explicitly LAB fixtures. Temporary NPCs/monsters removed; fixture is not included in startup. Witness now writes a1/a2; never assume payload_json exists. Durable SQLite inbox and cursor are atomic; fresh SELECT-only reads/restart/retry recovery tested. stdout is not consumer ACK.

## Next bounded gates
1. Re-read original report/CHECKS for exact milestone-A exit criteria, including PM and full combined test. Do not close milestone based only on four gameplay gates.
2. Bound position observation (change detection/heartbeat/retention) before sustained idle population. Manual QA clients are stopped after this run; protected profiles are retained. Verify live process state before relaunch; don't rerun one-time account provisioning.
3. Source generation/reset identity and timezone contract; current source-id is explicit, not automatic detection. Reader retries3 times and exits honestly on exhaustion; cursor remains durable.
4. Full cold-start/recovery and resource acceptance for actual integrated artifact. Relevant maps need loadevent; flags from temporary QA fixture do not establish global transition coverage.
5. Only then a simple non-LLM action cycle, not full social/memory architecture.

## Preservation
Server services remain available. Core guard/patches unchanged. Source tests separate from real DB/game evidence. Never publish raw defaults/profiles, DBs, inbox or logs. No cron/daemon installed for reader. Secrets remain local. Runtime accounts/schema already exist; provisioning intentionally refuses repeats.
