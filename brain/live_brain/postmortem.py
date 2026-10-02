"""Разбор смерти, опасные карты и монстры (AUT-009, AUT-010, AUT-011, AUT-043). Правила без LLM.

Наблюдения копятся в памяти процесса (кольцо последних 120 с): начало боя с монстром (attack),
действия плагина survival (зелье/крыло/danger), снимки состояния (HP, вес, зелья, карта).
При событии died — запись death_report только из наблюдённого; чего не видели — в «неизвестно».
    причина-кандидат: монстр, с которым начинали бой чаще всего за последние 30 с;
Опасная карта (AUT-010): 2 смерти на одной карте за MAP_WINDOW — карта исключается на MAP_BAN;
    распорядок не выбирает исключённую карту (если исключены все — первая из списка характера).
Опасный монстр (AUT-043): каталог monster_risk {имя: убивал меня, побеждён мной, последний раз};
    модель видит его в промпте, распорядок — через исключение карт.
Партнёру (AUT-011): житель в группе узнаёт о смерти — [party:dead:<карта>]; воскрешать он
    не обещает: у аколита нет ALL_RESURRECTION, это проверяется по профилю боя.
"""
import collections
import json
import logging
import time

log = logging.getLogger("postmortem")

RING = 120
CAUSE_WINDOW = 30
MAP_WINDOW = 7200
MAP_BAN = 7200
MAP_DEATHS = 2


class Postmortem:
    def __init__(self, mind, clock=time.time):
        self.mind = mind
        self.clock = clock
        self.ring = collections.deque()          # (ts, kind, data)

    def observe(self, kind, data):
        now = self.clock()
        self.ring.append((now, kind, data))
        while self.ring and now - self.ring[0][0] > RING:
            self.ring.popleft()

    def on_state(self, state):
        heal = state.get("items")
        self.observe("state", {"hp_pct": state.get("hp_pct"), "weight_pct": state.get("weight_pct"),
                               "map": state.get("map"), "potions": None if heal is None else
                               sum(int(heal.get(i, 0) or 0) for i in ("569", "501", "502", "503", "504"))})

    def on_kill(self, monster):
        if not monster:
            return
        risk = self.mind.mem.get("monster_risk", {})
        r = risk.setdefault(monster, {"killed_me": 0, "beaten": 0, "last": 0})
        r["beaten"] += 1
        self.mind.mem.set("monster_risk", risk)

    # ---------- смерть ----------

    def report(self, event):
        now = self.clock()
        recent = [x for x in self.ring if now - x[0] <= CAUSE_WINDOW]
        foes = collections.Counter(d.get("monster") for t, k, d in recent if k == "attack" and d.get("monster"))
        states = [d for t, k, d in self.ring if k == "state"]
        last = states[-1] if states else {}
        actions = [d.get("action") for t, k, d in recent if k == "survival"]
        dangers = [d for t, k, d in recent if k == "danger"]
        rep = {"map": event.get("map") or last.get("map"),
               "foes": dict(foes.most_common(5)),
               "cause": foes.most_common(1)[0][0] if foes else None,
               "hp_seen": last.get("hp_pct"), "weight_pct": last.get("weight_pct"),
               "potions_left": last.get("potions"), "survival_actions": actions,
               "max_dps": max((d.get("dps") or 0 for d in dangers), default=None)}
        unknown = [k for k, v in (("кто бил", rep["cause"]), ("запас зелий", rep["potions_left"]),
                                  ("вес", rep["weight_pct"])) if v is None]
        rep["unknown"] = unknown
        parts = [f"Погиб на {rep['map'] or '?'}"]
        if foes:
            parts.append("дрался с " + ", ".join(f"{m}×{n}" for m, n in foes.most_common(3)))
        if rep["potions_left"] is not None:
            parts.append(f"зелий оставалось {rep['potions_left']}")
        if rep["weight_pct"] is not None:
            parts.append(f"вес {rep['weight_pct']}%")
        if actions:
            parts.append("тело пыталось: " + ", ".join(actions))
        if unknown:
            parts.append("неизвестно: " + ", ".join(unknown))
        text = "; ".join(parts) + "."
        self.mind.mem.remember(text, 3)
        self.mind.mem.add_event("death_report", rep)
        self.mind.write_decision({"type": "postmortem", "text": text, **rep})
        log.info("%s", text)
        if rep["cause"]:
            risk = self.mind.mem.get("monster_risk", {})
            r = risk.setdefault(rep["cause"], {"killed_me": 0, "beaten": 0, "last": 0})
            r["killed_me"] += 1
            r["last"] = now
            self.mind.mem.set("monster_risk", risk)
        self.ban_map(rep["map"], now)
        return rep

    def ban_map(self, hmap, now):
        if not hmap or hmap not in self.mind.persona["hunt_maps"]:
            return
        deaths = [json.loads(r[0]).get("map") for r in self.mind.mem.db.execute(
            "SELECT data FROM events WHERE kind = 'death_report' AND ts >= ?", (now - MAP_WINDOW,))]
        if deaths.count(hmap) < MAP_DEATHS:
            return
        bans = self.bans(now)
        bans[hmap] = now + MAP_BAN
        self.mind.mem.set("map_bans", bans)
        self.mind.mem.remember(f"На {hmap} погиб {deaths.count(hmap)} раза за 2 часа — пока туда не хожу.", 3)
        self.mind.mem.add_event("map_banned", {"map": hmap, "until": bans[hmap]})
        log.info("карта %s исключена до %s", hmap, time.strftime("%H:%M", time.localtime(bans[hmap])))

    def bans(self, now=None):
        now = now or self.clock()
        return {m: t for m, t in (self.mind.mem.get("map_bans") or {}).items() if t > now}

    def risky_monsters(self):
        risk = self.mind.mem.get("monster_risk", {})
        return {m: r for m, r in risk.items() if r.get("killed_me")}

    def can_resurrect(self):
        combat = self.mind.state.get("combat") or {}
        applied = combat.get("applied") or {}
        return any("ALL_RESURRECTION" in (v or []) for v in applied.values())
