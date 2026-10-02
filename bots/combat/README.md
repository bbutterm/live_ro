# Боевые профили (все профессии)

`classes.json` — данные, `bots/plugins/combatProfile` — применение. Плагин сам узнаёт профессию в игре
и выставляет настройки OpenKore; бой, лечение и баффы идут по правилам OpenKore, без LLM.

- **Архетипы** (`archetypes`): `melee`, `ranged`, `caster` — дистанция атаки, отход от цели,
  атака оружием, пороги отдыха по HP/SP.
- **Профили** (`classes`): `jobs` — номера профессий (включая транс-классы), `inherits` — родитель
  (Knight ← Swordsman), `attack` / `self` / `party` — слоты `attackSkillSlot` / `useSelf_skill` /
  `partySkill` с условиями OpenKore (`"sp": "> 15%"`, `"hp": "< 50%"`, `"target_hp": "< 60%"`,
  `aggressives`, `whenStatusInactive`, `inLockOnly`, `timeout`, `maxUses`, `dist`, `maxDist`, `lvl`),
  `stats` / `skills` — авто-статы и авто-навыки.
- Включаются только **изученные** навыки, неизученные слоты получают `_disabled 1`. В авто-прокачку
  навыков попадают только навыки из дерева текущей профессии.
- Изменения файла подхватываются на ходу (проверка раз в 10 с), смена профессии и новый навык — тоже.

Добавить класс: новый ключ в `classes` с `jobs` и `archetype`; навыки — хэндлы из
`upstream/openkore/tables/SKILL_id_handle.txt`. Проверка: `perl -Ibots/tests/stubs bots/tests/combat_profile.t`.
