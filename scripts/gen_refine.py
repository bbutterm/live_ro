#!/usr/bin/env python3
"""ORG-072: данные заточки оружия для мозга (brain/live_brain/refine.py) из базы rAthena renewal.

Пишет brain/world/refine.json:
    weapons   — {"<уровень оружия>": [ID, ...]}: оружие с Refineable: true из db/re/item_db_equip.yml
                (WeaponLevel по умолчанию 1, как в rAthena), ID не больше MAX_ID (как gen_prices.py);
    levels    — {"<уровень оружия>": {"safe", "fee", "ore", "ore_name"}}: из db/re/refine.yml (группа Weapon,
                стоимость Normal): safe — наибольший уровень заточки, до которого все шаги с Rate 10000 (успех
                без риска); fee — плата за попытку (Price); ore — ID руды по имени Material (db item_db_etc.yml);
    shop      — где купить руду: NPC Vurewell (npc/merchants/refine.txt, функция phramain) — пункт меню и цена;
    smith     — кузнец +0..+10: Hollgrehenn (npc/merchants/refine.txt) — Refine UI при feature.refineui;
    source    — файлы и строки upstream, откуда взяты данные.
Детерминированно (ключи по возрастанию). Запуск: scripts/gen_refine.py [upstream/rathena] > brain/world/refine.json
"""
import json
import re
import sys
from pathlib import Path

MAX_ID = 20000


def blocks(path):
    return re.split(r"\n  - Id: ", Path(path).read_text(encoding="utf-8"))[1:]


def field(block, key):
    m = re.search(rf"\n    {key}: (.+)", block)
    return m.group(1).strip().strip('"') if m else None


def weapons(root):
    out = {}
    for b in blocks(root / "db/re/item_db_equip.yml"):
        iid = int(re.match(r"\d+", b).group())
        if field(b, "Type") != "Weapon" or field(b, "Refineable") != "true" or iid > MAX_ID:
            continue
        out.setdefault(str(int(field(b, "WeaponLevel") or 1)), []).append(iid)
    return {k: sorted(v) for k, v in sorted(out.items())}


def item_ids(root):
    ids = {}
    for name in ("item_db_etc.yml",):
        for b in blocks(root / "db/re" / name):
            ids[field(b, "AegisName")] = int(re.match(r"\d+", b).group())
    return ids


def levels(root, ids):
    """Группа Weapon из refine.yml: безопасный предел и стоимость Normal по уровню оружия."""
    text = (root / "db/re/refine.yml").read_text(encoding="utf-8")
    group = re.search(r"\n  - Group: Weapon\n(.*?)(?=\n  - Group: |\Z)", text, re.S).group(1)
    out = {}
    for lv_block in re.split(r"\n      - Level: ", group)[1:]:
        wlv = int(re.match(r"\d+", lv_block).group())
        safe, fee, ore = 0, None, None
        for rb in re.split(r"\n          - Level: ", lv_block)[1:]:
            rlv = int(re.match(r"\d+", rb).group())
            normal = re.search(r"- Type: Normal\n((?:                .+\n?)+)", rb)
            if not normal:
                break
            body = normal.group(1)
            rate = int((re.search(r"Rate: (\d+)", body) or [0, 0])[1])
            price = int((re.search(r"Price: (\d+)", body) or [0, 0])[1])
            mat = (re.search(r"Material: (\S+)", body) or [None, None])[1]
            if rlv == 1:
                fee, ore = price, mat
            if rate >= 10000 and rlv == safe + 1 and mat == ore and price == fee:
                safe = rlv
            else:
                break
        out[str(wlv)] = {"safe": safe, "fee": fee, "ore": ids.get(ore), "ore_name": ore}
    return out


def line_of(path, pattern):
    for i, line in enumerate(Path(path).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if re.search(pattern, line):
            return i
    return None


def main(root):
    root = Path(root)
    ids = item_ids(root)
    refine_txt = root / "npc/merchants/refine.txt"
    shop_npc, shop_fn = line_of(refine_txt, r"^prt_in,56,68"), line_of(refine_txt, r"^function\tscript\tphramain")
    smith_npc, smith_fn = line_of(refine_txt, r"^prt_in,63,60"), line_of(refine_txt, r"^function\tscript\trefinemain")
    data = {
        "_comment": "Сгенерировано scripts/gen_refine.py из upstream/rathena (renewal). Не редактировать руками.",
        "weapons": weapons(root),
        "levels": levels(root, ids),
        "shop": {"npc": "Vurewell", "map": "prt_in", "x": 56, "y": 68, "stand": {"x": 56, "y": 66},
                 "items": {"1010": {"menu": "Phracon", "price": 200}, "1011": {"menu": "Emveretarcon", "price": 1000}},
                 "max": 500},
        "smith": {"npc": "Hollgrehenn", "map": "prt_in", "x": 63, "y": 60, "stand": {"x": 63, "y": 58}},
        "source": {
            "weapons": "db/re/item_db_equip.yml (Type Weapon, Refineable, WeaponLevel)",
            "levels": "db/re/refine.yml (Group Weapon, Type Normal, Rate 10000)",
            "shop": f"npc/merchants/refine.txt:{shop_npc} Vurewell, phramain:{shop_fn}",
            "smith": f"npc/merchants/refine.txt:{smith_npc} Hollgrehenn (refineui), refinemain:{smith_fn}",
        },
    }
    return data


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "upstream" / "rathena"
    out = main(root)
    text = json.dumps(out, ensure_ascii=False, indent=1, sort_keys=False)
    # списки ID — в одну строку на уровень (diff читаемый)
    text = re.sub(r"\[\n\s+([\d,\s]+?)\n\s+\]", lambda m: "[" + " ".join(m.group(1).split()) + "]", text)
    sys.stdout.write(text + "\n")
