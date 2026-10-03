"""Бюджет внимания (ORG-109, Т-38): сколько житель говорит ПО СВОЕЙ ИНИЦИАТИВЕ — от характера и дел, не от времени в городе.

Суточный бюджет «инициатив» общий для говорящих модулей (social, gossip, rumors, rivalry, mentor, wed, director):
    база   = (base[0] + (base[1] − base[0]) · общительность) × настроение (1 / mood.talk_factor(), 0.7..1.3)
             × quiet_factor (0.5) в тихий день мира (world_calendar.is_quiet, ORG-110);
    дела   = deed_bonus за каждое дело дня из журнала событий (DEEDS: уровень, профессия, новое место, карта,
             первая встреча бестиария, гибель, меня вылечили, подарок, продажа, достижение, питомец), не больше
             deed_cap; плюс 1 жетон за каждые hunt_minutes охоты (routine.st["hunted"]) — «есть что рассказать»;
    остаток = база + дела − потрачено.
День бюджета — день жителя (с пробуждения, routine.today); без распорядка — сутки мира.
Цены (costs): chat 1 (открыть разговор), gossip 1, rumor 0.5, rival 1, mentor 0.5, wed 0, director 0.
Пара: не больше pair_max разговоров (chat), начатых мной, с одним жителем за день.

Протокол НЕ ограничивается: ответы [chat:*:2..4], [need:], [offer:], [order:], [party:], [crew:], [heal:], [spar:],
[mentor:ok|no], [wed:yes|no|off] — их модули бюджет не спрашивают. Модуль, получивший may() = False, ведёт себя так,
будто его пауза ещё не прошла: паузу не сбрасывает, событий не пишет (строки `# attention:` в модулях).
Отказ — decisions.jsonl {"type": "attention", "event": "deny", kind, peer, left}, не чаще раза в час на вид.

Модуль не тикает и не подписан на события тела: дела — это события памяти (mem.add_event), их число за день
читается одним запросом с кэшем CACHE_SEC. Снимок {day, base, deeds, spent, left} — kv attention (строка report).
Выключатель: BRAIN_DISABLE=attention или goals.json "attention": {"enabled": false} → mind.attention = None,
строки `# attention:` пропускают проверку — поведение как до ORG-109.
"""
import json
import time
from datetime import datetime, timedelta, timezone

from .world_calendar import is_quiet                                   # hush: ORG-110

DEEDS = ("level_up", "job_changed", "explore_found", "card_found", "bestiary_first", "died", "heal_confirmed",
         "gift_received", "trade_sold", "achievement_done", "pet_hatched")
DEFAULTS = {"enabled": True, "base": [3, 12], "deed_bonus": 1, "deed_cap": 6, "hunt_minutes": 90, "pair_max": 4,
            "quiet_factor": 0.5,                                        # hush: ORG-110 тихий день мира
            "costs": {"chat": 1, "gossip": 1, "rumor": 0.5, "rival": 1, "mentor": 0.5, "wed": 0, "director": 0}}
DENY_EVERY = 3600          # отказ одного вида — в журнал не чаще раза в час
CACHE_SEC = 30
MOOD_RANGE = (0.7, 1.3)


class Attention:
    # реестр модулей (modules.py, W8): только создание — не тикает, меток и событий нет
    ATTR, FEATURE, CONFIG, ENABLED, REQUIRES, ARGS = "attention", "attention", "attention", True, ("world",), "world"
    TICK_ORDER = None

    def __init__(self, mind, world=None, clock=None):
        self.mind = mind
        world = world or {}
        own = dict((world.get("attention") or {}))
        self.cfg = dict(DEFAULTS, **own)
        self.cfg["costs"] = dict(DEFAULTS["costs"], **(own.get("costs") or {}))
        self.tz = timezone(timedelta(hours=world.get("timezone_offset_hours", 0)))
        self.clock = clock or (lambda: time.time())   # время читается при вызове (реплей подменяет)
        self.st = mind.mem.get("attention") or {}
        self.denied = {}                              # вид -> когда отказ записан в журнал
        self.cache = (None, None, None)               # (время, день, число дел)

    # ---------- день ----------

    def routine(self):
        return getattr(self.mind, "routine", None)

    def day(self, now):
        r = self.routine()
        if r is not None and hasattr(r, "today"):
            try:
                return r.today(now)
            except Exception:                          # шпион/битый характер — сутки мира
                pass
        return datetime.fromtimestamp(now, self.tz).strftime("%Y-%m-%d")

    def day_start(self, now):
        """Начало дня жителя: полночь мира + сдвиг пробуждения (routine.day_shift)."""
        shift = 0.0
        r = self.routine()
        if r is not None and hasattr(r, "day_shift"):
            try:
                shift = float(r.day_shift())
            except Exception:
                shift = 0.0
        d = datetime.fromtimestamp(now - shift * 3600, self.tz)
        return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + shift * 3600

    def fresh(self, now):
        """Состояние бюджета на сегодня: новый день — сброс трат и пар."""
        day = self.day(now)
        if self.st.get("day") != day:
            self.st = {"day": day, "spent": 0.0, "pairs": {}}
        return self.st

    # ---------- бюджет ----------

    def trait(self, name):
        t = getattr(getattr(self.mind, "needs", None), "t", None)
        if not isinstance(t, dict):
            t = (self.mind.persona.get("traits") or {})
        try:
            return max(0.0, min(1.0, float(t.get(name, 0.5))))
        except (TypeError, ValueError):
            return 0.5

    def mood_factor(self):
        mood = getattr(self.mind, "mood", None)
        if mood is None:
            return 1.0
        try:
            f = float(mood.talk_factor())
        except Exception:
            return 1.0
        return max(MOOD_RANGE[0], min(MOOD_RANGE[1], 1.0 / f)) if f > 0 else 1.0

    def base(self, now):
        lo, hi = self.cfg["base"]
        b = (lo + (hi - lo) * self.trait("sociability")) * self.mood_factor()
        if is_quiet(self.mind, now):                                    # hush: ORG-110 тихий день — база ×quiet_factor
            b *= float(self.cfg.get("quiet_factor", 1.0))               # hush:
        return b

    def me(self):
        return (self.mind.state or {}).get("name") or self.mind.persona.get("name")

    def deed_count(self, now):
        """Дела дня по журналу событий (кэш CACHE_SEC); heal_confirmed — только если лечили меня."""
        day = self.day(now)
        t, cday, n = self.cache
        if t is not None and cday == day and abs(now - t) < CACHE_SEC:
            return n
        since = self.day_start(now)
        rows = self.mind.mem.db.execute(
            f"SELECT kind, data FROM events WHERE ts >= ? AND ts <= ? AND kind IN ({', '.join('?' * len(DEEDS))})",
            (since, now + 60, *DEEDS)).fetchall()
        n = 0
        for kind, data in rows:
            if kind == "heal_confirmed":
                try:
                    if json.loads(data).get("to") != self.me():
                        continue
                except (TypeError, ValueError, AttributeError):
                    continue
            n += 1
        self.cache = (now, day, n)
        return n

    def hunt_tokens(self, now):
        r = self.routine()
        st = getattr(r, "st", None) if r is not None else None
        if not isinstance(st, dict) or st.get("day") != self.day(now):
            return 0
        step = float(self.cfg["hunt_minutes"]) * 60
        return int(float(st.get("hunted") or 0) // step) if step > 0 else 0

    def budget(self, now=None):
        """{"base", "deeds", "spent", "left", "day"} на сегодня."""
        now = self.clock() if now is None else now
        st = self.fresh(now)
        base = self.base(now)
        deeds = min(self.cfg["deed_cap"], self.cfg["deed_bonus"] * self.deed_count(now)) + self.hunt_tokens(now)
        spent = float(st.get("spent", 0.0))
        return {"base": round(base, 2), "deeds": deeds, "spent": round(spent, 2),
                "left": round(base + deeds - spent, 2), "day": st["day"]}

    def cost(self, kind):
        try:
            return max(0.0, float(self.cfg["costs"].get(kind, 1)))
        except (TypeError, ValueError):
            return 1.0

    def may(self, kind, peer=None, now=None):
        """Можно ли сейчас начать инициативу вида kind (с жителем peer)."""
        now = self.clock() if now is None else now
        cost = self.cost(kind)
        st = self.fresh(now)
        b = self.budget(now)
        why = None
        if kind == "chat" and peer and int((st.get("pairs") or {}).get(peer, 0)) >= int(self.cfg["pair_max"]):
            why = "pair"
        elif cost > 0 and b["left"] < cost:
            why = "budget"
        if why is None:
            return True
        if now - self.denied.get(kind, -DENY_EVERY) >= DENY_EVERY:
            self.denied[kind] = now
            self.mind.write_decision({"type": "attention", "event": "deny", "kind": kind, "peer": peer,
                                      "left": b["left"], "why": why})
        return False

    def spend(self, kind, peer=None, now=None):
        now = self.clock() if now is None else now
        st = self.fresh(now)
        st["spent"] = round(float(st.get("spent", 0.0)) + self.cost(kind), 3)
        if kind == "chat" and peer:
            pairs = st.setdefault("pairs", {})
            pairs[peer] = int(pairs.get(peer, 0)) + 1
        b = self.budget(now)
        st["snapshot"] = {k: b[k] for k in ("base", "deeds", "spent", "left")}
        self.mind.mem.set("attention", st)

    def summary(self):
        b = self.budget()
        return f"потрачено {b['spent']:g} из {b['base'] + b['deeds']:g} (база {b['base']:g}, дел +{b['deeds']})"


def report_line(st):
    """Строка report из kv attention: «внимание: потрачено 7 из 11 (база 6, дел +5)»."""
    snap = (st or {}).get("snapshot") or {}
    if not snap:
        return None
    total = round(float(snap.get("base", 0)) + float(snap.get("deeds", 0)), 1)
    return (f"   внимание ({st.get('day')}): потрачено {snap.get('spent', 0):g} из {total:g} "
            f"(база {snap.get('base', 0):g}, дел +{snap.get('deeds', 0)})")
