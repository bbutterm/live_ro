#!/usr/bin/env python3
"""Ремёсла жителей (ORG-076 травник, ORG-075 Arrow Crafting): данные NPC и рецептов из скриптов rAthena + путь OpenKore.

Использование:
    scripts/gen_crafts.py [--rathena upstream/rathena] [--openkore upstream/openkore] [--atlas brain/world/atlas.json] \
        [--town prontera:156:185] [--hops 12] > brain/world/crafts.json

Что пишется (всё со ссылками file:line на скрипты rAthena):
    pharmacist  Old Pharmacist (npc/merchants/old_pharmacist.txt): клетка NPC, пункты меню по ТЕКСТУ (их выбирает
                плагин jobChange), рецепты из КОДА (не из текста «Mixing Information» — плата в тексте у Red Potion 2z,
                в коде 3z), условие свободного веса (MaxWeight - Weight, вес rAthena в 0.1), фраза итога;
    roberto     квест навыка Arrow Crafting (npc/quests/skills/archer_skills.txt): JobLevel, предметы, фраза итога;
    arrows      рецепты стрел db/create_arrow_db.yml (источник -> стрелы), ID и имена из db/re/item_db_*.yml;
    routes      путь от точки отдыха города до клетки у NPC: переходы portals.txt профиля бота, подтверждённые
                варпами сервера (как scripts/gen_explore_reach.py), клетка прибытия на каждой карте (шаги move плагина
                jobChange), обратный путь до города обязателен. Нет пути — null и причина (Морокк закрыт до патча
                порталов — квест Roberto не запускается).
Клетка у NPC — проходимая клетка поля OpenKore в 2 клетках от NPC, в компоненте, куда приводит путь (rAthena-поле
сверяет тест brain/tests/test_herbal.py). Вывод детерминирован (sort_keys). Это проверка данных, а не прохождение в игре.
"""
import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from okroute import Data  # noqa: E402
from gen_explore_reach import DEFAULT_PROFILE, SRC_TOL, DST_TOL, server_portals  # noqa: E402
from gen_atlas import loaded_files  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PHARMACIST = "npc/merchants/old_pharmacist.txt"
ROBERTO = "npc/quests/skills/archer_skills.txt"
ARROW_DB = "db/create_arrow_db.yml"
ITEM_DBS = ("db/re/item_db_usable.yml", "db/re/item_db_etc.yml", "db/re/item_db_equip.yml")
BOTTLE = 713
STAND_R = 2
WARP_GAP = 4          # клетка шага move не ближе 4 клеток к входу любого перехода карты (иначе тело унесёт варпом)
LEG_R = 8             # радиус поиска такой клетки от клетки прибытия


def lines_of(rathena, rel):
    return (Path(rathena) / rel).read_text(encoding="utf-8", errors="replace").splitlines()


def find(lines, pattern, start=0):
    rx = re.compile(pattern)
    for i in range(start, len(lines)):
        m = rx.search(lines[i])
        if m:
            return i, m
    raise ValueError(f"не найдено: {pattern}")


def items_db(rathena):
    """{AegisName: (id, Name)} и {id: Name} по item_db renewal."""
    by_aegis, by_id = {}, {}
    for rel in ITEM_DBS:
        text = (Path(rathena) / rel).read_text(encoding="utf-8", errors="replace")
        for block in re.split(r"\n  - Id: ", text)[1:]:
            iid = int(re.match(r"\d+", block).group())
            aegis = re.search(r"\n    AegisName: (\S+)", block)
            name = re.search(r"\n    Name: (.+)", block)
            if aegis:
                by_aegis[aegis.group(1)] = (iid, name.group(1).strip() if name else aegis.group(1))
                by_id[iid] = by_aegis[aegis.group(1)][1]
    return by_aegis, by_id


def npc_header(lines, map_name, name):
    i, m = find(lines, rf"^{map_name},(\d+),(\d+),\d+\tscript\t{re.escape(name)}\b")
    return i, int(m.group(1)), int(m.group(2))


def options(line):
    m = re.search(r'select\("([^"]+)"\)', line)
    return m.group(1).split(":") if m else []


def pharmacist(rathena, names):
    rel = PHARMACIST
    L = lines_of(rathena, rel)
    head, x, y = npc_header(L, "alberta_in", "Pharmacist")
    menu_i, _ = find(L, r'select\("Make Potion:', head)
    weight_i, wm = find(L, r"MaxWeight - Weight < (\d+)", menu_i)
    pot_i, _ = find(L, r'select\("Red Potion\.:', weight_i)
    potions = options(L[pot_i])
    amount_i, _ = find(L, r'select\("Make as many as I can\.:', pot_i)
    proof_i, _ = find(L, r'mes "Here you go\.', pot_i)
    sub_i, _ = find(L, r"^L_Making:")
    # подпрограмма: 2 травы на зелье, плата .@req_amount за штуку (проверяем, что код такой, как мы читаем)
    find(L, r"delitem \.@item_req,\.@max\*2;", sub_i)
    find(L, r"Zeny = Zeny - \(\.@max\*\.@req_amount\);", sub_i)
    find(L, r"delitem 713,\.@max;", sub_i)
    sub_amount_i, _ = find(L, r'select\("Make as many as I can\.:', sub_i)
    sub_proof_i, _ = find(L, r'mes "Here you go\.', sub_i)
    recipes = []
    end_i, _ = find(L, r"^\tcase 2:", pot_i)                      # конец switch выбора зелья (case 2 главного меню)
    for n, text in enumerate(potions, 1):
        if text.startswith("Actually"):
            continue
        ci, _ = find(L, rf"^\t\t\tcase {n}:", pot_i)
        if ci > end_i:
            raise ValueError(f"нет case {n} для {text}")
        m = re.search(r"callsub L_Making,(\d+),(\d+),(\d+);", L[ci + 1])
        if m:
            herb, fee, potion = map(int, m.groups())
            herbs, src = {str(herb): 2}, f"{rel}:{ci + 2}"
        else:
            nxt, _ = find(L, rf"^\t\t\tcase {n + 1}:", ci + 1)
            block = L[ci:nxt]
            herbs = {}
            fee = potion = None
            for j, line in enumerate(block):
                d = re.search(r"delitem (\d+),\.@max;", line)
                if d and int(d.group(1)) != BOTTLE:
                    herbs[d.group(1)] = 1
                z = re.search(r"set Zeny,Zeny-\(\.@max\*(\d+)\);", line)
                if z and fee is None:
                    fee, src = int(z.group(1)), f"{rel}:{ci + 1 + j}"
                g = re.search(r"getitem (\d+),\.@max;", line)
                if g and potion is None:
                    potion = int(g.group(1))
            if not herbs or fee is None or potion is None:
                raise ValueError(f"не разобран рецепт {text}")
        recipes.append({"potion": potion, "name": names.get(potion, text.rstrip(".")), "answer": text,
                        "herbs": herbs, "fee": fee, "src": src})
    return {"map": "alberta_in", "x": x, "y": y, "name": "Pharmacist", "src": f"{rel}:{head + 1}",
            "menu": {"text": "Make Potion", "src": f"{rel}:{menu_i + 1}"},
            "amount": {"text": options(L[amount_i])[0], "src": [f"{rel}:{amount_i + 1}", f"{rel}:{sub_amount_i + 1}"]},
            "proof": {"text": "Here you go", "src": [f"{rel}:{proof_i + 1}", f"{rel}:{sub_proof_i + 1}"]},
            "free_weight": int(wm.group(1)) // 10, "free_weight_src": f"{rel}:{weight_i + 1}",
            "bottle": BOTTLE, "potion_menu_src": f"{rel}:{pot_i + 1}", "recipes": recipes}


def roberto(rathena):
    rel = ROBERTO
    L = lines_of(rathena, rel)
    head, x, y = npc_header(L, "moc_ruins", "Roberto")
    job_i, jm = find(L, r"JobLevel >= (\d+)", head)
    items_i, _ = find(L, r"countitem\(907\)", job_i)
    items = {a: int(b) + 1 for a, b in re.findall(r"countitem\((\d+)\) > (\d+)", L[items_i])}
    proof_i, _ = find(L, r"as I promised, I will teach you the skill", items_i)
    skill_i, _ = find(L, r'skill "AC_MAKINGARROW",1', items_i)
    return {"map": "moc_ruins", "x": x, "y": y, "name": "Roberto", "src": f"{rel}:{head + 1}",
            "job_lv": int(jm.group(1)), "job_lv_src": f"{rel}:{job_i + 1}",
            "items": dict(sorted(items.items())), "items_src": f"{rel}:{items_i + 1}",
            "proof": {"text": "as I promised", "src": f"{rel}:{proof_i + 1}"},
            "skill": "AC_MAKINGARROW", "skill_src": f"{rel}:{skill_i + 1}"}


def arrows(rathena, by_aegis):
    text = (Path(rathena) / ARROW_DB).read_text(encoding="utf-8", errors="replace")
    out = {}
    for block in re.split(r"\n  - Source: ", text.split("\nBody:", 1)[1])[1:]:
        src = block.split("\n", 1)[0].strip()
        if src not in by_aegis:
            continue
        make = []
        for item, amount in re.findall(r"- Item: (\S+)\n\s+Amount: (\d+)", block):
            if item in by_aegis and int(amount) > 0:
                make.append([by_aegis[item][0], by_aegis[item][1], int(amount)])
        if make:
            sid, sname = by_aegis[src]
            out[str(sid)] = {"name": sname, "make": make}
    return out


# ---------- путь до клетки у NPC ----------

WARP_RE = re.compile(r"^(\w+),(\d+),(\d+),\d+\twarp2?\t[^\t]+\t\d+,\d+,(\w+),(\d+),(\d+)")


def inner_portals(data, rathena, maps):
    """Переходы ВНУТРИ карты (комнаты alberta_in): атлас их не хранит (to == map), поэтому строка portals.txt
    подтверждается варпом из скриптов, которые грузит сервер (npc/re/scripts_main.conf), с теми же допусками."""
    warps = []
    for rel in loaded_files(Path(rathena)):
        path = Path(rathena) / rel
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = WARP_RE.match(line)
            if m and m.group(1) in maps and m.group(4) == m.group(1):
                warps.append((m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(5)), int(m.group(6))))
    out = collections.defaultdict(list)
    for m in sorted(maps):
        for px, py, tm, tx, ty, cost, steps, *_ in data.portals.get(m, ()):
            if tm != m or cost or steps:
                continue
            if any(w[0] == m and abs(w[1] - px) <= SRC_TOL and abs(w[2] - py) <= SRC_TOL
                   and abs(w[3] - tx) <= DST_TOL and abs(w[4] - ty) <= DST_TOL for w in warps):
                out[m].append((px, py, tm, tx, ty))
    return out

def route_to(data, portals, town, tx, ty, goal, gx, gy, max_hops):
    """Кратчайший по переходам путь (карта, компонента) от точки города до компоненты клетки у NPC.
    {hops, path, legs [[карта, x, y]] — клетка прибытия на каждой карте пути, stand [x, y], back} или (None, причина)."""
    stands = []
    for a in range(gx - STAND_R, gx + STAND_R + 1):
        for b in range(gy - STAND_R, gy + STAND_R + 1):
            if (a, b) != (gx, gy) and data.walkable(goal, a, b) and \
                    all(max(abs(a - px), abs(b - py)) >= WARP_GAP for px, py in warp_cells(data, goal)):
                stands.append((max(abs(a - gx), abs(b - gy)), abs(a - gx) + abs(b - gy), a, b))
    if not stands:
        return None, f"у NPC {goal} {gx},{gy} нет проходимой клетки поля OpenKore"
    goal_comps = {}
    for _d, _m, a, b in sorted(stands):
        for c in data.comps(goal, a, b, 0):
            goal_comps.setdefault(c, (a, b))
    start = [(town, c) for c in sorted(data.comps(town, tx, ty, 1))]
    home = data.comps(town, tx, ty, 3)
    q = collections.deque((s, 0, [(s[0], tx, ty)]) for s in start)
    done = set()
    found = None
    while q:
        (m, c), h, legs = q.popleft()
        if (m, c) in done:
            continue
        done.add((m, c))
        if m == goal and c in goal_comps:
            found = (c, h, legs)
            break
        if h >= max_hops:
            continue
        for px, py, tm, ax, ay in portals.get(m, ()):
            if c in data.comps(m, px, py, 1):
                for c2 in sorted(data.comps(tm, ax, ay, 2)):
                    if (tm, c2) not in done:
                        cell = safe_cell(data, tm, ax, ay, c2)
                        if cell:
                            q.append(((tm, c2), h + 1, legs + [(tm, cell[0], cell[1])]))
    if not found:
        return None, f"нет пути по portals.txt профиля и варпам сервера за {max_hops} переходов"
    comp, hops, legs = found
    stand = goal_comps[comp]
    back = back_hops(data, portals, goal, comp, town, home, max_hops + 1)
    if back is None:
        return None, "нет обратного пути до города"
    return {"hops": hops, "path": [m for m, _x, _y in legs], "legs": [[m, x, y] for m, x, y in legs[1:]],
            "stand": [stand[0], stand[1]], "back": back}, None


def warp_cells(data, m):
    return [(lst[0], lst[1]) for lst in data.portals.get(m, ())]


def safe_cell(data, m, x, y, comp, r=LEG_R):
    """Ближайшая к (x, y) проходимая клетка компоненты comp, не ближе WARP_GAP к входу перехода; иначе None."""
    L = data.label(m)
    if not L:
        return None
    w, h, lab = L
    warps = warp_cells(data, m)
    best = None
    for a in range(x - r, x + r + 1):
        for b in range(y - r, y + r + 1):
            if not (0 <= a < w and 0 <= b < h) or lab[b * w + a] != comp:
                continue
            if any(max(abs(a - px), abs(b - py)) < WARP_GAP for px, py in warps):
                continue
            d = (max(abs(a - x), abs(b - y)), abs(a - x) + abs(b - y), a, b)
            if best is None or d < best:
                best = d
    return (best[2], best[3]) if best else None


def back_hops(data, portals, m0, c0, town, home, max_hops):
    q = collections.deque([((m0, c0), 0)])
    done = set()
    while q:
        (m, c), h = q.popleft()
        if (m, c) in done:
            continue
        done.add((m, c))
        if m == town and c in home:
            return h
        if h >= max_hops:
            continue
        for px, py, tm, ax, ay in portals.get(m, ()):
            if c in data.comps(m, px, py, 1):
                for c2 in sorted(data.comps(tm, ax, ay, 2)):
                    q.append(((tm, c2), h + 1))
    return None


def generate(rathena, openkore, atlas_path, towns, max_hops, profile=None):
    profile = DEFAULT_PROFILE if profile is None else profile
    by_aegis, names = items_db(rathena)
    doc = {"_comment": "Сгенерировано scripts/gen_crafts.py (ORG-075, ORG-076): NPC ремёсел и рецепты из скриптов "
                       "rAthena (ссылки file:line), путь OpenKore до NPC. Не править руками.",
           "pharmacist": pharmacist(rathena, names), "roberto": roberto(rathena), "arrows": arrows(rathena, by_aegis),
           "routes": {}}
    atlas_raw = Path(atlas_path).read_bytes()
    doc["atlas_sha"] = hashlib.sha256(atlas_raw).hexdigest()[:16]
    doc["max_hops"] = max_hops
    if openkore:
        data = Data(openkore, profile or None)
        portals = server_portals(data, json.loads(atlas_raw.decode("utf-8"))["maps"])
        for m, lst in inner_portals(data, rathena, {doc[k]["map"] for k in ("pharmacist", "roberto")}).items():
            portals[m] = list(portals.get(m, ())) + lst
        for spec in towns:
            town, x, y = spec.split(":")
            routes = {}
            for key in ("pharmacist", "roberto"):
                npc = doc[key]
                rec, why = route_to(data, portals, town, int(x), int(y), npc["map"], npc["x"], npc["y"], max_hops)
                routes[key] = rec if rec else {"route": None, "why": why}
            doc["routes"][town] = {"x": int(x), "y": int(y), **routes}
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rathena", default=str(ROOT / "upstream" / "rathena"))
    ap.add_argument("--openkore", default=str(ROOT / "upstream" / "openkore"))
    ap.add_argument("--atlas", default=str(ROOT / "brain" / "world" / "atlas.json"))
    ap.add_argument("--town", action="append", help="город:x:y (точка отдыха распорядка)")
    ap.add_argument("--hops", type=int, default=12)
    ap.add_argument("--profile", default=str(DEFAULT_PROFILE), help="профиль бота; пусто — только upstream tables")
    a = ap.parse_args()
    doc = generate(a.rathena, a.openkore, a.atlas, a.town or ["prontera:156:185"], a.hops, a.profile)
    print(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1))


if __name__ == "__main__":
    main()
