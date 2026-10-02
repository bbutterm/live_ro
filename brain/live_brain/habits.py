"""Привычки и скука (ORG-068). Правила без LLM, только факты памяти: события activity {name} (activity.start)
и social_walk {point} (прогулка к точке города) за window_days (7 суток).

Привычки (пересчёт раз в recount_minutes):
    after — последние streak (3) раза после занятия A следующим (не позже after_hours) было одно и то же B:
            «после охоты — по делам»; действует, когда текущее занятие — A;
    hour  — занятие B начиналось около одного часа (±1 по времени мира) в ≥ hour_days разных днях: «вечером гуляю»;
    place — точка прогулки выбиралась в ≥ place_days разных днях и чаще прочих: «люблю фонтан».
Новая привычка — событие habit_formed, воспоминание note «Привык(ла): …», решение type habits; пропала (повторов
в окне нет) — habit_lost. Привычка: положительная оценка занятия × habit_gain (1.5), вес точки × habit_gain.

Скука — долгая память однообразия (crowd.py, ORG-089, штрафует повторы только ЗА СЕГОДНЯ и толпу — здесь неделя):
    доля занятия = Σ 0.5^(возраст / boredom_half_days) его начал / Σ всех (при ≥ min_starts начал, иначе 0);
    boredom = clamp((наибольшая доля − 0.35) / 0.45), 0..1; штраф занятию boredom_weight × доля × whimsy —
    не причудливым однообразие почти не мешает («привычка и скука спорят, скука сильнее у причудливых»).
Причуда: boredom ≥ whim_at (0.7) и whimsy ≥ whim_whimsy (0.3) — не чаще раза в сутки на whim_minutes (60):
    привычки не действуют, занятию «давно не делал» (≥ stale_days или никогда) +whim_bonus, точке прогулки, где
    давно не был, × 2. Событие habit_whim, воспоминание «Надоело одно и то же — пойду куда-нибудь ещё».
Подключение: activity.scores (score = habits.adjust(...), # habits:) и social.pick_point (× point_factor, # habits:).
Выключатель: BRAIN_DISABLE=habits или goals.json "habits": {"enabled": false}; без событий — нейтрально.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("habits")

DEFAULTS = {"enabled": True, "recount_minutes": 10, "window_days": 7, "streak": 3, "after_hours": 3,
            "hour_days": 3, "place_days": 3, "habit_gain": 1.5, "min_starts": 6, "boredom_half_days": 2,
            "boredom_weight": 0.3, "whim_at": 0.7, "whim_whimsy": 0.3, "whim_minutes": 60, "whim_bonus": 0.5,
            "stale_days": 3}
BORED_FROM, BORED_SPAN = 0.35, 0.45


class Habits:
    # реестр модулей (modules.py, W8): создание, тик, подписки, промпт
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "habits", "habits", "habits", True, "world"
    TICK_ORDER = 205                  # после crowd (200), до episodes (210)
    PROMPT = [("привычки", "summary", 225)]

    def __init__(self, mind, world=None, clock=None, rng=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("habits") or {}))
        self.tz = timezone(timedelta(hours=(world or {}).get("timezone_offset_hours", 0)))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.st = mind.mem.get("habits") or {}
        self.st.setdefault("habits", {})
        self.next_recount = 0.0
        self.shares = {}                              # занятие -> доля за окно (свежие весомее)
        self.bored = 0.0
        self.last_point = {}                          # точка прогулки -> когда была последний раз

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("habits", self.st)

    def whimsy(self):
        return (getattr(getattr(self.mind, "needs", None), "t", None) or {}).get("whimsy", 0.5)

    def local(self, ts):
        return datetime.fromtimestamp(ts, self.tz)

    def rows(self, kind, field, since):
        out = []
        for ts, data in self.mind.mem.db.execute("SELECT ts, data FROM events WHERE kind = ? AND ts >= ? ORDER BY id",
                                                 (kind, since)):
            try:
                v = json.loads(data).get(field)
            except (ValueError, AttributeError):
                continue
            if v:
                out.append((ts, str(v)))
        return out

    def label(self, name):
        acts = getattr(self.mind, "activities", None)
        cat = getattr(acts, "catalog", None) or {}
        return (cat.get(name) or {}).get("label") or name

    def habits(self):
        return self.st["habits"]

    # ---------- пересчёт ----------

    def recount(self, now):
        since = now - self.cfg["window_days"] * 86400
        acts = self.rows("activity", "name", since)
        walks = self.rows("social_walk", "point", since)
        found = {}
        found.update(self.after_habits(acts))
        found.update(self.hour_habits(acts))
        found.update(self.place_habits(walks))
        self.last_point = {}
        for ts, p in walks:
            self.last_point[p] = ts
        self.boredom_from(acts, now)
        old = self.habits()
        for key, h in found.items():
            if key not in old:
                h["since"] = now
                self.note("habit_formed", f"Привык(ла): {h['text']}.", h)
            else:
                h["since"] = old[key].get("since", now)
        for key in [k for k in old if k not in found]:
            self.note("habit_lost", f"Отвык(ла): {old[key].get('text')}.", old[key], remember=False)
        self.st["habits"] = found
        self.whim_check(now)
        self.save()

    def after_habits(self, acts):
        nxt = {}
        for i, (ts, a) in enumerate(acts[:-1]):
            ts2, b = acts[i + 1]
            if b != a and ts2 - ts <= self.cfg["after_hours"] * 3600:
                nxt.setdefault(a, []).append(b)
        out = {}
        n = self.cfg["streak"]
        for a, bs in nxt.items():
            last = bs[-n:]
            if len(last) == n and len(set(last)) == 1:
                b = last[0]
                out[f"after:{a}:{b}"] = {"kind": "after", "cond": a, "what": b, "n": n,
                                         "text": f"после «{self.label(a)}» — «{self.label(b)}»"}
        return out

    def hour_habits(self, acts):
        days = {}                                     # (занятие, час) -> {дни}
        for ts, name in acts:
            d = self.local(ts)
            days.setdefault((name, d.hour), set()).add(d.date())
        out = {}
        best = {}
        for (name, h), _ in days.items():
            near = set().union(*(days.get((name, (h + k) % 24), set()) for k in (-1, 0, 1)))
            if len(near) >= self.cfg["hour_days"] and len(near) > best.get(name, (0, 0))[0]:
                best[name] = (len(near), h)
        for name, (n, h) in best.items():
            out[f"hour:{name}"] = {"kind": "hour", "cond": h, "what": name, "n": n,
                                   "text": f"около {h}:00 — «{self.label(name)}»"}
        return out

    def place_habits(self, walks):
        days = {}
        for ts, p in walks:
            days.setdefault(p, set()).add(self.local(ts).date())
        if not days:
            return {}
        ranked = sorted(days.items(), key=lambda kv: -len(kv[1]))
        p, d = ranked[0]
        if len(d) < self.cfg["place_days"] or (len(ranked) > 1 and len(ranked[1][1]) == len(d)):
            return {}
        return {f"place:{p}": {"kind": "place", "cond": None, "what": p, "n": len(d),
                               "text": f"любимое место — {p}"}}

    def boredom_from(self, acts, now):
        weights = {}
        for ts, name in acts:
            weights[name] = weights.get(name, 0.0) + 0.5 ** (max(0.0, now - ts) / (self.cfg["boredom_half_days"] * 86400))
        total = sum(weights.values())
        if len(acts) < self.cfg["min_starts"] or not total:
            self.shares, self.bored = {}, 0.0
            return
        self.shares = {k: round(v / total, 3) for k, v in weights.items()}
        top = max(self.shares.values())
        self.bored = round(max(0.0, min(1.0, (top - BORED_FROM) / BORED_SPAN)), 3)

    def note(self, kind, text, h, remember=True):
        if remember:
            self.mind.mem.remember(text, 2, kind="note")
        self.mind.mem.add_event(kind, {"kind": h.get("kind"), "what": h.get("what"), "cond": h.get("cond")})
        self.mind.write_decision({"type": "habits", "event": kind, "text": text})
        log.info("%s", text)

    # ---------- скука и причуда ----------

    def boredom(self, now=None):
        return self.bored

    def whim(self, now=None):
        now = now or self.clock()
        return now < self.st.get("whim_until", 0)

    def whim_check(self, now):
        if self.whim(now) or now - self.st.get("whim_at", 0) < 86400:
            return
        if self.bored < self.cfg["whim_at"] or self.whimsy() < self.cfg["whim_whimsy"]:
            return
        self.st["whim_at"] = now
        self.st["whim_until"] = now + self.cfg["whim_minutes"] * 60
        top = max(self.shares, key=self.shares.get) if self.shares else None
        self.st["whim_from"] = top
        self.mind.mem.remember("Надоело одно и то же — пойду куда-нибудь ещё.", 2, kind="note")
        self.mind.mem.add_event("habit_whim", {"boredom": self.bored, "from": top})
        self.mind.write_decision({"type": "habits", "event": "habit_whim", "boredom": self.bored, "from": top})
        log.info("скука %.2f: надоело «%s»", self.bored, top)

    def stale(self, name, now):
        acts = getattr(self.mind, "activities", None)
        last = ((getattr(acts, "st", None) or {}).get("last") or {}).get(name, 0)
        return name != self.st.get("whim_from") and now - last >= self.cfg["stale_days"] * 86400

    # ---------- для выбора ----------

    def applies(self, h, current, now):
        if h["kind"] == "after":
            return current == h["cond"]
        if h["kind"] == "hour":
            diff = abs(self.local(now).hour - int(h["cond"])) % 24
            return min(diff, 24 - diff) <= 1
        return False

    def adjust(self, name, score, current=None, now=None):
        """Оценка занятия для activity.scores: привычка ×, скука −, причуда + («давно не делал»)."""
        now = now or self.clock()
        if self.whim(now):
            if self.stale(name, now):
                score += self.cfg["whim_bonus"]
        elif score > 0 and any(h["what"] == name and self.applies(h, current, now) for h in self.habits().values()
                               if h["kind"] in ("after", "hour")):
            score *= self.cfg["habit_gain"]
        score -= self.cfg["boredom_weight"] * self.shares.get(name, 0.0) * self.whimsy()
        return round(score, 3)

    def point_factor(self, point, now=None):
        """Вес точки прогулки для social.pick_point: любимая × habit_gain; при причуде давняя × 2."""
        now = now or self.clock()
        if self.whim(now):
            return 2.0 if now - self.last_point.get(point, 0) >= self.cfg["stale_days"] * 86400 else 1.0
        return self.cfg["habit_gain"] if f"place:{point}" in self.habits() else 1.0

    # ---------- такт ----------

    def tick(self):
        now = self.clock()
        if now < self.next_recount:
            return
        self.next_recount = now + self.cfg["recount_minutes"] * 60
        self.recount(now)

    def summary(self):
        hs = [h["text"] for h in self.habits().values()]
        if not hs and not self.bored:
            return None
        out = {"привычки": hs[:5], "скука": self.bored}
        if self.whim():
            out["причуда"] = "надоело одно и то же — хочется нового"
        return out
