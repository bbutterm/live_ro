#!/usr/bin/env python3
"""Базовый словарь человеческих названий карт (ORG-084, ТЗ Т-25) по атласу: город, направление, тип.

Использование:
    scripts/gen_place_names.py [--atlas brain/world/atlas.json] > brain/world/place_names.json

Как строится имя:
    - город карты — город из префикса кода (prt_ -> prontera, pay_ -> payon, ...), иначе ближайший город по
      переходам атласа (ничья — по алфавиту); hops — число переходов от этого города (поиск в ширину);
    - направление — сумма шагов пути от города: шаг = (ворота в родительской карте относительно её центра)
      минус (обратные ворота в дочерней карте относительно её центра), центр карты — среднее координат выходов;
      8 румбов (север, северо-восток, ...); в RO ось y направлена на север;
    - тип: поле (у Пайона и Умбалы — лес, у Морокка — пустыня, Мьёльнир — гора), подземелье — по префиксу
      (пещера Пайона, пирамида Морокка, муравейник, часовая башня...) с ярусом по порядку кода;
    - 1 переход — «Южное поле Пронтеры», дальше — «Дальнее южное поле Пронтеры»; совпадения — римский номер;
    - формы падежей (nom, gen, loc с предлогом, acc, from) — grammar.place_forms; short — без города (житель
      своего города говорит «на Южном поле»).
Только поля, подземелья и города (интерьеры, PvP и прочие — без имени: в речи «на карте X»).
Вывод детерминирован (sort_keys).
"""
import argparse
import collections
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))
from live_brain.grammar import cap, place_forms  # noqa: E402

TOWNS = {   # код -> (им., род., предл. с предлогом, вин.)
    "prontera": ("Пронтера", "Пронтеры", "в Пронтере", "Пронтеру"),
    "izlude": ("Излюд", "Излюда", "в Излюде", "Излюд"),
    "geffen": ("Геффен", "Геффена", "в Геффене", "Геффен"),
    "payon": ("Пайон", "Пайона", "в Пайоне", "Пайон"),
    "morocc": ("Морокк", "Морокка", "в Морокке", "Морокк"),
    "alberta": ("Альберта", "Альберты", "в Альберте", "Альберту"),
    "aldebaran": ("Альдебаран", "Альдебарана", "в Альдебаране", "Альдебаран"),
    "yuno": ("Юно", "Юно", "в Юно", "Юно"),
    "comodo": ("Комодо", "Комодо", "в Комодо", "Комодо"),
    "umbala": ("Умбала", "Умбалы", "в Умбале", "Умбалу"),
    "amatsu": ("Амацу", "Амацу", "в Амацу", "Амацу"),
    "einbroch": ("Эйнброх", "Эйнброха", "в Эйнброхе", "Эйнброх"),
    "einbech": ("Эйнбех", "Эйнбеха", "в Эйнбехе", "Эйнбех"),
    "lighthalzen": ("Лайтхальцен", "Лайтхальцена", "в Лайтхальцене", "Лайтхальцен"),
    "hugel": ("Хюгель", "Хюгеля", "в Хюгеле", "Хюгель"),
    "rachel": ("Рашель", "Рашели", "в Рашели", "Рашель"),
    "veins": ("Вейнс", "Вейнса", "в Вейнсе", "Вейнс"),
    "moc_ruins": ("Руины Морокка", "Руин Морокка", "в Руинах Морокка", "Руины Морокка"),
    "pay_arche": ("Деревня лучников", "Деревни лучников", "в Деревне лучников", "Деревню лучников"),
}
PREFIX_TOWN = {"prt": "prontera", "pay": "payon", "gef": "geffen", "moc": "morocc", "iz": "izlude",
               "alb": "alberta", "alde": "aldebaran", "cmd": "comodo", "um": "umbala", "ein": "einbroch",
               "lhz": "lighthalzen", "hu": "hugel", "ra": "rachel", "ve": "veins", "yuno": "yuno", "ama": "amatsu"}
FIELD_NOUN = {"pay": "лес", "um": "лес", "moc": "пустыня", "mjolnir": "гора"}
# префикс подземелья -> (прилагательные, существительное, хвост; None — род. падеж города карты)
DUNGEONS = {
    "pay_dun": ((), "пещера", None),
    "gef_dun": ((), "подземелье", None),
    "moc_pryd": ((), "пирамида", None),
    "in_sphinx": ((), "сфинкс", None),
    "orcsdun": ((), "логово", "орков"),
    "anthell": ((), "муравейник", ""),
    "mjo_dun": ((), "шахта", "Мьёльнира"),
    "c_tower": (("Часовой",), "башня", ""),
    "alde_dun": ((), "подземелье", None),
    "prt_maze": ((), "лабиринт", None),
    "mag_dun": ((), "пещера", "вулкана"),
    "ice_dun": (("Ледяной",), "пещера", ""),
    "beach_dun": (("Прибрежный",), "пещера", ""),
    "ein_dun": ((), "шахта", None),
    "thor_v": ((), "вулкан", "Тора"),
    "gl_": ((), "замок", "Гласт Хейм"),
}
EXTRA_NOUNS = {   # существительные, которых нет в grammar.NOUNS (только для словаря)
    "сфинкс": {"g": "m", "nom": "сфинкс", "gen": "сфинкса", "loc": "сфинксе", "acc": "сфинкс", "prep": "в"},
    "лабиринт": {"g": "m", "nom": "лабиринт", "gen": "лабиринта", "loc": "лабиринте", "acc": "лабиринт", "prep": "в"},
    "вулкан": {"g": "m", "nom": "вулкан", "gen": "вулкана", "loc": "вулкане", "acc": "вулкан", "prep": "на"},
    "замок": {"g": "m", "nom": "замок", "gen": "замка", "loc": "замке", "acc": "замок", "prep": "в"},
    "гора": {"g": "f", "nom": "гора", "gen": "горы", "loc": "горе", "acc": "гору", "prep": "на"},
}
LAND = {   # слово для собственных имён мест (ORG-084): «Гиблый холм», «Луг встреч»
    "prt_fild08": "холм", "prt_fild05": "луг", "prt_fild07": "роща", "prt_fild06": "луг", "prt_fild01": "холм",
    "prt_fild02": "долина", "prt_fild04": "холм", "prt_fild09": "берег", "prt_fild10": "луг", "prt_fild11": "роща",
    "pay_fild01": "тропа", "gef_fild00": "холм",
}
DIRS = ["Восточный", "Северо-восточный", "Северный", "Северо-западный", "Западный", "Юго-западный", "Южный",
        "Юго-восточный"]
DIR_CODES = ["e", "ne", "n", "nw", "w", "sw", "s", "se"]
ROMAN = ["", "", " II", " III", " IV", " V", " VI", " VII", " VIII", " IX", " X"]


def nouns():
    from live_brain.grammar import NOUNS
    return dict(NOUNS, **EXTRA_NOUNS)


def exits(maps, code):
    return [e for e in maps[code].get("exits") or [] if e.get("to") in maps and e["to"] != code]


def center(maps, code):
    pts = [(e["x"], e["y"]) for e in maps[code].get("exits") or []]
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def unit(dx, dy):
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n else (0.0, 0.0)


def gate(maps, src, dst):
    """Средняя точка ворот src -> dst относительно центра src (единичный вектор) или (0, 0)."""
    c = center(maps, src)
    g = [(e["x"], e["y"]) for e in exits(maps, src) if e["to"] == dst]
    if not c or not g:
        return 0.0, 0.0
    return unit(sum(p[0] for p in g) / len(g) - c[0], sum(p[1] for p in g) / len(g) - c[1])


def bfs(maps, start):
    """{карта: (hops, путь)} от start по переходам атласа (без скриптовых)."""
    seen = {start: (0, [start])}
    q = collections.deque([start])
    while q:
        cur = q.popleft()
        for e in sorted(exits(maps, cur), key=lambda e: e["to"]):
            if e.get("script") or e["to"] in seen:
                continue
            seen[e["to"]] = (seen[cur][0] + 1, seen[cur][1] + [e["to"]])
            q.append(e["to"])
    return seen


def direction(maps, path):
    vx = vy = 0.0
    for a, b in zip(path, path[1:]):
        gx, gy = gate(maps, a, b)
        bx, by = gate(maps, b, a)
        sx, sy = unit(gx - bx, gy - by)
        vx, vy = vx + sx, vy + sy
    if not (vx or vy):
        return None
    i = int(round(math.atan2(vy, vx) / (math.pi / 4))) % 8
    return i


def town_of(maps, code, reach):
    pre = code.split("_")[0]
    t = PREFIX_TOWN.get(pre)
    if t in reach and code in reach[t]:
        return t
    near = sorted((reach[t][code][0], t) for t in reach if code in reach[t])
    return near[0][1] if near else None


def dungeon_spec(code):
    for pre, spec in DUNGEONS.items():
        if code.startswith(pre):
            return pre, spec
    return None, None


def build(atlas):
    maps = atlas["maps"]
    table = nouns()
    reach = {t: bfs(maps, t) for t in sorted(TOWNS) if t in maps}
    out_towns = {}
    for t, (nom, gen, loc, acc) in sorted(TOWNS.items()):
        if t in maps:
            prep_from = "из " + gen
            out_towns[t] = {"nom": nom, "gen": gen, "loc": loc, "acc": acc, "from": prep_from}
    raw = {}
    for code in sorted(maps):
        kind = maps[code].get("kind")
        if kind not in ("field", "dungeon") or code in TOWNS:
            continue
        town = town_of(maps, code, reach)
        if not town:
            continue
        hops, path = reach[town][code]
        town_gen = TOWNS[town][1]
        if kind == "field":
            d = direction(maps, path)
            noun = FIELD_NOUN.get(code.split("_")[0], "поле")
            adjs = ([DIRS[d]] if d is not None else [])
            if hops >= 2:
                adjs = ["Дальний"] + adjs
            raw[code] = {"town": town, "hops": hops, "kind": kind, "dir": DIR_CODES[d] if d is not None else None,
                         "noun": noun, "adjs": adjs, "tail": town_gen, "group": None}
        else:
            pre, spec = dungeon_spec(code)
            adjs, noun, tail = spec if spec else ((), "подземелье", None)
            tail = town_gen if tail is None else tail
            raw[code] = {"town": town, "hops": hops, "kind": kind, "dir": None, "noun": noun, "adjs": list(adjs),
                         "tail": tail, "group": pre or f"dun:{town}"}
    # ярусы подземелий: порядок кода внутри группы (одна карта — без яруса)
    groups = collections.defaultdict(list)
    for code, r in raw.items():
        if r["group"]:
            groups[r["group"]].append(code)
    for codes in groups.values():
        if len(codes) > 1:
            for i, code in enumerate(sorted(codes), 1):
                raw[code]["level"] = i
    # совпадения имён полей — римский номер по порядку кода
    seen = collections.defaultdict(list)
    for code, r in sorted(raw.items()):
        if r["kind"] == "field":
            seen[(r["noun"], tuple(r["adjs"]), r["tail"])].append(code)
    for codes in seen.values():
        if len(codes) > 1:
            for i, code in enumerate(codes, 1):
                raw[code]["roman"] = ROMAN[i] if i < len(ROMAN) else f" {i}"
    out_maps = {}
    for code, r in sorted(raw.items()):
        suffix = r.get("roman", "") if r["kind"] == "field" else ""
        level = f", ярус {r['level']}" if r.get("level") else ""
        full_tail = " ".join(x for x in (r["tail"],) if x) + suffix + level
        full = place_forms(r["noun"], r["adjs"], full_tail.strip(), nouns=table)
        own_town = r["kind"] == "field" and r["tail"] == TOWNS[r["town"]][1]     # подземелье — всегда с городом
        short_tail = ("" if own_town else r["tail"] or "") + suffix + level
        short = place_forms(r["noun"], r["adjs"], short_tail.strip(), nouns=table)
        entry = {"town": r["town"], "hops": r["hops"], "kind": r["kind"], "noun": r["noun"],
                 "land": LAND.get(code, r["noun"]), "full": fix_tail(full), "short": fix_tail(short)}
        if r["dir"]:
            entry["dir"] = r["dir"]
        out_maps[code] = entry
    return {"_comment": "ORG-084 (Т-25): человеческие названия карт по атласу — город, направление, тип. "
                        "Сгенерировано scripts/gen_place_names.py из brain/world/atlas.json, руками не править. "
                        "full — с городом, short — без города (так говорит житель своего города); "
                        "loc — с предлогом места, from — «с/из» + род. падеж; land — слово для собственных имён.",
            "towns": out_towns, "maps": out_maps}


def fix_tail(forms):
    """«Муравейник , ярус 2» -> «Муравейник, ярус 2»; первая буква — прописная."""
    return {k: cap(v.replace(" ,", ",")) if k in ("nom", "gen", "acc") else v.replace(" ,", ",")
            for k, v in forms.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--atlas", default=str(ROOT / "brain" / "world" / "atlas.json"))
    a = ap.parse_args(argv)
    atlas = json.loads(Path(a.atlas).read_text(encoding="utf-8"))
    sys.stdout.write(json.dumps(build(atlas), ensure_ascii=False, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
