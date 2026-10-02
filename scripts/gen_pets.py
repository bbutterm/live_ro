#!/usr/bin/env python3
"""ORG-051: питомцы жителей из базы rAthena (renewal, db/re/pet_db.yml + mob_db.yml + item_db_*.yml).

Пишет brain/world/pets.json: {"<mob id>": {"mob", "name", "level", "tame", "tame_name", "egg", "egg_name",
"food", "food_name", "capture", "fullness"}, ...}
    tame    — предмет приручения (TameItem), используется на монстре (pet c);
    egg     — яйцо (EggItem): после поимки в рюкзаке, вылупление — Pet Incubator (643) + pet h;
    food    — корм (FoodItem); OpenKore кормит сам (pet_autoFeed);
    capture — CaptureRate из pet_db (из 10000, без поправок на HP монстра и уровень).
Только питомцы с предметом приручения, яйцом и кормом из базы, монстр, предмет и корм ID < MAX_ID и уровень ≤ MAX_MOB_LV.

--items-control: строки items_control.txt, чтобы предметы питомцев не продавались по правилу «all 0 0 1»
(яйца, Pet Incubator, Pet Food; предметов приручения — TAME_KEEP) — дописываются в конец профиля.

Запуск: scripts/gen_pets.py [upstream/rathena] > brain/world/pets.json
        scripts/gen_pets.py --items-control [upstream/rathena]
"""
import json
import re
import sys
from pathlib import Path

MAX_MOB_LV = 99
MAX_ID = 20000
INCUBATOR = 643
PET_FOOD = 537
TAME_KEEP = 2
ITEM_FILES = ("item_db_usable.yml", "item_db_etc.yml", "item_db_equip.yml")


def blocks(path, key):
    text = Path(path).read_text(encoding="utf-8")
    return re.split(rf"\n  - {key}: ", text)[1:]


def field(block, key):
    m = re.search(rf"\n    {key}: (.+)", block)
    return m.group(1).split("#")[0].strip().strip('"') if m else None      # «200   # unknown» → 200


def mobs(root):
    out = {}
    for b in blocks(Path(root) / "db" / "re" / "mob_db.yml", "Id"):
        mid = int(re.match(r"\d+", b).group())
        out[field(b, "AegisName")] = {"id": mid, "name": field(b, "Name"), "level": int(field(b, "Level") or 1)}
    return out


def items(root):
    out = {}
    for name in ITEM_FILES:
        for b in blocks(Path(root) / "db" / "re" / name, "Id"):
            out[field(b, "AegisName")] = {"id": int(re.match(r"\d+", b).group()), "name": field(b, "Name")}
    return out


def build(root):
    mob, item = mobs(root), items(root)
    out = {}
    for b in blocks(Path(root) / "db" / "re" / "pet_db.yml", "Mob"):
        aegis = b.split("\n", 1)[0].strip()
        m = mob.get(aegis)
        tame, egg, food = (item.get(field(b, k)) for k in ("TameItem", "EggItem", "FoodItem"))
        if not (m and tame and egg and food) or m["id"] >= MAX_ID or m["level"] > MAX_MOB_LV:
            continue
        if max(tame["id"], food["id"]) >= MAX_ID:          # новые предметы renewal жителям недоступны
            continue
        out[m["id"]] = {"mob": aegis, "name": m["name"], "level": m["level"],
                        "tame": tame["id"], "tame_name": tame["name"], "egg": egg["id"], "egg_name": egg["name"],
                        "food": food["id"], "food_name": food["name"],
                        "capture": int(field(b, "CaptureRate") or 0), "fullness": int(field(b, "Fullness") or 0)}
    return out


def dump(pets):
    head = ('{"_comment": "Сгенерировано scripts/gen_pets.py из upstream/rathena db/re/pet_db.yml, mob_db.yml, '
            'item_db_*.yml. Не редактировать руками. Ключ — ID монстра.",\n')
    rows = [f"{json.dumps(str(i))}: {json.dumps(pets[i], ensure_ascii=False, separators=(',', ':'))}"
            for i in sorted(pets)]
    return head + ",\n".join(rows) + "}\n"


def items_control(pets):
    # id -> (минимум, продавать, имя): яйца, инкубатор и Pet Food — не продавать; предмет приручения — оставить
    # TAME_KEEP штук, лишнее продать (часто обычный лут). Корм, кроме Pet Food, — травы, руда: правила лута.
    keep = {INCUBATOR: (0, 0, "Pet Incubator"), PET_FOOD: (0, 0, "Pet Food")}
    for p in pets.values():
        keep.setdefault(p["tame"], (TAME_KEEP, 1, p["tame_name"]))
        keep.setdefault(p["egg"], (0, 0, p["egg_name"]))
    lines = ["", "##### ПИТОМЦЫ (live_ro, ORG-051; scripts/gen_pets.py --items-control) #####",
             f"# Яйца, Pet Incubator, Pet Food — не продавать; предмет приручения — держать {TAME_KEEP}, лишнее продать.",
             "# Ниже правила «all 0 0 1», поэтому действует это."]
    lines += [f"{i} {keep[i][0]} 0 {keep[i][1]} # {keep[i][2]}" for i in sorted(keep)]
    return "\n".join(lines) + "\n"


def main(argv):
    ic = "--items-control" in argv
    args = [a for a in argv if a != "--items-control"]
    pets = build(Path(args[0] if args else "upstream/rathena"))
    sys.stdout.write(items_control(pets) if ic else dump(pets))
    print(f"# питомцев {len(pets)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
