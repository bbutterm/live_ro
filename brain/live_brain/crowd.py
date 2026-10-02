"""Против одинаковости: стигмергия занятий и карт (ORG-089). Правила без LLM, только факты.

Каждый житель раз в publish_minutes кладёт в шину мира «где я и чем занят» — вид presence {map, activity, mode}
(важность 1, у жителя одна запись — «затирание»; в летопись, новости и дашборд не попадает) — и читает такие же
записи других (моложе stale_minutes). Жители своей группы (party.mates) толпой не считаются: вместе — это нормально.
На моей текущей карте к ним добавляются видимые игроки (state.players, не жители и не группа) с весом 0.5.

Доля толпы (share, 0..1) = (жители на карте/в занятии + 0.5 × видимые чужие) / число других жителей вне группы.
Штрафы при выборе — слабые, чтобы опыт и мотивы оставались главными:
    карта (maps.choose):  share × weight × (0.5 + 0.5 × смелость) — осторожным толпа не мешает (толпа = безопасность);
                          оценка карты s → s − |s| × штраф; неисследованные карты — сначала менее людные;
    занятие (activity.scores): share × weight × (1 − 0.5 × общительность, только в городе)
                          + repeat × min(сколько раз начинал это занятие сегодня, repeat_cap) — не для текущего.
Разнообразие занятий за сутки видно метрикой ORG-046 «разнообразие занятий» (__main__.organic_metrics).
Нет шины мира — работают только видимые игроки и штраф повторов.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("crowd")

DEFAULTS = {"enabled": True, "publish_minutes": 5, "stale_minutes": 15, "weight": 0.3, "repeat": 0.05,
            "repeat_cap": 4, "cache_seconds": 30}
HUMAN = 0.5


class Crowd:
    def __init__(self, mind, world=None, clock=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("crowd") or {}))
        self.tz = timezone(timedelta(hours=(world or {}).get("timezone_offset_hours", 0)))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.published = 0.0
        self.cache = (0.0, {})                        # (когда прочитано, {житель: presence})
        self.rep_cache = (0.0, {})                    # (когда посчитано, {занятие: раз сегодня})

    # ---------- данные ----------

    @property
    def bus(self):
        return getattr(getattr(self.mind, "world", None), "bus", None)

    def trait(self, name):
        return (getattr(getattr(self.mind, "needs", None), "t", None) or {}).get(name, 0.5)

    def me(self):
        return self.mind.state.get("name") or self.mind.persona.get("name")

    def mates(self):
        party = getattr(self.mind, "party", None)
        try:
            return set(party.mates()) if party else set()
        except (AttributeError, TypeError, ValueError):
            return set()

    def population(self):
        return max(1, len(set(self.mind.ctx.peers) - self.mates() - {self.me()}))

    def presence(self):
        acts = getattr(self.mind, "activities", None)
        r = getattr(self.mind, "routine", None)
        return {"map": self.mind.state.get("map"), "activity": (acts.st.get("name") if acts else None),
                "mode": (r.st.get("mode") if r and r.st else None)}

    # ---------- шина ----------

    def tick(self):
        now = self.clock()
        bus = self.bus
        if not bus or not getattr(self.mind, "fresh_state", True) or not self.mind.state.get("map"):
            return
        if now - self.published < self.cfg["publish_minutes"] * 60:
            return
        self.published = now
        try:
            bus.replace("presence", self.presence(), 1, now=now)
        except Exception as e:                       # общая БД занята — повтор в следующий раз
            log.warning("шина мира недоступна: %s", e)

    def others(self, now=None):
        """Где и чем заняты другие жители (без меня и своей группы), по шине; кэш cache_seconds."""
        now = now or self.clock()
        ts, data = self.cache
        if now - ts < self.cfg["cache_seconds"] and ts:
            return data
        data = {}
        bus = self.bus
        if bus:
            try:
                rows = bus.latest("presence", now - self.cfg["stale_minutes"] * 60)
            except Exception as e:
                log.warning("шина мира недоступна: %s", e)
                rows = {}
            skip = self.mates() | {self.me()}
            data = {b: r["data"] for b, r in rows.items() if b in self.mind.ctx.peers and b not in skip}
        self.cache = (now, data)
        return data

    def strangers_here(self):
        """Видимые чужие игроки на моей карте (не жители — те уже в шине, не группа)."""
        skip = set(self.mind.ctx.peers) | {self.me()}
        return {p.get("name") for p in self.mind.state.get("players") or []
                if isinstance(p, dict) and p.get("name") and p["name"] not in skip}

    # ---------- доли и штрафы ----------

    def map_share(self, hmap):
        n = sum(1 for p in self.others().values() if p.get("map") == hmap)
        if hmap and hmap == self.mind.state.get("map"):
            n += HUMAN * len(self.strangers_here())
        return round(min(1.0, n / self.population()), 3)

    def activity_share(self, name):
        n = sum(1 for p in self.others().values() if p.get("activity") == name)
        return round(min(1.0, n / self.population()), 3)

    def map_penalty(self, hmap):
        return round(self.map_share(hmap) * self.cfg["weight"] * (0.5 + 0.5 * self.trait("bravery")), 3)

    def repeats(self, now=None):
        """Сколько раз каждое занятие начиналось сегодня (события activity, по часовому поясу мира)."""
        now = now or self.clock()
        ts, data = self.rep_cache
        if ts and now - ts < self.cfg["cache_seconds"]:
            return data
        d = datetime.fromtimestamp(now, self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        data = {}
        for (raw,) in self.mind.mem.db.execute("SELECT data FROM events WHERE kind = 'activity' AND ts >= ?",
                                               (d.timestamp(),)):
            try:
                name = json.loads(raw).get("name")
            except (ValueError, AttributeError):
                continue
            data[name] = data.get(name, 0) + 1
        self.rep_cache = (now, data)
        return data

    def activity_penalty(self, name, mode=None, current=None):
        crowd = self.activity_share(name) * self.cfg["weight"]
        if mode == "town":
            crowd *= 1 - 0.5 * self.trait("sociability")     # общительным людные городские занятия не мешают
        rep = 0.0 if name == current else self.cfg["repeat"] * min(self.repeats().get(name, 0), self.cfg["repeat_cap"])
        return round(crowd + rep, 3)

    def summary(self):
        others = self.others()
        if not others:
            return None
        maps = {}
        for p in others.values():
            if p.get("map"):
                maps[p["map"]] = maps.get(p["map"], 0) + 1
        return {"где другие": maps}
