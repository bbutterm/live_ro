"""Дрейф характера (ORG-092, ТЗ Т-47): черты медленно сдвигаются от опыта. Правила без LLM, только факты памяти.

Врождённые черты — persona.traits (файл персоны НЕ меняется). Сдвиг хранится в kv traits_drift
{"drift": {черта: Δ}, "innate": {...}, "week": начало недели, "minutes0": минуты охоты на начало недели, "last": {...}}.
При создании модуля (раньше остальных модулей реестра) эффективные черты = clamp(врождённые + Δ) записываются в
mind.persona["traits"] (новый словарь; врождённые — persona["traits_innate"]) и в mind.needs.t — так их видят все
модули, читающие черты (needs, attention, economy, healer, dream, aims, maps, scars…).

Раз в week_days (7) правила по фактам недели:
    смелость    −0.04 за каждую смерть (died); без смертей +0.02 за каждые 10 ч охоты (минуты kv map_stats);
                +0.01 за победу над «сильным» (монстр, который меня убивал, или уровень атласа ≥ мой + 3), ≤ +0.03;
    щедрость    +0.01 за подарок или лечение другого (gift_given, heal_given), ≤ +0.03;
    общительность −0.02 при нуле разговоров за неделю (одиночество), +0.02 при ≥ talk_many (14);
    любопытство +0.02 при ≥ 2 новых местах (kv places, first в окне).
Перед шагом весь сдвиг притягивается к врождённому: Δ ×(1 − anchor), anchor 0.1 (не даёт всем сойтись к одному
характеру). Шаг одной черты за неделю ≤ step_max (0.05), сдвиг ≤ limit (0.2) от врождённой, черта в 0..1.
Пропущенные недели не догоняются: житель был выключен — новая неделя начинается с момента запуска.
Событие trait_shift {changes {черта: [было, стало]}, reasons {черта: причина}} — память (важность 3), решения,
хроника («стал осторожнее (3 смерти за неделю)»).
Выключатель: BRAIN_DISABLE=drift или "drift": {"enabled": false} в goals.json — черты = персона.
"""
import json
import logging
import time

log = logging.getLogger("drift")

TRAITS = ("bravery", "sociability", "greed", "curiosity", "diligence", "generosity", "patience", "whimsy")
RU = {"bravery": "смелость", "sociability": "общительность", "greed": "жадность", "curiosity": "любопытство",
      "diligence": "усердие", "generosity": "щедрость", "patience": "терпение", "whimsy": "причудливость"}
MORE = {"bravery": ("смелее", "осторожнее"), "sociability": ("общительнее", "замкнутее"),
        "generosity": ("щедрее", "скупее"), "curiosity": ("любопытнее", "домоседливее"),
        "greed": ("жаднее", "бескорыстнее"), "diligence": ("усерднее", "ленивее"),
        "patience": ("терпеливее", "нетерпеливее"), "whimsy": ("причудливее", "предсказуемее")}
DEFAULTS = {"enabled": True, "week_days": 7, "anchor": 0.1, "step_max": 0.05, "limit": 0.2,
            "death_step": 0.04, "hunt_step": 0.02, "hunt_hours": 10, "strong_step": 0.01, "strong_cap": 0.03,
            "strong_levels": 3, "give_step": 0.01, "give_cap": 0.03, "lonely_step": 0.02, "talk_many": 14,
            "talk_step": 0.02, "new_places": 2, "curious_step": 0.02}
TALK_KINDS = ("social_said", "social_heard", "meeting_confirmed")
GIVE_KINDS = ("gift_given", "heal_given")


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def innate_of(persona):
    """Врождённые черты: persona.traits_innate (сдвиг уже применён) или persona.traits."""
    src = persona.get("traits_innate") or persona.get("traits") or {}
    out = {}
    for k in TRAITS:
        try:
            out[k] = clamp(src.get(k, 0.5))
        except (TypeError, ValueError):
            out[k] = 0.5
    return out


def shift_text(trait, delta):
    up, down = MORE.get(trait, (f"{RU.get(trait, trait)} выше", f"{RU.get(trait, trait)} ниже"))
    return up if delta > 0 else down


def report_line(kv):
    """Строка report/census: «характер: смелость 0.52 (врожд. 0.60, −0.08); …» — только сдвинутые черты."""
    if not isinstance(kv, dict):
        return None
    drift = kv.get("drift") or {}
    innate = kv.get("innate") or {}
    parts = []
    for k in TRAITS:
        d = float(drift.get(k) or 0)
        if abs(d) < 0.005:
            continue
        base = float(innate.get(k, 0.5))
        parts.append(f"{RU[k]} {clamp(base + d):.2f} (врожд. {base:.2f}, {d:+.2f})")
    return "характер: " + ("; ".join(parts) if parts else "как врождённый")


def chronicle_text(d):
    reasons = d.get("reasons") or {}
    changes = d.get("changes") or {}
    out = []
    for k, (a, b) in changes.items():
        why = reasons.get(k)
        out.append(f"стал(а) {shift_text(k, b - a)}" + (f" ({why})" if why else ""))
    return "; ".join(out) or "характер изменился"


CHRONICLE_LINES = {"trait_shift": chronicle_text}


class TraitDrift:
    # реестр модулей (modules.py, W8): создаётся первым среди обычных модулей — до тех, кто читает черты
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "drift", "drift", "drift", True, "world"
    TICK_ORDER = 3
    TICK_EVERY = 600

    def __init__(self, mind, world=None, clock=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **(((world or {}).get("drift")) or {}))
        self.clock = clock or (lambda: time.time())
        self.innate = innate_of(mind.persona)
        self.st = mind.mem.get("traits_drift") or {}
        if not isinstance(self.st.get("drift"), dict):
            self.st["drift"] = {}
        self.apply()

    # ---------- черты ----------

    def drift(self):
        return {k: float(self.st["drift"].get(k) or 0.0) for k in TRAITS}

    def effective(self):
        d = self.drift()
        return {k: round(clamp(self.innate[k] + d[k]), 3) for k in TRAITS}

    def apply(self):
        """Эффективные черты -> persona.traits (новый словарь), врождённые -> persona.traits_innate, needs.t."""
        p = self.mind.persona
        eff = self.effective()
        p["traits_innate"] = dict(self.innate)
        p["traits"] = {**(p.get("traits") or {}), **eff}
        needs = getattr(self.mind, "needs", None)
        if needs is not None and isinstance(getattr(needs, "t", None), dict):
            needs.t = dict(eff)
        return eff

    def save(self):
        self.st["innate"] = dict(self.innate)
        self.mind.mem.set("traits_drift", self.st)

    # ---------- факты недели ----------

    def hunt_minutes(self):
        st = self.mind.mem.get("map_stats") or {}
        return sum(float((m or {}).get("minutes") or 0) for m in st.values() if isinstance(m, dict))

    def strong_wins(self, since, now):
        lv = (self.mind.state or {}).get("lv")
        risk = self.mind.mem.get("monster_risk") or {}
        killers = {m for m, r in risk.items() if (r or {}).get("killed_me")}
        info = {}
        n = 0
        for (data,) in self.mind.mem.db.execute(
                "SELECT data FROM events WHERE kind = 'kill' AND ts >= ? AND ts <= ?", (since, now + 60)):
            try:
                mob = json.loads(data).get("monster")
            except (TypeError, ValueError):
                continue
            if not mob:
                continue
            if mob in killers:
                n += 1
                continue
            if isinstance(lv, (int, float)) and lv:
                if mob not in info:
                    try:
                        from . import atlas
                        info[mob] = (atlas.default().monster_info(mob) or {}).get("level")
                    except (OSError, ValueError, KeyError, TypeError):
                        info[mob] = None
                if isinstance(info[mob], (int, float)) and info[mob] >= lv + self.cfg["strong_levels"]:
                    n += 1
        return n

    def week_facts(self, since, now):
        mem = self.mind.mem
        places = mem.get("places") or {}
        minutes0 = float(self.st.get("minutes0") or 0)
        return {
            "deaths": mem.count_events("died", since),
            "hunt_hours": max(0.0, self.hunt_minutes() - minutes0) / 60,
            "strong_wins": self.strong_wins(since, now),
            "given": sum(mem.count_events(k, since) for k in GIVE_KINDS),
            "talks": sum(mem.count_events(k, since) for k in TALK_KINDS),
            "new_places": sum(1 for p in places.values()
                              if isinstance(p, dict) and p.get("source") == "seen" and (p.get("first") or 0) >= since),
        }

    def proposal(self, f):
        """{черта: (шаг, причина)} — сырые шаги по правилам недели (до якоря и пределов)."""
        c = self.cfg
        out = {}
        brave, why = 0.0, []
        if f["deaths"]:
            brave -= c["death_step"] * f["deaths"]
            why.append(f"{f['deaths']} {plural(f['deaths'], 'смерть', 'смерти', 'смертей')} за неделю")
        else:
            blocks = int(f["hunt_hours"] // c["hunt_hours"])
            if blocks:
                brave += c["hunt_step"] * blocks
                why.append(f"{int(f['hunt_hours'])} ч охоты без смертей")
        if f["strong_wins"]:
            brave += min(c["strong_cap"], c["strong_step"] * f["strong_wins"])
            why.append(f"{f['strong_wins']} {plural(f['strong_wins'], 'победа', 'победы', 'побед')} над сильными")
        if brave:
            out["bravery"] = (brave, ", ".join(why))
        if f["given"]:
            out["generosity"] = (min(c["give_cap"], c["give_step"] * f["given"]),
                                 f"{f['given']} {plural(f['given'], 'подарок', 'подарка', 'подарков')} и лечений")
        if f["talks"] == 0:
            out["sociability"] = (-c["lonely_step"], "неделя без разговоров")
        elif f["talks"] >= c["talk_many"]:
            out["sociability"] = (c["talk_step"], f"{f['talks']} разговоров за неделю")
        if f["new_places"] >= c["new_places"]:
            out["curiosity"] = (c["curious_step"], f"{f['new_places']} новых мест")
        return out

    # ---------- неделя ----------

    def tick(self, now=None):
        now = now or self.clock()
        week = float(self.st.get("week") or 0)
        span = self.cfg["week_days"] * 86400
        if not week or now - week > 2 * span:          # первый запуск или долгий простой — неделя заново
            self.st.update(week=now, minutes0=self.hunt_minutes())
            self.save()
            return None
        if now - week < span:
            return None
        return self.shift(week, now)

    def shift(self, since, now):
        c = self.cfg
        facts = self.week_facts(since, now)
        prop = self.proposal(facts)
        before = self.effective()
        drift = self.drift()
        reasons = {}
        for k in TRAITS:
            d = drift[k] * (1 - c["anchor"])                    # якорь: тянет к врождённой черте
            step, why = prop.get(k, (0.0, None))
            step = max(-c["step_max"], min(c["step_max"], step))
            d = max(-c["limit"], min(c["limit"], d + step))
            d = clamp(self.innate[k] + d) - self.innate[k]      # черта остаётся в 0..1
            drift[k] = round(d, 4)
            if why and step:
                reasons[k] = why
        self.st["drift"] = {k: v for k, v in drift.items() if abs(v) >= 1e-4}
        self.st.update(week=now, minutes0=self.hunt_minutes(), last={"ts": now, "facts": facts, "reasons": reasons})
        after = self.apply()
        self.save()
        changes = {k: [before[k], after[k]] for k in TRAITS if abs(after[k] - before[k]) >= 0.005}
        if changes:
            data = {"changes": changes, "reasons": {k: v for k, v in reasons.items() if k in changes}}
            self.mind.mem.add_event("trait_shift", data)
            text = chronicle_text(data)
            self.mind.mem.remember(f"За неделю я {text}.", 3)
            write = getattr(self.mind, "write_decision", None)
            if write:
                write({"type": "trait_shift", **data})
            log.info("характер: %s", text)
        return changes

    def summary(self):
        return report_line({"drift": self.st.get("drift"), "innate": self.innate})
