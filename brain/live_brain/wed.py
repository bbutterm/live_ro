"""Помолвка и свадьба — мечта пары (ORG-062, ТЗ Т-35). Правила без LLM, ступени — только по фактам.

Ступени пары (kv wed.pairs, событие wed_stage при смене):
    friends  — affinity ≥ friend_min;
    close    — affinity ≥ close_min и эпизодов пары (episodes.with_peer, сумма times) ≥ close_episodes;
    engaged  — помолвка (kv wed.engaged), married — брак (кольцо 2634/2635 в рюкзаке или объявление сервера).
Помолвка — символический ритуал жителей, целиком в мозге (сервер о ней не знает):
    предложение шёпотом [wed:ask] жителю рядом (≤ near_cells), на ступени close, affinity ≥ engage_min, не в ссоре,
    в городе днём; раз в check_hours бросок 0.2 + 0.5 × общительность; одному жителю — не чаще ask_gap_days и не
    больше max_asks раз (без давления);
    ответ [wed:yes] / [wed:no:<why>] — по своей ступени, свободе и характеру (бросок 0.4 + 0.5 × общительность);
    помолвка у обоих: воспоминание 5, отношение +2, эмоция «сердце» (3), подарок — письмо economy.send_mail
    (gift_zeny; «отдал» economy пишет по ответу сервера), событие wed_engaged (предложивший, шина 4) /
    wed_accepted (согласившийся), тема разговора wed.
    Разрыв: ссора (society.quarrel) или affinity < break_below -> [wed:off:<why>], wed_broken у обоих (шина 3).
Свадьба rAthena (npc/other/marriage.txt) — долгая мечта пары в dream.py (вид wedding): этапы «уровень 45», копилка
(плата по полу + Diamond Ring + наряд), «обряд у епископа». Только разнополая пара (штатный скрипт), оба не в
браке. Обряд у NPC здесь только описан (ceremony_plan) — исполнителя нет, goals.json wed.ceremony: false:
дорого и необратимо (развода для игрока нет — Divorce Staff только для GM, развод — @divorce). Достигнуты уровень
и сумма — wed_ready один раз: «обряд — решение владельца».
Выключатель: BRAIN_DISABLE=wed или goals.json "wed": {"enabled": false}.
"""
import logging
import random
import re
import time

from .grammar import sex_of

log = logging.getLogger("wed")

TAG = re.compile(r"\[wed:(ask|yes|no|off)(?::([a-z]{1,12}))?\]")
DEFAULTS = {
    "enabled": True, "ceremony": False, "friend_min": 3, "close_min": 7, "close_episodes": 10, "engage_min": 8,
    "break_below": 3, "check_hours": 12, "answer_seconds": 120, "ask_gap_days": 14, "max_asks": 2, "near_cells": 8,
    "gift_zeny": 500, "keep_zeny": 5000, "tick_seconds": 30, "level": 45,
    "fee": {"m": 1300000, "f": 1200000}, "ring_price": 45000, "outfit_price": 43000,
}
STAGES = ("none", "friends", "close", "engaged", "married")
STAGE_RU = {"none": "знакомые", "friends": "друзья", "close": "близкие", "engaged": "помолвлены",
            "married": "в браке"}
RINGS = ("2634", "2635")          # Bridegroom_Ring, Bride_Ring (marriage.txt:726, :723)
HEART = 3                         # эмоция «lv» (safety.EMOTES)
PRONOUNCE = re.compile(r"I now pronounce you, (\S+) and (\S+), husband and wife\.")   # marriage.txt:734
# Шаги обряда (marriage.txt): только описание для владельца, исполнителя нет.
CEREMONY = {
    "outfit": {"npc": "Wedding Shop Dealer", "at": "prt_in 211,169", "src": "npc/merchants/shops.txt:253",
               "m": {"item": 7170, "name": "Tuxedo"}, "f": {"item": 2338, "name": "Wedding Dress"}},
    "ring": {"npc": "Jeweler", "at": "morocc 154,55", "item": 2613, "name": "Diamond Ring",
             "src": "npc/re/merchants/shops.txt:122", "note": "путь до Морокка в таблицах OpenKore не найден"},
    "apply": {"npc": "Wedding Staff#w", "at": "prt_church 97,100", "src": "npc/other/marriage.txt:28",
              "menu": ["Apply for Wedding", "Yes"], "input": "своё имя", "pay": "плата и вещи списываются здесь"},
    "party": {"what": "группа ровно из двоих", "src": "npc/other/marriage.txt:607"},
    "bishop": {"npc": "Bishop#w", "at": "prt_church 100,128", "src": "npc/other/marriage.txt:591",
               "m": {"first": True, "input": "имя невесты", "menu": ["I do."]},
               "f": {"first": False, "within_s": 180, "menu": ["I do.", "Yes, I do."]},
               "proof": "I now pronounce you, <жених> and <невеста>, husband and wife."},
}
PHRASES = {   # ≤ 60 символов без метки
    "wed_ask": ["Обещаю быть рядом. Обручимся?", "Ты мне дорог(а). Обручимся?",
                "Хочу идти дальше вместе. Обручимся?"],
    "wed_yes": ["Да! Обещаю и я.", "Да. Буду рядом.", "Да! Я давно ждал(а)."],
    "wed_no": ["Прости, я пока не готов(а).", "Давай останемся друзьями.", "Не сейчас, прости."],
    "wed_off": ["Нам лучше разойтись. Прости.", "Помолвки больше нет.", "Я возвращаю обещание."],
    "wed_engaged": ["Мы с {who} обручились!", "У нас с {who} помолвка!", "Я обручён(а) с {who}."],
    "wed_married": ["Мы с {who} теперь в браке!", "Я в браке с {who}. Сбылось!"],
    "wed_re": ["Поздравляю! Счастья вам!", "Вот это новость! Поздравляю!", "Как здорово! Счастья!"],
}


def dist(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


class Wed:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "wed", "wed", "wed", True, ("peers",), "world"
    TICK_ORDER = 143                          # до dream (145): мечта видит свежую помолвку
    TICK_EVERY = 30             # perf: реестр не зовёт tick до next_tick (modules.py)
    TAGS, TAG_ORDER = [(TAG, "on_tag")], 57   # после mentor (55), до guild (60)
    PROMPT = [("пара", "summary", 227)]       # после мечты (225)

    def __init__(self, mind, world=None, clock=None, rng=None):
        self.mind = mind
        raw = (world or {}).get("wed") or {}
        self.cfg = dict(DEFAULTS, **raw)
        self.cfg["fee"] = dict(DEFAULTS["fee"], **(raw.get("fee") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.st = mind.mem.get("wed") or {}
        for k in ("pairs", "asked", "told"):
            self.st.setdefault(k, {})
        self.next_tick = 0.0
        self.pending_emote = False
        social = getattr(mind, "social", None)
        if social is not None and hasattr(social, "register_topic"):
            for key, pool in PHRASES.items():
                if key in ("wed_engaged", "wed_married", "wed_re"):
                    social.phrases.setdefault(key, pool)
            social.register_topic("wed", self.facts, said=self.said, chance=0.6)

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("wed", self.st)

    def me(self):
        return (self.mind.state or {}).get("name") or self.mind.persona.get("name")

    def trait(self, name):
        return (getattr(getattr(self.mind, "needs", None), "t", None) or {}).get(name, 0.5)

    def affinity(self, peer):
        return (self.mind.mem.relation(peer) or {}).get("affinity", 0)

    def quarrel(self, peer):
        society = getattr(self.mind, "society", None)
        return bool(society and society.quarrel(peer))

    def episodes(self, peer):
        ep = getattr(self.mind, "episodes", None)
        if ep is None or not hasattr(ep, "with_peer"):
            return 0
        return sum(int(e.get("times") or 1) for e in ep.with_peer(peer))

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "wed", "event": kind, "text": text, **data})
        log.info("%s", text)

    def phrase(self, key, **fmt):
        own = (self.mind.persona.get("phrases") or {}).get(key)
        pool = own if isinstance(own, list) and own else PHRASES[key]
        try:
            text = self.rng.choice(pool).format(**fmt)
        except (KeyError, IndexError, ValueError):
            text = PHRASES[key][0].format(**fmt)
        return " ".join(text.split())[:60]

    async def whisper(self, to, text, reason):
        await self.mind.execute([{"action": "whisper", "to": to, "text": text}], source="wed",
                                reason=f"пара: {reason}", protocol=True)

    @property
    def fiance(self):
        return (self.st.get("engaged") or {}).get("peer")

    @property
    def spouse(self):
        return (self.st.get("married") or {}).get("peer")

    def partner(self):
        """Супруг или жених/невеста (для наследия ORG-083 и мечты)."""
        return self.spouse or self.fiance

    # ---------- ступени ----------

    def stage(self, peer):
        if peer == self.spouse:
            return "married"
        if peer == self.fiance:
            return "engaged"
        a = self.affinity(peer)
        if a >= self.cfg["close_min"] and self.episodes(peer) >= self.cfg["close_episodes"]:
            return "close"
        if a >= self.cfg["friend_min"]:
            return "friends"
        return "none"

    def update_stages(self, now):
        changed = False
        for peer in sorted(self.mind.ctx.peers):
            new = self.stage(peer)
            old = (self.st["pairs"].get(peer) or {}).get("stage", "none")
            if new != old:
                self.st["pairs"][peer] = {"stage": new, "since": now}
                changed = True
                if STAGES.index(new) > STAGES.index(old) and new in ("friends", "close"):
                    self.mind.mem.add_event("wed_stage", {"peer": peer, "stage": new, "was": old})
                    self.mind.write_decision({"type": "wed", "event": "wed_stage", "peer": peer, "stage": new,
                                              "was": old})
        return changed

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        state = self.mind.state or {}
        if not getattr(self.mind, "fresh_state", True) or state.get("dead") or not state.get("lv"):
            return
        changed = self.update_stages(now)
        changed |= self.check_married(state, now)
        pend = self.st.get("pending")
        if pend and now - pend["since"] > self.cfg["answer_seconds"]:
            self.st["pending"] = None
            changed = True
            self.mind.write_decision({"type": "wed", "event": "wed_no_answer", "peer": pend["peer"]})
        if changed:
            self.save()
        if self.fiance:
            why = self.break_why(self.fiance)
            if why:
                await self.break_off(self.fiance, why, now, tell=True)
                return
            await self.send_gift(now, state)
            self.check_ready(state, now)
            return
        if not self.spouse and not self.st.get("pending"):
            await self.propose(now, state)

    def break_why(self, peer):
        if self.quarrel(peer):
            return "quarrel"
        if self.affinity(peer) < self.cfg["break_below"]:
            return "cold"
        return None

    def near(self, peer, state):
        for p in state.get("players") or []:
            if isinstance(p, dict) and p.get("name") == peer and p.get("x") is not None and state.get("x") is not None:
                return dist(int(p["x"]), int(p["y"]), int(state["x"]), int(state["y"])) <= self.cfg["near_cells"]
        return False

    def free(self, now, state):
        """Тело и время подходят для предложения: в городе, днём, без встречи/экспедиции/квеста."""
        m = self.mind
        r = getattr(m, "routine", None)
        if not r or not getattr(r, "in_town_mode", False):
            return False
        social = getattr(m, "social", None)
        if social and social.is_night(now):
            return False
        explorer = getattr(m, "explorer", None)
        if m.plans.store.active() or (explorer and explorer.busy()):
            return False
        spar = getattr(m, "spar", None)
        return not (spar and spar.busy())

    def asks(self, peer, now=None):
        return list(self.st["asked"].get(peer) or [])

    def candidate(self, peer, now, state):
        if self.stage(peer) != "close" or self.affinity(peer) < self.cfg["engage_min"] or self.quarrel(peer):
            return False
        asked = self.asks(peer, now)
        if len(asked) >= self.cfg["max_asks"]:
            return False
        if asked and now - asked[-1] < self.cfg["ask_gap_days"] * 86400:
            return False
        return self.near(peer, state)

    async def propose(self, now, state):
        if now - self.st.get("last_check", 0) < self.cfg["check_hours"] * 3600:
            return
        cands = [p for p in sorted(self.mind.ctx.peers, key=lambda p: (-self.affinity(p), p))
                 if self.candidate(p, now, state)]
        if not cands or not self.free(now, state):
            return
        self.st["last_check"] = now
        peer = cands[0]
        if self.rng.random() >= 0.2 + 0.5 * self.trait("sociability"):
            self.save()
            self.mind.write_decision({"type": "wed", "event": "wed_not_today", "peer": peer})
            return
        self.st["asked"][peer] = self.asks(peer, now) + [now]
        self.st["pending"] = {"peer": peer, "since": now}
        self.save()
        self.mind.write_decision({"type": "wed", "event": "wed_ask", "peer": peer})
        await self.whisper(peer, f"{self.phrase('wed_ask')} [wed:ask]", f"предлагаю {peer} помолвку")

    # ---------- метки ----------

    async def on_tag(self, sender, text):
        m = TAG.search(text or "")
        if not m or sender not in self.mind.ctx.peers:
            return
        kind, arg = m.group(1), m.group(2) or ""
        now = self.clock()
        pend = self.st.get("pending")
        if kind == "ask":
            await self.answer(sender, now)
        elif kind == "yes" and pend and pend.get("peer") == sender:
            self.st["pending"] = None
            if self.fiance or self.spouse:
                self.save()
                await self.whisper(sender, f"{self.phrase('wed_off')} [wed:off:busy]", "уже не свободен(на)")
                return
            self.engage(sender, now, by=self.me())
        elif kind == "no" and pend and pend.get("peer") == sender:
            self.st["pending"] = None
            self.save()
            left = self.cfg["max_asks"] - len(self.asks(sender, now))
            self.note("wed_declined", f"{sender} не готов(а) к помолвке ({arg or '?'})."
                      + (" Больше не буду настаивать." if left <= 0 else ""), 3, peer=sender, why=arg)
        elif kind == "off" and sender == self.fiance:
            await self.break_off(sender, arg or "off", now, tell=False)

    def refuse_why(self, sender):
        if self.spouse or (self.fiance and self.fiance != sender):
            return "taken"
        if self.quarrel(sender) or self.affinity(sender) < 0:
            return "quarrel"
        if self.stage(sender) != "close" or self.affinity(sender) < self.cfg["engage_min"]:
            return "stage"
        return None

    async def answer(self, sender, now):
        why = self.refuse_why(sender)
        if not why and self.fiance == sender:
            return                                        # уже помолвлены — повтор метки
        if not why and self.rng.random() >= 0.4 + 0.5 * self.trait("sociability"):
            why = "heart"
        if why:
            self.mind.write_decision({"type": "wed", "event": "wed_refuse", "peer": sender, "why": why})
            await self.whisper(sender, f"{self.phrase('wed_no')} [wed:no:{why}]", f"отказ {sender}: {why}")
            return
        await self.whisper(sender, f"{self.phrase('wed_yes')} [wed:yes]", f"согласие {sender}")
        self.engage(sender, now, by=sender)

    def engage(self, peer, now, by):
        self.st["engaged"] = {"peer": peer, "since": now, "by": by}
        self.st["gift"] = {"peer": peer, "sent": False}
        self.st["ready"] = None
        self.save()
        self.mind.mem.update_relation(peer, 2, "помолвка")
        mine = by == self.me()
        kind = "wed_engaged" if mine else "wed_accepted"
        text = (f"Предложил(а) {peer} помолвку — {peer} согласился(лась). Обещали быть рядом." if mine else
                f"{peer} предложил(а) мне помолвку — я согласился(лась). Обещали быть рядом.")
        self.note(kind, text, 5, peer=peer)
        self.pending_emote = True

    async def break_off(self, peer, why, now, tell):
        eng = self.st.get("engaged") or {}
        self.st["engaged"] = None
        self.st["gift"] = None
        self.st["ready"] = None
        self.save()
        days = int((now - eng.get("since", now)) // 86400)
        reason = {"quarrel": "поссорились", "cold": "отношение остыло"}.get(why, why)
        self.note("wed_broken", f"Помолвка с {peer} разорвана: {reason} (через {days} дн.).", 4, peer=peer, why=why,
                  days=days)
        if tell:
            await self.whisper(peer, f"{self.phrase('wed_off')} [wed:off:{why}]", f"разрыв с {peer}: {why}")

    async def send_gift(self, now, state):
        """Эмоция-сердце рядом и подарок письмом (один раз за помолвку; почта занята — позже)."""
        peer = self.fiance
        if getattr(self, "pending_emote", False) and self.near(peer, state):
            self.pending_emote = False
            await self.mind.execute([{"action": "emote", "id": HEART}], source="wed", reason="пара: сердце",
                                    protocol=True)
        gift = self.st.get("gift") or {}
        if gift.get("sent") or gift.get("peer") != peer or self.cfg["gift_zeny"] <= 0:
            return
        econ = getattr(self.mind, "economy", None)
        if econ is None or not hasattr(econ, "send_mail"):
            return
        if econ.mail_busy() or econ.body_busy():
            return
        zeny = int(state.get("zeny") or 0)
        if zeny < self.cfg["gift_zeny"] + self.cfg["keep_zeny"]:
            return
        gift["sent"] = now
        self.st["gift"] = gift
        self.save()
        await econ.send_mail(peer, "Помолвка", "В знак обещания. Буду рядом.", "gift", zeny=self.cfg["gift_zeny"])

    # ---------- брак ----------

    def check_married(self, state, now):
        """Брак по факту: кольцо 2634/2635 в рюкзаке у помолвленного."""
        if self.spouse or not self.fiance:
            return False
        items = state.get("items") or {}
        if any(int(items.get(r, 0) or 0) > 0 for r in RINGS):
            self.marry(self.fiance, now, "ring")
            return True
        return False

    def on_announce(self, text, now=None):
        """Объявление сервера (rumors.on_world_msg): «I now pronounce you, A and B, husband and wife.»."""
        m = PRONOUNCE.search(text or "")
        if not m or self.spouse:
            return False
        me = self.me()
        pair = {m.group(1), m.group(2)}
        if me not in pair:
            return False
        other = (pair - {me}).pop() if len(pair) > 1 else None
        if not other:
            return False
        self.marry(other, now or self.clock(), "announce")
        return True

    def marry(self, peer, now, via):
        self.st["married"] = {"peer": peer, "since": now}
        self.st["engaged"] = None
        self.st["gift"] = None
        self.save()
        self.note("wed_married", f"Мы с {peer} в браке — подтверждено игрой ({'кольцо' if via == 'ring' else 'объявление'}).",
                  5, peer=peer, via=via)

    def married(self, state=None):
        return bool(self.spouse)

    # ---------- мечта wedding (dream.py) ----------

    def my_sex(self):
        return sex_of((self.mind.state or {}).get("sex")) or sex_of(self.mind.persona.get("sex"))

    def peer_sex(self, peer):
        return sex_of(((self.mind.mem.get("known_players") or {}).get(peer) or {}).get("sex"))

    def cost(self, sex=None):
        sex = sex or self.my_sex()
        if sex not in ("m", "f"):
            return None
        return int(self.cfg["fee"][sex]) + int(self.cfg["ring_price"]) + int(self.cfg["outfit_price"])

    def terms(self):
        """Условия мечты wedding: {peer, sex, cost, level} или None (нет помолвки, пол неизвестен, однополая пара)."""
        peer = self.fiance
        if not peer or self.spouse:
            return None
        me, other = self.my_sex(), self.peer_sex(peer)
        if me is None or other is None or me == other:
            return None
        return {"peer": peer, "sex": me, "cost": self.cost(me), "level": int(self.cfg["level"]),
                "since": (self.st.get("engaged") or {}).get("since", 0)}

    def check_ready(self, state, now):
        t = self.terms()
        if not t or self.st.get("ready"):
            return
        lv = state.get("lv")
        wealth = int(state.get("zeny") or 0) + int((self.mind.mem.get("savings") or {}).get("bank") or 0)
        if not isinstance(lv, int) or lv < t["level"] or wealth < t["cost"]:
            return
        self.st["ready"] = now
        self.save()
        self.note("wed_ready", f"Мы с {t['peer']} готовы к свадьбе: уровень {lv}, накоплено {wealth} зени. "
                  f"Обряд у епископа — решение владельца (wed.ceremony).", 4, peer=t["peer"], cost=t["cost"],
                  ceremony=bool(self.cfg["ceremony"]))

    def ceremony_plan(self, role=None):
        """Шаги обряда rAthena для владельца (исполнителя нет): [{step, npc, at, what, src}]."""
        sex = role or self.my_sex() or "m"
        c = CEREMONY
        return [
            {"step": "outfit", "npc": c["outfit"]["npc"], "at": c["outfit"]["at"], "src": c["outfit"]["src"],
             "what": f"купить {c['outfit'][sex]['name']} ({c['outfit'][sex]['item']})"},
            {"step": "ring", "npc": c["ring"]["npc"], "at": c["ring"]["at"], "src": c["ring"]["src"],
             "what": f"купить {c['ring']['name']} ({c['ring']['item']}); {c['ring']['note']}"},
            {"step": "apply", "npc": c["apply"]["npc"], "at": c["apply"]["at"], "src": c["apply"]["src"],
             "what": f"меню {' → '.join(c['apply']['menu'])}, ввести своё имя; {c['apply']['pay']} "
                     f"({self.cfg['fee'][sex]} зени)"},
            {"step": "party", "src": c["party"]["src"], "what": c["party"]["what"]},
            {"step": "bishop", "npc": c["bishop"]["npc"], "at": c["bishop"]["at"], "src": c["bishop"]["src"],
             "what": ("первым: ввести имя невесты, «I do.»" if sex == "m" else
                      "после жениха, за 180 с: «I do.», «Yes, I do.»"),
             "proof": c["bishop"]["proof"]},
        ]

    # ---------- тема разговора и промпт ----------

    def facts(self, peer, now):
        if peer in (self.fiance, self.spouse):
            return None
        who, key = (self.spouse, "wed_married") if self.spouse else (self.fiance, "wed_engaged")
        if not who or (self.st.get("told") or {}).get(peer) == f"{key}:{who}":
            return None
        return {"who": who, "_key": key}

    def said(self, peer, facts, now):
        self.st.setdefault("told", {})[peer] = f"{facts.get('_key')}:{facts.get('who')}"
        self.save()

    def summary(self):
        if self.spouse:
            return {"в браке с": self.spouse}
        if self.fiance:
            t = self.terms()
            out = {"помолвка с": self.fiance}
            if t:
                out["свадьба"] = f"мечта: уровень {t['level']}, {t['cost']} зени"
            return out
        close = [p for p, v in sorted(self.st["pairs"].items()) if (v or {}).get("stage") == "close"]
        return {"близкие": ", ".join(close)} if close else None


CHRONICLE_LINES = {
    "wed_engaged": lambda d: f"обручился(ась) с {d.get('peer')}",
    "wed_accepted": lambda d: f"принял(а) предложение {d.get('peer')}",
    "wed_broken": lambda d: f"помолвка с {d.get('peer')} разорвана ({d.get('why')})",
    "wed_married": lambda d: f"в браке с {d.get('peer')} — подтверждено игрой",
    "wed_ready": lambda d: f"готов(а) к свадьбе с {d.get('peer')}: обряд — решение владельца",
}
