"""Шрамы, страх места и реванш (ORG-093, ТЗ Т-48). Правила без LLM, только факты игры.

Смерть оставляет шрам — kv scars [{map, mob, ts, lv, deaths, tries, tried, closed}] (≤ max_scars): карта и монстр
из последнего death_report (postmortem.py), уровень в момент смерти. Бан карты postmortem (MAP_BAN 2 ч после двух
смертей) не меняется — шрам живёт дольше и действует иначе в зависимости от смелости (эффективной, с дрейфом ORG-092):
    страх       fear(карта) = 0.5^(возраст / half_life_days), открытые шрамы. Осторожный (смелость < brave_min)
                не выбирает карту, пока страх ≥ avoid_min (0.25 — две недели при полураспаде 7 дней); если так
                исключено всё — список как был;
    реванш      смелый через revenge_after_days (2) … revenge_until_days (14) после смерти тянется на эту карту,
                если уровень ≥ уровня смерти + revenge_levels (2), HP ≥ revenge_hp (90 %), карта прошла фильтр
                уровня/риска (maps.safe_for_level) и попыток было < revenge_tries (3; попытка — не чаще раза в 6 ч);
    взял реванш победа над тем же монстром на той же карте (шрам без монстра — любая победа там) не раньше часа
                после смерти: событие revenge {map, mob, days, tries} — память (важность 4), решения, хроника;
                шрам закрыт (страха больше нет).
Встраивание: maps.choose (строки `# scars:`): avoid после банов, revenge — раньше неизведанных карт и очков.
Подписки реестра: died (после postmortem.report — death_report уже записан), kill. Событие не поглощается.
Выключатель: BRAIN_DISABLE=scars или "scars": {"enabled": false} в goals.json — выбор карты как раньше.
"""
import json
import logging
import time

log = logging.getLogger("scars")

DEFAULTS = {"enabled": True, "half_life_days": 7, "avoid_min": 0.25, "brave_min": 0.5, "revenge_after_days": 2,
            "revenge_until_days": 14, "revenge_levels": 2, "revenge_hp": 90, "revenge_tries": 3, "max_scars": 20}
TRY_GAP = 6 * 3600
CLOSE_AFTER = 3600


def plural_days(n):
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    return f"{n} дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else f"{n} дней"


CHRONICLE_LINES = {
    "revenge": lambda d: (f"взял(а) реванш" + (f" у {d.get('mob')}" if d.get("mob") else "")
                          + f" на {d.get('map')} (через {plural_days(d.get('days') or 0)})"),
}


class Scars:
    # реестр модулей (modules.py, W8): без тика; события died/kill после ядра (postmortem уже разобрал смерть)
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "scars", "scars", "scars", True, "world"
    EVENTS, EVENT_ORDER = {"died": "on_died", "kill": "on_kill"}, 95

    def __init__(self, mind, world=None, clock=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **(((world or {}).get("scars")) or {}))
        self.clock = clock or (lambda: time.time())
        self.items = [s for s in (mind.mem.get("scars") or []) if isinstance(s, dict) and s.get("map")]

    def save(self):
        self.items = self.items[-int(self.cfg["max_scars"]):]
        self.mind.mem.set("scars", self.items)

    def open(self):
        return [s for s in self.items if not s.get("closed")]

    def bravery(self):
        t = getattr(getattr(self.mind, "needs", None), "t", None)
        if not isinstance(t, dict):
            t = self.mind.persona.get("traits") or {}
        try:
            return float(t.get("bravery", 0.5))
        except (TypeError, ValueError):
            return 0.5

    def brave(self):
        return self.bravery() >= self.cfg["brave_min"]

    # ---------- смерть ----------

    def on_died(self, event):
        now = self.clock()
        row = self.mind.mem.db.execute(
            "SELECT data FROM events WHERE kind = 'death_report' ORDER BY id DESC LIMIT 1").fetchone()
        try:
            rep = json.loads(row[0]) if row else {}
        except (TypeError, ValueError):
            rep = {}
        hmap = (event or {}).get("map") or rep.get("map") or (self.mind.state or {}).get("map")
        if not hmap:
            return None
        mob = rep.get("cause") if rep.get("map") in (None, hmap) else None
        lv = (self.mind.state or {}).get("lv")
        for s in self.open():
            if s["map"] == hmap and s.get("mob") == mob:
                s.update(ts=now, lv=lv or s.get("lv"), deaths=s.get("deaths", 1) + 1)
                self.save()
                return s
        s = {"map": hmap, "mob": mob, "ts": now, "lv": lv, "deaths": 1, "tries": 0, "tried": 0, "closed": None}
        self.items.append(s)
        self.save()
        log.info("шрам: %s (%s)", hmap, mob or "?")
        return s

    # ---------- страх ----------

    def fear(self, hmap, now=None):
        now = now or self.clock()
        half = self.cfg["half_life_days"] * 86400
        return max([0.5 ** (max(0.0, now - s["ts"]) / half) for s in self.open() if s["map"] == hmap] or [0.0])

    def avoid(self, maps, now=None):
        """Осторожный обходит карты со свежим шрамом; смелому — список как есть."""
        if self.brave():
            return list(maps)
        now = now or self.clock()
        ok = [m for m in maps if self.fear(m, now) < self.cfg["avoid_min"]]
        return ok or list(maps)

    # ---------- реванш ----------

    def revenge(self, maps, now=None):
        """Шрам, ради которого смелый идёт на карту из maps, или None."""
        if not self.brave():
            return None
        now = now or self.clock()
        c = self.cfg
        state = self.mind.state or {}
        lv, hp = state.get("lv"), state.get("hp_pct")
        if not isinstance(lv, (int, float)) or not isinstance(hp, (int, float)) or hp < c["revenge_hp"]:
            return None
        best = None
        for s in self.open():
            age = now - s["ts"]
            if s["map"] not in maps or s.get("tries", 0) >= c["revenge_tries"]:
                continue
            if not c["revenge_after_days"] * 86400 <= age <= c["revenge_until_days"] * 86400:
                continue
            if not isinstance(s.get("lv"), (int, float)) or lv < s["lv"] + c["revenge_levels"]:
                continue
            if best is None or s["ts"] > best["ts"]:
                best = s
        return best

    def take(self, scar, now=None):
        """Карта реванша выбрана: попытка (не чаще раза в TRY_GAP) и причина для журнала распорядка."""
        now = now or self.clock()
        if now - (scar.get("tried") or 0) >= TRY_GAP:
            scar["tries"] = scar.get("tries", 0) + 1
            scar["tried"] = now
            self.save()
            self.mind.mem.add_event("revenge_try", {"map": scar["map"], "mob": scar.get("mob"), "tries": scar["tries"]})
        days = int((now - scar["ts"]) // 86400)
        party = getattr(self.mind, "party", None)
        try:
            grouped = bool(party.confirmed()) if party is not None and callable(getattr(party, "confirmed", None)) else False
        except Exception:                                   # шпион/битая группа — без пометки
            grouped = False
        with_group = " с группой" if grouped else ""
        who = f" меня убил {scar['mob']}" if scar.get("mob") else " я погиб"
        return f"реванш{with_group}: здесь{who} {plural_days(days)} назад, теперь я сильнее"

    def on_kill(self, event):
        monster = (event or {}).get("monster")
        hmap = (event or {}).get("map") or (self.mind.state or {}).get("map")
        if not hmap:
            return None
        now = self.clock()
        for s in self.open():
            if s["map"] != hmap or now - s["ts"] < CLOSE_AFTER:
                continue
            if s.get("mob") and s["mob"] != monster:
                continue
            s["closed"] = now
            self.save()
            days = int((now - s["ts"]) // 86400)
            data = {"map": hmap, "mob": s.get("mob"), "days": days, "tries": s.get("tries", 0)}
            self.mind.mem.add_event("revenge", data)
            text = CHRONICLE_LINES["revenge"](data)
            self.mind.mem.remember(f"Я {text}.", 4)
            write = getattr(self.mind, "write_decision", None)
            if write:
                write({"type": "revenge", **data})
            log.info("%s", text)
            return data
        return None

    def summary(self):
        now = self.clock()
        return [{"map": s["map"], "mob": s.get("mob"), "days": int((now - s["ts"]) // 86400),
                 "fear": round(self.fear(s["map"], now), 2)} for s in self.open()]
