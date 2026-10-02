"""Мотивы жителя (ORG-015) и характер числами (ORG-019). Правила без LLM, только факты игры.

Мотив — число 0..1 «насколько сейчас важно», из состояния тела и памяти:
    safety    — недавние смерти/опасность, низкий HP, восстановление;
    supply    — мало зелий, рюкзак тяжёлый, мало зени на закупку;
    progress  — давно нет нового уровня, места охоты слишком лёгкие;
    social    — давно не общался с жителями (быстрее растёт у общительных);
    curiosity — давно не был в новом месте;
    rest      — устал: доля пройденной сессии охоты, поздний час;
    wealth    — мало зени относительно цели;
    care      — житель группы в беде (умер, опасность, просил помочь).
Вес мотива — из черт характера (traits): например care ~ generosity, social ~ sociability.
Итог (value × вес) нужен модулям для выбора: длина сессии, карта охоты (смелость), частота общения,
поездка по делам. Модель видит мотивы в промпте как подсказку, решения по ним принимают правила.
"""
import time

TRAITS = ("bravery", "sociability", "greed", "curiosity", "diligence", "generosity", "patience", "whimsy")
DEFAULT_TRAIT = 0.5
HEAL_IDS = ("569", "501", "502", "503", "504")


def clamp(x):
    return max(0.0, min(1.0, float(x)))


def traits(persona):
    t = persona.get("traits") or {}
    return {k: clamp(t.get(k, DEFAULT_TRAIT)) for k in TRAITS}


WEIGHTS = {   # мотив -> (черта, базовый вес): вес = база + 0.5 * черта
    "safety": ("bravery", None),          # смелые меньше беспокоятся: 1 - 0.5 * смелость
    "supply": ("diligence", 0.6),
    "progress": ("diligence", 0.5),
    "social": ("sociability", 0.3),
    "curiosity": ("curiosity", 0.3),
    "rest": ("patience", None),           # терпеливые устают позже: 1 - 0.5 * терпение
    "wealth": ("greed", 0.3),
    "care": ("generosity", 0.5),
}


BOOST_RANGE = (0.6, 2.0)   # общий предел поправок мотива: шесть множителей вместе давали до ×4.9 (ревизия №3)


class Needs:
    def __init__(self, mind, clock=None):
        self.mind = mind
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.t = traits(mind.persona)

    def weight(self, need):
        trait, base = WEIGHTS[need]
        if base is None:
            return 1.0 - 0.5 * self.t[trait]
        return base + 0.5 * self.t[trait]

    def since(self, kind, default=86400):
        row = self.mind.mem.db.execute("SELECT MAX(ts) FROM events WHERE kind = ?", (kind,)).fetchone()
        return self.clock() - row[0] if row and row[0] else default

    def values(self):
        now = self.clock()
        s = self.mind.state
        mem = self.mind.mem
        r = self.mind.routine.st if getattr(self.mind, "routine", None) and self.mind.routine.st else {}
        hp = s.get("hp_pct")
        deaths = mem.count_events("died", now - 6 * 3600)
        danger = mem.count_events("danger", now - 600)
        safety = max(0.6 * min(deaths, 3) / 3 + 0.4 * min(danger, 3) / 3,
                     (1 - hp / 100) if isinstance(hp, (int, float)) else 0, 0.8 if r.get("recover") else 0)
        items = s.get("items") or {}
        potions = sum(int(items.get(i, 0) or 0) for i in HEAL_IDS) if items else None
        weight = s.get("weight_pct") or 0
        zeny = s.get("zeny") or 0
        supply = max(clamp((weight - 30) / 20),                      # 30% → 0, 50% → 1
                     clamp(1 - potions / 20) if potions is not None else 0)
        progress = clamp(self.since("level_up") / (8 * 3600))
        if r.get("day") and r.get("grow_day") == r.get("day"):
            progress = max(progress, 0.8)                            # места охоты стали лёгкими
        last_talk = max([v for k, v in getattr(self.mind.ctx, "last", {}).items() if k.startswith("talk:")] or [0])
        social = clamp((now - last_talk) / (3 * 3600)) if last_talk else 1.0
        places = mem.get("places", {}) or {}
        newest = max([p.get("first", 0) for p in places.values()] or [0])
        curiosity = clamp((now - newest) / (24 * 3600)) if newest else 0.5
        rest = 0.0
        if r.get("mode") == "hunt" and r.get("session_end"):
            rest = clamp(r.get("hunted", 0) / max(r["session_end"], 1))
        wealth = clamp(1 - zeny / 50000)
        care = clamp(0.5 * mem.count_events("party_member_dead", now - 1800) +
                     0.5 * mem.count_events("party_danger", now - 600))
        return {"safety": round(safety, 2), "supply": round(supply, 2), "progress": round(progress, 2),
                "social": round(social, 2), "curiosity": round(curiosity, 2), "rest": round(rest, 2),
                "wealth": round(wealth, 2), "care": round(care, 2)}

    def weighted(self):
        aims = getattr(self.mind, "aims", None)                     # ORG-038: невыполненные цели недели усиливают мотив
        rivalry = getattr(self.mind, "rivalry", None)               # rivalry: ORG-060 отстающему progress выше
        cal = getattr(self.mind, "calendar", None)                  # calendar: день недели и праздник (ORG-059)
        director = getattr(self.mind, "director", None)             # director: повод/затишье режиссёра (ORG-086)
        dream = getattr(self.mind, "dream", None)                   # dreams: мотив этапа мечты (ORG-081)
        def mult(k):   # произведение поправок мира (цели, мечта, соперник, календарь, режиссёр) — в пределах BOOST_RANGE
            m = ((aims.boost(k) if aims else 1.0)
                 * (dream.boost(k) if dream else 1.0)                    # dreams:
                 * (rivalry.boost(k) if rivalry else 1.0)                # rivalry:
                 * (cal.factor(k, self.clock()) if cal else 1.0)         # calendar:
                 * (director.boost(k) if director else 1.0))             # director:
            return min(BOOST_RANGE[1], max(BOOST_RANGE[0], m))
        return {k: round(v * self.weight(k) * mult(k), 2) for k, v in self.values().items()}

    def top(self, n=3):
        w = self.weighted()
        return sorted(w.items(), key=lambda kv: -kv[1])[:n]

    # ---------- параметры поведения из характера ----------

    def session_factor(self):
        """Усердные охотятся дольше, нетерпеливые — короче: множитель длины сессии 0.75..1.25."""
        return 0.75 + 0.5 * (0.6 * self.t["diligence"] + 0.4 * self.t["patience"])

    def risk_tolerance(self):
        """Допустимый риск карты (atlas.danger_for 0..1): смелый терпит больше; «день осторожности» режиссёра — ниже."""
        director = getattr(self.mind, "director", None)                         # director: ORG-086
        return (0.25 + 0.5 * self.t["bravery"]) * (director.risk_factor() if director else 1.0)   # director:

    def noise(self):
        """Доля случайности в выборе: причудливые менее предсказуемы."""
        return 0.05 + 0.25 * self.t["whimsy"]
