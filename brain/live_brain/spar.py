"""Дружеский спарринг жителей на арене PvP Yoyo (ORG-061, ТЗ Т-30). Правила без LLM, итог — только по фактам тела.

ПО УМОЛЧАНИЮ ВЫКЛЮЧЕНО (goals.json "spar": {"enabled": false}): бой на PvP-карте, в игре не проверено.

Только житель против жителя и только по согласию обоих; против людей — никогда (мост принимает спарринг только с
жителем из residents, плагин бьёт только соперника по имени и уходит, увидев постороннего).

Протокол (шёпот жителю, метка ≤ 78 символов; social.py не отвечает — метка не [chat:]):
    вызывающий (в городе, днём, тело свободно):  «Разомнёмся на арене? [spar:ask]»
    партнёр — по характеру и здоровью:            [spar:yes] или [spar:no:<why>]
    вызывающий: действие spar {to, role first, room} -> плагин ведёт к Gate Keeper prt_in,52,140, платит 500 z,
                в приёмной #8 берёт комнату, только если в ней 0 игроков; на арене (spar_step arena) — [spar:in:<комната>]
    партнёр: spar {role second, room} — та же комната, только если в ней ровно 1 игрок (вызвавший)
    бой (плагин): kill только по сопернику; HP < 30 % — yield, упал — down, соперник упал — won, лимит — draw,
                посторонний — aborted stranger; выход — Butterfly Wing (nopenalty на pvp_y_*, смерть без потерь)
    проигравший: [spar:yield] -> победитель: spar_stop, итог won;   отмена: [spar:off:<why>] в обе стороны.
Кто подходит: соперник недели (rivalry.py) или друг (affinity ≥ friend_min); не в ссоре (society.py) — бой не для
мести. Недавнее примирение (society_reconciled за reconcile_days) — наоборот, повод «размяться в знак мира»:
согласие охотнее. Согласие (willing): смелость ≥ min_bravery, шанс 0.3 + 0.6 × смелость (+0.2 сопернику,
+0.25 после примирения), не «день осторожности» режиссёра (ORG-086).
Общие условия (blocker): уровень ≥ 31 (Gate Keeper: BaseLevel > 30), зени ≥ fee + zeny_reserve, крыло бабочки (602)
≥ 1, HP ≥ min_hp, без смерти за hurt_hours, в городе отдыха (Prontera — Gate Keeper в prt_in), не ночь, нет плана
встречи и экспедиции, арбитр разрешает, не больше max_per_day спаррингов в день и пауза gap_hours.
Итог: воспоминание, событие памяти spar_bout {peer, outcome, why} (событие тела spar_result память пишет сама); победитель — spar_won {winner, loser}
(шина, важность 3), ничья — spar_draw у вызвавшего (шина, 2); счёт пары (kv spar.score), отношение +1 за честный
бой, реплика и эмоция; соперничество считает победы недели (rivalry.py, метрика spar).
Пока идёт спарринг (busy) — арбитр (lifecycle) отдаёт тело владельцу plan: распорядок, прогулки и экспедиции ждут;
мост сам отклоняет от мозга всё, кроме реплик и spar_stop.
Выключатель: goals.json "spar": {"enabled": true} включает; BRAIN_DISABLE=spar выключает.
"""
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("spar")

TAG = re.compile(r"\[spar:(ask|yes|no|in|yield|off)(?::([A-Za-z0-9_]{1,16}))?\]")
DEFAULTS = {
    "enabled": False, "town": "prontera", "rooms": ["Prontera"], "min_level": 31, "fee": 500, "zeny_reserve": 1000,
    "min_hp": 90, "min_bravery": 0.35, "friend_min": 3, "check_minutes": 30, "answer_seconds": 120,
    "wait_minutes": 10, "max_minutes": 20, "after_seconds": 60, "max_per_day": 1, "gap_hours": 6, "hurt_hours": 2,
    "reconcile_days": 7, "tick_seconds": 5,
}
WING = "602"
ROOMS = ("Prontera", "Izlude", "Payon", "Alberta", "Morocc")   # пункты приёмной #8 (npc/other/pvp.txt:297)
ACTIVE = ("going", "arena", "fight", "after")
PHRASES = {   # ≤ 60 символов без метки
    "spar_ask": ["Разомнёмся на арене?", "Пойдём на арену, разомнёмся?", "Спарринг на арене? По-дружески."],
    "spar_peace": ["Мир? Тогда разомнёмся на арене!", "Помирились — давай на арену, по-дружески?"],
    "spar_yes": ["Давай! Иду к арене.", "С удовольствием! Встречаемся на арене."],
    "spar_no": ["Не сегодня.", "Сейчас не могу, в другой раз."],
    "spar_won": ["Хороший бой, {who}! Счёт {w}:{l}.", "Спасибо за бой, {who}! {w}:{l}.", "Ты держался(лась) молодцом, {who}!"],
    "spar_lost": ["Сдаюсь! Ты сильнее сегодня, {who}.", "Твоя взяла, {who}! Реванш за мной.", "Хороший бой, {who}. {w}:{l}."],
    "spar_draw": ["Ничья, {who}! Ещё сойдёмся.", "Никто не уступил — ничья, {who}!"],
}
EMOTES = {"won": 29, "lost": 18, "draw": 33}     # gg / heh / ok (safety.EMOTES)


class Spar:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "spar", "spar", "spar", False, ("peers",), "world"
    TICK_ORDER = 195                          # после rivalry (190): соперник недели уже выбран
    TAGS, TAG_ORDER = [(TAG, "on_tag")], 47   # после boss (45), до crew (50)
    EVENTS = {"spar_step": {"call": "on_step", "own": True}, "spar_result": {"call": "on_result", "own": True}}
    EVENT_ORDER = 85
    # Поля промпта нет: выключенный по умолчанию модуль добавлял бы пустой ключ всем жителям (как boss).

    def __init__(self, mind, world=None, clock=None, rng=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("spar") or {}))
        self.cfg["rooms"] = [r for r in self.cfg["rooms"] if r in ROOMS] or ["Prontera"]
        self.tz = timezone(timedelta(hours=(world or {}).get("timezone_offset_hours", 0)))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.st = mind.mem.get("spar") or {}
        self.st.setdefault("score", {})
        self.st.setdefault("history", [])
        self.next_tick = 0.0
        self.next_check = 0.0

    # ---------- данные ----------

    @property
    def cur(self):
        return self.st.get("cur")

    def save(self):
        self.mind.mem.set("spar", self.st)

    def me(self):
        return self.mind.state.get("name") or self.mind.persona.get("name")

    def trait(self, name):
        return (getattr(getattr(self.mind, "needs", None), "t", None) or {}).get(name, 0.5)

    def day(self, now):
        return datetime.fromtimestamp(now, self.tz).strftime("%Y-%m-%d")

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "spar", "event": kind, "text": text, **data})
        log.info("%s", text)

    def rival(self):
        r = getattr(self.mind, "rivalry", None)
        return (r.st or {}).get("rival") if r else None

    def quarrel(self, peer):
        society = getattr(self.mind, "society", None)
        return bool(society and society.quarrel(peer))

    def affinity(self, peer):
        return (self.mind.mem.relation(peer) or {}).get("affinity", 0)

    def reconciled(self, peer, now):
        """Помирились недавно (society_reconciled за reconcile_days)?"""
        since = now - self.cfg["reconcile_days"] * 86400
        for (data,) in self.mind.mem.db.execute("SELECT data FROM events WHERE kind = 'society_reconciled' "
                                                 "AND ts >= ?", (since,)):
            if f'"{peer}"' in (data or ""):
                return True
        return False

    def busy(self):
        """Тело в спарринге: арбитр (lifecycle) отдаёт его владельцу plan, распорядок не двигает."""
        cur = self.cur
        return bool((cur and cur.get("phase") in ACTIVE) or (self.mind.state.get("spar") or {}).get("running"))

    def today(self, now):
        day = self.day(now)
        return sum(1 for ts in self.st["history"] if self.day(ts) == day)

    def blocker(self, now):
        """None — могу на арену сейчас; иначе короткая причина (для [spar:no:<why>] и решений)."""
        m, s, cfg = self.mind, self.mind.state, self.cfg
        if s.get("dead") or not getattr(m, "fresh_state", True):
            return "dead"
        if not isinstance(s.get("lv"), int) or s["lv"] < cfg["min_level"]:
            return "level"
        if (s.get("zeny") or 0) < cfg["fee"] + cfg["zeny_reserve"]:
            return "zeny"
        if int((s.get("items") or {}).get(WING, 0) or 0) < 1:
            return "wing"
        if (s.get("hp_pct") or 0) < cfg["min_hp"]:
            return "hp"
        if m.mem.count_events("died", now - cfg["hurt_hours"] * 3600) > 0:
            return "hurt"
        if self.today(now) >= cfg["max_per_day"]:
            return "today"
        if self.st["history"] and now - self.st["history"][-1] < cfg["gap_hours"] * 3600:
            return "today"
        r = getattr(m, "routine", None)
        if (not r or not r.st or r.st.get("mode") != "town" or s.get("map") != cfg["town"]
                or (r.town or {}).get("map") != cfg["town"]):
            return "town"
        social = getattr(m, "social", None)
        if social and social.is_night(now):
            return "night"
        explorer = getattr(m, "explorer", None)
        if m.plans.store.active() or (explorer and explorer.busy()):
            return "busy"
        may_move = getattr(m, "may_move", None)
        if may_move and not may_move("routine")[0]:
            return "busy"
        return None

    def relation_why(self, peer):
        if peer not in self.mind.ctx.peers:
            return "who"
        if self.quarrel(peer) or self.affinity(peer) < 0:
            return "quarrel"
        if peer != self.rival() and self.affinity(peer) < self.cfg["friend_min"]:
            return "who"
        return None

    def partners(self):
        """Соперник недели, затем друзья по убыванию отношения; без ссоры и известных уровней ниже минимума."""
        known = self.mind.mem.get("known_players") or {}
        out = []
        for peer in sorted(self.mind.ctx.peers):
            if self.relation_why(peer):
                continue
            lv = (known.get(peer) or {}).get("lv")
            if isinstance(lv, int) and lv < self.cfg["min_level"]:
                continue
            out.append((0 if peer == self.rival() else 1, -self.affinity(peer), peer))
        return [p for *_, p in sorted(out)]

    def willing(self, peer, now):
        """Согласие по характеру: смелость, соперник, недавнее примирение; «день осторожности» — нет."""
        director = getattr(self.mind, "director", None)
        if director and director.active("caution"):
            return False
        bravery = self.trait("bravery")
        if bravery < self.cfg["min_bravery"]:
            return False
        p = 0.3 + 0.6 * bravery + (0.2 if peer == self.rival() else 0.0) + (0.25 if self.reconciled(peer, now) else 0.0)
        return self.rng.random() < min(0.95, p)

    def phrase(self, key, **fmt):
        own = (self.mind.persona.get("phrases") or {}).get(key)
        pool = own if isinstance(own, list) and own else PHRASES[key]
        try:
            text = self.rng.choice(pool).format(**fmt)
        except (KeyError, IndexError, ValueError):
            text = PHRASES[key][0].format(**fmt)
        return " ".join(text.split())[:60]

    async def whisper(self, to, text, reason):
        await self.mind.execute([{"action": "whisper", "to": to, "text": text}], source="spar",
                                reason=f"спарринг: {reason}", protocol=True)

    async def act(self, action, reason):
        await self.mind.execute([action], source="spar", reason=f"спарринг: {reason}", protocol=True)

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        if not getattr(self.mind, "fresh_state", True):
            return
        cur = self.cur
        if cur:
            await self.watch(now, cur)
            return
        if now < self.next_check:
            return
        self.next_check = now + self.cfg["check_minutes"] * 60
        await self.propose(now)

    async def watch(self, now, cur):
        phase = cur.get("phase")
        if phase in ("asked", "agreed") and now > cur.get("deadline", 0):
            self.st["cur"] = None
            self.save()
            self.mind.write_decision({"type": "spar", "event": "spar_expired", "peer": cur["peer"], "phase": phase})
        elif phase == "after" and now > cur.get("deadline", 0):
            await self.finish(now, cur.get("result") or "aborted", cur.get("why") or "соперник пропал с арены без слова")
        elif phase in ACTIVE and now - cur.get("started", now) > self.cfg["max_minutes"] * 60:
            await self.act({"action": "spar_stop", "why": "timeout"}, "сторож: слишком долго")
            await self.whisper(cur["peer"], "Что-то не так, отбой. [spar:off:timeout]", "отбой по сторожу")
            await self.finish(now, "aborted", f"нет итога {self.cfg['max_minutes']} мин")

    async def propose(self, now):
        why = self.blocker(now)
        if why:
            return
        for peer in self.partners():
            if not self.willing(peer, now):
                self.mind.write_decision({"type": "spar", "event": "spar_not_today", "peer": peer})
                return
            peace = self.reconciled(peer, now)
            self.st["cur"] = {"peer": peer, "role": "first", "phase": "asked", "ts": now, "room": self.cfg["rooms"][0],
                              "deadline": now + self.cfg["answer_seconds"], "peace": peace}
            self.save()
            self.mind.write_decision({"type": "spar", "event": "spar_ask", "peer": peer, "rival": peer == self.rival(),
                                      "peace": peace})
            await self.whisper(peer, f"{self.phrase('spar_peace' if peace else 'spar_ask')} [spar:ask]",
                               f"зову {peer} на арену")
            return

    # ---------- метки ----------

    async def on_tag(self, sender, text):
        m = TAG.search(text or "")
        if not m or sender not in self.mind.ctx.peers:
            return
        kind, arg = m.group(1), m.group(2) or ""
        now, cur = self.clock(), self.cur
        mine = bool(cur and cur.get("peer") == sender)
        if kind == "ask":
            await self.answer(now, sender)
        elif kind == "yes" and mine and cur["phase"] == "asked":
            why = self.blocker(now)
            if why:
                self.st["cur"] = None
                self.save()
                await self.whisper(sender, f"Ой, не выйдет. [spar:off:{why}]", f"сам не готов: {why}")
                return
            await self.go(now, cur, "first", cur["room"])
        elif kind == "no" and mine and cur["phase"] == "asked":
            self.st["cur"] = None
            self.save()
            self.note("spar_declined", f"{sender} не захотел(а) на арену ({arg or '?'}).", 1, peer=sender, why=arg)
        elif kind == "in" and mine and cur["phase"] == "agreed":
            if arg not in self.cfg["rooms"] or self.blocker(now) not in (None,):
                why = "room" if arg not in self.cfg["rooms"] else self.blocker(now)
                self.st["cur"] = None
                self.save()
                await self.whisper(sender, f"Прости, не приду. [spar:off:{why}]", f"не иду: {why}")
                return
            await self.go(now, cur, "second", arg)
        elif kind == "yield" and mine and cur["phase"] in ACTIVE:
            await self.closing(now, cur, "won", f"{sender} сдался(лась)")
        elif kind == "off" and mine:
            if cur["phase"] in ACTIVE:
                await self.closing(now, cur, "aborted", f"{sender} отменил(а): {arg or '?'}")
            else:
                self.st["cur"] = None
                self.save()
                self.mind.write_decision({"type": "spar", "event": "spar_off", "peer": sender, "why": arg})

    async def answer(self, now, sender):
        why = "busy" if self.cur else (self.relation_why(sender) or self.blocker(now))
        if not why and not self.willing(sender, now):
            why = "fear"
        if why:
            self.mind.write_decision({"type": "spar", "event": "spar_refuse", "peer": sender, "why": why})
            await self.whisper(sender, f"{self.phrase('spar_no')} [spar:no:{why}]", f"отказ {sender}: {why}")
            return
        self.st["cur"] = {"peer": sender, "role": "second", "phase": "agreed", "ts": now,
                          "deadline": now + self.cfg["wait_minutes"] * 60}
        self.save()
        self.mind.write_decision({"type": "spar", "event": "spar_agree", "peer": sender})
        await self.whisper(sender, f"{self.phrase('spar_yes')} [spar:yes]", f"согласен(на) с {sender}")

    async def go(self, now, cur, role, room):
        cur.update(phase="going", role=role, room=room, started=now)
        self.st["history"] = [ts for ts in self.st["history"] if now - ts < 7 * 86400] + [now]
        self.save()
        self.mind.write_decision({"type": "spar", "event": "spar_go", "peer": cur["peer"], "role": role, "room": room})
        await self.act({"action": "spar", "to": cur["peer"], "role": role, "room": room},
                       f"на арену с {cur['peer']} ({role}, {room})")

    async def closing(self, now, cur, outcome, why):
        """Итог известен из шёпота соперника: остановить плагин (итог — по spar_result stopped) или сразу закрыть."""
        cur.update(result=outcome, why=why)
        if (self.mind.state.get("spar") or {}).get("running") or cur["phase"] in ("going", "arena", "fight"):
            cur["phase"] = "after"
            cur["deadline"] = now + self.cfg["after_seconds"]
            self.save()
            await self.act({"action": "spar_stop", "why": why[:40]}, why)
            return
        await self.finish(now, outcome, why)

    # ---------- события тела ----------

    async def on_step(self, event):
        cur = self.cur
        if not cur or cur.get("phase") not in ACTIVE or event.get("to") not in (None, cur["peer"]):
            return
        phase = event.get("phase")
        if phase == "arena":
            cur["phase"] = "arena"
            self.save()
            if cur["role"] == "first":
                await self.whisper(cur["peer"], f"Я на арене, жду! [spar:in:{cur['room']}]", "жду на арене")
        elif phase == "fight":
            cur["phase"] = "fight"
            self.save()
            self.mind.write_decision({"type": "spar", "event": "spar_fight", "peer": cur["peer"]})

    async def on_result(self, event):
        cur = self.cur
        if not cur or cur.get("phase") not in ACTIVE:
            return
        now = self.clock()
        outcome, reason = str(event.get("outcome") or ""), str(event.get("reason") or "")
        peer = cur["peer"]
        if outcome in ("yield", "down"):
            await self.whisper(peer, f"{self.phrase('spar_lost', who=peer, **self.counts(peer, 'lost'))} [spar:yield]",
                               "сдаюсь")
            await self.finish(now, "lost", "сдался(лась): " + reason if outcome == "yield" else "упал(а): " + reason,
                              announced=True)
        elif outcome == "won":
            await self.finish(now, "won", reason)
        elif outcome == "draw":
            await self.finish(now, "draw", reason)
        elif outcome == "stopped" and cur.get("result"):
            await self.finish(now, cur["result"], cur.get("why") or reason)
        elif outcome == "gone":                      # соперник ушёл крылом — ждём его [spar:yield] / [spar:off]
            cur.update(phase="after", deadline=now + self.cfg["after_seconds"], why="соперник пропал с арены")
            self.save()
        else:                                        # aborted (посторонний, комната занята, меню, таймаут) / stopped
            code = re.match(r"[a-z]{3,10}", reason)
            await self.whisper(peer, f"Отбой, не вышло. [spar:off:{code.group(0) if code else 'fail'}]", "отбой")
            await self.finish(now, "aborted", reason or outcome)

    # ---------- итог ----------

    def counts(self, peer, outcome=None):
        sc = dict(self.st["score"].get(peer) or {"w": 0, "l": 0, "d": 0})
        if outcome == "won":
            sc["w"] += 1
        elif outcome == "lost":
            sc["l"] += 1
        return {"w": sc["w"], "l": sc["l"]}

    async def finish(self, now, outcome, why, announced=False):
        cur = self.cur or {}
        peer = cur.get("peer")
        self.st["cur"] = None
        if not peer:
            self.save()
            return
        key = {"won": "w", "lost": "l", "draw": "d"}.get(outcome)
        sc = self.st["score"].setdefault(peer, {"w": 0, "l": 0, "d": 0}) if key else {"w": 0, "l": 0, "d": 0}
        if key:
            sc[key] += 1
        self.save()
        self.mind.mem.add_event("spar_bout", {"peer": peer, "outcome": outcome, "why": why, "role": cur.get("role"),
                                                "room": cur.get("room")})
        if outcome == "won":
            self.note("spar_won", f"Победил(а) {peer} в спарринге на арене (счёт {sc['w']}:{sc['l']}).", 3,
                      winner=self.me(), loser=peer, room=cur.get("room"))
        elif outcome == "lost":
            self.mind.mem.remember(f"Уступил(а) {peer} в спарринге на арене (счёт {sc['w']}:{sc['l']}).", 2)
        elif outcome == "draw":
            text = f"Ничья с {peer} в спарринге на арене."
            if cur.get("role") == "first":
                self.note("spar_draw", text, 2, a=self.me(), b=peer)
            else:
                self.mind.mem.remember(text, 2)
        else:
            self.mind.mem.remember(f"Спарринг с {peer} не состоялся: {why}.", 1)
        self.mind.write_decision({"type": "spar", "event": "spar_done", "peer": peer, "outcome": outcome, "why": why,
                                  "score": dict(sc)})
        if outcome in ("won", "lost", "draw"):
            self.mind.mem.update_relation(peer, 1, note="честный спарринг на арене")
            await self.act({"action": "emote", "emotion": EMOTES[outcome]}, f"эмоция после боя ({outcome})")
            if not announced:
                text = self.phrase(f"spar_{outcome}", who=peer, w=sc["w"], l=sc["l"])
                await self.whisper(peer, f"{text} [chat:spar:4]", f"после боя с {peer}")

    def summary(self):
        cur = self.cur
        if cur:
            return {"спарринг": f"с {cur['peer']}", "этап": cur.get("phase")}
        score = {p: f"{s.get('w', 0)}:{s.get('l', 0)}" + (f" (ничьих {s['d']})" if s.get("d") else "")
                 for p, s in self.st["score"].items()}
        return {"счёт_спаррингов": score} if score else None


CHRONICLE_LINES = {
    "spar_won": lambda d: f"победил(а) {d.get('loser')} в спарринге на арене",
    "spar_draw": lambda d: f"ничья с {d.get('b')} в спарринге на арене",
    "spar_bout": lambda d: {"lost": f"уступил(а) {d.get('peer')} в спарринге",
                              "aborted": f"спарринг с {d.get('peer')} не состоялся"}.get(d.get("outcome")),
}
