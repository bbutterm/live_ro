"""Жизнь группы (ORG-053): куда идти решают вместе, болтовня в чате группы, прогулка за лидером. Правила без LLM.

Работает поверх party.py (состав, лидер, режим лидера) и только при группе, подтверждённой сервером.

Куда идти — вместе:
    участник раз в PREF_GAP (и при смене своего выбора) шёпотом сообщает лидеру своё желание и уровень —
    [crew:pref:<карта>:<уровень>] (своя оценка maps.choose: опыт, добыча, смелость, любопытство);
    лидер, выбирая карту на новую сессию (routine.pick_map), берёт свои карты без исключённых, отсекает
    те, что не по силам самому слабому (атлас, уровень), и считает: свой опыт карты + голос за каждое желание
    (своё тоже голос). По очереди: чьё желание проиграло в прошлый раз, в следующий весит вдвое.
    Решение — запись crew_group_choice и фраза в чат группы «идём на X».
Болтовня (чат группы, команда OpenKore «p <текст>»): пришли на карту охоты, серия побед, свой уровень,
    спасибо за лечение, «прикройте» при опасности, товарищ погиб, уходим в город, решение о карте.
    Не чаще CHAT_GAP и CHAT_HOUR в час; одна и та же тема — не чаще TOPIC_GAP. Фразы — persona.phrases
    ключи party_<тема> или встроенные.
Прогулка за лидером: участник в городе, лидер на той же карте и рядом — с вероятностью от общительности
    идёт за ним (follow) WALK_MIN минут, не чаще WALK_GAP; лидер уходит на охоту — party ведёт как обычно.
"""
import logging
import random
import re
import time

log = logging.getLogger("crew")

TAG = re.compile(r"\[crew:(pref):([a-z0-9_]{1,16}):(\d{1,3})\]")
PREF_GAP = 1200
PREF_TTL = 3600
VOTE = 30.0               # голос желания в очках карты (maps.score ~ опыт%·10 + зени/1000 в час)
CHAT_GAP = 90
CHAT_HOUR = 20
TOPIC_GAP = 900
STREAK_WIN = 300
STREAK_N = 8
WALK_MIN = 8
WALK_GAP = 2400
WALK_DIST = 20

PHRASES = {
    "arrive": ["Пришли на {map}. Погнали!", "{map} — наше место на сегодня.", "Ну что, {map}. Держимся вместе."],
    "streak": ["Хорошо идём — {n} за пять минут!", "Отличный темп, {n} подряд.", "Вот это охота: {n} за пять минут."],
    "level": ["Есть {lv} уровень!", "Апнулся до {lv}.", "{lv} уровень — спасибо, что рядом."],
    "thanks": ["Спасибо за лечение, {who}!", "{who}, выручил(а), спасибо.", "Спасибо, {who}, полегчало."],
    "danger": ["Тяжело, прикройте!", "Меня зажали, помогите!", "Помогите, много их."],
    "mate_dead": ["{who} упал(а)... держимся.", "Эх, {who}. Подождём у города.", "{who}, возвращайся скорее."],
    "town": ["Идём в город, передохнём.", "Перерыв — в город.", "Хватит пока, в город."],
    "decide": ["Решили: идём на {map}.", "Сегодня {map} — всем по силам.", "Голосуем за {map}, пошли."],
    "walk": ["Пройдусь с тобой, {who}.", "{who}, я с тобой.", "Подожди, {who}, я рядом."],
    "rival": ["{text}"],                                       # rivalry: ORG-060 подначка сопернику в группе
    "explore": ["Дошли до {map}! Новое место.", "{map} — тут мы ещё не были.", "Вот он, {map}. Осмотримся."],  # explore:
}


class Crew:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, REQUIRES, ARGS = "crew", "crew", "crew", ("party",), "config"
    TICK_ORDER = 90
    TAGS, TAG_ORDER = [(TAG, "on_tag")], 50
    ECHO = [("party", r"\[party:dead:", "on_mate_dead", 20)]          # смерть в группе — в чат группы
    EVENT_ORDER = 10
    EVENTS = dict.fromkeys(("level_up", "support", "danger"), {"call": "on_event", "kind": True})

    def __init__(self, mind, cfg=None, clock=None, rng=None):
        self.mind = mind
        self.cfg = cfg or {}
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.st = mind.mem.get("crew") or {"prefs": {}}
        self.last = {}                                 # тема -> время; не в БД
        self.chat_times = []
        self.sent_pref = None                          # (карта, время)
        self.walk_until = 0.0
        self.last_walk = 0.0
        self.prev = {}                                 # прошлый режим/карта — для фраз о переходах

    # ---------- общее ----------

    @property
    def party(self):
        return getattr(self.mind, "party", None)

    def active(self):
        p = self.party
        return bool(p and p.st.get("confirmed") and self.cfg.get("enabled", True))

    def save(self):
        self.mind.mem.set("crew", self.st)

    def note(self, kind, text, importance, **data):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, data)
        self.mind.write_decision({"type": "crew", "event": kind, "text": text, **data})
        log.info("%s", text)

    def trait(self, name, default=0.5):
        needs = getattr(self.mind, "needs", None)
        return (getattr(needs, "t", None) or {}).get(name, default)

    # ---------- болтовня ----------

    async def say(self, topic, **fmt):
        """Фраза в чат группы. True — отправлена."""
        if not self.active():
            return False
        now = self.clock()
        self.chat_times = [t for t in self.chat_times if now - t < 3600]
        if (len(self.chat_times) >= CHAT_HOUR or (self.chat_times and now - self.chat_times[-1] < CHAT_GAP)
                or now - self.last.get(topic, 0) < TOPIC_GAP):
            return False
        own = (self.mind.persona.get("phrases") or {}).get(f"party_{topic}")
        pool = own if isinstance(own, list) and own else PHRASES[topic]
        try:
            text = self.rng.choice(pool).format(**fmt)
        except (KeyError, IndexError, ValueError):
            return False
        self.chat_times.append(now)
        self.last[topic] = now
        await self.mind.execute([{"action": "party_say", "text": text}], source="crew",
                                reason=f"группа: {topic}", protocol=True)
        return True

    # ---------- выбор карты вместе ----------

    def my_choice(self):
        """Своя карта по опыту и характеру — без побочных эффектов (распорядок не трогается)."""
        maps, r = getattr(self.mind, "maps", None), self.mind.routine
        if not maps or not r:
            return None
        choice, _ = maps.choose(self.mind.persona["hunt_maps"], r.bans(), level=self.mind.state.get("lv"),
                                needs=getattr(self.mind, "needs", None), rng=random.Random(int(self.clock() // 3600)))
        return choice

    def fresh_prefs(self, now):
        mates = self.party.mates() if self.party else set()
        return {n: p for n, p in self.st.get("prefs", {}).items() if n in mates and now - p["ts"] < PREF_TTL}

    def group_choice(self, choice, why):
        """Для лидера (routine.pick_map): карта с учётом желаний и уровней группы. Возвращает (карта, почему)."""
        p = self.party
        if not (self.active() and p.is_leader):
            return choice, why
        now = self.clock()
        prefs = self.fresh_prefs(now)
        if not prefs:
            return choice, why
        r, maps = self.mind.routine, getattr(self.mind, "maps", None)
        bans = r.bans() if r else set()
        cands = [m for m in self.mind.persona["hunt_maps"] if m not in bans] or list(self.mind.persona["hunt_maps"])
        levels = [lv for lv in [self.mind.state.get("lv")] + [q.get("lv") for q in prefs.values()] if lv]
        weakest = min(levels) if levels else None
        if maps and weakest:
            needs = getattr(self.mind, "needs", None)
            cands = maps.safe_for_level(cands, weakest, needs.risk_tolerance() if needs else None)
        votes = {}
        owed = self.st.get("owed")                      # чьё желание проиграло в прошлый раз — сегодня весит вдвое
        for name, m in [(self.mind.state.get("name"), choice)] + [(n, q["map"]) for n, q in prefs.items()]:
            votes[m] = votes.get(m, 0) + (2 if name == owed else 1)
        score = {m: ((maps.score(m) or 0) if maps else 0) + VOTE * votes.get(m, 0) for m in cands}
        best = max(cands, key=lambda m: (score[m], m == choice))
        who = ", ".join(f"{n}→{q['map']}" for n, q in sorted(prefs.items()))
        losers = sorted(n for n, q in prefs.items() if q["map"] != best) + ([self.mind.state.get("name")]
                                                                            if choice != best else [])
        self.st["owed"] = losers[0] if losers else None                      # по очереди: в следующий раз — им
        if best != choice or votes.get(best, 0) > 1:
            text = f"Решили вместе: {best} (желания: {who}; слабейшему {weakest} ур.)."
            self.note("crew_group_choice", text, 2, map=best, mine=choice, prefs=who, weakest=weakest)
            self.st["decided"] = {"map": best, "ts": now}
            self.save()
            return best, f"решили вместе с группой ({who})"
        return choice, why

    async def send_pref(self, now):
        p = self.party
        if p.is_leader:
            return
        lead = p.member(p.leader)
        if not lead or not lead.get("online"):
            return
        choice = self.my_choice()
        lv = self.mind.state.get("lv")
        if not choice or not lv or choice not in self.mind.persona["hunt_maps"]:
            return
        if self.sent_pref and self.sent_pref[0] == choice and now - self.sent_pref[1] < PREF_GAP:
            return
        self.sent_pref = (choice, now)
        await self.mind.execute([{"action": "whisper", "to": p.leader, "text": f"[crew:pref:{choice}:{int(lv)}]"}],
                                source="crew", reason=f"группа: хочу на {choice}", protocol=True)

    async def on_tag(self, sender, text):
        m = TAG.search(text or "")
        if not m or sender not in self.mind.ctx.peers:
            return
        _, hmap, lv = m.groups()
        self.st.setdefault("prefs", {})[sender] = {"map": hmap, "lv": int(lv), "ts": self.clock()}
        self.save()
        log.info("желание %s: %s (%s ур.)", sender, hmap, lv)

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        state = self.mind.state
        if not self.mind.fresh_state or state.get("dead") or not self.active():
            self.walk_until = 0.0
            return
        p, r = self.party, self.mind.routine
        mode = r.st.get("mode") if r and r.st else None
        await self.send_pref(now)
        # переходы: пришли на карту охоты / ушли в город
        cur = (mode, state.get("map"))
        prev, self.prev = self.prev, {"mode": mode, "map": state.get("map")}
        if prev and mode == "hunt" and state.get("map") != prev.get("map") \
                and state.get("map") in self.mind.persona["hunt_maps"]:
            decided = self.st.get("decided") or {}
            if p.is_leader and decided.get("map") == state.get("map") and now - decided.get("ts", 0) < 3600:
                await self.say("decide", map=cur[1])
            else:
                await self.say("arrive", map=cur[1])
        elif prev and prev.get("mode") == "hunt" and mode in ("town", "rest") and p.is_leader:
            await self.say("town")
        if mode == "hunt" and self.mind.mem.count_events("kill", now - STREAK_WIN) >= STREAK_N:
            await self.say("streak", n=self.mind.mem.count_events("kill", now - STREAK_WIN))
        await self.walk(now, state, mode)

    async def walk(self, now, state, mode):
        p = self.party
        if p.is_leader:
            return
        explorer = getattr(self.mind, "explorer", None)            # explore: в экспедиции за лидером по городу не гуляем
        busy = bool(explorer and explorer.busy())                  # review2: и начатую прогулку закончить (follow
        lead = p.member(p.leader)                                  # review2: в очереди AI — lockMap не работает)
        gone = not (lead and lead.get("online") and lead.get("map") == state.get("map"))   # review2: лидер ушёл
        if self.walk_until and (now >= self.walk_until or mode == "hunt" or gone or busy):  # review2:
            self.walk_until = 0.0
            if state.get("follow") == p.leader and mode != "hunt":
                await self.mind.execute([{"action": "unfollow"}], source="crew", reason="группа: прогулка окончена")
            return
        if busy:                                                   # explore:
            return                                                 # explore:
        if self.walk_until or mode == "hunt" or now - self.last_walk < WALK_GAP:
            return
        if not (lead and lead.get("online") and lead.get("map") == state.get("map")
                and None not in (lead.get("x"), state.get("x"))
                and max(abs(int(lead["x"]) - int(state["x"])), abs(int(lead["y"]) - int(state["y"]))) <= WALK_DIST):
            return
        self.last_walk = now
        if self.rng.random() > 0.3 + 0.6 * self.trait("sociability"):
            return
        may_move = getattr(self.mind, "may_move", None)
        if may_move and not may_move("party")[0]:
            return
        self.walk_until = now + WALK_MIN * 60
        self.note("crew_walk", f"Гуляю по городу вместе с {p.leader}.", 1, who=p.leader)
        await self.mind.execute([{"action": "follow", "to": p.leader}], source="crew",
                                reason=f"группа: гуляю с {p.leader}", protocol=True)
        await self.say("walk", who=p.leader)

    def walking(self):
        return self.walk_until > self.clock()

    # ---------- события ----------

    async def on_event(self, kind, event):
        if not self.active():
            return
        me = self.mind.state.get("name")
        if kind == "level_up":
            await self.say("level", lv=event.get("level") or self.mind.state.get("lv"))
        elif kind == "support" and event.get("to") == me and event.get("from") in self.mind.ctx.peers \
                and event.get("skill") == "AL_HEAL" and event.get("amount"):
            await self.say("thanks", who=event["from"])
        elif kind == "danger":
            await self.say("danger")

    async def on_mate_dead(self, who):
        await self.say("mate_dead", who=who)
