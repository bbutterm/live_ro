"""Наследие и уход на покой (ORG-083, ТЗ Т-36). Правила без LLM, только факты памяти и тела.

ПО УМОЛЧАНИЮ ВЫКЛЮЧЕНО (goals.json "legacy": {"enabled": false}): решение об уходе жителя — за владельцем.

Житель, проживший долгий путь, решает «уйти на покой»: передаёт ценное наследнику, пишет прощальную главу мемуаров,
событие в шине и летописи. Сам уход — только совет владельцу в переписи (scripts/lab census): active: false в
brain/world/roster.json ставит владелец. Модуль НЕ останавливает процессы, НЕ трогает реестр, БД сервера и профили.

Условия (любое, проверка раз в check_hours):
    мечта сбылась после долгого пути (dream_done.days ≥ long_dream_days);
    уровень ≥ elder_level;
    возраст ≥ elder_days — от born в roster.json (mentor.roster_born), иначе от первого события памяти.
Не сейчас: помолвка без брака (wed), ученик под опекой (mentor), тело занято (квест, экспедиция, спарринг, встреча,
сделка, почта).
Фазы kv legacy: ready -> bequest (посылки) -> farewell -> retired.
Наследник: последний ученик (kv mentor.history, роль mentor), затем супруг/жених (wed.partner), затем друг с
наибольшим отношением ≥ heir_min; в ссоре — нет; наследника нет — уход без передачи.
Посылки: карты из рюкзака (prices.is_card) — по стопке на письмо, зени сверх keep_zeny (≤ max_zeny, сбор почты 2 %
и 2500 z за предмет) — не больше max_parcels писем, по одному через economy.send_mail(kind gift), когда почта свободна.
«Передал» — только события gift_given (ответ сервера), их и считает прощание.
Прощание: kv legacy_farewell (Memoir дописывает главу «Прощание» в memoir.md — memoir.farewell_lines), событие legacy_retired (шина 5),
воспоминание 5, тема разговора legacy. Выключатель: goals.json legacy.enabled; BRAIN_DISABLE=legacy.
"""
import json
import logging
import time

from .lifecycle import quest_busy
from .mentor import roster_born
from .prices import Prices

log = logging.getLogger("legacy")

DEFAULTS = {"enabled": False, "tick_seconds": 60, "check_hours": 24, "elder_level": 90, "elder_days": 90,
            "long_dream_days": 30, "heir_min": 4, "keep_zeny": 20000, "max_zeny": 100000, "max_parcels": 3,
            "min_zeny_parcel": 1000}
MAIL_TAX_ITEM = 2500           # rAthena conf/battle/misc.conf mail_attachment_price (как economy.MAIL_TAX_ITEM)
PHRASES = {   # ≤ 60 символов без метки
    "legacy": ["Я ухожу на покой. Наследник — {heir}.", "Пора на покой. Всё ценное — {heir}.",
               "Ухожу на покой. Береги мир, {heir} в курсе."],
    "legacy_alone": ["Я ухожу на покой. Спасибо за всё.", "Пора на покой. Было хорошо."],
    "legacy_re": ["Спасибо тебе за всё!", "Будем помнить. Отдыхай!", "Заслужил(а). Счастливо!"],
}


def _loads(text):
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


class Legacy:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "legacy", "legacy", "legacy", False, ("peers",), "world"
    TICK_ORDER = 232                          # после мемуаров (230)
    TICK_EVERY = 60             # perf: реестр не зовёт tick до next_tick (modules.py)
    # Поля промпта нет: выключенный по умолчанию модуль добавлял бы пустой ключ всем жителям (как spar).

    def __init__(self, mind, world=None, clock=None, prices=None, roster_dir=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("legacy") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self._prices = prices
        self.roster_dir = roster_dir
        self.st = mind.mem.get("legacy") or {}
        self.next_tick = 0.0
        social = getattr(mind, "social", None)
        if social is not None and hasattr(social, "register_topic"):
            for key, pool in PHRASES.items():
                social.phrases.setdefault(key, pool)
            social.register_topic("legacy", self.facts, said=self.said, chance=0.8)

    # ---------- данные ----------

    @property
    def prices(self):
        if self._prices is None:
            self._prices = Prices.load()
        return self._prices

    def save(self):
        self.mind.mem.set("legacy", self.st)

    def me(self):
        return (self.mind.state or {}).get("name") or self.mind.persona.get("name")

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "legacy", "event": kind, "text": text, **data})
        log.info("%s", text)

    def affinity(self, peer):
        return (self.mind.mem.relation(peer) or {}).get("affinity", 0)

    def quarrel(self, peer):
        society = getattr(self.mind, "society", None)
        return bool(society and society.quarrel(peer))

    def retired(self):
        return self.st.get("phase") == "retired"

    # ---------- условия ----------

    def born(self):
        ts = roster_born(self.me(), self.roster_dir)
        if ts:
            return ts
        row = self.mind.mem.db.execute("SELECT MIN(ts) FROM events").fetchone()
        return row[0] if row and row[0] else None

    def reasons(self, state, now):
        out = []
        for (data,) in self.mind.mem.db.execute("SELECT data FROM events WHERE kind = 'dream_done' ORDER BY ts"):
            d = _loads(data)
            if int(d.get("days") or 0) >= self.cfg["long_dream_days"]:
                out.append(f"мечта сбылась: {d.get('dream')} ({d.get('days')} дн.)")
                break
        lv = state.get("lv")
        if isinstance(lv, int) and lv >= self.cfg["elder_level"]:
            out.append(f"уровень {lv}")
        born = self.born()
        if born and now - born >= self.cfg["elder_days"] * 86400:
            out.append(f"прожито {int((now - born) // 86400)} дн.")
        return out

    def blocker(self, state, now):
        m = self.mind
        wed = getattr(m, "wed", None)
        if wed is not None and getattr(wed, "fiance", None) and not getattr(wed, "spouse", None):
            return "помолвка: сначала свадьба"
        mentor = getattr(m, "mentor", None)
        if mentor is not None and getattr(mentor, "role", None) == "mentor":
            return "ученик под опекой"
        if state.get("dead"):
            return "мёртв"
        if m.plans.store.active() or quest_busy(m, state, now):
            return "тело занято: встреча или квест"
        for attr in ("explorer", "trek", "spar"):
            mod = getattr(m, attr, None)
            if mod is not None and mod.busy():
                return "тело занято"
        econ = getattr(m, "economy", None)
        if econ is not None and (econ.body_busy() or econ.mail_busy()):
            return "сделка или почта"
        return None

    def heir(self):
        """Ученик, затем супруг/жених, затем лучший друг (≥ heir_min); в ссоре — нет."""
        peers = set(self.mind.ctx.peers)
        ok = lambda p: p in peers and not self.quarrel(p) and self.affinity(p) >= 0
        mentor = getattr(self.mind, "mentor", None)
        hist = ((mentor.st if mentor is not None else self.mind.mem.get("mentor")) or {}).get("history") or []
        for h in reversed(hist):
            if isinstance(h, dict) and h.get("role") == "mentor" and ok(h.get("peer")):
                return h["peer"], "ученик"
        wed = getattr(self.mind, "wed", None)
        partner = wed.partner() if wed is not None and hasattr(wed, "partner") else None
        if partner and ok(partner):
            return partner, "спутник"
        friends = sorted((p for p in peers if ok(p) and self.affinity(p) >= self.cfg["heir_min"]),
                         key=lambda p: (-self.affinity(p), p))
        return (friends[0], "друг") if friends else (None, None)

    def plan(self, state):
        """Посылки наследнику: карты стопками, затем зени сверх запаса (≤ max_parcels писем)."""
        out = []
        items = state.get("items") or {}
        for iid in sorted(items, key=lambda i: int(i) if str(i).isdigit() else 0):
            n = int(items.get(iid) or 0)
            if n > 0 and self.prices.is_card(str(iid)) and len(out) < self.cfg["max_parcels"] - 1:
                out.append({"item": int(iid), "amount": n, "name": self.prices.name(str(iid)) or str(iid)})
        zeny = int(state.get("zeny") or 0) - self.cfg["keep_zeny"] - MAIL_TAX_ITEM * len(out)
        zeny = min(self.cfg["max_zeny"], int(zeny / 1.02))
        if zeny >= self.cfg["min_zeny_parcel"] and len(out) < self.cfg["max_parcels"]:
            out.append({"zeny": zeny})
        return out

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        state = self.mind.state or {}
        if not getattr(self.mind, "fresh_state", True) or not state.get("lv"):
            return
        phase = self.st.get("phase")
        if phase == "retired":
            return
        if not phase:
            self.consider(state, now)
        elif phase == "bequest":
            await self.bequest(state, now)
        elif phase == "farewell":
            self.farewell(now)

    def consider(self, state, now):
        if now - self.st.get("last_check", 0) < self.cfg["check_hours"] * 3600:
            return
        self.st["last_check"] = now
        reasons = self.reasons(state, now)
        why = self.blocker(state, now) if reasons else None
        if not reasons or why:
            self.save()
            if reasons:
                self.mind.write_decision({"type": "legacy", "event": "legacy_wait", "why": why, "reasons": reasons})
            return
        heir, kind = self.heir()
        parcels = self.plan(state) if heir else []
        cursor = self.mind.mem.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        self.st.update(phase="bequest" if parcels else "farewell", since=now, reasons=reasons, heir=heir,
                       heir_kind=kind, parcels=parcels, sent=0, cursor=cursor)
        self.save()
        self.note("legacy_ready", f"Решил(а) уйти на покой: {'; '.join(reasons)}. "
                  + (f"Наследник — {heir} ({kind})." if heir else "Наследника нет — ничего не передаю."),
                  4, reasons=reasons, heir=heir, parcels=len(parcels))

    async def bequest(self, state, now):
        econ = getattr(self.mind, "economy", None)
        parcels, i = self.st.get("parcels") or [], int(self.st.get("sent") or 0)
        if econ is None or not hasattr(econ, "send_mail"):
            self.st["phase"] = "farewell"
            self.save()
            return
        if econ.mail_busy() or econ.body_busy():
            return
        if i >= len(parcels):
            self.st["phase"] = "farewell"
            self.save()
            self.farewell(now)
            return
        p, heir = parcels[i], self.st["heir"]
        self.st["sent"] = i + 1
        self.save()
        self.mind.mem.add_event("legacy_parcel", {"to": heir, **p})
        if "zeny" in p:
            if int(state.get("zeny") or 0) - p["zeny"] < self.cfg["keep_zeny"] // 2:
                return                                          # зени ушли на другое — не отдаю последнее
            await econ.send_mail(heir, "Наследство", "Ухожу на покой. Это тебе — пусть пригодится.", "gift",
                                 zeny=p["zeny"])
        else:
            await econ.send_mail(heir, "Наследство", f"Ухожу на покой. Береги {p.get('name')}.", "gift",
                                 item=p["item"], amount=p["amount"])

    def gifts(self, heir, cursor):
        """Подтверждённые сервером передачи наследнику после решения (gift_given пишет economy по mail_result)."""
        out = []
        for (data,) in self.mind.mem.db.execute("SELECT data FROM events WHERE kind = 'gift_given' AND id > ?",
                                                (cursor,)):
            d = _loads(data)
            if d.get("peer") == heir:
                out.append({"item": d.get("item"), "amount": d.get("amount")})
        return out

    def farewell(self, now):
        heir = self.st.get("heir")
        gifts = self.gifts(heir, int(self.st.get("cursor") or 0)) if heir else []
        born = self.born()
        dreams = [_loads(d).get("dream") for (d,) in
                  self.mind.mem.db.execute("SELECT data FROM events WHERE kind = 'dream_done' ORDER BY ts")]
        fw = {"ts": now, "name": self.me(), "reasons": self.st.get("reasons") or [], "heir": heir,
              "heir_kind": self.st.get("heir_kind"), "gifts": gifts,
              "days": int((now - born) // 86400) if born else None, "dreams": [d for d in dreams if d]}
        self.mind.mem.set("legacy_farewell", fw)
        self.st.update(phase="retired", retired=now, gifts=len(gifts))
        self.save()
        memoir = getattr(self.mind, "memoir", None)
        if memoir is not None:
            self.mind.mem.set("memoir_week", None)          # Memoir пересоберёт книгу с главой «Прощание»
            memoir.next_tick = 0
        self.note("legacy_retired", f"Ухожу на покой. " + (f"Наследнику {heir} передано посылок: {len(gifts)} "
                  f"(по ответу сервера)." if heir else "Без наследника.") + " Уход — решение владельца.", 5,
                  heir=heir, gifts=len(gifts), reasons=fw["reasons"])
        self.mind.write_decision({"type": "legacy", "event": "recommend_retire", "roster": "active: false",
                                  "who": self.me(), "note": "рекомендация владельцу (scripts/lab census)"})

    # ---------- тема разговора ----------

    def facts(self, peer, now):
        if not self.retired() or peer in (self.st.get("told") or []):
            return None
        heir = self.st.get("heir")
        return {"heir": heir} if heir else {"_key": "legacy_alone", "who": peer}

    def said(self, peer, facts, now):
        self.st["told"] = (self.st.get("told") or []) + [peer]
        self.save()


CHRONICLE_LINES = {
    "legacy_ready": lambda d: "решил(а) уйти на покой" + (f", наследник — {d.get('heir')}" if d.get("heir") else ""),
    "legacy_retired": lambda d: "ушёл(ушла) на покой" + (f"; наследнику {d.get('heir')} передано посылок: "
                                                          f"{d.get('gifts')}" if d.get("heir") else ""),
}
