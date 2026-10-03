"""Угощение по поводу (ORG-099, ТЗ Т-45 в docs/IDEAS2.md). Правила без LLM.

Видимый поступок вместо слов (риск R12) и выход для запасов (R8): житель отдаёт другу зелья по поводу, а отданное
потом докупает buyAuto у NPC — это и есть сток (событие npc_bought, Т-46).

Повод (occasions):
    birthday    сегодня день рождения жителя P (calendar.day().birthdays, поле born в roster.json);
    reconciled  я помирился с P (событие society_reconciled) за occasion_hours;
    graduated   мой ученик P выпустился (событие mentor_graduated, я наставник) за occasion_hours.
Условия: P виден рядом (economy.sender_near, ≤ 8 клеток); режим отдыха распорядка, тело свободно (нет передачи,
сделки, письма, лавки, квеста, сна, встречи); щедрость ≥ min_generosity; одно угощение на cooldown_days;
один повод (вид + житель + день/событие) — один раз (kv treat.done).
Что: первый из items (ID зелий, которые считает плагин economy), которого у меня больше keep (и больше
economy.share[id].keep) — amount штук или сколько есть сверх запаса; стоимость по цене NPC (prices.npc_buy) не
больше budget_share зени сверх keep_zeny и копилки мечты.
Исполнение — общий канал экономики: economy.giving (+ поля treat, zeny) и действие give; итог — give_result ok ->
gift_given (как раньше) и treat_given {peer, item, amount, zeny, occasion} в economy.on_give_result (# sinks:).
Выключатель: goals.json "treat": {"enabled": false} или BRAIN_DISABLE=treat.
"""
import json
import logging
import secrets
import time

log = logging.getLogger("treat")

DEFAULTS = {"enabled": True, "tick_seconds": 30, "items": [503, 502, 501], "amount": 3, "keep": 5,
            "budget_share": 0.05, "cooldown_days": 7, "occasion_hours": 24, "min_generosity": 0.3,
            "occasions": ["birthday", "reconciled", "graduated"]}
WORDS = {"birthday": "С днём рождения, {p}! Угощайся.", "reconciled": "{p}, мир? Держи, угощаю.",
         "graduated": "{p}, с выпуском! Это тебе в дорогу."}
TITLES = {"birthday": "день рождения", "reconciled": "примирение", "graduated": "выпуск ученика"}


class Treat:
    # реестр модулей (modules.py, W8): создание и тик
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "treat", "treat", "treat", True, "world"
    REQUIRES = ("economy", "peers")             # канал передачи и цены — у экономики; угощать некого — не создаётся
    TICK_ORDER = 149                            # после снаряжения (148), до целей недели (150)
    WARMUP = 60                 # warmup: необязательная инициатива — через 60–120 с после пробуждения (modules.py)
    TICK_EVERY = 30

    def __init__(self, mind, world=None, clock=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("treat") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.st = mind.mem.get("treat") or {}
        self.next_tick = 0.0

    def save(self):
        self.mind.mem.set("treat", self.st)

    # ---------- данные ----------

    @property
    def econ(self):
        return getattr(self.mind, "economy", None)

    def generosity(self):
        return float((getattr(getattr(self.mind, "needs", None), "t", None) or {}).get("generosity", 0.5))

    def recent(self, kind, since):
        rows = self.mind.mem.db.execute("SELECT ts, data FROM events WHERE kind = ? AND ts >= ? ORDER BY id DESC "
                                        "LIMIT 5", (kind, since)).fetchall()
        out = []
        for ts, data in rows:
            try:
                out.append((ts, json.loads(data) or {}))
            except ValueError:
                continue
        return out

    def occasions(self, now):
        """[(вид, житель, ключ)] — поводы угостить сейчас."""
        peers = set(getattr(self.mind.ctx, "peers", ()) or ())
        me = (self.mind.state or {}).get("name")
        since = now - self.cfg["occasion_hours"] * 3600
        out = []
        kinds = self.cfg["occasions"]
        cal = getattr(self.mind, "calendar", None)
        if "birthday" in kinds and cal is not None:
            day = cal.day(now)
            out += [("birthday", p, f"birthday:{p}:{day['date']}") for p in day.get("birthdays") or []
                    if p in peers and p != me]
        if "reconciled" in kinds:
            out += [("reconciled", d.get("peer"), f"reconciled:{d.get('peer')}:{int(ts)}")
                    for ts, d in self.recent("society_reconciled", since) if d.get("peer") in peers]
        if "graduated" in kinds:
            out += [("graduated", d.get("mentee"), f"graduated:{d.get('mentee')}:{int(ts)}")
                    for ts, d in self.recent("mentor_graduated", since) if d.get("mentee") in peers]
        done = set(self.st.get("done") or [])
        return [o for o in out if o[2] not in done]

    def spare(self, state):
        econ = self.econ
        keep = int((econ.market or {}).get("keep_zeny", 5000))
        return int(state.get("zeny") or 0) - keep - econ.reserve()

    def choose(self, state):
        """(ID, штук, стоимость) — что отдать, или None: зелий мало или дорого для кошелька."""
        econ = self.econ
        budget = self.cfg["budget_share"] * self.spare(state)
        for iid in self.cfg["items"]:
            keep = max(int(self.cfg["keep"]), int((econ.share.get(str(iid)) or {}).get("keep", 0)))
            n = min(int(self.cfg["amount"]), econ.have(str(iid), state) - keep)
            if n <= 0:
                continue
            cost = econ.prices.npc_buy(iid, n)
            if 0 < cost <= budget:
                return iid, n, cost
        return None

    def body_free(self, state):
        econ = self.econ
        routine = getattr(self.mind, "routine", None)
        if routine and (not routine.in_town_mode or getattr(routine, "sleeping", False)):
            return False
        if econ.giving or econ.mailing or econ.busy_trade() or econ.body_busy() or econ.body_elsewhere():
            return False
        plans = getattr(self.mind, "plans", None)
        if plans and plans.store.active():
            return False
        return not (state.get("give") or (state.get("vend") or {}).get("open") or state.get("dead"))

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        state = self.mind.state or {}
        if not getattr(self.mind, "fresh_state", True) or not state.get("items") or not isinstance(state.get("zeny"), int):
            return
        if now - (self.st.get("last") or 0) < self.cfg["cooldown_days"] * 86400:
            return
        if self.generosity() < self.cfg["min_generosity"] or not self.body_free(state):
            return
        for kind, peer, key in self.occasions(now):
            if not self.econ.sender_near(peer, state):
                continue
            pick = self.choose(state)
            if not pick:
                return                                   # зелий мало или кошелёк пуст — не угощаем никого
            await self.give(kind, peer, key, pick, now)
            return

    async def give(self, kind, peer, key, pick, now):
        iid, n, cost = pick
        econ = self.econ
        self.st.update(last=now, done=((self.st.get("done") or []) + [key])[-50:])
        self.save()
        econ.giving = {"id": secrets.token_hex(3), "to": peer, "item": str(iid), "amount": n, "since": now,
                       "treat": kind, "zeny": cost}
        self.mind.write_decision({"type": "treat", "event": "give", "peer": peer, "occasion": kind, "item": iid,
                                  "amount": n, "zeny": cost})
        await self.mind.execute([{"action": "whisper", "to": peer, "text": WORDS[kind].format(p=peer)}],
                                source="treat", reason=f"угощение: {TITLES[kind]}", protocol=True)
        await self.mind.execute([{"action": "give", "to": peer, "item": iid, "amount": n}], source="treat",
                                reason=f"угощение: {TITLES[kind]} — {n} × {econ.prices.name(iid)} для {peer}",
                                protocol=True)


CHRONICLE_LINES = {
    "treat_given": lambda d: (f"угостил(а) {d.get('peer')} ({TITLES.get(d.get('occasion'), d.get('occasion'))}): "
                              f"{d.get('amount')} × {d.get('item')}, ~{d.get('zeny')}z"),
}
