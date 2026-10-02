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
            if self.st["hunted"] >= self.st["session_end"] or self.st["hunted"] >= self.st["budget"]:
                await self.to_town(now)
        else:
            await self.in_town(now, state)
        await self.enforce(now, state)
        self.save()

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
