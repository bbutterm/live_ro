"""Привычки и скука (habits.py, ORG-068): привычки after/hour/place, скука, причуда, связь с activity и social.

Настоящий Mind (Arkady) с поддельным телом; события activity/social_walk пишутся в память с нужным временем.
Время модуля — подменные часы. Запуск: cd brain && python3 -m unittest -v tests.test_habits
"""
import json
import random
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZ = timezone(timedelta(hours=WORLD.get("timezone_offset_hours", 0)))
PEERS = {"Arkady", "Vera"}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def noon_today():
    return datetime.now(TZ).replace(hour=12, minute=0, second=0, microsecond=0).timestamp()


class HabitsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        self.persona.pop("sleep", None)
        self.mem = Memory(self.root / "m.sqlite")

        async def send(a):
            return 1
        self.mind = Mind(Settings.from_env({}), self.persona, self.mem, send, self.root / "d.jsonl", RuleGate(),
                         peers=PEERS, world=WORLD)
        self.clock = Clock(noon_today())
        self.h = self.mind.habits
        self.h.clock = self.clock
        self.set_whimsy(0.5)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def set_whimsy(self, w):
        self.mind.needs.t["whimsy"] = w

    def ev(self, kind, ts, **data):
        self.mem.db.execute("INSERT INTO events (ts, kind, data) VALUES (?, ?, ?)", (ts, kind, json.dumps(data)))
        self.mem.db.commit()

    def act(self, name, ts):
        self.ev("activity", ts, name=name)

    def recount(self):
        self.h.next_recount = 0
        self.h.tick()

    def decisions(self):
        p = self.root / "d.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    # ---------- привычки ----------

    def test_after_habit_and_bonus_only_after(self):
        t = self.clock.t
        for d in (3, 2, 1):                                      # три дня подряд: охота -> по делам
            base = t - d * 86400
            self.act("keep_hunting", base)
            self.act("service", base + 1800)
            self.act("rest", base + 7200)
        self.recount()
        self.assertIn("after:keep_hunting:service", self.h.habits())
        self.assertTrue(any(d.get("event") == "habit_formed" for d in self.decisions()))
        self.assertIn("Привык", " ".join(m["text"] for m in self.mem.top_memories(20)))
        self.h.shares = {}
        later = t + 6 * 3600                                     # не в час привычки hour:service (около 12:30)
        self.assertEqual(self.h.adjust("service", 0.4, "keep_hunting", later), 0.6)
        self.assertEqual(self.h.adjust("service", 0.4, "rest", later), 0.4)
        self.assertEqual(self.h.adjust("service", -0.2, "keep_hunting", later), -0.2)   # отрицательное не усиливается

    def test_broken_streak_no_habit(self):
        t = self.clock.t
        for d, nxt in ((3, "service"), (2, "stroll"), (1, "service")):
            self.act("keep_hunting", t - d * 86400)
            self.act(nxt, t - d * 86400 + 600)
        self.recount()
        self.assertNotIn("after:keep_hunting:service", self.h.habits())

    def test_hour_habit(self):
        t = self.clock.t
        for d in (4, 3, 1):
            self.act("stroll", t - d * 86400 + 600)              # около полудня, три разных дня
        self.recount()
        self.assertIn("hour:stroll", self.h.habits())
        self.h.shares = {}
        self.assertEqual(self.h.adjust("stroll", 0.2, None, t), 0.3)
        self.assertEqual(self.h.adjust("stroll", 0.2, None, t + 5 * 3600), 0.2)   # в другой час — нет

    def test_place_habit_and_point_factor(self):
        t = self.clock.t
        for d in (3, 2, 1):
            self.ev("social_walk", t - d * 86400, point="fountain", why="по настроению")
        self.ev("social_walk", t - 86400 + 60, point="kafra", why="по настроению")
        self.recount()
        self.assertIn("place:fountain", self.h.habits())
        self.assertEqual(self.h.point_factor("fountain"), 1.5)
        self.assertEqual(self.h.point_factor("kafra"), 1.0)

    def test_habit_lost(self):
        t = self.clock.t
        for d in (3, 2, 1):
            self.ev("social_walk", t - d * 86400, point="fountain")
        self.recount()
        self.assertIn("place:fountain", self.h.habits())
        self.clock.t += 8 * 86400                                # неделя без повторов
        self.recount()
        self.assertEqual(self.h.habits(), {})
        self.assertTrue(any(d.get("event") == "habit_lost" for d in self.decisions()))

    # ---------- скука ----------

    def test_boredom_grows_and_falls(self):
        t = self.clock.t
        for i in range(8):
            self.act("keep_hunting", t - i * 3600)
        self.recount()
        self.assertEqual(self.h.boredom(), 1.0)
        for i, name in enumerate(("stroll", "service", "rest", "socialize", "gathering", "explore", "change_map")):
            for k in range(2):
                self.act(name, t - 60 - i * 60 - k)
        self.recount()
        self.assertLess(self.h.boredom(), 0.3)

    def test_few_starts_no_boredom(self):
        for i in range(3):
            self.act("keep_hunting", self.clock.t - i * 60)
        self.recount()
        self.assertEqual(self.h.boredom(), 0.0)
        self.assertEqual(self.h.adjust("keep_hunting", 0.5, None), 0.5)

    def test_penalty_scales_with_whimsy(self):
        t = self.clock.t
        for i in range(6):
            self.act("keep_hunting", t - i * 3600)
        for i in range(2):
            self.act("rest", t - 10 * 3600 - i)
        self.set_whimsy(0.0)
        self.recount()
        self.st_clear_whim()
        calm = self.h.adjust("keep_hunting", 0.5, None)
        self.set_whimsy(1.0)
        whimsical = self.h.adjust("keep_hunting", 0.5, None)
        self.assertEqual(calm, 0.5)
        self.assertLess(whimsical, 0.35)

    def st_clear_whim(self):
        self.h.st["whim_until"] = 0

    def test_whim_once_a_day_and_stale_bonus(self):
        t = self.clock.t
        for i in range(10):
            self.act("keep_hunting", t - i * 3600)
        self.mind.activities.st["last"] = {"keep_hunting": t, "stroll": t - 3600, "service": t - 5 * 86400}
        self.set_whimsy(0.6)
        self.recount()
        self.assertTrue(self.h.whim())
        self.assertEqual(self.mem.count_events("habit_whim", 0), 1)
        self.assertEqual(self.h.st["whim_from"], "keep_hunting")
        self.h.shares = {}
        self.assertEqual(self.h.adjust("service", 0.1, "keep_hunting"), 0.6)    # давно не делал (5 сут)
        self.assertEqual(self.h.adjust("stroll", 0.1, "keep_hunting"), 0.1)     # делал час назад
        self.assertEqual(self.h.adjust("keep_hunting", 0.1, "keep_hunting"), 0.1)
        self.clock.t += 2 * 3600                                 # причуда кончилась, но сутки не прошли
        self.recount()
        self.assertFalse(self.h.whim())
        self.assertEqual(self.mem.count_events("habit_whim", 0), 1)

    def test_whim_disables_habits_and_prefers_old_point(self):
        t = self.clock.t
        for d in (3, 2, 1):
            self.ev("social_walk", t - d * 86400, point="fountain")
        self.ev("social_walk", t - 6 * 86400, point="kafra")
        for i in range(10):
            self.act("keep_hunting", t - i * 3600)
        self.set_whimsy(0.8)
        self.recount()
        self.assertTrue(self.h.whim())
        self.assertEqual(self.h.point_factor("fountain"), 1.0)                  # любимая не тянет
        self.assertEqual(self.h.point_factor("kafra"), 2.0)                     # давно не был

    def test_no_whim_for_calm(self):
        for i in range(10):
            self.act("keep_hunting", self.clock.t - i * 3600)
        self.set_whimsy(0.1)
        self.recount()
        self.assertFalse(self.h.whim())

    # ---------- подключение ----------

    def test_not_duplicating_crowd_today(self):
        """Повторы за сегодня штрафует crowd; habits без недельной истории (< min_starts) — нейтрален."""
        t = self.clock.t
        for i in range(3):
            self.act("stroll", t - i * 60)
        self.recount()
        self.assertEqual(self.h.adjust("stroll", 0.3, None), 0.3)
        self.assertGreater(self.mind.crowd.activity_penalty("stroll", "town", None), 0)

    def test_activity_scores_use_habits(self):
        t = self.clock.t
        for d in (3, 2, 1):
            self.act("keep_hunting", t - d * 86400)
            self.act("service", t - d * 86400 + 600)
        self.recount()
        acts = self.mind.activities
        calls = []
        real = self.h.adjust
        self.h.adjust = lambda name, score, current, now: calls.append(name) or real(name, score, current, now)
        acts.rng = random.Random(1)
        self.mind.routine.st.update(mode="town", arrived=True)
        acts.scores(self.mind.state)
        self.assertTrue(calls)

    def test_switch_and_prompt(self):
        m = Mind(Settings.from_env({"BRAIN_DISABLE": "habits"}), self.persona, Memory(":memory:"),
                 lambda a: None, self.root / "x.jsonl", RuleGate(), peers=PEERS, world=WORLD)
        self.assertIsNone(m.habits)
        w = json.loads(json.dumps(WORLD))
        w["habits"] = {"enabled": False}
        m2 = Mind(Settings.from_env({}), self.persona, Memory(":memory:"), lambda a: None,
                  self.root / "y.jsonl", RuleGate(), peers=PEERS, world=w)
        self.assertIsNone(m2.habits)
        self.assertIsNone(self.h.summary())
        for d in (3, 2, 1):
            self.ev("social_walk", self.clock.t - d * 86400, point="fountain")
        self.recount()
        self.assertIn("любимое место — fountain", self.h.summary()["привычки"])


if __name__ == "__main__":
    unittest.main()
