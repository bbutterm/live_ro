"""Распорядок дня и глобальные цели (brain/world/goals.json + routine в характере).

Жители не качаются круглые сутки: в день 4-5 часов охоты сессиями по 60-100 минут,
между ними и после дневной нормы — отдых в городе (сидят, общаются).
Всё это — правила без LLM (тик 1 с). Модель видит распорядок и цели в промпте и может
лишь выбрать предпочитаемую карту охоты на следующую сессию.

Режимы (kv "routine" в SQLite):
    hunt  — охота на карте охоты (OpenKore: lockMap <карта>, без точки)
    town  — отдых в городе у точки (lockMap <город>, lockMap_x/y; сидит по прибытии)

Усталость считается только по фактическому времени на карте охоты (жив, карта из списка).
Переходы:
    hunt -> town: сессия закончилась или дневная норма выбрана;
    town -> hunt: перерыв прошёл и норма не выбрана; новый день — новая норма.
Сверка с игрой каждый тик: если настройка OpenKore не соответствует режиму (например, после
перезапуска бота), команда отправляется заново — не чаще раза в RESEND секунд.
Во время активного плана встречи распорядок не вмешивается.
"""
import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("routine")

RESEND = 60
MAX_TICK_GAP = 5          # не засчитывать охоту за время, когда мозг не работал
STUCK_SEC = 300           # на охоте без движения и без боя дольше — «застрял»
UNSTUCK_GAP = 120         # не чаще раза в 2 минуты
DEATH_WINDOW = 1800       # 3 смерти за 30 минут — отдых и карта полегче
DEATH_LIMIT = 3


def load_world(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def merged_routine(world, persona):
    routine = dict(world.get("routine", {}))
    routine.update(persona.get("routine", {}))
    return routine


class Routine:
    def __init__(self, mind, world, rng=None, clock=time.time):
        self.mind = mind
        self.world = world
        self.cfg = merged_routine(world, mind.persona)
        self.town = self.cfg["town"]
        self.tz = timezone(timedelta(hours=world.get("timezone_offset_hours", 0)))
        self.rng = rng or random.Random()
        self.clock = clock
        self.last_tick = None
        self.last_sent = 0.0
        self.anchor = None          # (map, x, y, время) — где стоял в последний раз
        self.last_combat = 0.0
        self.last_unstuck = 0.0
        self.st = mind.mem.get("routine") or {}

    # ---------- данные ----------

    def save(self):
        self.mind.mem.set("routine", self.st)

    def today(self, now):
        return datetime.fromtimestamp(now, self.tz).strftime("%Y-%m-%d")

    def minutes(self, key):
        lo, hi = self.cfg[key]
        return self.rng.uniform(lo, hi) * 60

    def hunt_map(self):
        pref = self.st.get("prefer_map")
        maps = self.mind.persona["hunt_maps"]
        return pref if pref in maps else maps[0]

    def summary(self, now=None):
        now = now or self.clock()
        if not self.st:
            return None
        s = self.st
        out = {"режим": "охота" if s["mode"] == "hunt" else "отдых в городе",
               "наохотился_сегодня_мин": int(s["hunted"] / 60),
               "норма_на_день_мин": int(s["budget"] / 60),
               "карта_охоты": self.hunt_map(),
               "город": self.town["map"]}
        if s["mode"] == "hunt":
            out["до_конца_сессии_мин"] = max(0, int((s["session_end"] - s["hunted"]) / 60))
        else:
            if s["rest_until"] == float("inf"):
                out["отдых"] = "до завтра: дневная норма охоты выбрана"
            else:
                out["отдых_ещё_мин"] = max(0, int((s["rest_until"] - now) / 60))
        return out

    def goals(self):
        state = self.mind.state
        result = []
        for g in self.world.get("goals", []) + self.mind.persona.get("goals_extra", []):
            item = {"цель": g["text"]}
            if g.get("metric") and state.get(g["metric"]) is not None:
                item["сейчас"] = state[g["metric"]]
                item["нужно"] = g.get("target")
            result.append(item)
        return result

    # ---------- тик ----------

    def new_day(self, now, keep_mode=None):
        if self.st.get("day"):
            self.diary(self.st)
        self.st = {"day": self.today(now), "budget": self.minutes("hunt_hours_per_day") * 60,
                   "hunted": 0.0, "mode": keep_mode or "hunt", "mode_since": now,
                   "session_end": 0.0, "rest_until": 0.0, "arrived": False,
                   "prefer_map": self.st.get("prefer_map")}
        self.st["session_end"] = min(self.st["budget"], self.minutes("session_minutes"))
        log.info("новый день %s: норма охоты %d мин", self.st["day"], self.st["budget"] / 60)

    async def tick(self):
        now = self.clock()
        if not self.mind.fresh_state:
            return
        state = self.mind.state
        if not self.st:
            self.new_day(now)
        elif self.st["day"] != self.today(now):
            self.new_day(now, keep_mode=self.st["mode"])
            if self.st["mode"] == "town":
                self.st["rest_until"] = now          # новый день: можно идти охотиться сразу
        gap = (now - self.last_tick) if self.last_tick else 0
        self.last_tick = now
        if self.mind.plans.store.active():
            self.save()
            return                                    # план встречи важнее распорядка

        if self.st["mode"] == "hunt":
            if (0 < gap <= MAX_TICK_GAP and not state.get("dead")
                    and state.get("map") in self.mind.persona["hunt_maps"]):
                self.st["hunted"] += gap
                await self.check_stuck(now, state)
            if self.st["hunted"] >= self.st["session_end"] or self.st["hunted"] >= self.st["budget"]:
                await self.to_town(now)
        else:
            await self.in_town(now, state)
        await self.enforce(now, state)
        self.save()

    # ---------- самостоятельность: застревание, смерти, дневник ----------

    def on_combat(self, now=None):
        self.last_combat = now or self.clock()

    async def check_stuck(self, now, state):
        pos = (state.get("map"), state.get("x"), state.get("y"))
        if pos[1] is None:
            return
        if (not self.anchor or self.anchor[0] != pos[0]
                or max(abs(self.anchor[1] - pos[1]), abs(self.anchor[2] - pos[2])) > 2):
            self.anchor = (pos[0], pos[1], pos[2], now)
            return
        still = now - self.anchor[3]
        if (still >= STUCK_SEC and now - self.last_combat >= STUCK_SEC
                and now - self.last_unstuck >= UNSTUCK_GAP):
            self.last_unstuck = now
            self.anchor = (pos[0], pos[1], pos[2], now)
            self.note("routine_stuck", f"Застрял на {pos[0]} ({pos[1]},{pos[2]}) — {int(still / 60)} мин "
                                       "без движения и боя, пробую выбраться.", 1)
            await self.send({"action": "unstuck"}, "распорядок: застрял")

    async def on_death(self, now=None):
        now = now or self.clock()
        deaths = self.mind.mem.count_events("died", now - DEATH_WINDOW)
        if deaths < DEATH_LIMIT or not self.st or self.st.get("mode") != "hunt":
            return
        maps = self.mind.persona["hunt_maps"]
        bad = self.hunt_map()
        self.st["prefer_map"] = maps[0] if bad != maps[0] else (maps[1] if len(maps) > 1 else maps[0])
        self.note("routine_deaths", f"Погиб {deaths} раза за полчаса на {bad} — отдохну и пойду на "
                                    f"{self.st['prefer_map']}.", 3)
        await self.to_town(now)
        self.save()

    def diary(self, day_state):
        """Итог прошедшего дня — одно воспоминание без LLM."""
        start = datetime.strptime(day_state["day"], "%Y-%m-%d").replace(tzinfo=self.tz).timestamp()
        end = start + 86400
        mem = self.mind.mem
        count = lambda kind: mem.db.execute(
            "SELECT COUNT(*) FROM events WHERE kind = ? AND ts >= ? AND ts < ?", (kind, start, end)).fetchone()[0]
        levels = [json.loads(r[0]).get("level") for r in mem.db.execute(
            "SELECT data FROM events WHERE kind = 'level_up' AND ts >= ? AND ts < ?", (start, end))]
        met = sorted({json.loads(r[0]).get("partner") for r in mem.db.execute(
            "SELECT data FROM events WHERE kind = 'meeting_confirmed' AND ts >= ? AND ts < ?", (start, end))} - {None})
        hunted = int(day_state.get("hunted", 0) / 60)
        parts = [f"охотился {hunted // 60} ч {hunted % 60} мин", f"победил {count('kill')} монстров"]
        deaths = count("died")
        if deaths:
            parts.append(f"погиб {deaths} раз")
        if levels:
            parts.append(f"достиг {max(l for l in levels if l is not None) if any(levels) else '?'} уровня")
        if met:
            parts.append("встречался с " + ", ".join(met))
        text = f"Дневник {day_state['day']}: " + ", ".join(parts) + "."
        mem.remember(text, 3)
        mem.add_event("diary", {"day": day_state["day"], "text": text})
        self.mind.write_decision({"type": "routine", "event": "diary", "text": text})
        log.info("%s", text)

    async def to_town(self, now):
        done = self.st["hunted"] >= self.st["budget"]
        rest = self.minutes("break_minutes")
        self.st.update(mode="town", mode_since=now, arrived=False,
                       rest_until=now + rest if not done else float("inf"))
        hunted_min = int(self.st["hunted"] / 60)
        text = (f"Наохотился за день ({hunted_min} мин), иду в {self.town['map']} отдыхать и общаться."
                if done else f"Устал после охоты ({hunted_min} мин за день), иду в {self.town['map']} "
                             f"передохнуть минут {int(rest / 60)}.")
        self.note("routine_town", text, 2)
        self.last_sent = 0

    async def in_town(self, now, state):
        at_town = (state.get("map") == self.town["map"] and state.get("x") is not None
                   and max(abs(int(state["x"]) - self.town["x"]), abs(int(state["y"]) - self.town["y"]))
                   <= self.town.get("radius", 3) + 2)
        if at_town and not self.st["arrived"]:
            self.st["arrived"] = True
            self.note("routine_arrived", f"Я в городе {self.town['map']}, отдыхаю.", 1)
            if self.cfg.get("sit_in_town", True):
                await self.send({"action": "sit"}, "распорядок: сесть отдохнуть в городе")
        if self.st["hunted"] < self.st["budget"] and now >= self.st["rest_until"]:
            left = self.st["budget"] - self.st["hunted"]
            self.st.update(mode="hunt", mode_since=now, arrived=False,
                           session_end=self.st["hunted"] + min(left, self.minutes("session_minutes")))
            self.note("routine_hunt", f"Отдохнул, иду качаться на {self.hunt_map()}.", 2)
            self.last_sent = 0

    async def enforce(self, now, state):
        """Сверка настройки OpenKore с режимом; при расхождении — команда, не чаще RESEND с."""
        if now - self.last_sent < RESEND:
            return
        if self.st["mode"] == "hunt":
            ok = state.get("lock_map") == self.hunt_map() and state.get("lock_x") is None
            action = {"action": "hunt", "map": self.hunt_map()}
            reason = f"распорядок: охота на {self.hunt_map()}"
        else:
            ok = (state.get("lock_map") == self.town["map"] and state.get("lock_x") == self.town["x"]
                  and state.get("lock_y") == self.town["y"])
            action = {"action": "meet_point", "map": self.town["map"], "x": self.town["x"], "y": self.town["y"]}
            reason = f"распорядок: отдых в {self.town['map']}"
        if not ok:
            self.last_sent = now
            await self.send(action, reason)

    async def send(self, action, reason):
        await self.mind.execute([action], source="routine", reason=reason, protocol=True)

    def note(self, kind, text, importance):
        self.mind.mem.remember(text, importance)
        self.mind.mem.add_event(kind, {"hunted_min": int(self.st["hunted"] / 60),
                                       "budget_min": int(self.st["budget"] / 60)})
        self.mind.write_decision({"type": "routine", "event": kind, "text": text,
                                  "hunted_min": int(self.st["hunted"] / 60), "mode": self.st["mode"]})
        log.info("распорядок: %s", text)

    # ---------- оператор ----------

    async def force(self, what):
        """Команда оператора для быстрой проверки: rest — в город сейчас, hunt — на охоту сейчас."""
        now = self.clock()
        if not self.st:
            self.new_day(now)
        if what == "rest":
            if self.st["mode"] == "town":
                return "уже отдыхает"
            await self.to_town(now)
        elif what == "hunt":
            if self.st["mode"] == "hunt":
                return "уже охотится"
            if self.st["hunted"] >= self.st["budget"]:
                self.st["budget"] = self.st["hunted"] + self.minutes("session_minutes")
            self.st["rest_until"] = now
            await self.in_town(now, self.mind.state)
        else:
            return "rest или hunt"
        self.save()
        return None

    # ---------- модель ----------

    def prefer(self, hunt_map):
        """Модель выбрала карту: в охоте — сразу, в городе — на следующую сессию."""
        if hunt_map not in self.mind.persona["hunt_maps"]:
            return "карта не из списка hunt_maps"
        self.st["prefer_map"] = hunt_map
        self.last_sent = 0
        self.save()
        return None if self.st.get("mode") == "hunt" else "запомнил: пойду туда после отдыха"

    @property
    def in_town_mode(self):
        return self.st.get("mode") == "town"
