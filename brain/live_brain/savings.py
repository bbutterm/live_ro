"""Копилка мечты и банк (ORG-073, ТЗ Т-18). Правила без LLM; факт — только состояние тела и пакеты сервера.

Цель копилки — сколько зени нужно мечте жителя (dream.save_target(), ORG-081): job2 — снаряжение новой профессии,
explorer — крылья и телепорт Kafra, pet — корм и приручение, rich — вся сумма мечты (goals.json dream.save).
Нет мечты или цели — нет копилки. Состояние — kv savings {key, bank, milestone, fails, off_until, last_op, pending}.

    Отложено (reserve)  = min(зени в кармане, цель − банк): economy не тратит их на необязательное — подарок зени
                          жителю ([need:..:z:..], почтой тоже) и покупку на перепродажу; покупки из списка нужд
                          (зелья, предметы карьеры, market.wish) — по-прежнему (economy.py, блоки # dreams:).
    Прогресс            = min(цель, карман + банк) / цель; вехи 25/50/75/100 % -> событие savings_progress
                          (100 % — воспоминание важности 4 «накопил(а) на мечту»), заметка в отчёте и промпте.

Банк rAthena (goals.json savings.bank, по умолчанию false — в игре не проверено). Сервер: feature.banking: on
(conf/battle/feature.conf), пакеты 0x9a7/0x9a9/0x9ab при PACKETVER >= 20130717, banking_state_enforce: no —
открывать окно банка не нужно; вклад — на аккаунт (#BANKVAULT). Мост (brainBridge.pl): bank_check {},
bank_deposit {zeny}, bank_withdraw {zeny} -> sendBanking* OpenKore; ответы сервера -> события bank_balance {vault},
bank_result {op, ok, reason, vault, zeny}. Правила:
    только в городе отдыха, в режиме отдыха распорядка, тело свободно (нет сделки, передачи, письма, лавки, сна,
    квеста), не чаще bank_gap_minutes, одна операция в пути (ответ ждём BANK_TIMEOUT с);
    в начале сессии — bank_check (баланс неизвестен — ничего не кладём);
    карман < keep_zeny экономики и в банке есть — снять до pocket (зелья важнее мечты);
    карман > pocket (keep_zeny × 2) — вклад излишка, но не больше «цель − банк», не меньше min_deposit;
    три отказа сервера подряд — банк выключен на сутки (воспоминание note).
Факт вклада/снятия — только bank_result ok (пакет сервера): события bank_deposit / bank_withdraw в памяти.
Выключатель: BRAIN_DISABLE=savings или goals.json "savings": {"enabled": false}; без мечты (dream) модуль не создаётся.
"""
import logging
import time

log = logging.getLogger("savings")

DEFAULTS = {"enabled": True, "tick_seconds": 30, "bank": False, "bank_gap_minutes": 10, "min_deposit": 5000,
            "max_op": 1_000_000}
MILESTONES = (25, 50, 75, 100)
BANK_TIMEOUT = 60
MAX_FAILS = 3
KEEP_ZENY = 5000                     # economy.MARKET keep_zeny по умолчанию


class Savings:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "savings", "savings", "savings", True, "world"
    REQUIRES = ("dream",)                       # цель копилки — от мечты (Dream стоит выше в MODULES)
    TICK_ORDER = 147                            # после мечты (145), до целей недели (150)
    EVENT_ORDER = 70
    EVENTS = {"bank_result": {"call": "on_bank", "kind": True, "own": True},
              "bank_balance": {"call": "on_bank", "kind": True, "own": True}}
    PROMPT = [("копилка", "summary", 135)]      # рядом с хозяйством (130) и рынком (140)

    def __init__(self, mind, world=None, clock=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("savings") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.st = mind.mem.get("savings") or {}
        self.next_tick = 0.0
        self.checked = False                    # баланс банка известен в этой сессии мозга

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("savings", self.st)

    def goal(self):
        dream = getattr(self.mind, "dream", None)
        if not dream:
            return 0, None, None
        target, text = dream.save_target()
        st = dream.st
        return int(target or 0), text, (f"{st.get('kind')}:{int(st.get('since') or 0)}" if target else None)

    def keep(self):
        econ = getattr(self.mind, "economy", None)
        return int((getattr(econ, "market", None) or {}).get("keep_zeny", KEEP_ZENY))

    def pocket(self):
        return 2 * self.keep()

    def bank(self):
        return int(self.st.get("bank") or 0)

    def reserve(self, state=None):
        """Зени в кармане, отложенные на мечту: economy не тратит их на необязательное."""
        state = state if state is not None else (self.mind.state or {})
        target, _, _ = self.goal()
        if not target:
            return 0
        return max(0, min(int(state.get("zeny") or 0), target - self.bank()))

    def progress(self, state=None):
        state = state if state is not None else (self.mind.state or {})
        target, text, _ = self.goal()
        if not target:
            return None
        saved = min(target, int(state.get("zeny") or 0) + self.bank())
        return {"goal": text, "target": target, "saved": saved, "pct": int(100 * saved / target), "bank": self.bank()}

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        state = self.mind.state or {}
        if not getattr(self.mind, "fresh_state", True) or state.get("dead") or not isinstance(state.get("zeny"), int):
            return
        self.milestones(state)
        if self.cfg["bank"]:
            await self.bank_tick(state, now)

    def milestones(self, state):
        p = self.progress(state)
        _, _, key = self.goal()
        if key != self.st.get("key"):
            self.st.update(key=key, milestone=0)
            if p:                                      # новая цель: вехи, уже пройденные, — молча
                self.st["milestone"] = max([m for m in MILESTONES if p["pct"] >= m] or [0])
            self.save()
            return
        if not p:
            return
        reached = max([m for m in MILESTONES if p["pct"] >= m] or [0])
        if reached <= (self.st.get("milestone") or 0):
            return
        self.st["milestone"] = reached
        self.save()
        self.mind.mem.add_event("savings_progress", {"goal": p["goal"], "pct": reached, "saved": p["saved"],
                                                     "target": p["target"]})
        if reached >= 100:
            self.mind.mem.remember(f"Накопил(а) на мечту «{p['goal']}»: {p['target']} зени (по данным игры).", 4)
        self.mind.write_decision({"type": "savings", "event": "progress", "pct": reached, "saved": p["saved"],
                                  "target": p["target"]})

    def body_free(self, state):
        """Город отдыха, режим отдыха, тело ничем не занято (сделка, передача, письмо, лавка, квест, сон)."""
        dream = getattr(self.mind, "dream", None)
        town = dream.home_town() if dream else None
        if not town or state.get("map") != town:
            return False
        routine = getattr(self.mind, "routine", None)
        if routine and (not routine.in_town_mode or getattr(routine, "sleeping", False)):
            return False
        econ = getattr(self.mind, "economy", None)
        if econ and (econ.body_busy() or econ.mail_busy() or econ.busy_trade() or econ.body_elsewhere()):
            return False
        plans = getattr(self.mind, "plans", None)
        if plans and plans.store.active():
            return False
        return not (state.get("give") or (state.get("vend") or {}).get("open")
                    or (state.get("job_change") or {}).get("running"))

    async def bank_tick(self, state, now):
        pend = self.st.get("pending")
        if pend:
            if now - pend.get("since", 0) < BANK_TIMEOUT:
                return
            self.st["pending"] = None
            self.fail(f"нет ответа сервера на {pend.get('op')} за {BANK_TIMEOUT} с", now)
        if now < (self.st.get("off_until") or 0) or now - (self.st.get("last_op") or 0) < self.cfg["bank_gap_minutes"] * 60:
            return
        if not self.body_free(state):
            return
        if not self.checked:
            await self.send("bank_check", None, now, "банк: узнать вклад")
            return
        zeny, bank = int(state["zeny"]), self.bank()
        target, text, _ = self.goal()
        if zeny < self.keep() and bank > 0:
            amount = min(bank, self.pocket() - zeny, self.cfg["max_op"])
            await self.send("bank_withdraw", amount, now, f"банк: снять {amount}z — в кармане мало на зелья")
            return
        amount = min(zeny - self.pocket(), max(0, target - bank), self.cfg["max_op"])
        if target and amount >= self.cfg["min_deposit"]:
            await self.send("bank_deposit", amount, now, f"банк: отложить {amount}z на мечту «{text}»")

    async def send(self, op, amount, now, reason):
        a = {"action": op}
        if amount is not None:
            a["zeny"] = int(amount)
        self.st.update(pending={"op": op, "zeny": amount, "since": now}, last_op=now)
        self.save()
        await self.mind.execute([a], source="savings", reason=reason, protocol=True)

    def fail(self, why, now):
        self.st["fails"] = int(self.st.get("fails") or 0) + 1
        self.mind.mem.add_event("bank_failed", {"why": why, "fails": self.st["fails"]})
        if self.st["fails"] >= MAX_FAILS:
            self.st.update(off_until=now + 86400, fails=0)
            self.mind.mem.remember(f"Банк не отвечает ({why}) — до завтра коплю только в кармане.", 2, kind="note")
        self.save()

    # ---------- события моста ----------

    def on_bank(self, kind, event):
        now = self.clock()
        vault = event.get("vault")
        if kind == "bank_balance":
            if isinstance(vault, int):
                self.st["bank"] = vault
                self.checked = True
                if (self.st.get("pending") or {}).get("op") == "bank_check":
                    self.st["pending"] = None
                self.save()
            return
        pend = self.st.get("pending") or {}
        op = event.get("op")
        self.st["pending"] = None
        if event.get("ok") and isinstance(vault, int):
            amount = abs(vault - self.bank()) if self.checked else pend.get("zeny")   # баланс знали — по разнице
            self.st.update(bank=vault, fails=0)
            self.checked = True
            kind_ev = "bank_deposit" if op == "deposit" else "bank_withdraw"
            self.mind.mem.add_event(kind_ev, {"zeny": amount, "vault": vault, "pocket": event.get("zeny")})
            self.mind.mem.remember(("Положил(а) в банк" if op == "deposit" else "Снял(а) из банка")
                                   + f" {amount}z, в банке {vault}z (сервер подтвердил).", 2)
            self.mind.write_decision({"type": "savings", "event": kind_ev, "zeny": amount, "vault": vault})
            self.save()
        else:
            self.fail(f"сервер: {op} код {event.get('reason')}", now)

    # ---------- наружу ----------

    def summary(self):
        p = self.progress()
        if not p:
            return None
        out = {"копит_на": p["goal"], "отложено": f"{p['saved']}/{p['target']} ({p['pct']} %)"}
        if self.cfg["bank"] or p["bank"]:
            out["в_банке"] = p["bank"]
        return out


CHRONICLE_LINES = {
    "savings_progress": lambda d: f"копит на мечту «{d.get('goal')}»: {d.get('pct')} %",
    "bank_deposit": lambda d: f"положил(а) в банк {d.get('zeny')}z (в банке {d.get('vault')}z)",
    "bank_withdraw": lambda d: f"снял(а) из банка {d.get('zeny')}z",
}
