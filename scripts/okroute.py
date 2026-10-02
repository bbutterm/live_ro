#!/usr/bin/env python3
"""Маршрут по данным OpenKore: поля fields/*.fld2.gz + переходы tables/portals.txt (newborn, NB-1, NB-2).

Ищет хоть один маршрут от точки до точки так, как его может построить OpenKore: клетки проходимы по полю
(байт & 1), переходы — строки portals.txt. Таблицы берутся как у бота (scripts/lab: --tables=профиль:upstream):
с --profile bots/bot01 сначала bots/bot01/tables (portals.txt с !include, поля field_<карта> из servers.txt,
src/Field.pm:854), затем upstream/openkore/tables. Без --profile — только upstream.
Простые переходы (6 полей) идут всегда; NPC-переходы с диалогом (Kafra) — только с --zeny N, если их цена
(в сумме по маршруту) не больше N: так OpenKore ограничивает маршрут бюджетом (src/Task/CalcMapRoute.pm:96-106,
по умолчанию — зени персонажа). Поиск по числу переходов, а не по длине пути: OpenKore сам может выбрать
другой маршрут. Это проверка данных, а не прохождение в игре.

Использование:
    scripts/okroute.py [--openkore upstream/openkore] [--profile bots/bot01] [--zeny 1200] \
        [--avoid prt_fild09,pay_fild04] prontera 156 180 payon_in02 63 71 [радиус]
Печатает JSON: список переходов [карта, x, y, карта_назначения, x, y] (у NPC-перехода седьмой элемент
{"cost": зени, "steps": "диалог"}) или null.
Пути в brain/world/progression.json (paths.*.route.hops) записаны этим скриптом.
"""
import argparse
import collections
import gzip
import json
import re
import struct
from pathlib import Path

PORTAL_RE = re.compile(r"^([\w|@-]+)\s(\d{1,3})\s(\d{1,3})\s([\w|@-]+)\s(\d{1,3})\s(\d{1,3})\s?(.*)")


def read_table(path, seen=()):
    """Строки файла таблицы с !include (src/Utils/TextReader.pm:169-176, путь — от включающего файла :80-81)."""
    path = Path(path)
    if path in seen:
        raise ValueError(f"{path} включает сам себя")
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*!include(?:_create_if_missing)?\s+(.*?)\s*$", line)
        if m:
            out += read_table(path.parent / m.group(1), seen + (path,))
        else:
            out.append(line)
    return out


def parse_portal(line):
    """Как FileParsers.pm:parsePortals: (src, x, y, dst, x, y, cost, steps, group, block) или None.
    steps None — простой переход; group/block — динамические группы [Имя] / [^Имя] (Eden: выход возвращает
    туда, откуда вошёл; войдя через [^Имя], OpenKore не планирует выходы [Имя], CalcMapRoute.pm:753-820)."""
    if line.startswith("#"):
        return None
    line = re.sub(r"\s+", " ", line.replace("\r", "")).strip()
    line = re.sub(r"(.*)[\s\t]+#.*$", r"\1", line)
    m = PORTAL_RE.match(line)
    if not m:
        return None
    g = m.groups()
    groups = re.findall(r"(?:^|\s)\[(\^?)([A-Za-z0-9_]+)\](?=\s|$)", g[6])
    group = next((n for b, n in groups if not b), None)
    block = next((n for b, n in groups if b), None)
    misc = re.sub(r"(?:^|\s)\[\^?[A-Za-z0-9_]+\](?=\s|$)", " ", g[6]).strip()
    cost, steps = 0, None
    if misc:
        mm = re.match(r"^(\d+)\s(\d)\s(.*)$", misc) or re.match(r"^(\d+)\s(.*)$", misc)
        if mm:
            cost, steps = int(mm.group(1)), mm.group(mm.lastindex)
        else:
            steps = misc
    return g[0], int(g[1]), int(g[2]), g[3], int(g[4]), int(g[5]), cost, steps, group, block


def tables_dirs(openkore, profile=None):
    dirs = []
    if profile and (Path(profile) / "tables").is_dir():
        dirs.append(Path(profile) / "tables")
    return dirs + [Path(openkore) / "tables"]


def find_table(name, openkore, profile=None):
    """Первый найденный файл (src/Settings.pm:_findFileFromFolders) — без слияния."""
    return next(d / name for d in tables_dirs(openkore, profile) if (d / name).is_file())


def field_aliases(openkore, profile=None):
    """field_<карта> <поле> из servers.txt (src/Field.pm:854 — $masterServer->{"field_$name"})."""
    f = find_table("servers.txt", openkore, profile)
    out = {}
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*field_(\S+)\s+(\S+)", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


class Data:
    def __init__(self, openkore, profile=None):
        self.root = Path(openkore)
        self.fields = {}
        self.labels = {}
        self.aliases = field_aliases(openkore, profile) if profile else {}
        self.portals = collections.defaultdict(list)
        self.portals_file = find_table("portals.txt", openkore, profile)
        for line in read_table(self.portals_file):
            p = parse_portal(line)
            if p:
                self.portals[p[0]].append(p[1:])

    def entries(self):
        """Все строки как кортежи (src, x, y, dst, x, y, cost, steps, group, block)."""
        return [(m,) + p for m, ps in self.portals.items() for p in ps]

    def field_name(self, m):
        return self.aliases.get(m, m)

    def field(self, m):
        if m not in self.fields:
            f = self.root / "fields" / f"{self.field_name(m)}.fld2.gz"
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

    def route(self, start, goal, radius=2, avoid=(), zeny=None):
        """start/goal = (map, x, y); avoid — карты, через которые не идти; zeny — бюджет на NPC-переходы
        (None — только простые переходы, пешком). Список переходов или None."""
        m, x, y = start
        q = collections.deque(((m, c), [], 0, frozenset()) for c in self.comps(m, x, y, 0))
        done = set()
        while q:
            (m, c), hops, spent, blocked = q.popleft()
            if (m, c, blocked) in done:
                continue
            done.add((m, c, blocked))
            if m == goal[0] and c in self.comps(m, goal[1], goal[2], radius):
                return hops
            for px, py, tm, tx, ty, cost, steps, group, block in self.portals[m]:
                if tm in avoid or group in blocked:
                    continue
                if steps is not None and (zeny is None or spent + cost > zeny):
                    continue
                if c in self.comps(m, px, py, 1 if steps is None else 2):   # к NPC подходят на 1-2 клетки
                    hop = [m, px, py, tm, tx, ty] + ([] if steps is None else [{"cost": cost, "steps": steps}])
                    nb = blocked | {block} if block else blocked
                    for c2 in self.comps(tm, tx, ty, 2):   # клетка прибытия в portals.txt бывает неточной
                        q.append(((tm, c2), hops + [hop], spent + cost, nb))
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--openkore", default="upstream/openkore")
    ap.add_argument("--profile", default=None, help="профиль бота (bots/bot01): его tables раньше upstream")
    ap.add_argument("--zeny", type=int, default=None, help="бюджет на NPC-переходы (Kafra); без него — только пешком")
    ap.add_argument("src_map")
    ap.add_argument("src_x", type=int)
    ap.add_argument("src_y", type=int)
    ap.add_argument("dst_map")
    ap.add_argument("dst_x", type=int)
    ap.add_argument("dst_y", type=int)
    ap.add_argument("radius", type=int, nargs="?", default=2)
    ap.add_argument("--avoid", default="", help="карты через запятую, через которые не идти")
    a = ap.parse_args()
    d = Data(a.openkore, a.profile)
    print(json.dumps(d.route((a.src_map, a.src_x, a.src_y), (a.dst_map, a.dst_x, a.dst_y), a.radius,
                             avoid=set(filter(None, a.avoid.split(","))), zeny=a.zeny)))


if __name__ == "__main__":
    main()
