# Питомцы жителей (ORG-051)

Правила без LLM. В игре не проверено — только тесты на заглушках (`brain/tests/test_pets.py`, `bots/tests/pets.t`).

## Данные
`scripts/gen_pets.py upstream/rathena > brain/world/pets.json` — из `db/re/pet_db.yml`, `mob_db.yml`, `item_db_*.yml`:
монстр, предмет приручения (TameItem), яйцо (EggItem), корм (FoodItem), CaptureRate. 45 питомцев: монстр, предмет
и корм с ID < 20000, уровень монстра ≤ 99.
`scripts/gen_pets.py --items-control upstream/rathena` — раздел в конце `bots/<bot>/control/items_control.txt`:
яйца, Pet Incubator (643), Pet Food (537) не продаются; предмет приручения — держать 2, лишнее продать
(в профиле стоит `all 0 0 1`). Тест сверяет pets.json и раздел профиля с генератором.

## Мозг — `brain/live_brain/pets.py`
- **Любимцы** (до 3): монстры с карт охоты жителя (атлас), уровень не выше уровня жителя; впереди те, что
  едят Pet Food (его можно докупить), затем по шансу поимки.
- **Желание** — черта generosity (мотив care) ≥ 0.2. Предметы приручения любимцев и инкубатор к найденному яйцу
  попадают в список желаний экономики: житель купит их у другого жителя (`[offer:]`, docs/ECONOMY.md).
- После каждого подключения тела — `pet_setup` (что отслеживать, докупать ли Pet Food).
- Яйцо + Pet Incubator → `pet_hatch` (HP ≥ 60%).
- Предмет приручения + его монстр в 8 клетках, режим охоты, HP ≥ 60% → `pet_tame`; не чаще раза в 5 мин,
  не больше 6 попыток в сутки.
- Голод ≤ 20 без корма в рюкзаке — запись в память раз в 2 ч. Питомца больше нет — `pet_gone`.
- Факты только от тела: `pet_tamed`, `pet_hatched` (шина мира и хроника), `pet_fed` (пакет кормления).
- Выключить: `"pets": {"enabled": false}` в goals.json или `BRAIN_DISABLE=pets`.

## Тело — плагин `bots/plugins/pets`
- `pet_tame {item, mob}`: `conf attackAuto 1`, `is <предмет>` → пакет pet_capture_process → `pet c <монстр>` →
  pet_capture_result → событие `pet_tame_result`; attackAuto возвращается.
- `pet_hatch {egg}`: `is <Pet Incubator>` → egg_list → `pet h <яйцо>` → pet_info → событие `pet_hatched {mob, name}`.
- Корм — OpenKore сам (`pet_autoFeed 1`, `pet_hunger 25`, `pet_return 20`). Пакет pet_food → событие `pet_fed`.
- Докупка Pet Food: блок `buyAuto Pet Food` в config.txt (Pet Groomer prontera 218,211, `npc_steps c r0 n`,
  rAthena `npc/re/merchants/pet_groomer.txt`) выключен (`disabled 1`); плагин включает его, пока есть питомец,
  которому нужен Pet Food.
- Состояние: `state.pet {has, type, name, hungry, friendly, running, eggs, items, near}`.

## Не проверено и риски
- Пакеты 0x19E/0x1A0/0x1A6/0x1A2 для PACKETVER 20180620 и порядок «использовать предмет → цель» в OpenKore.
- `npc_steps c r0 n` у Pet Groomer (меню select, затем close2 и callshop).
- Пока житель приручает, OpenKore может атаковать цель (attackAuto 1 — только в ответ).
- Питомец — актёр рядом с жителем: в бою его не защищают; `pet_return 20` возвращает голодного в яйцо.
