#!/usr/bin/env python3
"""Достижимость карт для экспедиций жителей (ORG-054): куда OpenKore дойдёт пешком из города и вернётся.

Использование:
    scripts/gen_explore_reach.py [--openkore upstream/openkore] [--atlas brain/world/atlas.json] \
        [--town prontera:156:185] [--hops 5] > brain/world/explore_reach.json

Как OpenKore строит маршрут между картами: Task::CalcMapRoute ищет путь по переходам из tables/portals.txt
(строки «карта x y карта x y [зени] [диалог]»), а по карте идёт по полю fields/<карта>.fld2.gz (байт клетки & 1 —
проходимо). Генератор повторяет это офлайн (scripts/okroute.py Data: поля, компоненты связности клеток):
    - переходы — только простые строки portals.txt (6 полей: без платы и диалога с NPC; Kafra-телепорты не берём);
    - переход берётся, только если такой же варп есть у НАШЕГО сервера (атлас brain/world/atlas.json, данные
      rAthena): выход src->dst, клетка входа в пределах SRC_TOL и клетка прибытия в пределах DST_TOL. Так
      отсекаются устаревшие строки portals.txt (izlude перестроен в renewal: prt_fild08 371,212 ведёт в
      izlude 24,98, а в portals.txt — в 30,78; поле OpenKore другого размера) — туда житель не пойдёт;
    - вход в портал — клетка той же компоненты связности (радиус 1), выход — компонента клетки прибытия (радиус 2,
      как в okroute: клетка прибытия в portals.txt бывает неточной);
    - поиск в ширину по числу переходов (не по длине пути), не дальше --hops;
    - обратный путь: из клетки прибытия до точки города (радиус 3) не длиннее hops+1 переходов — иначе карта
      не записывается (житель должен вернуться сам, без крыльев и телепортов).
Для каждой достижимой карты пишется: hops, клетка прибытия x,y (проходима по полю OpenKore), путь maps.
Вывод детерминирован (sort_keys). Это проверка данных, а не прохождение в игре.
"""
import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from okroute import Data, read_table  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC_TOL = 3
DST_TOL = 8
DEFAULT_TOWNS = ["prontera:156:185"]
DEFAULT_PROFILE = ROOT / "bots" / "bot01"     # таблицы renewal (bots/common/tables/portals.txt через !include)


def server_portals(data, atlas):
    """Переходы portals.txt, подтверждённые варпами сервера: {map: [(x, y, to, tx, ty)]}."""
    out = collections.defaultdict(list)
    for m, lst in data.portals.items():
        exits = (atlas.get(m) or {}).get("exits") or []
        for px, py, tm, tx, ty, cost, steps, *_ in lst:
            if tm not in atlas or cost or steps:             # NPC-переходы (Kafra, стражи) экспедиции не берут
                continue
            ok = any(e["to"] == tm and not e.get("script")
                     and abs(e["x"] - px) <= SRC_TOL and abs(e["y"] - py) <= SRC_TOL
                     and abs(e["tx"] - tx) <= DST_TOL and abs(e["ty"] - ty) <= DST_TOL for e in exits)
            if ok:
                out[m].append((px, py, tm, tx, ty))
    return out


def walk_cell(data, m, x, y, comp, r=3):
    """Ближайшая к (x, y) проходимая клетка компоненты comp (радиус r) или None."""
    L = data.label(m)
    if not L:
        return None
    w, h, lab = L
    best = None
    for a in range(x - r, x + r + 1):
        for b in range(y - r, y + r + 1):
            if 0 <= a < w and 0 <= b < h and lab[b * w + a] == comp:
                d = (max(abs(a - x), abs(b - y)), abs(a - x) + abs(b - y), a, b)
                if best is None or d < best:
                    best = d
    return (best[2], best[3]) if best else None


def bfs(data, portals, start_states, max_hops, goal=None):
    """Поиск в ширину по (карта, компонента). goal=(map, comps) — вернуть число переходов до цели или None;
    без goal — {map: (hops, comp, (tx, ty), путь карт)} для первой компоненты, в которую пришли."""
    q = collections.deque((s, 0, [s[0]], None) for s in start_states)
    done, first = set(), {}
    while q:
        (m, c), h, path, arrive = q.popleft()
        if (m, c) in done:
            continue
        done.add((m, c))
        if goal and m == goal[0] and c in goal[1]:
            return h
        if not goal and m not in first:
            first[m] = (h, c, arrive, path)
        if h >= max_hops:
            continue
        for px, py, tm, tx, ty in portals.get(m, ()):
            if c in data.comps(m, px, py, 1):
                for c2 in sorted(data.comps(tm, tx, ty, 2)):
                    if (tm, c2) not in done:
                        q.append(((tm, c2), h + 1, path + [tm], (tx, ty)))
    return None if goal else first


def reach(data, atlas, town, tx, ty, max_hops):
    portals = server_portals(data, atlas)
    start = [(town, c) for c in sorted(data.comps(town, tx, ty, 1))]
    home = (town, data.comps(town, tx, ty, 3))
    out = {}
    for m, (h, c, arrive, path) in sorted(bfs(data, portals, start, max_hops).items()):
        if m == town or arrive is None:
            continue
        cell = walk_cell(data, m, arrive[0], arrive[1], c)
        if not cell:
            continue
        back = bfs(data, portals, [(m, c)], max_hops + 1, goal=home)
        if back is None:
            continue
        out[m] = {"hops": h, "x": cell[0], "y": cell[1], "path": path, "back": back}
    return out


def generate(openkore, atlas_path, towns, max_hops, profile=None):
    """profile — профиль бота (bots/bot01): его tables (portals.txt renewal, field_*) раньше upstream, как у OpenKore."""
    profile = DEFAULT_PROFILE if profile is None else profile
    data = Data(openkore, profile or None)
    atlas_raw = Path(atlas_path).read_bytes()
    atlas = json.loads(atlas_raw.decode("utf-8"))["maps"]
    portals_raw = "\n".join(read_table(data.portals_file)).encode("utf-8")   # с !include — как читает OpenKore
    doc = {
        "_comment": "Сгенерировано scripts/gen_explore_reach.py (ORG-054): карты, куда OpenKore дойдёт пешком из "
                    "города по portals.txt, подтверждённым варпами сервера (atlas.json), и откуда вернётся. "
                    "Не править руками.",
        "max_hops": max_hops,
        "atlas_sha": hashlib.sha256(atlas_raw).hexdigest()[:16],
        "portals_sha": hashlib.sha256(portals_raw).hexdigest()[:16],
        "towns": {},
    }
    for spec in towns:
        town, x, y = spec.split(":")
        doc["towns"][town] = {"x": int(x), "y": int(y), "maps": reach(data, atlas, town, int(x), int(y), max_hops)}
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--openkore", default=str(ROOT / "upstream" / "openkore"))
    ap.add_argument("--atlas", default=str(ROOT / "brain" / "world" / "atlas.json"))
    ap.add_argument("--town", action="append", help="город:x:y (точка отдыха распорядка)")
    ap.add_argument("--hops", type=int, default=5)
    ap.add_argument("--profile", default=str(DEFAULT_PROFILE), help="профиль бота; пусто — только upstream tables")
    a = ap.parse_args()
    doc = generate(a.openkore, a.atlas, a.town or DEFAULT_TOWNS, a.hops, a.profile)
    print(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1))


if __name__ == "__main__":
    main()
