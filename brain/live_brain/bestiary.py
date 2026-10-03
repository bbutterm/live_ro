"""Бестиарий мира и первооткрыватели (ORG-077, ТЗ Т-23). Правила без LLM, только факты памяти и шины мира.

Свой бестиарий — kv bestiary {kills: {монстр: {n, first, map}}, places: {карта: first}, firsts: [...], told, seeded}:
счёт побед по событию kill {monster} (brainBridge onKill; карты в событии нет — берётся state.map) и посещённые карты
из kv places (maps.seen). Личная «первая победа над видом» уже есть у коллекции (collection.trophy_first) — бестиарий
отвечает за социальный слой: кто ПЕРВЫМ ИЗ ЖИТЕЛЕЙ.

Мир знает (world_known): тихие снимки других жителей в шине bestiary_known {m: [виды], p: [карты]} (затирание,
world_bus.QUIET) и записи monster_first / place_first всех жителей. Новый для меня вид или карта, которых мир не знает:
    monster_first {monster, map, level, boss}  шина, важность 3 (мини-босс из goals.json boss.targets или Boss/MVP — 4);
    place_first {map, terrain, name}           шина, важность 4; только поле/подземелье атласа; топоним «тропа Arkady»
                                               (поле) или «ход Arkady» (подземелье);
    bestiary_first {what: monster|place, ...}  событие памяти (ключей kind/ts в данных нет — recent_events) (летопись), воспоминание, тема разговора bestiary.
Первый запуск (создание модуля) — молча: прошлые победы (события kill памяти) и посещённые карты — в бестиарий и снимок, без объявлений.
Без шины мира «первый из жителей» не проверить — открытия не объявляются, ведётся только свой счёт.

Дашборд: world_bestiary(lab_root, bots) — общий счёт видов по жителям и первооткрыватели (только чтение).
Метрики report: «видов в бестиарии», «открытий первым». Выключатель: BRAIN_DISABLE=bestiary или
goals.json "bestiary": {"enabled": false}.
"""
import json
import logging
import random
import sqlite3
import time
from pathlib import Path

from .world_bus import WorldBus, encode, MAX_DATA, read_period

log = logging.getLogger("bestiary")

DEFAULTS = {"enabled": True, "tick_seconds": 10, "snapshot_minutes": 10, "known_cache_seconds": 60,
            "brag_days": 7, "brag_chance": 0.6, "kinds": ["field", "dungeon"]}
TOPONYM = {"field": "тропа", "dungeon": "ход"}
NAME_MAX = 24
KNOWN_LIMIT = 500
PHRASES = {   # ≤ 60 символов без метки
    "bestiary": ["Я первым(ой) из нас победил(а) {mob}!", "Знаешь, {mob} — я одолел(а) его первым(ой)!"],
    "bestiary_place": ["Я первым(ой) из нас дошёл(шла) до {place}!", "На {place} я был(а) первым(ой): {name}."],
    "bestiary_re": ["Ничего себе! Расскажешь, как?", "Вот это открытие!", "Здорово, первооткрыватель!"],
}


class Bestiary:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "bestiary", "bestiary", "bestiary", True, "world"
    TICK_ORDER = 225                          # после collection (220): та же память kill
    TICK_EVERY = 10             # perf: реестр не зовёт tick до next_tick (modules.py)
    EVENTS, EVENT_ORDER = {"kill": "on_kill"}, 75   # после boss (70), событие не поглощается
    PROMPT = [("бестиарий", "summary", 235)]  # после соперника (230), до слухов (240)

    def __init__(self, mind, world=None, clock=None, rng=None, atlas=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("bestiary") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self._atlas = atlas
        self.minis = {str(t.get("mob")) for t in (((world or {}).get("boss") or {}).get("targets") or [])
                      if isinstance(t, dict) and t.get("mob")} or {"Vocal", "Eclipse"}
        self.st = mind.mem.get("bestiary") or {}
        for key, val in (("kills", {}), ("places", {}), ("firsts", []), ("told", {})):
            self.st.setdefault(key, val)
        self.dirty = False
        self.next_tick = 0.0
        self.next_snap = 0.0
        self.snap_sent = None
        self.known_cache = (0.0, None)
        if not self.st.get("seeded"):
            self.seed()                                  # первый запуск: история — молча
        social = getattr(mind, "social", None)
        if social is not None and hasattr(social, "register_topic"):
            for key, pool in PHRASES.items():
                social.phrases.setdefault(key, pool)
            social.register_topic("bestiary", self.facts, said=self.said, chance=self.cfg["brag_chance"],
                                  interest="bestiary")   # interest: ORG-103

    # ---------- данные ----------

    def me(self):
        return (self.mind.state or {}).get("name") or self.mind.persona["name"]

    def atlas(self):
        if self._atlas is None:
            from . import atlas
            self._atlas = atlas.default()
        return self._atlas

    def bus(self):
        bus = getattr(getattr(self.mind, "world", None), "bus", None)
        return bus if isinstance(bus, WorldBus) else None          # только настоящая шина мира (world_bus.Feed)

    def save(self):
        self.mind.mem.set("bestiary", self.st)
        self.dirty = False

    def seed(self):
        """Первый запуск: прошлые победы и посещённые карты — молча, без объявлений."""
        now = self.clock()
        for ts, data in self.mind.mem.db.execute("SELECT ts, data FROM events WHERE kind = 'kill' ORDER BY id"):
            try:
                mob = (json.loads(data) or {}).get("monster")
            except (TypeError, ValueError):
                continue
            if mob:
                rec = self.st["kills"].setdefault(str(mob), {"n": 0, "first": ts, "map": None})
                rec["n"] += 1
        for hmap, p in (self.mind.mem.get("places") or {}).items():
            if isinstance(p, dict) and (p.get("first") or p.get("source") == "seen"):
                self.st["places"].setdefault(hmap, p.get("first") or now)
        self.st["seeded"] = True
        self.save()

    def world_known(self):
        """(виды, карты), которые мир уже знает: снимки других жителей и записи monster_first/place_first."""
        now = self.clock()
        ts, data = self.known_cache
        if data is not None and now - ts < self.cfg["known_cache_seconds"]:
            return data
        mobs, maps = set(), set()
        bus = self.bus()
        if bus:
            for r in bus.latest("bestiary_known").values():
                mobs |= set((r.get("data") or {}).get("m") or [])
                maps |= set((r.get("data") or {}).get("p") or [])
            mobs |= {str(r["data"].get("monster")) for r in bus.recent("monster_first", 0, KNOWN_LIMIT)}
            maps |= {str(r["data"].get("map")) for r in bus.recent("place_first", 0, KNOWN_LIMIT)}
        self.known_cache = (now, (mobs, maps))
        return mobs, maps

    # ---------- события ----------

    def on_kill(self, event):
        mob = event.get("monster")
        if not mob:
            return
        mob, now, hmap = str(mob), self.clock(), (self.mind.state or {}).get("map")
        rec = self.st["kills"].get(mob)
        if rec is None:
            rec = self.st["kills"][mob] = {"n": 0, "first": now, "map": hmap}
        rec["n"] += 1
        self.dirty = True
        if rec["n"] == 1:
            self.save()
            try:
                self.discover_monster(mob, hmap, now)
            except sqlite3.Error as e:                   # общая БД занята — открытие не объявлено, счёт цел
                log.warning("шина мира: %s", e)

    def discover_monster(self, mob, hmap, now):
        bus = self.bus()
        if not bus or mob in self.world_known()[0]:
            return
        info = self.atlas().monster_info(mob) or {}
        boss = bool(info.get("boss")) or mob in self.minis
        data = {"monster": mob, "map": hmap, "level": info.get("level"), "boss": boss}
        bus.publish("monster_first", data, 4 if boss else 3, now=now)
        self.first("monster", data, f"Первым(ой) из жителей победил(а) {mob}" + (f" на {hmap}." if hmap else "."),
                   4 if boss else 3)

    def discover_place(self, hmap, now):
        bus = self.bus()
        kind = ((self.atlas().maps.get(hmap) or {}).get("kind"))
        if not bus or kind not in self.cfg["kinds"] or hmap in self.world_known()[1]:
            return
        name = f"{TOPONYM.get(kind, 'место')} {self.me()}"
        data = {"map": hmap, "terrain": kind, "name": name}
        bus.publish("place_first", data, 4, now=now)
        self.first("place", data, f"Первым(ой) из жителей дошёл(шла) до {hmap}. Зовут: {name}.", 4)

    def first(self, what, data, text, importance):
        self.known_cache = (0.0, None)
        rec = dict(data, what=what, ts=self.clock())
        self.st["firsts"] = (self.st["firsts"] + [rec])[-50:]
        self.save()
        self.mind.mem.add_event("bestiary_first", dict(data, what=what))
        self.mind.mem.remember(text, importance)
        self.mind.write_decision({"type": "bestiary", "event": "first", "what": what, **data})
        log.info("%s", text)

    # ---------- такт ----------

    def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        try:
            for hmap, p in (self.mind.mem.get("places") or {}).items():
                if hmap in self.st["places"] or not isinstance(p, dict) or not p.get("first"):
                    continue
                self.st["places"][hmap] = p["first"]
                self.dirty = True
                self.discover_place(hmap, now)
            if self.dirty:
                self.save()
            self.snapshot(now)
        except sqlite3.Error as e:
            log.warning("шина мира: %s — повторю позже", e)

    def snapshot(self, now):
        """Тихий снимок «что я знаю» в шине (затирание): при изменении, не чаще snapshot_minutes."""
        bus = self.bus()
        if not bus or now < self.next_snap:
            return
        data = {"m": sorted(self.st["kills"]), "p": sorted(self.st["places"])}
        while len(encode(data)) >= MAX_DATA and (data["m"] or data["p"]):
            key = "m" if len(data["m"]) >= len(data["p"]) else "p"
            data[key] = data[key][:-max(1, len(data[key]) // 10)]
        if data == self.snap_sent:
            return
        self.next_snap = now + self.cfg["snapshot_minutes"] * 60
        bus.replace("bestiary_known", data, 1, now=now)
        self.snap_sent = data

    # ---------- тема разговора (ORG-066) ----------

    def facts(self, peer, now):
        """Своё свежее открытие, о котором этому жителю ещё не говорил -> {mob} или {place, name}."""
        told = set(self.st["told"].get(peer) or [])
        for rec in reversed(self.st["firsts"]):
            key = f"{rec.get('what')}:{rec.get('monster') or rec.get('map')}"
            if key in told or now - rec.get("ts", 0) > self.cfg["brag_days"] * 86400:
                continue
            if rec.get("what") == "monster":
                return {"mob": str(rec["monster"])[:NAME_MAX], "_first": key}
            return {"place": str(rec["map"])[:NAME_MAX], "name": str(rec.get("name"))[:NAME_MAX],
                    "_first": key, "_key": "bestiary_place"}
        return None

    def said(self, peer, facts, now):
        key = (facts or {}).get("_first")
        if key:
            self.st["told"][peer] = ((self.st["told"].get(peer) or []) + [key])[-50:]
            self.save()

    def summary(self):
        if not self.st["kills"]:
            return None
        out = {"видов": len(self.st["kills"]), "мест": len(self.st["places"])}
        if self.st["firsts"]:
            f = self.st["firsts"][-1]
            out["последнее_открытие"] = f.get("monster") or f.get("map")
        return out


def first_text(d):
    if d.get("what") == "place" or (d.get("map") and not d.get("monster")):
        return f"первым(ой) из жителей дошёл(шла) до {d.get('map')} — «{d.get('name')}»"
    return f"первым(ой) из жителей победил(а) {d.get('monster')}" + (" (мини-босс)" if d.get("boss") else "")


CHRONICLE_LINES = {"bestiary_first": first_text}


def species(memory):
    """Для метрик (report): число видов в бестиарии жителя (kv bestiary)."""
    return len(((memory.get("bestiary") or {}).get("kills") or {}))


def world_bestiary(lab_root, bots, now=None):
    """Для дашборда: общий бестиарий жителей (только чтение памяти и шины).
    {"monsters": [{monster, kills, by {житель: n}, first_by, first_ts}], "places": [{map, name, by, ts}]}"""
    now = now or time.time()
    mobs, names = {}, {}
    for bot in bots:
        path = Path(lab_root) / "state" / bot / "memory.sqlite"
        if not path.exists():
            continue
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
            try:
                kv = dict(db.execute("SELECT key, value FROM kv WHERE key IN ('bestiary', 'last_state')").fetchall())
            finally:
                db.close()
        except sqlite3.Error:
            continue
        try:
            best = json.loads(kv.get("bestiary") or "{}") or {}
            who = (json.loads(kv.get("last_state") or "{}") or {}).get("name") or bot
        except (TypeError, ValueError):
            continue
        names[bot] = who
        for mob, rec in (best.get("kills") or {}).items():
            m = mobs.setdefault(mob, {"monster": mob, "kills": 0, "by": {}, "first_by": None, "first_ts": None,
                                      "seen_first": None})
            n = int((rec or {}).get("n") or 0)
            m["kills"] += n
            m["by"][who] = n
            ts = (rec or {}).get("first")
            if ts and (m["seen_first"] is None or ts < m["seen_first"][0]):
                m["seen_first"] = (ts, who)
    places = []
    for e in read_period(Path(lab_root) / "state" / "shared" / "world.sqlite", 0, now + 1):
        if e["kind"] == "monster_first":
            mob = str(e["data"].get("monster"))
            m = mobs.setdefault(mob, {"monster": mob, "kills": 0, "by": {}, "first_by": None, "first_ts": None,
                                      "seen_first": None})
            if m["first_ts"] is None or e["ts"] < m["first_ts"]:
                m.update(first_by=e["bot"], first_ts=e["ts"], boss=bool(e["data"].get("boss")))
        elif e["kind"] == "place_first":
            places.append({"map": e["data"].get("map"), "name": e["data"].get("name"), "by": e["bot"], "ts": e["ts"]})
    out = []
    for m in mobs.values():
        seen = m.pop("seen_first")
        if m["first_by"] is None and seen:                 # шины нет или запись старше 30 дней — по памяти
            m["first_ts"], m["first_by"], m["guess"] = seen[0], seen[1], True
        out.append(m)
    out.sort(key=lambda m: (-m["kills"], m["monster"]))
    return {"monsters": out, "places": sorted(places, key=lambda p: p["ts"])}
