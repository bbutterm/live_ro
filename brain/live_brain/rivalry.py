"""Дружеское соперничество жителей (ORG-060). Правила без LLM, только факты игры и памяти.

Счёт жителя (score) — из его памяти и состояния тела: уровень, победы за день и за неделю (события kill),
открытые места (explore_found, ORG-054), выполненные цели недели (aim_done, ORG-038). Раз в publish_minutes
житель кладёт снимок счёта в шину мира (вид rival_score, важность 1, «затирание»: одна запись на жителя;
в летопись и новости не попадает) и читает снимки других.
Соперник — раз в неделю (ISO-неделя по часовому поясу мира): житель с разницей уровней ≤ max_level_gap,
отношение ≥ 0, не в ссоре (society.py); ближе по уровню, при равенстве — та же профессия, затем имя.
Черта rivalry (persona.traits.rivalry, по умолчанию (bravery + diligence) / 2) задаёт азарт: ниже MIN_TRAIT —
соперника нет; чем выше, тем сильнее мотив progress у отстающего (boost: 1 + 0.2 × черта) и чаще подначки.
Обгон: знак «моё − соперника» по метрике (уровень, победы недели, места, цели) сменился с ≤ 0 на > 0 —
событие rival_overtook (летопись «обогнал(а) Vera по уровню (12 против 11)», шина важность 2) и подначка.
Подначки дня — по победам за день (разница ≥ 10 % и ≥ 5), не больше одной в день.
Куда говорить: соперник в моей подтверждённой группе — чат группы через crew.say("rival"); иначе шёпот
с меткой [chat:rival:4] (шаг 4 — social.on_tag не отвечает, петли нет). С LLM — только повод mind.trigger.
Ограничения: не больше max_per_day сообщений в сутки; в ссоре — молчит; отношения (affinity) не меняет никогда.
"""
import logging
import random
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("rivalry")

METRICS = {   # метрика -> как сказать «по чему»
    "level": "по уровню",
    "kills_week": "по победам за неделю",
    "places": "по открытым местам",
    "aims": "по целям недели",
}
SCORE_KEY = {"level": "lv", "kills_week": "kills_week", "places": "places", "aims": "aims"}
DEFAULTS = {"enabled": True, "max_level_gap": 3, "publish_minutes": 20, "max_per_day": 3, "tick_seconds": 30,
            "boost": 0.2, "lead_min": 5}
MIN_TRAIT = 0.2
STALE = 2 * 86400
MAX_TEXT = 60                     # фраза без метки (метка [chat:rival:4] — ещё 15 символов, итого ≤ 78)
PHRASES = {
    "rival_overtake": ["Обогнал(а) тебя {label}, {who}! {mine} против {theirs}.",
                       "{who}, я впереди {label}: {mine}:{theirs}. Догоняй!",
                       "Ну что, {who}, {mine} против {theirs}. Моя взяла!"],
    "rival_lead": ["Сегодня у меня {mine} побед, у тебя {theirs}. Догоняй!",
                   "{who}, {mine} побед за день против {theirs}!",
                   "У меня {mine}, у тебя {theirs}. Не отставай, {who}!"],
    "rival_behind": ["У тебя {theirs} побед, у меня {mine}. Догоню!",
                     "{who}, {theirs} против моих {mine}? Ещё посмотрим!",
                     "Ладно, {who}, сегодня ты впереди. Пока что!"],
}


class Rivalry:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "rivalry", "rivalry", "rivalry", True, ("peers",), "world"
    TICK_ORDER = 190
    TICK_EVERY = 30             # perf: реестр не зовёт tick до next_tick (modules.py)
    PROMPT = [("соперник", "summary", 230)]

    def __init__(self, mind, world=None, clock=None, rng=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("rivalry") or {}))
        self.tz = timezone(timedelta(hours=(world or {}).get("timezone_offset_hours", 0)))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.rng = rng or random.Random()
        self.st = mind.mem.get("rivalry") or {}
        self.next_tick = 0.0

    # ---------- данные ----------

    @property
    def bus(self):
        feed = getattr(self.mind, "world", None)
        return getattr(feed, "bus", None)

    def save(self):
        self.mind.mem.set("rivalry", self.st)

    def trait(self):
        t = (self.mind.persona.get("traits") or {}).get("rivalry")
        if isinstance(t, (int, float)):
            return max(0.0, min(1.0, float(t)))
        needs = getattr(self.mind, "needs", None)
        tr = getattr(needs, "t", None) or {}
        return round((tr.get("bravery", 0.5) + tr.get("diligence", 0.5)) / 2, 3)

    def local(self, now):
        return datetime.fromtimestamp(now, self.tz)

    def day(self, now):
        return self.local(now).strftime("%Y-%m-%d")

    def week(self, now):
        y, w, _ = self.local(now).isocalendar()
        return f"{y}-W{w:02d}"

    def day_start(self, now):
        return self.local(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def week_start(self, now):
        d = self.local(now).replace(hour=0, minute=0, second=0, microsecond=0)
        return (d - timedelta(days=d.weekday())).timestamp()

    def score(self, now=None):
        now = now or self.clock()
        mem, s = self.mind.mem, self.mind.state
        wk = self.week_start(now)
        return {"lv": s.get("lv"), "job": s.get("job"), "day": self.day(now), "week": self.week(now),
                "kills_day": mem.count_events("kill", self.day_start(now)),
                "kills_week": mem.count_events("kill", wk),
                "places": mem.count_events("explore_found", 0),
                "aims": mem.count_events("aim_done", wk)}

    def others(self, now=None):
        now = now or self.clock()
        bus = self.bus
        if not bus:
            return {}
        try:
            rows = bus.latest("rival_score", now - STALE)
        except Exception as e:                       # общая БД занята/недоступна — без соперничества в этот тик
            log.warning("шина мира недоступна: %s", e)
            return {}
        return {b: r["data"] for b, r in rows.items() if b in self.mind.ctx.peers}

    def quarrel(self, peer):
        society = getattr(self.mind, "society", None)
        return bool(society and society.quarrel(peer))

    def affinity(self, peer):
        return (self.mind.mem.relation(peer) or {}).get("affinity", 0)

    # ---------- соперник ----------

    def pick(self, now=None, others=None):
        """Соперник на неделю: близкий уровень, отношение ≥ 0, не в ссоре. None — нет подходящего."""
        now = now or self.clock()
        if self.trait() < MIN_TRAIT:
            return None
        lv, job = self.mind.state.get("lv"), self.mind.state.get("job")
        if not isinstance(lv, int):
            return None
        others = self.others(now) if others is None else others
        cand = []
        for name, sc in others.items():
            olv = sc.get("lv")
            if not isinstance(olv, int) or abs(olv - lv) > self.cfg["max_level_gap"]:
                continue
            if self.affinity(name) < 0 or self.quarrel(name):
                continue
            cand.append((abs(olv - lv), 0 if sc.get("job") == job else 1, name))
        return min(cand)[2] if cand else None

    def rival(self, now, others):
        week, day = self.week(now), self.day(now)
        if self.st.get("week") != week or (not self.st.get("rival") and self.st.get("picked") != day):
            name = self.pick(now, others)            # без соперника — повторная попытка раз в день
            if not name and not others:
                return None                          # данных ещё нет — выбрать позже на этой неделе
            self.st.update(week=week, rival=name, cmp={}, picked=day)
            self.save()
            if name:
                self.mind.mem.add_event("rival_chosen", {"rival": name, "week": week})
                self.mind.write_decision({"type": "rivalry", "event": "chosen", "rival": name, "week": week})
                log.info("соперник недели: %s", name)
        return self.st.get("rival")

    # ---------- такт ----------

    async def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + self.cfg["tick_seconds"]
        state = self.mind.state
        if not getattr(self.mind, "fresh_state", True) or not state.get("lv") or state.get("dead"):
            return
        mine = self.score(now)
        self.publish(now, mine)
        others = self.others(now)
        name = self.rival(now, others)
        theirs = others.get(name) if name else None
        if not theirs:
            return
        await self.compare(now, name, mine, theirs)
        await self.daily(now, name, mine, theirs)

    def publish(self, now, mine):
        bus = self.bus
        if not bus or now - self.st.get("published", 0) < self.cfg["publish_minutes"] * 60:
            return
        try:
            bus.replace("rival_score", mine, 1, now=now)
        except Exception as e:
            log.warning("шина мира недоступна: %s", e)
            return
        self.st["published"] = now
        self.save()

    def values(self, metric, mine, theirs):
        k = SCORE_KEY[metric]
        a, b = mine.get(k), theirs.get(k)
        if metric in ("kills_week", "aims") and theirs.get("week") != mine.get("week"):
            b = 0                                    # снимок соперника с прошлой недели
        return a, b

    async def compare(self, now, name, mine, theirs):
        cmp = self.st.setdefault("cmp", {})
        changed = False
        for metric, label in METRICS.items():
            a, b = self.values(metric, mine, theirs)
            if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
                continue
            sign = (a > b) - (a < b)
            prev = cmp.get(metric)
            if prev != sign:
                cmp[metric] = sign
                changed = True
            if prev is not None and prev <= 0 < sign and (metric != "kills_week" or a >= self.cfg["lead_min"]):
                data = {"rival": name, "metric": metric, "label": label, "mine": a, "theirs": b}
                self.mind.mem.add_event("rival_overtook", data)
                self.mind.mem.remember(f"Обогнал(а) {name} {label}: {a} против {b}.", 2)
                self.mind.write_decision({"type": "rivalry", "event": "overtook", **data})
                log.info("обогнал(а) %s %s (%s против %s)", name, label, a, b)
                await self.tease(now, name, "rival_overtake", label=label, mine=a, theirs=b)
        if changed:
            self.save()

    async def daily(self, now, name, mine, theirs):
        """Одна подначка в день по победам за день — если разница заметная."""
        day = mine["day"]
        if self.st.get("daily") == day or theirs.get("day") != day:
            return
        a, b = mine.get("kills_day") or 0, theirs.get("kills_day") or 0
        if abs(a - b) < max(self.cfg["lead_min"], 0.1 * max(a, b)):
            return
        if self.rng.random() > 0.3 + 0.7 * self.trait():
            self.st["daily"] = day                   # не в настроении подначивать сегодня
            self.save()
            return
        if await self.tease(now, name, "rival_lead" if a > b else "rival_behind", mine=a, theirs=b):
            self.st["daily"] = day
            self.save()

    # ---------- подначка ----------

    def said_today(self, now):
        said = self.st.get("said") or {}
        return said.get("n", 0) if said.get("day") == self.day(now) else 0

    def phrase(self, key, **fmt):
        own = (self.mind.persona.get("phrases") or {}).get(key)
        pool = own if isinstance(own, list) and own else PHRASES[key]
        try:
            text = self.rng.choice(pool).format(**fmt)
        except (KeyError, IndexError, ValueError):
            text = PHRASES[key][0].format(**fmt)
        return " ".join(text.split())[:MAX_TEXT]

    async def tease(self, now, name, key, **fmt):
        """Подначка сопернику. True — отправлена. Отношения не трогает."""
        if self.quarrel(name) or self.said_today(now) >= self.cfg["max_per_day"]:
            return False
        text = self.phrase(key, who=name, **fmt)
        s = getattr(self.mind, "s", None)
        crew, party = getattr(self.mind, "crew", None), getattr(self.mind, "party", None)
        if getattr(s, "llm_enabled", False):
            self.mind.trigger(f"Соперник {name} (житель): {text} — можно подначить по-дружески",
                              {"from": name}, kind="chat")
            sent = True
        elif crew and party and crew.active() and name in party.mates():
            sent = await crew.say("rival", text=text)
        else:
            await self.mind.execute([{"action": "whisper", "to": name, "text": f"{text} [chat:rival:4]"}],
                                    source="rivalry", reason=f"соперничество: {key} жителю {name}")
            sent = True
        if sent:
            self.st["said"] = {"day": self.day(now), "n": self.said_today(now) + 1}
            self.save()
            self.mind.mem.add_event("rival_said", {"peer": name, "key": key})
        return sent

    # ---------- мотив ----------

    def boost(self, need):
        """Множитель мотива: отстающему по уровню или победам недели progress выше (1..1 + boost)."""
        if need != "progress" or not self.st.get("rival"):
            return 1.0
        cmp = self.st.get("cmp") or {}
        if cmp.get("level", 0) < 0 or cmp.get("kills_week", 0) < 0:
            return round(1 + self.cfg["boost"] * self.trait(), 3)
        return 1.0

    def summary(self):
        if not self.st.get("rival"):
            return None
        cmp = self.st.get("cmp") or {}
        word = {1: "впереди", 0: "вровень", -1: "позади"}
        return {"соперник": self.st["rival"], **{METRICS[m]: word[v] for m, v in cmp.items() if m in METRICS}}


CHRONICLE_LINES = {
    "rival_overtook": lambda d: f"обогнал(а) {d.get('rival')} {d.get('label')} ({d.get('mine')} против {d.get('theirs')})",
    "rival_chosen": lambda d: f"соперник недели — {d.get('rival')}",
}
