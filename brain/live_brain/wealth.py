"""Относительная бедность: мотив денег не умирает (ORG-100, часть 1; ТЗ Т-43 в docs/IDEAS2.md). Правила без LLM.

Раньше мотив wealth = 1 − zeny / 50 000: через неделю-две у всех больше 50 000 z, мотив навсегда 0, и жадный ведёт
себя как щедрый (риск R8). Теперь — от того, сколько житель хочет, и от того, как живут соседи:
    target = max(floor, цель копилки мечты, median_factor × медиана зени мира) × (1 − greed_scale/2 + greed_scale × жадность)
    wealth = 1 − (зени + банк копилки) / target, 0..1.
Медиана мира — по последним снимкам presence в шине (crowd.py кладёт туда zeny), я тоже в выборке; других жителей
нет или нет шины — медиана не участвует. Пересчёт не чаще cache_seconds, снимок — kv wealth (для report).
Выключен (BRAIN_DISABLE=wealth или goals.json wealth.enabled=false) — needs.py считает по-старому (50 000).
Учёт притока и стоков (вторая часть ORG-100) — позже: событий покупки у NPC в мосте пока нет.
"""
import logging
import time

log = logging.getLogger("wealth")

DEFAULTS = {"enabled": True, "floor": 5000, "median_factor": 1.5, "median_days": 3, "greed_scale": 0.5,
            "cache_seconds": 600}
ABSOLUTE = 50000                   # прежний порог (needs.py без модуля)


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


class Wealth:
    # реестр модулей (modules.py, W8): только создание, без тика и подписок
    ATTR, FEATURE, CONFIG, ENABLED, ARGS = "wealth", "wealth", "wealth", True, "world"
    TICK_ORDER = None

    def __init__(self, mind, world=None, clock=None):
        self.mind = mind
        self.cfg = dict(DEFAULTS, **((world or {}).get("wealth") or {}))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.cache = (0.0, [])                         # (когда прочитано, [зени других жителей])

    # ---------- данные ----------

    @property
    def bus(self):
        return getattr(getattr(self.mind, "world", None), "bus", None)

    def greed(self):
        return float((getattr(getattr(self.mind, "needs", None), "t", None) or {}).get("greed", 0.5))

    def own(self, state):
        bank = (self.mind.mem.get("savings") or {}).get("bank") or 0
        return int((state or {}).get("zeny") or 0) + int(bank)

    def goal(self):
        dream = getattr(self.mind, "dream", None)
        if dream is None or not hasattr(dream, "save_target"):
            return 0
        try:
            return int(dream.save_target()[0] or 0)
        except (KeyError, TypeError, ValueError):
            return 0

    def others(self, now):
        """Зени других жителей из шины (presence.zeny моложе median_days); кэш cache_seconds."""
        ts, data = self.cache
        if ts and now - ts < self.cfg["cache_seconds"]:
            return data
        data = []
        bus = self.bus
        if bus is not None:
            try:
                rows = bus.latest("presence", now - self.cfg["median_days"] * 86400)
            except Exception as e:                       # общая БД занята — без медианы до следующего раза
                log.warning("шина мира недоступна: %s", e)
                rows = {}
            for rec in rows.values():
                z = (rec.get("data") or {}).get("zeny")
                if isinstance(z, (int, float)) and z >= 0:
                    data.append(int(z))
        self.cache = (now, data)
        return data

    def median(self, own, now=None):
        others = self.others(now or self.clock())
        return median(others + [own]) if others else None

    # ---------- мотив ----------

    def target(self, state=None, now=None):
        now = now or self.clock()
        state = state if state is not None else (self.mind.state or {})
        own = self.own(state)
        med = self.median(own, now)
        goal = self.goal()
        g = self.cfg["greed_scale"]
        scale = 1 - g / 2 + g * self.greed()
        base = max(self.cfg["floor"], goal, self.cfg["median_factor"] * med if med is not None else 0)
        return {"target": int(base * scale), "floor": self.cfg["floor"], "goal": goal,
                "median": int(med) if med is not None else None, "scale": round(scale, 2), "own": own}

    def value(self, state=None, now=None):
        now = now or self.clock()
        fresh = not (self.cache[0] and now - self.cache[0] < self.cfg["cache_seconds"])
        t = self.target(state, now)
        v = round(max(0.0, min(1.0, 1 - t["own"] / max(1, t["target"]))), 2)
        if fresh:                                        # снимок для report — только при пересчёте медианы
            try:
                self.mind.mem.set("wealth", dict(t, value=v, ts=round(now)))
            except Exception as e:                       # noqa: BLE001 — мотив не падает из-за отчёта
                log.warning("снимок достатка не записан: %s", e)
        return v


def report_line(snap):
    """Строка report: «достаток: 120000 из 315000 z (медиана мира 200000 × 1.5; копилка 30000) — мотив 0.62»."""
    if not isinstance(snap, dict) or not snap.get("target"):
        return None
    why = []
    if snap.get("median") is not None:
        why.append(f"медиана мира {snap['median']}")
    if snap.get("goal"):
        why.append(f"копилка {snap['goal']}")
    return (f"достаток: {snap.get('own', 0)} из {snap['target']} z"
            + (f" ({'; '.join(why)})" if why else f" (порог {snap.get('floor')})")
            + f" — мотив {snap.get('value', 0):.2f}")
