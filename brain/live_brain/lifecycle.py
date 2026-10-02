"""Явные состояния жителя и арбитр движения тела (AUT-001, 002, 003, 005). Без LLM.

Состояние вычисляется каждый тик из данных тела и модулей мозга (первое подходящее):
    SLEEPING    — распорядок: сон (житель вышел из игры по своей воле, ORG-012)
    OFFLINE     — нет свежего состояния тела дольше STALE_SEC (или нет связи)
    DEAD        — тело сообщает dead
    ESCAPING    — за ESCAPE_SEC было событие survival/danger/escape
    BLOCKED     — распорядок признал тупик (застревание после лестницы, восстановление невозможно)
    RECOVERING  — после смерти/крыла/низкого HP: отдых до min_hp_to_hunt
    QUEST       — идёт этап квеста смены профессии (плагин jobChange)
    SOCIAL      — активный план встречи или передача вещей жителю
    SERVICING   — продажа, закупка, склад, разговор с NPC, сделка
    FIGHTING    — бой (activity attack или атака за FIGHT_SEC)
    TRAVELING   — идёт по маршруту или не на карте назначения
    RESTING     — отдых в городе / сидит
    HUNTING     — на карте охоты
Переход пишется в decisions.jsonl (type status: from, to, why) и в kv "status".

Арбитр (AUT-001, 005): двигать тело может один владелец; приоритет
    survival (мёртв/спасается) > plan (встреча) > economy (передача) > party (поводок/помощь) > routine.
may_move(владелец) — False, если сейчас активен владелец с большим приоритетом.
"""
import logging
import time

log = logging.getLogger("lifecycle")

STALE_SEC = 60
ESCAPE_SEC = 10
FIGHT_SEC = 10
SERVICE = ("storageAuto", "sellAuto", "buyAuto", "NPC", "deal", "items_take", "take")
TRAVEL = ("route", "mapRoute", "move", "follow")
PRIORITY = ("survival", "plan", "economy", "party", "routine")
QUEST_SENT_SEC = 30       # review2: этап jobChange отправлен, а state.job_change.running ещё не пришёл


def quest_busy(mind, state, now):
    """review2: плагин jobChange занят — арбитр жизненного цикла (с окном отправки) или, без него, state."""
    life = getattr(mind, "life", None)
    return life.quest_busy(now) if life else bool((state.get("job_change") or {}).get("running")
                                                  or (state.get("refine") or {}).get("running")    # refine:
                                                  or (state.get("spar") or {}).get("running"))     # review4:


class Lifecycle:
    def __init__(self, mind, clock=None):
        self.mind = mind
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.current = mind.mem.get("status", {}).get("state")
        self.last_alarm = 0.0       # survival/danger/escape
        self.last_fight = 0.0

    def on_event(self, kind):
        now = self.clock()
        if kind in ("survival", "danger", "escape"):
            self.last_alarm = now
        elif kind in ("attack", "kill"):
            self.last_fight = now

    def compute(self, now=None):
        now = now or self.clock()
        m, s = self.mind, self.mind.state
        if m.routine and m.routine.st and m.routine.st.get("mode") == "sleep":
            return "SLEEPING", "сон до " + time.strftime("%H:%M", time.localtime(m.routine.st.get("wake_at", 0)))
        if not m.fresh_state:
            return "OFFLINE", "нет свежего состояния тела"
        if s.get("dead"):
            return "DEAD", "тело сообщает смерть"
        if now - self.last_alarm < ESCAPE_SEC:
            return "ESCAPING", "survival: опасность"
        r = m.routine
        st = (r.st if r else None) or {}
        if st.get("blocked"):
            return "BLOCKED", st["blocked"]
        if st.get("recover"):
            return "RECOVERING", "восстановление после смерти или опасности"
        if (s.get("job_change") or {}).get("running"):
            return "QUEST", f"этап смены профессии {(s.get('job_change') or {}).get('stage')}"
        if m.plans.store.active():
            return "SOCIAL", "план встречи"
        econ = getattr(m, "economy", None)
        if (econ and econ.giving) or s.get("give"):
            return "SOCIAL", "передача вещей жителю"
        activity = s.get("activity") or ""
        if activity in SERVICE:
            return "SERVICING", activity
        if activity == "attack" or now - self.last_fight < FIGHT_SEC:
            return "FIGHTING", "бой"
        if activity in TRAVEL or (s.get("lock_map") and s.get("map") != s.get("lock_map")):
            return "TRAVELING", activity or f"к {s.get('lock_map')}"
        if st.get("mode") == "town" or s.get("sitting"):
            return "RESTING", "отдых"
        return "HUNTING", s.get("map") or "?"

    def tick(self, now=None):
        now = now or self.clock()
        new, why = self.compute(now)
        if new != self.current:
            old, self.current = self.current, new
            self.mind.mem.set("status", {"state": new, "why": why, "since": now})
            self.mind.write_decision({"type": "status", "from": old, "to": new, "why": why})
            log.info("состояние: %s -> %s (%s)", old, new, why)
        return new

    # ---------- арбитр ----------

    def quest_busy(self, now=None):
        """review2: плагин jobChange занят (дом: Kafra, карьера: квест) — по state или только что отправлен этап.
        Один плагин на оба модуля: без окна отправки дом и карьера в одном такте шлют по этапу, а распорядок и
        экспедиция успевают переставить lockMap под уже начатым этапом."""
        now = now or self.clock()
        return bool((self.mind.state.get("job_change") or {}).get("running")
                    or (self.mind.state.get("refine") or {}).get("running")      # refine: заточка ведёт тело (ORG-072)
                    or self.spar_busy()                                          # review4: спарринг — тоже «plan»
                    or now - (getattr(self.mind, "job_change_sent", 0) or 0) < QUEST_SENT_SEC)

    def spar_busy(self):
        """review4: спарринг ведёт тело (владелец «plan», как этап квеста): дом, карьера, травник, стрелы и заточка
        спрашивают may_move("plan") — им же он разрешён, поэтому спарринг входит в quest_busy (их общий запрет)."""
        spar = getattr(self.mind, "spar", None)
        return bool((spar and spar.busy()) or (self.mind.state.get("spar") or {}).get("running"))

    def active_owners(self, now=None):
        now = now or self.clock()
        m, s = self.mind, self.mind.state
        owners = set()
        if s.get("dead") or now - self.last_alarm < ESCAPE_SEC:
            owners.add("survival")
        if m.plans.store.active() or self.quest_busy(now):   # review2: и только что отправленный этап
            owners.add("plan")                       # встреча или этап квеста профессии
        econ = getattr(m, "economy", None)
        if (econ and econ.body_busy()) or s.get("give") or s.get("buy"):   # review: и торговля (offer_sell/offer_buy)
            owners.add("economy")
        party = getattr(m, "party", None)
        if party and (party.waiting_since is not None or party.help_until > now):
            owners.add("party")
        spar = getattr(m, "spar", None)                      # spar: спарринг ведёт тело (ORG-061) — как этап квеста
        if (spar and spar.busy()) or (s.get("spar") or {}).get("running"):   # spar:
            owners.add("plan")                               # spar:
        return owners

    def may_move(self, owner, now=None):
        owners = self.active_owners(now)
        higher = PRIORITY[:PRIORITY.index(owner)]
        blocker = next((o for o in higher if o in owners), None)
        return blocker is None, blocker
