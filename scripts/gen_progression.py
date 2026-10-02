#!/usr/bin/env python3
"""AUT-052, 079, 083: справочник прогрессии жителей из данных rAthena (renewal).

Читает из сабмодуля rAthena только то, что реально грузится в renewal-сервере. Какие NPC-файлы
загружены, где магазины (с duplicate и выгрузкой торговцев при feature.barter) и где появляются
монстры — разбирает тем же кодом, что атлас (scripts/gen_atlas.py: loaded_files, collect_npcs);
    db/re/item_db_*.yml — предметы (цена, профессии, слоты, уровень);
    db/re/mob_db.yml — монстры и их добыча (Drops, MvpDrops).
Пишет brain/world/jobs/catalog.json:
    equipment — оружие и броня из магазинов карт SHOP_MAPS (Пронтера, Изюд) с ценой и профессиями;
    items     — источники предметов квестов смены профессии (QUEST_ITEMS): магазины и монстры
                (уровень, шанс в %, карты появления). Предмет без магазина и без монстра,
                который где-то появляется, мозг помечает недостижимым.
Мозг читает только JSON (без PyYAML). Генератору нужен PyYAML (python3-yaml).
Использование: scripts/gen_progression.py upstream/rathena > brain/world/jobs/catalog.json
"""
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_atlas import collect_npcs, loaded_files  # noqa: E402  (общий разбор NPC с атласом)

SHOP_MAPS = ("prontera", "prt_in", "prt_church", "izlude", "izlude_in")
# Предметы сценариев brain/world/progression.json (Knight: оба набора Sir Andrew; Priest: Rosary для помощи).
QUEST_ITEMS = (1040, 7006, 931, 1057, 903, 1028, 1042, 950, 1032, 966, 7031, 946)
EQUIP_TYPES = ("Weapon", "Armor")
TOP_MOBS = 8               # источников-монстров на предмет (самые низкоуровневые)
TOP_MAPS = 4               # карт на монстра (где их больше всего)

try:
    Loader = yaml.CSafeLoader
except AttributeError:     # без libyaml медленнее, но работает
    Loader = yaml.SafeLoader


def load_items(root):
    items = {}
    for name in ("item_db_equip.yml", "item_db_etc.yml", "item_db_usable.yml"):
        doc = yaml.load((root / "db" / "re" / name).read_text(encoding="utf-8"), Loader=Loader)
        for it in doc.get("Body") or []:
            items[it["Id"]] = it
    return items


def load_mobs(root):
    doc = yaml.load((root / "db" / "re" / "mob_db.yml").read_text(encoding="utf-8"), Loader=Loader)
    return doc.get("Body") or []


def jobs_of(it):
    """Профессии из поля Jobs (All + исключения) — имена rAthena (Swordman, Knight, Acolyte, Priest…)."""
    jobs = it.get("Jobs") or {"All": True}
    if jobs.get("All"):
        allowed = {"Swordman", "Knight", "Acolyte", "Priest", "Mage", "Wizard", "Archer", "Hunter",
                   "Merchant", "Blacksmith", "Thief", "Assassin", "Crusader", "Monk", "Sage", "Rogue",
                   "Alchemist", "Bard", "Dancer", "Novice", "SuperNovice", "Taekwon", "Star_Gladiator",
                   "Soul_Linker", "Gunslinger", "Ninja", "Kagerou", "Oboro", "Rebellion", "Summoner"}
        return sorted(j for j in allowed if jobs.get(j, True))
    return sorted(j for j, v in jobs.items() if v)


def price(it, shop_price):
    return shop_price if shop_price > 0 else int(it.get("Buy") or 0)


def build(root):
    _, spawn_list, shops, _, _, _ = collect_npcs(root, list(loaded_files(root)))
    spawns = {}
    for mp, mob, amount, _boss in spawn_list:
        spawns.setdefault(mob, {})
        spawns[mob][mp] = spawns[mob].get(mp, 0) + amount
    items = load_items(root)
    aegis = {it["AegisName"]: iid for iid, it in items.items()}
    mobs = load_mobs(root)

    equipment = {}
    sold = {}                                     # предмет -> магазины (любые карты)
    for shop in shops:
        if shop.get("market"):
            continue
        for iid, p in shop["items"]:
            iid = int(iid) if str(iid).isdigit() else None
            it = items.get(iid)
            if not it:
                continue
            where = {"npc": shop["npc"], "map": shop["map"], "x": shop["x"], "y": shop["y"],
                     "price": price(it, p)}
            sold.setdefault(iid, []).append(where)
            if shop["map"] not in SHOP_MAPS or it.get("Type") not in EQUIP_TYPES:
                continue
            e = equipment.setdefault(iid, {
                "id": iid, "name": it.get("Name"), "aegis": it.get("AegisName"), "type": it.get("Type"),
                "subtype": it.get("SubType"), "locations": sorted(k for k, v in (it.get("Locations") or {}).items() if v),
                "attack": int(it.get("Attack") or 0), "defense": int(it.get("Defense") or 0),
                "slots": int(it.get("Slots") or 0), "weight": int(it.get("Weight") or 0) / 10,
                "equip_lv": int(it.get("EquipLevelMin") or 0), "weapon_lv": int(it.get("WeaponLevel") or 0),
                "gender": it.get("Gender", "Both"), "jobs": jobs_of(it),
                "price": price(it, p), "shops": []})
            e["price"] = min(e["price"], price(it, p))
            e["shops"].append({k: where[k] for k in ("npc", "map", "x", "y")})

    drops = {}
    for mob in mobs:
        maps = spawns.get(mob["Id"], {})
        for kind in ("Drops", "MvpDrops"):
            for d in mob.get(kind) or []:
                iid = aegis.get(d.get("Item"))
                if iid is None:
                    continue
                drops.setdefault(iid, []).append({
                    "mob_id": mob["Id"], "mob": mob.get("Name"), "level": int(mob.get("Level") or 1),
                    "rate_pct": round(int(d.get("Rate") or 0) / 100, 2), "mvp": kind == "MvpDrops",
                    "maps": dict(sorted(maps.items(), key=lambda kv: -kv[1])[:TOP_MAPS])})
    quest = {}
    for iid in QUEST_ITEMS:
        it = items.get(iid, {})
        src = sorted(drops.get(iid, []), key=lambda d: (not d["maps"], d["level"], -d["rate_pct"]))
        quest[str(iid)] = {"name": it.get("Name"), "aegis": it.get("AegisName"),
                           "shops": sold.get(iid, []), "drops": src[:TOP_MOBS],
                           "drop_mobs_total": len(src), "spawning_mobs": sum(1 for d in src if d["maps"])}
    return {
        "_comment": "Сгенерировано scripts/gen_progression.py из upstream/rathena (renewal). Не редактировать руками.",
        "shop_maps": list(SHOP_MAPS),
        "equipment": sorted(equipment.values(), key=lambda e: (e["type"], e["price"], e["id"])),
        "items": quest,
    }


def main(argv):
    root = Path(argv[0] if argv else "upstream/rathena")
    data = build(root)
    json.dump(data, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    print(f"# снаряжения {len(data['equipment'])}, предметов квестов {len(data['items'])}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
