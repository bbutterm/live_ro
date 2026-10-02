#!/usr/bin/env python3
"""ORG-030: цены предметов для экономики жителей из базы rAthena (renewal, db/re/item_db_*.yml).

Пишет brain/world/prices.json: {"<id>": {"name", "buy", "sell", "weight", "type"}, ...}
    buy    — цена покупки у NPC без скидок (поле Buy; если нет — Sell*2, как в rAthena);
    sell   — сколько платит NPC без Overcharge (поле Sell; если нет — Buy/2, как в rAthena);
    weight — вес в единицах игры (в базе rAthena — в 0.1);
    type   — Etc, Card, Healing, Usable, DelayConsume, Weapon, Armor.
Только то, что нужно жителям (размер файла ≤ 1.5 МБ):
    - без предметов за реальные деньги (Cash), яиц и брони питомцев, патронов, теневой экипировки;
    - без предметов без цены (Buy и Sell 0) — квестовые/коллекционные, ценность неизвестна (AUT-019);
    - снаряжение: только с EquipLevelMin не выше MAX_EQUIP_LV и без костюмов (Costume_*);
    - ID не больше MAX_ID (новые эпизоды и события renewal жителям лаборатории недоступны).
Детерминированно: ключи по возрастанию ID, одна запись на строку (diff читаемый).

Запуск: scripts/gen_prices.py [upstream/rathena] > brain/world/prices.json
"""
import json
import re
import sys
from pathlib import Path

FILES = ("item_db_usable.yml", "item_db_etc.yml", "item_db_equip.yml")
TYPES = {"Etc", "Card", "Healing", "Usable", "DelayConsume", "Weapon", "Armor"}
MAX_EQUIP_LV = 99
MAX_ID = 20000
LIMIT_BYTES = 1_500_000


def items(path):
    text = Path(path).read_text(encoding="utf-8")
    for block in re.split(r"\n  - Id: ", text)[1:]:
        get = lambda key: (re.search(rf"\n    {key}: (.+)", block) or [None, None])[1]
        locs = re.search(r"\n    Locations:\n((?:      .+\n?)+)", block)
        yield {"id": int(re.match(r"\d+", block).group()), "name": (get("Name") or "").strip().strip('"'),
               "type": (get("Type") or "Etc").strip(), "buy": int(get("Buy") or 0),
               "sell": int(get("Sell")) if get("Sell") else None, "weight": int(get("Weight") or 0),
               "equip_lv": int(get("EquipLevelMin") or 0),
               "costume": bool(locs and "Costume_" in locs.group(1))}


def norm_type(t):
    t = t[:1].upper() + t[1:]
    return "DelayConsume" if t.lower() == "delayconsume" else t


def entry(it):
    buy, sell = it["buy"], it["sell"]
    if not buy and sell:
        buy = sell * 2                      # rAthena: Buy не указан — вдвое больше Sell
    if sell is None:
        sell = buy // 2                     # rAthena: Sell не указан — половина Buy
    w = it["weight"] / 10
    return {"name": it["name"], "buy": buy, "sell": sell, "weight": int(w) if w == int(w) else w,
            "type": norm_type(it["type"])}


def wanted(it):
    t = norm_type(it["type"])
    if t not in TYPES or it["id"] > MAX_ID or not it["name"]:
        return False
    if not it["buy"] and not it["sell"]:
        return False
    if t in ("Weapon", "Armor") and (it["equip_lv"] > MAX_EQUIP_LV or it["costume"]):
        return False
    return True


def build(root):
    out = {}
    for name in FILES:
        for it in items(Path(root) / "db" / "re" / name):
            if wanted(it):
                out[it["id"]] = entry(it)
    return out


def dump(prices):
    head = ('{"_comment": "Сгенерировано scripts/gen_prices.py из upstream/rathena db/re/item_db_*.yml. '
            'Не редактировать руками. buy/sell — цены NPC без скидок (Sell или Buy/2), weight — вес в единицах игры.",\n')
    rows = [f"{json.dumps(str(i))}: {json.dumps(prices[i], ensure_ascii=False, separators=(',', ':'))}"
            for i in sorted(prices)]
    return head + ",\n".join(rows) + "}\n"


def main(argv):
    root = Path(argv[0] if argv else "upstream/rathena")
    text = dump(build(root))
    size = len(text.encode("utf-8"))
    if size > LIMIT_BYTES:
        print(f"prices.json {size} байт > {LIMIT_BYTES}: сузьте фильтр", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    print(f"# предметов {text.count(chr(10)) - 1}, {size} байт", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
