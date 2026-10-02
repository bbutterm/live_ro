#!/usr/bin/env python3
"""AUT-022: полезность лута по данным rAthena — не подбирать тяжёлое дешёвое.

Читает item_db_etc.yml (renewal: db/re) из сабмодуля rAthena и печатает строки pickupitems.txt
`<ID> 0` для предметов типа Etc, у которых:
    вес >= MIN_WEIGHT (в базе rAthena вес в 0.1) и цена продажи NPC за единицу веса < MIN_ZENY_PER_WEIGHT.
Цена продажи — поле Sell или Buy/2 (как в rAthena). Предметы без цены (Buy 0) не трогаем:
это квестовые/коллекционные вещи — их ценность неизвестна (AUT-019, не выдумывать).
Использование: scripts/gen_loot_rules.py upstream/rathena > /tmp/loot.txt (вставить в блок в pickupitems.txt).
"""
import re
import sys
from pathlib import Path

MIN_WEIGHT = 50              # 5 единиц веса и тяжелее (в базе вес хранится в 0.1)
MIN_ZENY_PER_WEIGHT = 2      # меньше 20z за единицу веса — не стоит места в рюкзаке
KEEP = {984, 985, 756, 757, 1010, 1011}   # материалы заточки — несём на склад, не бросаем


def items(path):
    text = Path(path).read_text(encoding="utf-8")
    for block in re.split(r"\n  - Id: ", text)[1:]:
        get = lambda key: (re.search(rf"\n    {key}: (.+)", block) or [None, None])[1]
        yield {"id": int(re.match(r"\d+", block).group()), "name": (get("Name") or "").strip(),
               "type": (get("Type") or "Etc").strip(), "buy": int(get("Buy") or 0),
               "sell": int(get("Sell")) if get("Sell") else None, "weight": int(get("Weight") or 0)}


def junk(it):
    if it["type"] != "Etc" or it["weight"] < MIN_WEIGHT or it["id"] in KEEP:
        return False
    sell = it["sell"] if it["sell"] is not None else it["buy"] // 2
    if it["buy"] <= 2 and not it["sell"]:
        return False          # цена 0-2z — обычно квестовый предмет (Chivalry Emblem): ценность неизвестна
    return sell / it["weight"] < MIN_ZENY_PER_WEIGHT


def main(argv):
    root = Path(argv[0] if argv else "upstream/rathena")
    out = [it for it in items(root / "db" / "re" / "item_db_etc.yml") if junk(it)]
    for it in sorted(out, key=lambda x: x["id"]):
        sell = it["sell"] if it["sell"] is not None else it["buy"] // 2
        print(f"{it['id']} 0 # {it['name']}: {sell}z, вес {it['weight'] / 10:g}")
    print(f"# всего {len(out)}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
