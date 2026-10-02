"""Атлас мира: карты, монстры, переходы, магазины и Kafra из данных rAthena (AUT-031..036, 039, 043,
045, 073, 074, 077). Правила без LLM, только чтение brain/world/atlas.json (scripts/gen_atlas.py).

Атлас — знание из таблиц сервера, а не опыт: «в таблицах есть», а не «я там был» (AUT-073).
Опыт по картам — maps.py (map_stats/places), смерти — postmortem.py; атлас их не заменяет.

Риск карты для персонажа (danger_for, 0..1, с причинами):
    r_agg  = clamp((L_agg - level + 5) / 20), L_agg — уровень самого высокого агрессивного монстра
             (Aggressive/Angry, не босс). Редкий (меньше RARE_SHARE популяции, одиночные «чемпионы»
             вроде Vocal/Choco) — вдвое слабее: встречу с ним можно обойти;
    r_lvl  = clamp((средний уровень бойцов - level) / 20): обычные монстры сильнее героя;
    r_boss = 0.3, если на карте есть босс/MVP выше уровня героя;
    risk   = 1 - (1-r_agg)(1-r_lvl)(1-r_boss); pvp/gvg-карта — 1.0, неизвестная карта — 0.5.
Подходящие карты (suitable_maps): поле (по умолчанию) в пределах hops обычных варпов от around,
    средний уровень бойцов в коридоре [level-10, level+3], нет массовых агрессивных выше level+5,
    risk < MAX_RISK. Очки: близость к level-3 (сладкая точка опыта renewal) × (1-risk) − 0.04×hops
    и поправка архетипа:
        melee   — минус доля монстров стихии Ghost (обычная атака по ним почти не проходит);
        ranged  — минус доля агрессивных (их не удержать на дистанции);
        magic   — минус доля монстров с MagicDefense > Defense;
        support — плюс доля Undead/Demon (святой урон и Heal аколита), минус доля агрессивных.
Маршрут (route): Дейкстра по числу переходов. Карта-посредник с агрессивными выше level+AVOID_GAP
    стоит +AVOID_COST (обходим, если есть альтернатива), pvp/gvg — +PVP_COST. Платные телепорты Kafra
    и скриптовые переходы (условия не проверены, AUT-034) — только по явному разрешению.
    Варп внутри карты выбирается ближайший к точке прибытия, а на последнем шаге — ближайший к цели.
Версия (atlas_version): хэш содержимого файла. Если версия изменилась, знания, выведенные из атласа
    (плохие переходы AUT-039, выбор карт), надо перепроверить (AUT-074).
"""
import hashlib
import heapq
import json
import logging
import math
from pathlib import Path

log = logging.getLogger("atlas")

ATLAS_PATH = Path(__file__).resolve().parents[1] / "world" / "atlas.json"
RARE_SHARE = 0.02          # агрессивных этого вида меньше 2% популяции — «редкий»
AGG_GAP = 5                # агрессивный выше level+5 — «намного выше» для выбора карты охоты
AVOID_GAP = 10             # агрессивный выше level+10 — маршрут обходит карту, если может
AVOID_COST = 50
PVP_COST = 1000
MAX_RISK = 0.5
HUNT_KINDS = ("field",)
ARCHETYPES = {
    "melee": ("swordman", "knight", "crusader", "lord knight", "paladin", "rune knight", "royal guard",
              "merchant", "blacksmith", "alchemist", "whitesmith", "creator", "mechanic", "genetic",
              "thief", "assassin", "rogue", "assassin cross", "stalker", "guillotine cross",
              "shadow chaser", "monk", "champion", "sura", "taekwon", "star gladiator", "novice"),
    "ranged": ("archer", "hunter", "bard", "dancer", "sniper", "clown", "gypsy", "ranger", "minstrel",
               "wanderer", "gunslinger", "rebellion"),
    "magic": ("mage", "wizard", "sage", "high wizard", "professor", "warlock", "sorcerer", "soul linker",
              "ninja"),
    "support": ("acolyte", "priest", "high priest", "arch bishop"),
}


def archetype(job):
    """Архетип боя по названию профессии (Swordman -> melee, Acolyte -> support); неизвестно — melee."""
    job = (job or "").strip().lower()
    for arch, jobs in ARCHETYPES.items():
        if job in jobs:
            return arch
    return "melee"


def clamp(x):
    return max(0.0, min(1.0, x))


class Atlas:
    def __init__(self, data, raw=b""):
        self.data = data
        self.maps = data.get("maps", {})
        self.monsters = data.get("monsters", {})
        self.items = data.get("items", {})
        self.item_shops = data.get("item_shops", {})
        self.version = hashlib.sha256(raw).hexdigest()[:16] if raw else None
        self._by_name = {}
        spawned = {}
        for m in self.maps.values():
            for mid, c in m.get("monsters", {}).items():
                spawned[mid] = spawned.get(mid, 0) + c
        for mid in sorted(self.monsters, key=lambda i: (-spawned.get(i, 0), int(i))):
            mo = self.monsters[mid]
            for key in (mo["name"].lower(), mo["aegis"].lower()):
                self._by_name.setdefault(key, mid)        # одноимённые: чаще встречающийся в мире

    @classmethod
    def load(cls, path=ATLAS_PATH):
        raw = Path(path).read_bytes()
        return cls(json.loads(raw.decode("utf-8")), raw)

    # ---------- монстры ----------

    def monster_info(self, name):
        """Уровень, агрессивность, стихия и где живёт — для каталога monster_risk (AUT-043)."""
        key = str(name).strip()
        mid = key if key in self.monsters else self._by_name.get(key.lower())
        if mid is None:
            return None
        mo = self.monsters[mid]
        where = sorted(((c, m) for m, d in self.maps.items() for i, c in d.get("monsters", {}).items() if i == mid),
                       key=lambda x: (-x[0], x[1]))
        return {"id": int(mid), "name": mo["name"], "level": mo["level"], "hp": mo["hp"],
                "attack": [mo["atk"], mo["atk2"]], "aggressive": aggressive(mo),
                "assist": "Assist" in mo["modes"], "looter": "Looter" in mo["modes"],
                "boss": boss(mo), "element": f'{mo["element"]} {mo["element_level"]}', "race": mo["race"],
                "size": mo["size"], "base_exp": mo["base_exp"], "maps": [m for _, m in where[:5]]}

    def _mobs(self, hmap):
        return [(self.monsters[i], c) for i, c in self.maps.get(hmap, {}).get("monsters", {}).items()
                if i in self.monsters]

    # ---------- риск ----------

    def danger_for(self, hmap, level):
        """Риск карты 0..1 для персонажа уровня level и список причин по-русски."""
        m = self.maps.get(hmap)
        if m is None:
            return 0.5, [f"карты {hmap} нет в атласе — риск неизвестен"]
        if {"pvp", "gvg", "gvg_castle"} & set(m.get("flags", ())) or m.get("kind") == "pvp":
            return 1.0, ["pvp/gvg-карта"]
        mobs = self._mobs(hmap)
        if not mobs:
            return 0.0, ["монстров нет"]
        total = sum(c for _, c in mobs)
        why, r_agg = [], 0.0
        agg = [(mo, c) for mo, c in mobs if aggressive(mo) and not boss(mo)]
        if agg:
            mo, c = max(agg, key=lambda x: (x[0]["level"], x[1]))
            r_agg = clamp((mo["level"] - level + 5) / 20)
            rare = c / total < RARE_SHARE
            if rare:
                r_agg /= 2
            if r_agg > 0:
                why.append(f'агрессивный {mo["name"]} ур. {mo["level"]}' + (" (редкий)" if rare else ""))
        lvl = (m.get("level") or {}).get("avg") or 0
        r_lvl = clamp((lvl - level) / 20)
        if r_lvl > 0:
            why.append(f"средний уровень монстров {lvl:g} выше моего {level}")
        bosses = [mo for mo, _ in mobs if boss(mo) and mo["level"] > level]
        r_boss = 0.3 if bosses else 0.0
        if bosses:
            why.append(f'босс {max(bosses, key=lambda x: x["level"])["name"]}')
        risk = 1 - (1 - r_agg) * (1 - r_lvl) * (1 - r_boss)
        return round(risk, 2), why or ["монстры по силам"]

    # ---------- граф ----------

    def _edges(self, hmap, paid, scripted):
        m = self.maps.get(hmap, {})
        for e in m.get("exits", ()):
            if e.get("script") and not scripted:
                continue
            yield e["to"], dict(e, via="script" if e.get("script") else "warp", zeny=0)
        if paid and m.get("kafra"):
            k = m["kafra"][0]
            for t in m.get("kafra_teleport", ()):
                yield t["to"], {"x": k["x"], "y": k["y"], "to": t["to"], "tx": t["tx"], "ty": t["ty"],
                                "via": "kafra", "zeny": t["zeny"], "npc": k["npc"]}

    def _cost(self, hmap, level, is_dst):
        if is_dst:
            return 0
        m = self.maps.get(hmap)
        if m is None:
            return 0
        if {"pvp", "gvg", "gvg_castle"} & set(m.get("flags", ())) or m.get("kind") == "pvp":
            return PVP_COST
        if level is not None:
            top = max((mo["level"] for mo, c in self._mobs(hmap) if aggressive(mo) and not boss(mo)), default=0)
            if top > level + AVOID_GAP:
                return AVOID_COST
        return 0

    def _search(self, src, level=None, paid=False, scripted=False, dst=None):
        dist, prev, heap = {src: 0}, {}, [(0, src)]
        while heap:
            d, cur = heapq.heappop(heap)
            if d > dist.get(cur, math.inf) or cur == dst:
                continue
            for nxt, edge in self._edges(cur, paid, scripted):
                nd = d + 1 + (edge["zeny"] / 1000) + self._cost(nxt, level, nxt == dst)
                if nd < dist.get(nxt, math.inf) - 1e-9:
                    dist[nxt], prev[nxt] = nd, cur
                    heapq.heappush(heap, (nd, nxt))
        return dist, prev

    def _path(self, src, dst, prev, paid, scripted, goal=None, start=None):
        maps = [dst]
        while maps[-1] != src:
            maps.append(prev[maps[-1]])
        maps.reverse()
        steps, pos = [], start
        for i, (a, b) in enumerate(zip(maps, maps[1:])):
            cand = [e for to, e in self._edges(a, paid, scripted) if to == b]
            plain = [e for e in cand if e["via"] == "warp"] or cand
            last = goal if i == len(maps) - 2 else None
            e = min(plain, key=lambda e: (near(pos, (e["x"], e["y"])) + near(last, (e["tx"], e["ty"])),
                                          e["x"], e["y"]))
            steps.append({"from": a, "x": e["x"], "y": e["y"], "to": b, "tx": e["tx"], "ty": e["ty"],
                          "via": e["via"], "zeny": e["zeny"]})
            pos = (e["tx"], e["ty"])
        return maps, steps

    def route(self, src, dst, level=None, paid=False, scripted=False, goal=None, start=None):
        """Кратчайший путь по переходам: {maps, steps, hops, zeny, risky} или None.

        level — обходить карты с агрессивными выше level+AVOID_GAP, если есть альтернатива;
        paid — разрешить платные телепорты Kafra; scripted — скриптовые переходы (условия не проверены);
        goal/start — (x, y) цели на dst и старта на src: выбрать ближайшие варпы.
        """
        if src == dst:
            return {"maps": [src], "steps": [], "hops": 0, "zeny": 0, "risky": []}
        if src not in self.maps:
            return None
        dist, prev = self._search(src, level, paid, scripted, dst)
        if dst not in dist:
            return None
        maps, steps = self._path(src, dst, prev, paid, scripted, goal, start)
        risky = [m for m in maps[1:-1] if self._cost(m, level, False) > 0]
        return {"maps": maps, "steps": steps, "hops": len(steps), "zeny": sum(s["zeny"] for s in steps),
                "risky": risky}

    def neighbors(self, hmap, scripted=False):
        return sorted({e["to"] for e in self.maps.get(hmap, {}).get("exits", ()) if scripted or not e.get("script")})

    # ---------- ближайшее ----------

    def _nearest(self, places, from_map, level, paid, key=lambda p: 0):
        dist, prev = self._search(from_map, level, paid, False)
        best = None
        for p in places:
            if p["map"] not in dist:
                continue
            calm = self.maps.get(p["map"], {}).get("kind") in ("town", "interior")   # при равенстве — не в поле
            rank = (dist[p["map"]], key(p), not calm, p["map"], p["x"], p["y"])
            if best is None or rank < best[0]:
                best = (rank, p)
        if best is None:
            return None
        p = dict(best[1])
        if p["map"] == from_map:
            p["route"] = {"maps": [from_map], "steps": [], "hops": 0, "zeny": 0, "risky": []}
        else:
            maps, steps = self._path(from_map, p["map"], prev, paid, False, goal=(p["x"], p["y"]))
            p["route"] = {"maps": maps, "steps": steps, "hops": len(steps), "zeny": sum(s["zeny"] for s in steps),
                          "risky": [m for m in maps[1:-1] if self._cost(m, level, False) > 0]}
        p["hops"] = p["route"]["hops"]
        return p

    def nearest_shop(self, item_id, from_map, level=None, paid=False):
        """Ближайший NPC-магазин с предметом: {map, x, y, npc, price, hops, route} или None.

        Порядок: число переходов (с учётом риска для level), цена, магазин в городе/интерьере раньше поля.
        """
        places = [{"map": m, "x": x, "y": y, "price": price, "npc": npc}
                  for m, x, y, price, npc in self.item_shops.get(str(item_id), ())]
        return self._nearest(places, from_map, level, paid, key=lambda p: p["price"])

    def nearest_kafra(self, from_map, level=None, paid=False):
        places = [{"map": m, "x": k["x"], "y": k["y"], "npc": k["npc"]}
                  for m, d in self.maps.items() for k in d.get("kafra", ())]
        return self._nearest(places, from_map, level, paid)

    # ---------- охота ----------

    def suitable_maps(self, level, job_archetype="melee", around="prontera", hops=4, top=5, kinds=HUNT_KINDS):
        """Карты охоты по уровню и архетипу рядом с around: [{map, score, hops, avg_level, risk, why}]."""
        dist, _ = self._search(around)
        near_maps = {m for m, d in dist.items() if d <= hops}
        out = []
        for name in sorted(near_maps):
            m = self.maps.get(name)
            if not m or m.get("kind") not in kinds or not m.get("level"):
                continue
            avg = m["level"]["avg"]
            if not level - 10 <= avg <= level + 3:
                continue
            mobs = self._mobs(name)
            total = sum(c for _, c in mobs)
            strong = [(mo, c) for mo, c in mobs if aggressive(mo) and not boss(mo) and mo["level"] > level + AGG_GAP]
            if sum(c for _, c in strong) / total >= RARE_SHARE:
                continue
            risk, reasons = self.danger_for(name, level)
            if risk >= MAX_RISK:
                continue
            adj, note = arch_adjust(job_archetype, mobs, total)
            score = (1 - abs(avg - (level - 3)) / 13) * (1 - risk) - 0.04 * dist[name] + adj
            why = [f"средний уровень {avg:g} при моём {level}", f"{int(dist[name])} перех. от {around}",
                   f"риск {risk:.2f}"] + ([note] if note else [])
            if strong:
                why.append("редкие агрессивные: " + ", ".join(sorted({mo["name"] for mo, _ in strong})))
            elif risk > 0:
                why.append("; ".join(reasons))
            out.append({"map": name, "score": round(score, 3), "hops": int(dist[name]), "avg_level": avg,
                        "risk": risk, "why": "; ".join(why)})
        out.sort(key=lambda x: (-x["score"], x["map"]))
        return out[:top]

    # ---------- помощники интеграции ----------

    def filter_hunt_maps(self, maps, level, max_risk=MAX_RISK):
        """Для maps.choose: (допустимые карты по порядку, {отклонённая: причина}). Пусто — решает вызывающий."""
        ok, rejected = [], {}
        for name in maps:
            risk, why = self.danger_for(name, level)
            if name in self.maps and risk >= max_risk:
                rejected[name] = f"риск {risk:.2f}: " + "; ".join(why)
            else:
                ok.append(name)
        return ok, rejected

    def check_hunt_maps(self, maps):
        """Для doctor: проблемы карт охоты по атласу (пустой список — всё в порядке)."""
        out = []
        for name in maps:
            m = self.maps.get(name)
            if m is None:
                out.append(f"{name}: нет в атласе (не загружена сервером или вне региона)")
            elif m.get("kind") == "pvp":
                out.append(f"{name}: pvp/gvg-карта")
            elif not m.get("monsters"):
                out.append(f"{name}: монстров по данным сервера нет")
        return out


def aggressive(mo):
    return bool({"Aggressive", "Angry"} & set(mo["modes"]))


def boss(mo):
    return mo["class"] == "Boss" or "Mvp" in mo["modes"]


def near(a, b):
    return 0 if a is None else math.hypot(a[0] - b[0], a[1] - b[1])


def arch_adjust(arch, mobs, total):
    share = lambda pred: sum(c for mo, c in mobs if pred(mo)) / total
    if arch == "melee":
        s = share(lambda mo: mo["element"] == "Ghost")
        return -0.5 * s, f"призраков {s:.0%}" if s >= 0.01 else None
    if arch == "ranged":
        s = share(aggressive)
        return -0.2 * s, f"агрессивных {s:.0%}" if s >= 0.01 else None
    if arch == "magic":
        s = share(lambda mo: mo["mdef"] > mo["def"])
        return -0.3 * s, f"стойких к магии {s:.0%}" if s >= 0.01 else None
    if arch == "support":
        good, bad = share(lambda mo: mo["race"] in ("Undead", "Demon")), share(aggressive)
        note = ", ".join(x for x in (f"нежить/демоны {good:.0%}" if good >= 0.01 else "",
                                     f"агрессивных {bad:.0%}" if bad >= 0.01 else "") if x)
        return 0.2 * good - 0.3 * bad, note or None
    return 0.0, None


# ---------- атлас по умолчанию (brain/world/atlas.json) ----------

_default = None


def default():
    global _default
    if _default is None:
        _default = Atlas.load()
    return _default


def reload(path=ATLAS_PATH):
    """Перечитать атлас (после перегенерации); вернуть новую версию."""
    global _default
    _default = Atlas.load(path)
    return _default.version


def atlas_version():
    return default().version


def suitable_maps(level, job_archetype="melee", around="prontera", hops=4, top=5, kinds=HUNT_KINDS):
    return default().suitable_maps(level, job_archetype, around, hops, top, kinds)


def route(src, dst, level=None, **kw):
    return default().route(src, dst, level, **kw)


def danger_for(hmap, level):
    return default().danger_for(hmap, level)


def nearest_shop(item_id, from_map, level=None, paid=False):
    return default().nearest_shop(item_id, from_map, level, paid)


def nearest_kafra(from_map, level=None, paid=False):
    return default().nearest_kafra(from_map, level, paid)


def monster_info(name):
    return default().monster_info(name)
