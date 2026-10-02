#!/usr/bin/env python3
"""AUT-031: все карты, нужные жителям, включены в rAthena (иначе планировщик ведёт в никуда).

Использование: scripts/world_maps.py RATHENA_DIR PROFILE_ROOT BOT...
Нужные карты: город отдыха (brain/world/goals.json), карты охоты (brain/personas/<bot>.json),
lockMap/saveMap и NPC торговли/склада (bots/<bot>/control/config.txt).
Включённые: map:/delmap: в conf/maps_athena.conf и conf/import/*.txt (как читает map-server).
Только чтение. Код выхода 1, если какой-то нужной карты нет.
"""
import json
import re
import sys
from pathlib import Path

NPC_KEYS = re.compile(r"^\s*(sellAuto_npc|storageAuto_npc|npc|lockMap|saveMap)\s+([a-z0-9_@.-]+)\b", re.I)


def enabled_maps(rd):
    maps = set()
    files = [rd / "conf" / "maps_athena.conf", *sorted((rd / "conf" / "import").glob("*.txt"))]
    for f in files:
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.split("//", 1)[0].strip()
            m = re.match(r"^(map|delmap)\s*:\s*(\S+)", line)
            if m:
                (maps.add if m.group(1) == "map" else maps.discard)(m.group(2))
    return maps


def needed_maps(pr, bots):
    need = {}
    goals = pr / "brain" / "world" / "goals.json"
    if goals.is_file():
        town = json.loads(goals.read_text(encoding="utf-8")).get("routine", {}).get("town", {}).get("map")
        if town:
            need.setdefault(town, set()).add("город отдыха (goals.json)")
    for bot in bots:
        persona = pr / "brain" / "personas" / f"{bot}.json"
        if persona.is_file():
            for m in json.loads(persona.read_text(encoding="utf-8")).get("hunt_maps", []):
                need.setdefault(m, set()).add(f"охота {bot}")
        cfg = pr / "bots" / bot / "control" / "config.txt"
        if cfg.is_file():
            for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                m = NPC_KEYS.match(line.split("#", 1)[0])
                if m:
                    need.setdefault(m.group(2), set()).add(f"{m.group(1)} {bot}")
    return need


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    rd, pr, bots = Path(argv[0]), Path(argv[1]), argv[2:]
    have = enabled_maps(rd)
    if not have:
        print(f"  [??] нет списка карт в {rd}/conf/maps_athena.conf — проверка пропущена")
        return 0
    bad = 0
    for name, why in sorted(needed_maps(pr, bots).items()):
        if name in have:
            print(f"  [ok] карта {name} включена ({', '.join(sorted(why))})")
        else:
            bad += 1
            print(f"  [!!] карта {name} НЕ включена в rAthena, а нужна: {', '.join(sorted(why))}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
