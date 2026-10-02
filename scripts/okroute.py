#!/usr/bin/env python3
"""Маршрут по данным OpenKore: поля fields/*.fld2.gz + переходы tables/portals.txt (newborn).

Ищет хоть один пеший маршрут от точки до точки так, как его может построить OpenKore: клетки
проходимы по полю (байт & 1), переходы — только простые строки portals.txt (6 полей, без диалога
и платы; телепорты Kafra не учитываются). Поиск по числу переходов, а не по длине пути: OpenKore
сам может выбрать другой маршрут. Это проверка данных, а не прохождение в игре.

Использование:
    scripts/okroute.py [--openkore upstream/openkore] [--avoid prt_fild09,pay_fild04] prontera 156 180 payon_in02 63 71 [радиус]
Печатает JSON: список переходов [карта, x, y, карта_назначения, x, y] или null.
Пути в brain/world/progression.json (paths.*.route.hops) записаны этим скриптом.
"""
import argparse
import collections
import gzip
import json
import struct
from pathlib import Path


class Data:
    def __init__(self, openkore):
        self.root = Path(openkore)
        self.fields = {}
        self.labels = {}
        self.portals = collections.defaultdict(list)
        for line in (self.root / "tables" / "portals.txt").read_text(errors="replace").splitlines():
            p = line.split()
            if len(p) == 6 and not line.startswith("#") and all(v.isdigit() for v in (p[1], p[2], p[4], p[5])):
                self.portals[p[0]].append((int(p[1]), int(p[2]), p[3], int(p[4]), int(p[5])))

    def field(self, m):
        if m not in self.fields:
            f = self.root / "fields" / f"{m}.fld2.gz"
            if not f.exists():
                self.fields[m] = None
            else:
                raw = gzip.open(f).read()
                w, h = struct.unpack("<HH", raw[:4])
                self.fields[m] = (w, h, raw[4:])
        return self.fields[m]

    def walkable(self, m, x, y):
        f = self.field(m)
        if not f:
            return False
        w, h, cells = f
        return 0 <= x < w and 0 <= y < h and bool(cells[y * w + x] & 1)

    def label(self, m):
        """Компоненты связности поля (8 соседей): список номеров по клеткам, -1 — непроходимо."""
        if m in self.labels:
            return self.labels[m]
        f = self.field(m)
        if not f:
            self.labels[m] = None
            return None
        w, h, cells = f
        lab = [-1] * (w * h)
        k = 0
        for i in range(w * h):
            if cells[i] & 1 and lab[i] < 0:
                lab[i] = k
                stack = [i]
                while stack:
                    j = stack.pop()
                    x, y = j % w, j // w
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            a, b = x + dx, y + dy
                            if 0 <= a < w and 0 <= b < h:
                                n = b * w + a
                                if cells[n] & 1 and lab[n] < 0:
                                    lab[n] = k
                                    stack.append(n)
                k += 1
        self.labels[m] = (w, h, lab)
        return self.labels[m]

    def comps(self, m, x, y, r):
        L = self.label(m)
        if not L:
            return set()
        w, h, lab = L
        return {lab[b * w + a] for a in range(x - r, x + r + 1) for b in range(y - r, y + r + 1)
                if 0 <= a < w and 0 <= b < h and lab[b * w + a] >= 0}

    def route(self, start, goal, radius=2, avoid=()):
        """start/goal = (map, x, y); avoid — карты, через которые не идти. Список переходов или None."""
        m, x, y = start
        q = collections.deque(((m, c), []) for c in self.comps(m, x, y, 0))
        done = set()
        while q:
            (m, c), hops = q.popleft()
            if (m, c) in done:
                continue
            done.add((m, c))
            if m == goal[0] and c in self.comps(m, goal[1], goal[2], radius):
                return hops
            for px, py, tm, tx, ty in self.portals[m]:
                if tm in avoid:
                    continue
                if c in self.comps(m, px, py, 1):
                    for c2 in self.comps(tm, tx, ty, 2):   # клетка прибытия в portals.txt бывает неточной
                        q.append(((tm, c2), hops + [[m, px, py, tm, tx, ty]]))
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--openkore", default="upstream/openkore")
    ap.add_argument("src_map")
    ap.add_argument("src_x", type=int)
    ap.add_argument("src_y", type=int)
    ap.add_argument("dst_map")
    ap.add_argument("dst_x", type=int)
    ap.add_argument("dst_y", type=int)
    ap.add_argument("radius", type=int, nargs="?", default=2)
    ap.add_argument("--avoid", default="", help="карты через запятую, через которые не идти")
    a = ap.parse_args()
    d = Data(a.openkore)
    print(json.dumps(d.route((a.src_map, a.src_x, a.src_y), (a.dst_map, a.dst_x, a.dst_y), a.radius,
                                avoid=set(filter(None, a.avoid.split(","))))))


if __name__ == "__main__":
    main()
