#!/usr/bin/env python3
"""ORG-080: справочник достижений сервера для мозга из rAthena (renewal).

Читает db/re/achievement_db.yml (и db/import/achievement_db.yml, если есть) и пишет brain/world/achievements.json:
    {"source": ..., "achievements": {"<id>": {"name", "group", "score"}}}
Мозг (brain/live_brain/achieve.py) читает только JSON (без PyYAML): название и группа нового достижения для
воспоминания, летописи и разговора. Генератору нужен PyYAML (python3-yaml).
Использование: scripts/gen_achievements.py upstream/rathena > brain/world/achievements.json
"""
import json
import sys
from pathlib import Path

import yaml

try:
    Loader = yaml.CSafeLoader
except AttributeError:     # без libyaml медленнее, но работает
    Loader = yaml.SafeLoader

FILES = ("db/re/achievement_db.yml", "db/import/achievement_db.yml")


def build(root):
    root = Path(root)
    out = {}
    for rel in FILES:
        path = root / rel
        if not path.exists():
            continue
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=Loader) or {}
        for row in data.get("Body") or []:
            if not isinstance(row, dict) or "Id" not in row:
                continue
            out[str(int(row["Id"]))] = {"name": str(row.get("Name") or "").strip(), "group": row.get("Group"),
                                        "score": int(row.get("Score") or 0)}
    return {"_comment": "ORG-080: scripts/gen_achievements.py из rAthena db/re/achievement_db.yml — руками не править",
            "source": FILES[0], "achievements": dict(sorted(out.items(), key=lambda kv: int(kv[0])))}


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    json.dump(build(argv[1]), sys.stdout, ensure_ascii=False, indent=1, sort_keys=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
