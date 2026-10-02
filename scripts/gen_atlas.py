#!/usr/bin/env python3
"""Атлас мира (AUT-031..036, 043, 045, 073, 074, 077): знания о картах из данных rAthena, без LLM.

Использование: scripts/gen_atlas.py RATHENA_DIR > brain/world/atlas.json
Только чтение rAthena. Вывод детерминирован (sort_keys, без времени генерации): тот же upstream —
тот же файл, поэтому хэш файла служит версией мира (AUT-074, atlas.atlas_version()).

Что грузится. Как map-server в renewal: обход npc/re/scripts_main.conf с import:/npc:/delnpc:
(npc/scripts_*.conf общие + npc/re/scripts_*.conf + scripts_custom.conf). Файлы, которые сервер не
читает (npc/pre-re/**, закомментированные строки), не учитываются. Карты — только включённые в
conf/maps_athena.conf и conf/import/*.txt (как scripts/world_maps.py): NPC на выключенной карте
сервер пропускает, и мы тоже.

Что извлекается:
    warp/warp2           — переходы map,x,y -> tomap,tx,ty (с размером зоны срабатывания);
    monster/boss_monster — спавны (суммарное количество по виду на карте);
    shop/marketshop      — магазины (включая duplicate(...) магазинов); цена -1 = Buy из item_db
                           (нет Buy — Sell*2); cashshop/itemshop/pointshop не нужны;
    script с callfunc "F_Kafra" — Kafra (склад/телепорт/точка сохранения);
    F_KafSet/F_KafTele   — платные телепорты Kafra (город -> город, цена в зени, без VIP-множителя);
    mapflag              — town, pvp, gvg, gvg_castle, noteleport, noreturn, nosave, nomemo.
Монстры — db/re/mob_db.yml: Level, Hp, Attack/Attack2, Defense/MagicDefense, Element, Race, Size,
Class, BaseExp/JobExp и режимы. Режимы считаются как в mob.cpp: Ai NN -> биты MONSTER_TYPE_NN
(mob.hpp; нет Ai — 06), затем Modes: X: true/false включают/выключают бит.

Регион (ограничение размера ~1.5 МБ). Берём карты, достижимые по обычным варпам (warp/warp2) не более
чем за REGION_HOPS переходов от стартовых городов REGION_TOWNS (Пронтера, Излюд, Геффен, Пайон, Морок,
Альберта — мир эпизодов 1-2, где живут и будут жить наши жители). Скриптовые переходы (OnTouch-зоны
script-NPC: дирижабль, случайные входы, квестовые двери) и Kafra-телепорты в расширение региона не
входят: их условия (зени, квест, предмет) генератор не проверяет. Замер на закреплённом rAthena: вся
компонента связности по обычным варпам от этих городов — около 300 карт (самая дальняя в 17 переходах),
~0.7 МБ с отступами, поэтому REGION_HOPS = 20 берёт её целиком; другие континенты (Rune-Midgard
восток, Арунафельц, Шварцвальд за дирижаблем) остаются за границей. В атласе есть и высокоуровневые
данжи (gl_*, mag_dun, abyss): атлас нужен боту и затем, чтобы знать, куда НЕ ходить.
Выходы из региона наружу сохраняются (сосед известен, но его содержимое — нет).

Опасность карты (статическая, без уровня персонажа; персональная — atlas.danger_for):
    agg_max_level — максимальный уровень агрессивного (Aggressive или Angry) монстра, не MVP/босса;
    agg_power     — сумма count * Hp * (Attack+Attack2)/2 по агрессивным видам (грубая «огневая мощь»);
    agg_share     — доля агрессивных особей среди всех спавнов карты;
    boss_max_level — максимальный уровень MVP/босса (Class Boss или Mvp) на карте, если есть.
Уровень карты (level: min/max/avg/count) — по «бойцам» (режим CanAttack): растения, яйца и прочие
неподвижные цели не размывают средний уровень данжа; если бойцов нет — по всем. Средний уровень —
взвешенный по количеству особей; мин/макс — по видам; count — число бойцов.
"""
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from world_maps import enabled_maps  # noqa: E402  (тот же разбор maps_athena.conf, что у doctor)

REGION_TOWNS = ["prontera", "izlude", "geffen", "payon", "morocc", "alberta"]
REGION_HOPS = 20
FLAGS = {"town", "pvp", "gvg", "gvg_castle", "noteleport", "noreturn", "nosave", "nomemo"}

# mob.hpp: e_aegis_monstertype; status.hpp: e_mode
AI_MODES = {"01": 0x81, "02": 0x83, "03": 0x1089, "04": 0x3885, "05": 0x2085, "06": 0x0,
            "07": 0x108B, "08": 0x7085, "09": 0x3095, "10": 0x84, "11": 0x84, "12": 0x2085,
            "13": 0x308D, "17": 0x91, "19": 0x3095, "20": 0x3295, "21": 0x3695, "24": 0xA1,
            "25": 0x1, "26": 0xB695, "27": 0x8084, "ABR_PASSIVE": 0x21, "ABR_OFFENSIVE": 0xA5}
MODE_BITS = {"CanMove": 0x1, "Looter": 0x2, "Aggressive": 0x4, "Assist": 0x8,
             "CastSensorIdle": 0x10, "NoRandomWalk": 0x20, "NoCast": 0x40, "CanAttack": 0x80,
             "CastSensorChase": 0x200, "ChangeChase": 0x400, "Angry": 0x800,
             "ChangeTargetMelee": 0x1000, "ChangeTargetChase": 0x2000, "TargetWeak": 0x4000,
             "RandomTarget": 0x8000, "IgnoreMelee": 0x10000, "IgnoreMagic": 0x20000,
             "IgnoreRanged": 0x40000, "Mvp": 0x80000, "IgnoreMisc": 0x100000,
             "KnockBackImmune": 0x200000, "TeleportBlock": 0x400000, "FixedItemDrop": 0x1000000,
             "Detector": 0x2000000, "StatusImmune": 0x4000000, "SkillImmune": 0x8000000}
SHOWN_MODES = ["Aggressive", "Angry", "Assist", "Looter", "CastSensorIdle", "CastSensorChase",
               "TargetWeak", "Detector", "Mvp", "CanMove", "CanAttack"]

HEADER = re.compile(r"^(?P<pos>[^\t]+)\t(?P<type>[^\t]+)\t(?P<name>[^\t]+)(?:\t(?P<rest>.*))?$")


# ---------- какие файлы грузит сервер ----------

def loaded_files(rd, conf="npc/re/scripts_main.conf", seen=None, out=None):
    """npc:/delnpc:/import: в порядке чтения map-server; путь относительно корня rAthena."""
    seen = set() if seen is None else seen
    out = {} if out is None else out
    path = rd / conf
    if conf in seen or not path.is_file():
        return out
    seen.add(conf)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("//", 1)[0].strip()
        m = re.match(r"^(npc|delnpc|import)\s*:\s*(\S+)", line)
        if not m:
            continue
        kind, f = m.groups()
        if kind == "import":
            loaded_files(rd, f, seen, out)
        elif kind == "npc":
            out[f] = True
        else:
            out.pop(f, None)
    return out


# ---------- разбор NPC-скриптов ----------

def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), text, flags=re.S)
    return re.sub(r"(?m)//.*$", "", text)


def top_level(text):
    """(заголовок, тело) для объявлений верхнего уровня; тело скрипта — текст в фигурных скобках."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        i += 1
        if "\t" not in line or line.startswith((" ", "\t")):
            continue
        body = []
        if line.endswith("{") or line.endswith("{}"):
            depth = brace_delta(line)
            while depth > 0 and i < len(lines):
                body.append(lines[i])
                depth += brace_delta(lines[i])
                i += 1
        yield line, "\n".join(body)


def brace_delta(line):
    line = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
    return line.count("{") - line.count("}")


def parse_pos(pos):
    parts = pos.split(",")
    if parts[0] == "-" or len(parts) < 3:
        return None
    try:
        return parts[0], int(parts[1]), int(parts[2])
    except ValueError:
        return None


def collect_npcs(rd, files):
    warps, spawns, shops, kafras, flags, dups, named = [], [], [], [], defaultdict(set), [], {}
    spawn_kind, unload, barter = {}, set(), battle_flag(rd, "feature.barter")
    for f in files:
        path = rd / f
        if not path.is_file():
            continue
        text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        for line, body in top_level(text):
            m = HEADER.match(line)
            if not m:
                continue
            typ, name, rest = m["type"].strip(), m["name"].strip(), (m["rest"] or "").strip()
            if typ == "mapflag":
                flag = name.split()[0] if name else ""
                mname = m["pos"].strip()
                if flag in FLAGS:
                    if rest.lower() == "off":
                        flags[mname].discard(flag)
                    else:
                        flags[mname].add(flag)
                continue
            pos = parse_pos(m["pos"])
            if typ in ("warp", "warp2") and pos:
                p = rest.split(",")
                if len(p) >= 5:
                    warps.append({"map": pos[0], "x": pos[1], "y": pos[2], "to": p[2].strip(),
                                  "tx": int(p[3]), "ty": int(p[4]), "w": int(p[0]), "h": int(p[1])})
            elif typ in ("monster", "boss_monster", "miniboss_monster"):
                mp = m["pos"].split(",")[0]
                p = rest.split(",")
                try:
                    mob, amount = int(p[0]), int(p[1])
                except (ValueError, IndexError):
                    continue
                spawns.append((mp, mob, amount, typ != "monster"))
                d = "dungeon" if "/dungeons/" in f else "field" if "/fields/" in f else None
                if d:
                    spawn_kind.setdefault(mp, d)
            elif typ in ("shop", "marketshop"):
                items = parse_shop(rest)
                named[unique_name(name)] = ("shop", items, typ)
                named[name] = ("shop", items, typ)
                if pos:
                    shops.append({"map": pos[0], "x": pos[1], "y": pos[2], "npc": display(name), "id": name,
                                  "items": items, "market": typ == "marketshop"})
            elif typ == "script":
                kafra = 'callfunc "F_Kafra"' in body
                if pos and kafra:
                    kafras.append({"map": pos[0], "x": pos[1], "y": pos[2], "npc": display(name)})
                named.setdefault(unique_name(name), ("kafra" if kafra else "script", None, typ))
                area = re.match(r"^[^,]+,(\d+),(\d+),\{", rest)
                if pos and area:                                   # OnTouch-зона: скриптовый переход
                    for t in re.finditer(r'\bwarp\s*"(\w+)"\s*,\s*(\d+)\s*,\s*(\d+)', body):
                        warps.append({"map": pos[0], "x": pos[1], "y": pos[2], "to": t.group(1),
                                      "tx": int(t.group(2)), "ty": int(t.group(3)), "w": int(area.group(1)),
                                      "h": int(area.group(2)), "script": True})
                if 'getbattleflag("feature.barter")' in body and "unloadnpc" in body:
                    unload |= barter_unload(body, barter)
            elif typ.startswith("duplicate(") and pos:
                dups.append((typ[len("duplicate("):-1].strip(), pos, name))
    for src, pos, name in dups:
        kind = named.get(src)
        if kind and kind[0] == "shop":
            shops.append({"map": pos[0], "x": pos[1], "y": pos[2], "npc": display(name), "id": name,
                          "items": kind[1], "market": kind[2] == "marketshop"})
        elif kind and kind[0] == "kafra":
            kafras.append({"map": pos[0], "x": pos[1], "y": pos[2], "npc": display(name)})
    shops = [s for s in shops if unique_name(s["id"]) not in unload]
    return warps, spawns, shops, kafras, flags, spawn_kind


def barter_unload(body, on):
    """Dealer_Update.txt: при feature.barter OnInit выгружает старых торговцев, иначе — новых."""
    head, _, tail = body.partition("} else {")
    return set(re.findall(r'unloadnpc\s*"([^"]+)"', head if on else tail))


def battle_flag(rd, key):
    val = None
    for f in [rd / "conf" / "battle" / "feature.conf", rd / "conf" / "import" / "battle_conf.txt"]:
        if f.is_file():
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(rf"^\s*{re.escape(key)}\s*:\s*(\S+)", line.split("//", 1)[0])
                if m:
                    val = m.group(1).lower() in ("on", "yes", "1", "true")
    return bool(val)


def unique_name(name):
    return name.split("::", 1)[1] if "::" in name else name


def display(name):
    """Видимое имя NPC: без ::уникального и #скрытого суффикса."""
    return name.split("::", 1)[0].split("#", 1)[0].strip()


def parse_shop(rest):
    out = []
    for tok in rest.split(",")[1:]:
        p = tok.strip().split(":")
        if len(p) >= 2:
            try:
                out.append((p[0].strip(), int(p[1])))
            except ValueError:
                pass
    return out


def kafra_teleports(rd, files):
    """F_KafSet: город -> [(название, цена)], F_KafTele: название -> (карта, x, y)."""
    f = "npc/kafras/functions_kafras.txt"
    if f not in files or not (rd / f).is_file():
        return {}
    text = strip_comments((rd / f).read_text(encoding="utf-8", errors="replace"))
    dest = {}
    for m in re.finditer(r'@wrpD\$\[\.@j\] == "([^"]+)"\)(.*?)warp "(\w+)",\s*(\d+),\s*(\d+)', text, re.S):
        if "else if" not in m.group(2):
            dest.setdefault(m.group(1), (m.group(3), int(m.group(4)), int(m.group(5))))
    tele = {}
    kafset = text[text.find("function\tscript\tF_KafSet"):]
    for m in re.finditer(r'\.@map\$ == "(\w+)"\s*\)\s*\{(.*?)\}', kafset, re.S):
        names = re.search(r"@wrpD\$\[0\],([^;]*);", m.group(2))
        prices = re.search(r"@wrpP\[0\],([^;]*);", m.group(2))
        if not (names and prices):
            continue
        names = re.findall(r'"([^"]+)"', names.group(1))
        prices = [int(p) for p in re.findall(r"\d+", prices.group(1))]
        for n, p in zip(names, prices):
            if n in dest:
                tm, tx, ty = dest[n]
                tele.setdefault(m.group(1), []).append({"to": tm, "tx": tx, "ty": ty, "zeny": p, "name": n})
    return tele


# ---------- базы ----------

def yaml_blocks(path):
    """Записи `  - Id:` из YAML rAthena: верхние ключи (4 пробела) и вложенные Modes (6 пробелов)."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for block in re.split(r"\n  - Id: ", text)[1:]:
        rec = {"Id": int(re.match(r"\d+", block).group())}
        sub = None
        for line in block.splitlines()[1:]:
            if line.startswith("#") or not line.strip():
                continue
            m = re.match(r"^    (\w+):\s*(.*)$", line)
            if m:
                k, v = m.groups()
                rec[k] = v.strip().strip('"')
                sub = k if v.strip() == "" else None
                if sub:
                    rec[k] = {}
                continue
            m = re.match(r"^      (\w+):\s*(.*)$", line)
            if m and sub and isinstance(rec.get(sub), dict):
                rec[sub][m.group(1)] = m.group(2).strip()
        yield rec


def load_mobs(rd):
    mobs = {}
    for r in yaml_blocks(rd / "db" / "re" / "mob_db.yml"):
        mode = AI_MODES.get(r.get("Ai", "06"), 0)
        for k, v in (r.get("Modes") or {}).items():
            bit = MODE_BITS.get(k)
            if bit:
                mode = mode | bit if v.lower() == "true" else mode & ~bit
        num = lambda k: int(r.get(k) or 0)
        mobs[r["Id"]] = {
            "name": r.get("Name", r.get("AegisName", "")), "aegis": r.get("AegisName", ""),
            "level": num("Level") or 1, "hp": num("Hp") or 1, "atk": num("Attack"), "atk2": num("Attack2"),
            "def": num("Defense"), "mdef": num("MagicDefense"), "base_exp": num("BaseExp"),
            "job_exp": num("JobExp"), "range": num("AttackRange") or 1,
            "element": r.get("Element", "Neutral"), "element_level": num("ElementLevel") or 1,
            "race": r.get("Race", "Formless"), "size": r.get("Size", "Small"),
            "class": r.get("Class", "Normal"),
            "modes": sorted(k for k in SHOWN_MODES if mode & MODE_BITS[k]),
        }
    return mobs


def load_items(rd):
    items = {}
    for f in ("item_db_usable.yml", "item_db_equip.yml", "item_db_etc.yml"):
        p = rd / "db" / "re" / f
        if p.is_file():
            for r in yaml_blocks(p):
                buy = int(r.get("Buy") or 0) or int(r.get("Sell") or 0) * 2
                items[r["Id"]] = {"name": r.get("Name", ""), "aegis": r.get("AegisName", ""), "buy": buy}
    return items


def upstream_commit(rd):
    try:
        out = subprocess.run(["git", "-C", str(rd), "rev-parse", "HEAD"], capture_output=True, text=True)
        return out.stdout.strip() if out.returncode == 0 else None
    except OSError:
        return None


# ---------- сборка атласа ----------

def kind_of(name, flags, spawn_kind):
    if "town" in flags or name in REGION_TOWNS:
        return "town"
    if "gvg_castle" in flags or "pvp" in flags or "gvg" in flags:
        return "pvp"
    if name in spawn_kind:
        return spawn_kind[name]
    if re.search(r"_in\d*$|^in_|_in_", name):
        return "interior"
    if "fild" in name:
        return "field"
    if "dun" in name:
        return "dungeon"
    return "other"


def region(edges, towns, hops):
    dist = {t: 0 for t in towns}
    q = deque(towns)
    while q:
        cur = q.popleft()
        if dist[cur] >= hops:
            continue
        for nxt in sorted(edges.get(cur, ())):
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    return dist


def build(rd):
    files = loaded_files(rd)
    have = enabled_maps(rd)
    ok = (lambda m: m in have) if have else (lambda m: True)
    warps, spawns, shops, kafras, flags, spawn_kind = collect_npcs(rd, files)
    warps = [w for w in warps if ok(w["map"]) and ok(w["to"])]
    edges = defaultdict(set)
    for w in warps:
        if w["to"] != w["map"] and not w.get("script"):
            edges[w["map"]].add(w["to"])
    dist = region(edges, [t for t in REGION_TOWNS if ok(t)], REGION_HOPS)
    inside = set(dist)

    mobs, items = load_mobs(rd), load_items(rd)
    tele = kafra_teleports(rd, files)
    aliases = {v["aegis"]: k for k, v in items.items()}

    maps = {m: {"kind": kind_of(m, flags.get(m, set()), spawn_kind), "hops_from_town": dist[m],
                "flags": sorted(flags.get(m, set())), "exits": [], "monsters": {}, "shops": [],
                "kafra": [], "kafra_teleport": tele.get(m, [])} for m in inside}
    seen = set()
    for w in sorted(warps, key=lambda w: (w["map"], w["x"], w["y"], w["to"], w["tx"], w["ty"], "script" in w)):
        key = (w["map"], w["x"], w["y"], w["to"], w["tx"], w["ty"])
        if w["map"] in maps and w["to"] != w["map"] and key not in seen:
            seen.add(key)
            e = {k: w[k] for k in ("x", "y", "to", "tx", "ty")}
            if w.get("script"):
                e["script"] = True                                # OnTouch-скрипт: условия не проверены
            maps[w["map"]]["exits"].append(e)

    used_mobs = set()
    for mp, mob, amount, boss in spawns:
        if mp in maps and mob in mobs and amount > 0:
            mons = maps[mp]["monsters"]
            mons[str(mob)] = mons.get(str(mob), 0) + amount
            used_mobs.add(mob)

    used_items, item_index = set(), defaultdict(list)
    for s in shops:
        if s["map"] not in maps:
            continue
        sold = []
        for iid, price in s["items"]:
            iid = int(iid) if iid.isdigit() else aliases.get(iid)
            if iid is None or iid not in items:
                continue
            price = items[iid]["buy"] if price < 0 else price
            sold.append([iid, price])
            used_items.add(iid)
            item_index[str(iid)].append([s["map"], s["x"], s["y"], price, s["npc"]])
        if sold:
            maps[s["map"]]["shops"].append({"npc": s["npc"], "x": s["x"], "y": s["y"],
                                            "market": s["market"], "items": sorted(sold)})
    for k in kafras:
        if k["map"] in maps:
            maps[k["map"]]["kafra"].append({"npc": k["npc"], "x": k["x"], "y": k["y"]})

    for name, m in maps.items():
        m["shops"].sort(key=lambda s: (s["x"], s["y"], s["npc"]))
        m["kafra"].sort(key=lambda s: (s["x"], s["y"], s["npc"]))
        m.update(summary(m["monsters"], mobs))
        if not m["kafra_teleport"]:
            del m["kafra_teleport"]

    return {
        "meta": {"source": "rAthena renewal npc/re/scripts_main.conf + db/re",
                 "rathena_commit": upstream_commit(rd), "region_towns": REGION_TOWNS,
                 "region_hops": REGION_HOPS, "maps": len(maps), "monsters": len(used_mobs),
                 "shops": sum(len(m["shops"]) for m in maps.values()),
                 "kafras": sum(len(m["kafra"]) for m in maps.values())},
        "maps": maps,
        "monsters": {str(i): mobs[i] for i in used_mobs},
        "items": {str(i): {"name": items[i]["name"], "aegis": items[i]["aegis"], "buy": items[i]["buy"]}
                  for i in used_items},
        "item_shops": {k: sorted(v) for k, v in item_index.items()},
    }


def summary(monsters, mobs):
    if not monsters:
        return {"level": None, "danger": None}
    fight = {i: c for i, c in monsters.items() if "CanAttack" in mobs[int(i)]["modes"]} or monsters
    total = sum(fight.values())
    levels = [mobs[int(i)]["level"] for i in fight]
    agg = {int(i): c for i, c in monsters.items()
           if {"Aggressive", "Angry"} & set(mobs[int(i)]["modes"])}
    bosses = [mobs[int(i)]["level"] for i in monsters
              if mobs[int(i)]["class"] == "Boss" or "Mvp" in mobs[int(i)]["modes"]]
    agg_norm = {i: c for i, c in agg.items() if mobs[i]["class"] != "Boss" and "Mvp" not in mobs[i]["modes"]}
    avg = sum(mobs[int(i)]["level"] * c for i, c in fight.items()) / total
    total_all = sum(monsters.values())
    return {
        "level": {"min": min(levels), "max": max(levels), "avg": round(avg, 1), "count": total},
        "danger": {
            "agg_max_level": max((mobs[i]["level"] for i in agg_norm), default=0),
            "agg_power": sum(c * mobs[i]["hp"] * (mobs[i]["atk"] + mobs[i]["atk2"]) // 2 for i, c in agg.items()),
            "agg_share": round(sum(agg.values()) / total_all, 3),
            "boss_max_level": max(bosses, default=0),
        },
    }


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    rd = Path(argv[0])
    if not (rd / "npc" / "re" / "scripts_main.conf").is_file():
        print(f"нет {rd}/npc/re/scripts_main.conf — это не каталог rAthena", file=sys.stderr)
        return 2
    atlas = build(rd)
    text = json.dumps(atlas, ensure_ascii=False, sort_keys=True, indent=1)   # отступы: читаемый diff
    sys.stdout.write(text + "\n")
    print(f"атлас: карт {atlas['meta']['maps']}, монстров {atlas['meta']['monsters']}, "
          f"магазинов {atlas['meta']['shops']}, Kafra {atlas['meta']['kafras']}, {len(text) // 1024} КБ, "
          f"sha {hashlib.sha256(text.encode()).hexdigest()[:12]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
