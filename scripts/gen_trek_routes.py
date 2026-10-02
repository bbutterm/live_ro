#!/usr/bin/env python3
"""Плечи дальних походов (ORG-078): какие города OpenKore пройдёт пешком из города и вернётся.

Использование:
    scripts/gen_trek_routes.py [--openkore upstream/openkore] [--atlas brain/world/atlas.json] \
        [--homes brain/world/homes.json] [--hops 7] > brain/world/trek_routes.json

Правило то же, что у scripts/gen_explore_reach.py (переходы portals.txt профиля бота, подтверждённые варпами сервера,
поиск по числу переходов, обязателен обратный путь не длиннее hops+1), но старт — точка отдыха КАЖДОГО города
brain/world/homes.json (там есть Kafra — привал и сохранение), а цель — другие города из homes.json не дальше --hops.
Плечо: {hops, back, path, x, y}. Маршрут похода (цепочка плеч) строит мозг: brain/live_brain/trek.py.
Вывод детерминирован (sort_keys). Это проверка данных, а не прохождение в игре.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from okroute import Data  # noqa: E402
from gen_explore_reach import DEFAULT_PROFILE, reach  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def generate(openkore, atlas_path, homes_path, max_hops, profile=None):
    profile = DEFAULT_PROFILE if profile is None else profile
    data = Data(openkore, profile or None)
    atlas_raw = Path(atlas_path).read_bytes()
    atlas = json.loads(atlas_raw.decode("utf-8"))["maps"]
    homes = json.loads(Path(homes_path).read_text(encoding="utf-8"))["towns"]
    doc = {"_comment": "Сгенерировано scripts/gen_trek_routes.py (ORG-078): плечи «город -> город» дальних походов, "
                       "которые OpenKore пройдёт пешком по portals.txt, подтверждённым варпами сервера, и вернётся. "
                       "Не править руками.",
           "max_hops": max_hops, "atlas_sha": hashlib.sha256(atlas_raw).hexdigest()[:16], "towns": {}}
    for town in sorted(homes):
        rest = homes[town].get("rest") or homes[town]["savepoint"]
        out = reach(data, atlas, town, int(rest["x"]), int(rest["y"]), max_hops)
        legs = {m: rec for m, rec in out.items() if m in homes and m != town}
        doc["towns"][town] = {"x": int(rest["x"]), "y": int(rest["y"]), "legs": legs}
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--openkore", default=str(ROOT / "upstream" / "openkore"))
    ap.add_argument("--atlas", default=str(ROOT / "brain" / "world" / "atlas.json"))
    ap.add_argument("--homes", default=str(ROOT / "brain" / "world" / "homes.json"))
    ap.add_argument("--hops", type=int, default=7)
    ap.add_argument("--profile", default=str(DEFAULT_PROFILE), help="профиль бота; пусто — только upstream tables")
    a = ap.parse_args()
    print(json.dumps(generate(a.openkore, a.atlas, a.homes, a.hops, a.profile), ensure_ascii=False, sort_keys=True,
                     indent=1))


if __name__ == "__main__":
    main()
