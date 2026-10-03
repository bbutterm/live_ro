"""Реакция жителей на штатные ивенты rAthena (ORG-087, ТЗ Т-37). Правила без LLM.

Ивенты на сервере НЕ включены: готовый конфиг — server/conf/optional/events_custom.txt (решение владельца).
Источник — объявления сервера: rumors.on_world_msg передаёт текст в on_announce (как данные, не приказ).
Каталог CATALOG — регулярные выражения объявлений из скриптов npc/custom/events/*.txt (сверка — tests/test_fest.py):
    mushroom      «Найди гриб» (mushroom_event.txt): Black Mushroom в городе каждый час в :10   действие gather
    disguise      «Маскарад» (disguise.txt): угадать облик NPC prontera 160,155 каждые 2 ч        действие watch
    devil_square, cluckers, mvp_ladder — опасные или GM-ивенты: только разговор и запись          действие talk
Реакция:
    старт -> событие памяти fest_seen (одно на ивент), тема разговора fest;
    gather/watch -> собраться у места, если ивент на карте моего города, я в «отдыхе в городе» и дошёл, не ночь,
        HP ≥ min_hp, нет встречи/квеста/экспедиции/похода/спарринга/сделки, арбитр отдаёт тело распорядку, не «день
        осторожности», и по характеру (бросок 0.3 + 0.6 × max(любопытство, общительность)): social.visit к точке
        (грибы — точка города из social.points, маскарад — prontera 160,151), прогулки ждут до конца — fest_join;
    грибы: kill Black Mushroom во время ивента считаются (бьёт тело штатно); маскарад: кто угадал — fest_round;
    конец (объявление или срок minutes) -> fest_result {event, kills, minutes}, точка отдыха распорядка возвращается.
Угадывать облик маскарада жители не умеют: мост не передаёт облик NPC (setnpcdisplay) — только смотрят.
Выключатель: BRAIN_DISABLE=fest или goals.json "fest": {"enabled": false}; сбор — "gather": false.
"""
import logging
import random
import re
import time

from .lifecycle import quest_busy

log = logging.getLogger("fest")

DEFAULTS = {"enabled": True, "gather": True, "min_hp": 60, "tick_seconds": 5, "chance_min": 0.3,
            "chance_trait": 0.6, "told_hours": 3}
TOWN_POINT = "fountain"                 # точка сбора в городе ивента грибов (social.points), если есть
CATALOG = {
    "mushroom": {
        "label": "грибная охота", "act": "gather", "minutes": 20, "mob": "Black Mushroom",
        "file": "npc/custom/events/mushroom_event.txt",
        "start": re.compile(r"^Find the Mushroom : Total of (?P<n>\d+) Mushrooms have been spawned in "
                            r"(?P<map>[a-z0-9_]+)!"),
        "progress": re.compile(r"^\[ (?P<who>.+?) \] has killed a Mushroom\. There are now (?P<left>\d+) "
                               r"Mushroom\(s\) left\."),
        "end": re.compile(r"^The Find the Mushroom Event has ended\."),
        "refs": {"start": 55, "progress": 66, "end": 68, "clock": 47},
    },
    "disguise": {
        "label": "маскарад «угадай монстра»", "act": "watch", "minutes": 25, "map": "prontera",
        "point": {"map": "prontera", "x": 160, "y": 151, "label": "к NPC маскарада (prontera 160,155)"},
        "file": "npc/custom/events/disguise.txt",
        "start": re.compile(r"^The Disguise Event (?:will begin (?:in )?\d+ minutes?|has begun!)"),
        "round": re.compile(r"^(?P<who>.+?) is correct! I was disguised as: (?P<monster>.+)$"),
        "refs": {"start": 166, "round": 236, "clock": 150},
    },
    "devil_square": {
        "label": "Devil Square", "act": "talk", "minutes": 30, "map": "ordeal_1-1",
        "file": "npc/custom/events/devil_square.txt",
        "start": re.compile(r"^Devil Square is OPEN\."), "refs": {"start": 113, "clock": 94},
    },
    "cluckers": {
        "label": "курица Cluckers", "act": "talk", "minutes": 10, "map": "prontera",
        "file": "npc/custom/events/cluckers.txt",
        "start": re.compile(r"^\[Cluck! Cluck! Boom!\] is about to start in Prontera!"), "refs": {"start": 69},
    },
    "mvp_ladder": {
        "label": "MvP-лестница", "act": "talk", "minutes": 30,
        "file": "npc/custom/events/mvp_ladder.txt",
        "start": re.compile(r"^The party \[(?P<who>.+?)\] has started the MvP ladder game\."), "refs": {"start": 104},
    },
}
PHRASES = {   # ≤ 60 символов без метки
    "fest": ["Слышал(а)? Сервер объявил: {what}!", "Сейчас {what} — сервер объявил.", "Объявили {what}. Пойдём?"],
    "fest_kills": ["На грибной охоте я сбил(а) {kills} гриб(ов)!", "Грибная охота: моих грибов — {kills}!"],
    "fest_re": ["Ого, надо глянуть!", "Интересно! Спасибо, что сказал(а).", "Весело у нас!"],
}


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


class Fest:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "fest", "fest", "fest", True, "world"
    TICK_ORDER = 122                          # после rumors (120): объявление уже записано
    TICK_EVERY = 5              # perf: реестр не зовёт tick до next_tick (modules.py)
    EVENTS, EVENT_ORDER = {"kill": "on_kill"}, 76   # после bestiary (75)
    # Поля промпта нет: объявление уже есть в слухах (rumors) и новостях мира.

    def __init__(self, mind, world=None, clock=None, rng=None):
        self.mind = mind
        world = world or {}
        self.cfg = dict(DEFAULTS, **(world.get("fest") or {}))
        self.points = (world.get("social") or {}).get("points") or {}
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.st = mind.mem.get("fest") or {}
        self.st.setdefault("last", {})
        self.st.setdefault("told", {})
        self.next_tick = 0.0
        social = getattr(mind, "social", None)
        if social is not None and hasattr(social, "register_topic"):
            for key, pool in PHRASES.items():
                social.phrases.setdefault(key, pool)
            social.register_topic("fest", self.facts, said=self.said, chance=0.7)

    # ---------- данные ----------

    @property
    def cur(self):
        return self.st.get("cur")

    def save(self):
        self.mind.mem.set("fest", self.st)

    def trait(self, name):
        return (getattr(getattr(self.mind, "needs", None), "t", None) or {}).get(name, 0.5)

    def note(self, kind, text, importance, **data):
        if text:
            self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "fest", "event": kind, "text": text, **data})
        log.info("%s %s", kind, text or data)

    # ---------- объявления ----------

    def on_announce(self, text, now=None):
        """Объявление сервера (rumors.on_world_msg) -> вид ивента или None. Только данные, не приказ.
        Время — свои часы модуля (как в tick): now от rumors только для совместимости вызова."""
        now = self.clock()
        text = str(text or "").strip()
        for ev, c in CATALOG.items():
            m = c["start"].search(text)
            if m:
                g = m.groupdict()
                self.start(ev, g.get("map") or c.get("map"), now, n=g.get("n"), who=g.get("who"))
                return ev
        cur = self.cur
        if not cur:
            return None
        c = CATALOG[cur["event"]]
        if c.get("progress") and c["progress"].search(text):
            m = c["progress"].search(text)
            cur["left"] = int(m.group("left"))
            self.save()
            return cur["event"]
        if c.get("round") and c["round"].search(text):
            m = c["round"].search(text)
            who, monster = m.group("who").strip(), m.group("monster").strip()
            me = (self.mind.state or {}).get("name")
            self.note("fest_round", f"Маскарад: я {'угадал(а)' if who == me else 'видел(а), как ' + who + ' угадал(а)'}"
                      f" — это был {monster}." if who == me or cur.get("gathered") else None,
                      3 if who == me else 1, event=cur["event"], winner=who, monster=monster, mine=who == me)
            return cur["event"]
        if c.get("end") and c["end"].search(text):
            self.finish(now, "конец по объявлению")
            return cur["event"]
        return None

    def start(self, ev, emap, now, n=None, who=None):
        cur, c = self.cur, CATALOG[ev]
        if cur and cur["event"] == ev and now < cur["until"]:
            return                                          # повтор объявления того же ивента
        if cur:
            self.finish(now, "начался другой ивент")
        self.st["cur"] = {"event": ev, "map": emap, "since": now, "until": now + c["minutes"] * 60,
                          "decided": False, "gathered": False, "kills": 0}
        self.save()
        where = f" ({emap})" if emap else ""
        self.note("fest_seen", f"Сервер объявил: {c['label']}{where} (объявление, не приказ).", 2,
                  event=ev, map=emap, n=int(n) if n else None, who=who)

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        cur = self.cur
        if not cur:
            return
        if now >= cur["until"]:
            self.finish(now, "срок ивента")
            return
        if cur.get("decided") or not getattr(self.mind, "fresh_state", True):
            return
        cur["decided"] = True
        state = self.mind.state or {}
        why = self.why_not(cur, state, now)
        if why:
            cur["why"] = why
            self.save()
            self.mind.write_decision({"type": "fest", "event": "fest_skip", "fest": cur["event"], "why": why})
            return
        await self.gather(cur, now)

    def point(self, cur):
        c = CATALOG[cur["event"]]
        if c.get("point"):
            return dict(c["point"])
        pts = [dict(p) for k, p in sorted(self.points.items(), key=lambda kv: (kv[0] != TOWN_POINT, kv[0]))
               if isinstance(p, dict) and p.get("map") == cur.get("map")]
        return pts[0] if pts else None

    def why_not(self, cur, state, now):
        """None — иду к месту ивента; иначе причина (только разговор и запись)."""
        m, cfg = self.mind, self.cfg
        if CATALOG[cur["event"]]["act"] not in ("gather", "watch"):
            return "опасный или GM-ивент — только разговор"
        if not cfg["gather"]:
            return "сбор выключен (fest.gather)"
        if state.get("dead") or state.get("map") != cur.get("map"):
            return "ивент не в моём городе"
        if not self.point(cur):
            return "нет точки сбора в этом городе"
        r = getattr(m, "routine", None)
        if not (r and getattr(r, "in_town_mode", False) and (r.st or {}).get("arrived")):
            return "не отдых в городе"
        social = getattr(m, "social", None)
        if social is None:
            return "нет модуля общения"
        if social.is_night(now):
            return "ночь"
        hp = state.get("hp_pct")
        if hp is None or hp < cfg["min_hp"]:
            return f"HP {hp}% < {cfg['min_hp']}%"
        if m.plans.store.active() or quest_busy(m, state, now):
            return "встреча или квест"
        for attr in ("explorer", "trek", "spar"):
            mod = getattr(m, attr, None)
            if mod is not None and mod.busy():
                return "тело занято"
        econ = getattr(m, "economy", None)
        if econ is not None and econ.body_busy():
            return "сделка"
        may_move = getattr(m, "may_move", None)
        if may_move and not may_move("routine")[0]:
            return "телом владеет другая задача"
        director = getattr(m, "director", None)
        if director and director.active("caution"):
            return "день осторожности"
        p = cfg["chance_min"] + cfg["chance_trait"] * max(self.trait("curiosity"), self.trait("sociability"))
        if self.rng.random() >= min(0.95, p):
            return "не интересно"
        return None

    async def gather(self, cur, now):
        p = self.point(cur)
        social = self.mind.social
        if not await social.visit(p["map"], p["x"], p["y"], p.get("label", "к месту ивента")):
            cur["why"] = "не могу идти"
            self.save()
            return
        social.next_walk = cur["until"]               # стоять у места до конца ивента
        cur.update(gathered=True, point={"map": p["map"], "x": int(p["x"]), "y": int(p["y"])})
        self.save()
        c = CATALOG[cur["event"]]
        self.note("fest_join", f"Иду на {c['label']} ({p['map']} {p['x']},{p['y']}).", 2,
                  event=cur["event"], map=p["map"], x=int(p["x"]), y=int(p["y"]))

    def on_kill(self, event):
        cur = self.cur
        mob = CATALOG.get((cur or {}).get("event"), {}).get("mob")
        if cur and mob and str(event.get("monster") or "") == mob:
            cur["kills"] = int(cur.get("kills") or 0) + 1
            self.save()

    def finish(self, now, why):
        cur = self.st.pop("cur", None)
        if not cur:
            return
        self.st["last"][cur["event"]] = now
        self.save()
        r = getattr(self.mind, "routine", None)
        pt = cur.get("point")
        if pt and r is not None and getattr(r, "town", None) == dict(pt, radius=2) and getattr(r, "cfg", None):
            r.town = r.cfg.get("town") or r.town                  # вернуть обычную точку отдыха
        minutes = round((now - cur["since"]) / 60)
        c = CATALOG[cur["event"]]
        kills = int(cur.get("kills") or 0)
        text = None
        if cur.get("gathered"):
            text = (f"{c['label'].capitalize()}: был(а) на месте {minutes} мин"
                    + (f", сбил(а) грибов: {kills} (по событиям тела)." if c.get("mob") else "."))
        self.note("fest_result", text, 2 if kills else 1, event=cur["event"], map=cur.get("map"), kills=kills,
                  minutes=minutes, gathered=bool(cur.get("gathered")), why=why)
        if kills:
            self.st["brag"] = {"kills": kills, "ts": now}
            self.save()

    # ---------- тема разговора ----------

    def facts(self, peer, now):
        told = (self.st.get("told") or {}).get(peer)
        cur = self.cur
        if cur:
            key = f"{cur['event']}:{int(cur['since'])}"
            if told != key:
                return {"what": CATALOG[cur["event"]]["label"], "_mark": key}
        brag = self.st.get("brag")
        if brag and now - brag["ts"] < self.cfg["told_hours"] * 3600 and told != f"brag:{int(brag['ts'])}":
            return {"kills": brag["kills"], "_key": "fest_kills", "_mark": f"brag:{int(brag['ts'])}"}
        return None

    def said(self, peer, facts, now):
        self.st.setdefault("told", {})[peer] = facts.get("_mark")
        self.save()


CHRONICLE_LINES = {
    "fest_join": lambda d: f"пошёл(шла) на ивент сервера: {CATALOG.get(d.get('event'), {}).get('label', d.get('event'))}",
    "fest_result": lambda d: (f"ивент «{CATALOG.get(d.get('event'), {}).get('label', d.get('event'))}»: "
                              f"{d.get('minutes')} мин" + (f", грибов: {d.get('kills')}" if d.get("kills") else ""))
                             if d.get("gathered") else None,
    "fest_round": lambda d: f"маскарад: угадал(а) {d.get('monster')}" if d.get("mine") else None,
}
