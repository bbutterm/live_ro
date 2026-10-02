# VPS QA — HANDOFF task 9 — cbeb2a9

## Scope and result

Tested `claude/brain-routine` at `cbeb2a9` on the existing VPS lab. No login/char/map restart, no game database edits for task 9. Only the two bodies and brains were restarted. Existing per-bot memories and API limits were retained. No Claude process was launched.

Result: repository checks, Python/Perl tests, combat-profile application and short live hunt → town → brain restart → hunt checks passed. This is not a full-day durability test and not a proof of cooperative healing.

## 0 — static checks

`python3 scripts/check.py`: `Repository checks OK; runtime not tested`.

`python3 -m unittest -v tests.test_brain tests.test_rules tests.test_typesafe tests.test_limits tests.test_plans tests.test_routine`:

```
Ran 48 tests in 85.247s
OK
```

`perl -Ibots/tests/stubs bots/tests/combat_profile.t`: 29 `ok` lines, `1..29`, exit 0.

The first short runner deadlines were insufficient for the integration tests. They were not test failures; the full rerun above completed successfully. Do not use a 35–60s timeout for the whole suite.

## 1 — actual combat profiles

Console after skill packets arrived:

```
[combatProfile] Swordsman (Swordsman): атака [SM_BASH], себе [], группе []; не изучено: [SM_MAGNUM,SM_ENDURE]
[combatProfile] Acolyte (Acolyte): атака [], себе [AL_HEAL,AL_INCAGI], группе [AL_HEAL,AL_INCAGI]; не изучено: [AL_BLESSING]
```

Immediately after login both profiles briefly reported no learned skills; they refreshed automatically when skills arrived. No manual skill grants were made. `party: null` on both bodies: configuring party healing does NOT demonstrate actual healing of Arkady.

## 2–3 — routine and actual town arrival

Daily budgets persisted in SQLite:

- Arkady: `budget=14776.329441317666`, `hunted=40.240973472595215` before town.
- Vera: `budget=17701.581510862383`, `hunted=9.062066316604614`.

Issued `scripts/lab routine bot01 rest` and `scripts/lab routine bot02 rest` after fresh state became available. Both reached Prontera without admin teleport:

- Arkady: `prontera 156,183`, `lock_map=prontera`, `lock_x=156`, `lock_y=185`, `dead=false`.
- Vera: `prontera 154,184`, same lock point, `dead=false`.
- Both had `mode=town`, `arrived=true` and saw each other with correct class/sex/level.

Arkady decisions recorded `routine_town`, the `meet_point` action, then `routine_arrived` and `sit`. His console contained the actual `conf lockMap prontera; conf lockMap_x 156; conf lockMap_y 185` command. No `Invalid coordinates`/`unwalkable` was observed in the inspected live console tail.

A rest command sent immediately after process launch, before fresh state, did not yield the expected town mode; it was reissued after fresh state. Treat this startup timing as a follow-up to investigate, not as proof the first command succeeded.

## 4 — restart only Arkady's brain

Stopped and started `brain bot01`, keeping his body and all server processes running. Shortened check (~15s rather than the handoff's 70s): persisted `mode=town`, `arrived=true`, budget and hunted values were unchanged. The recent decision log did not show a duplicate town/sit action following the restart, before the explicit hunt command.

## 5 — return to actual hunting

Issued `scripts/lab routine bot01 hunt`.

Real decision log:

```
{"type":"routine","event":"routine_hunt","text":"Отдохнул, иду качаться на prt_fild08.","mode":"hunt"}
{"type":"decision","source":"routine","actions":[{"action":"hunt","map":"prt_fild08","id":1}],"rejected":[]}
```

Actual later state: Arkady alive on `prt_fild08 119,227`, `activity=attack`, `hp_pct=100`, `lv=32`, `job_lv=15`, lock coordinates cleared. Hunted counter increased from `40.240973472595215` to `102.5825126171112`. Console confirmed actual hits and deaths of Poring, Fabre and Lunatic, not merely commands or process presence.

Vera remains alive in Prontera `154,184`, HP 100%, town break active. She was not forced back to hunting for the Arkady-only step 5.

## Owner-requested revival and important limitations

Before deploying task 9, Arkady was dead in Payon Dungeon. Issued OpenKore `respawn`: he returned alive to Prontera. Respawn left him with effectively zero HP percentage, and inventory weight over 70% prevented normal recovery. Used his own existing red potions via inventory slot 0, without SQL/GM healing; eight confirmed uses raised HP to 83%, two further uses raised it to 100%.

Final weight remains about 72%: automatic inventory management and consumable healing still need a real survival test. This short QA does not make the bot immortal or establish sustained survival in Payon. The previous Payon runtime edits were preserved in a named git stash, not silently discarded. The original server's small enabled-map set remains present.

## Preserved runtime process-discovery fix

Reapplied the already-used three-line `find_procs` guard: an explicitly labelled `/bot02/` OpenKore process must not be classified as an unlabelled fallback bot01. The task-9 checkout otherwise removed this local patch. `bash -n scripts/lab` passed; live `scripts/lab status` correctly listed separate bot01/bot02 bodies and brains, with the original server PIDs unchanged.

## Follow-ups for the developer

1. Reliable respawn/recovery without owner intervention; avoid `pause` in the middle of incoming monster attacks.
2. Heavy inventory and ID-based healing consumables: sitting does not heal an overloaded character.
3. Party creation and real, server-acknowledged healing of Arkady by Vera, not just configured skill slots.
4. Queue/persist operator rest commands sent before the first fresh body state.
5. Align old free-text goal memories with the actual routine; the live body can hunt while its last prose goal still says it waits in town.

No secrets included. Full raw logs and SQLite databases stay outside Git.
