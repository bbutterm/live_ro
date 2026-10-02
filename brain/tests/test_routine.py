"""Распорядок дня без сети и без процессов: поддельные часы прокручивают сутки.

Запуск: cd brain && python3 -m unittest -v tests.test_routine
"""
import asyncio
import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from live_brain.memory import Memory
from live_brain.routine import Routine, load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
HOUR = 3600


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeMind:
    def __init__(self, mem):
        self.persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        self.mem = mem
        self.state = {"name": "Arkady", "map": "prt_fild08", "x": 100, "y": 100, "lock_map": "prt_fild08",
                      "lock_x": None, "lock_y": None, "dead": False, "lv": 41, "job_lv": 20}
        self.fresh_state = True
        self.active_plan = None
        self.plans = SimpleNamespace(store=SimpleNamespace(active=lambda: self.active_plan))
        self.sent, self.decisions = [], []

    async def execute(self, actions, source, reason, protocol=False):
        self.sent.extend(actions)
        # тело исполняет команду: состояние OpenKore меняется
        for a in actions:
            if a["action"] == "hunt":
                self.state.update(lock_map=a["map"], lock_x=None, lock_y=None)
            elif a["action"] == "meet_point":
                self.state.update(lock_map=a["map"], lock_x=a["x"], lock_y=a["y"])

    def write_decision(self, rec):
        self.decisions.append(rec)


class RoutineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.mind = FakeMind(self.mem)
        # 2026-10-02 08:00 по местному времени мира (UTC+3)
        tz = timezone(timedelta(hours=WORLD["timezone_offset_hours"]))
        self.clock = Clock(datetime(2026, 10, 2, 8, 0, tzinfo=tz).timestamp())
        self.r = Routine(self.mind, WORLD, rng=random.Random(1), clock=self.clock)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def run_for(self, seconds, step=1.0, on_tick=None):
        async def go():
            end = self.clock.t + seconds
            while self.clock.t < end:
                self.clock.t += step
                if on_tick:
                    on_tick()
                await self.r.tick()
        asyncio.run(go())

    def walk_to_town(self):
        town = WORLD["routine"]["town"]
        if self.r.st.get("mode") == "town" and self.mind.state.get("lock_map") == town["map"]:
            self.mind.state.update(map=town["map"], x=town["x"], y=town["y"])
        elif self.r.st.get("mode") == "hunt" and self.mind.state.get("lock_map") == "prt_fild08":
            self.mind.state.update(map="prt_fild08")

    def test_daily_budget_between_4_and_5_hours(self):
        self.run_for(2)
        self.assertTrue(4 * HOUR <= self.r.st["budget"] <= 5 * HOUR)
        self.assertTrue(60 * 60 <= self.r.st["session_end"] <= 100 * 60)

    def test_session_tired_town_sit_then_hunt_again(self):
        self.run_for(101 * 60, step=5, on_tick=self.walk_to_town)
        self.assertEqual(self.r.st["mode"], "town")
        # фейковый бот «охотится» неподвижно — правило застревания тоже срабатывает, здесь оно не важно
        kinds = [d["event"] for d in self.mind.decisions if d["type"] == "routine" and d["event"] != "routine_stuck"]
        self.assertEqual(kinds[:2], ["routine_town", "routine_arrived"])
        self.assertIn({"action": "sit"}, self.mind.sent)
        self.assertEqual(self.mind.state["lock_map"], "prontera")
        self.run_for(91 * 60, step=5, on_tick=self.walk_to_town)       # перерыв не больше 90 мин
        self.assertEqual(self.r.st["mode"], "hunt")
        self.assertEqual(self.mind.state["lock_map"], "prt_fild08")
        self.assertIsNone(self.mind.state["lock_x"])

    def test_budget_exhausted_until_next_day(self):
        self.run_for(14 * HOUR, step=5, on_tick=self.walk_to_town)     # до 22:00 (шаг <= 5 с: больший разрыв не засчитывается)
        self.assertEqual(self.r.st["mode"], "town")
        self.assertGreaterEqual(self.r.st["hunted"], self.r.st["budget"])
        self.assertLessEqual(self.r.st["hunted"], self.r.st["budget"] + 60)
        self.assertIn("до завтра", self.r.summary()["отдых"])
        day = self.r.st["day"]
        self.run_for(3 * HOUR, step=5, on_tick=self.walk_to_town)      # после полуночи
        self.assertNotEqual(self.r.st["day"], day)
        self.assertEqual(self.r.st["mode"], "hunt")
        self.assertLess(self.r.st["hunted"], 3 * HOUR)

    def test_time_counts_only_on_hunt_map_alive(self):
        self.run_for(2)
        self.mind.state.update(map="prontera")                       # бот где-то не на охоте
        before = self.r.st["hunted"]
        self.run_for(600, step=5)
        self.assertEqual(self.r.st["hunted"], before)
        self.mind.state.update(map="prt_fild08", dead=True)
        self.run_for(600, step=5)
        self.assertEqual(self.r.st["hunted"], before)

    def test_meeting_plan_pauses_routine(self):
        self.run_for(2)
        self.mind.active_plan = {"id": "x"}
        self.run_for(3 * HOUR, step=10)
        self.assertEqual(self.r.st["mode"], "hunt")                  # не ушёл в город посреди встречи

    def test_restart_reconciles_body_config(self):
        self.run_for(101 * 60, step=5, on_tick=self.walk_to_town)
        self.assertEqual(self.r.st["mode"], "town")
        # перезапуск бота: OpenKore снова с профилем из Git (охота, без точки); мозг перезапущен
        self.mind.state.update(lock_map="prt_fild08", lock_x=None, lock_y=None)
        r2 = Routine(self.mind, WORLD, rng=random.Random(2), clock=self.clock)
        self.r = r2
        self.mind.sent.clear()
        self.run_for(2)
        self.assertEqual(r2.st["mode"], "town")                      # режим из SQLite
        self.assertEqual(self.mind.sent[0]["action"], "meet_point")   # точка в городе выставлена заново
        self.mind.sent.clear()
        self.run_for(30)
        self.assertEqual(self.mind.sent, [])                          # совпадает — команды не повторяются

    def test_llm_hunt_map_preference(self):
        self.run_for(2)
        self.assertIsNone(self.r.prefer("prt_fild07"))
        self.run_for(61)
        self.assertEqual(self.mind.state["lock_map"], "prt_fild07")
        self.assertIsNotNone(self.r.prefer("gef_dun02"))

    def test_goals_with_progress(self):
        goals = self.r.goals()
        self.assertEqual(goals[0]["сейчас"], 41)
        self.assertEqual(goals[0]["нужно"], 60)

    def test_operator_rest_and_hunt(self):
        self.run_for(2)
        self.assertIsNone(asyncio.run(self.r.force("rest")))
        self.assertEqual(self.r.st["mode"], "town")
        self.run_for(61, on_tick=self.walk_to_town)
        self.assertEqual(self.mind.state["lock_map"], "prontera")
        self.assertIsNone(asyncio.run(self.r.force("hunt")))
        self.assertEqual(self.r.st["mode"], "hunt")
        self.run_for(61)
        self.assertEqual((self.mind.state["lock_map"], self.mind.state["lock_x"]), ("prt_fild08", None))
        self.assertEqual(asyncio.run(self.r.force("hunt")), "уже охотится")


    def test_stuck_unstuck_once_and_not_while_fighting(self):
        self.run_for(2)
        self.run_for(301, step=5)                                     # стоит на месте, без боя
        self.assertEqual([a for a in self.mind.sent if a["action"] == "unstuck"], [{"action": "unstuck"}])
        self.mind.sent.clear()
        self.run_for(301, step=5, on_tick=lambda: self.r.on_combat(self.clock.t))   # дерётся на месте
        self.assertEqual([a for a in self.mind.sent if a["action"] == "unstuck"], [])

    def test_death_streak_rests_and_picks_easier_map(self):
        self.run_for(2)
        self.r.prefer("prt_fild07")
        for i in range(3):
            self.mem.db.execute("INSERT INTO events (ts, kind, data) VALUES (?, 'died', '{}')", (self.clock.t - i * 60,))
        asyncio.run(self.r.on_death(self.clock.t))
        self.assertEqual(self.r.st["mode"], "town")
        self.assertEqual(self.r.st["prefer_map"], "prt_fild08")
        self.assertTrue(any("Погиб 3 раза" in d["text"] for d in self.mind.decisions if d["type"] == "routine"))

    def test_diary_written_at_day_end(self):
        self.run_for(2)
        for _ in range(5):
            self.mem.db.execute("INSERT INTO events (ts, kind, data) VALUES (?, 'kill', '{}')", (self.clock.t,))
        self.mem.db.execute("INSERT INTO events (ts, kind, data) VALUES (?, 'level_up', ?)",
                            (self.clock.t, json.dumps({"level": 42})))
        self.mem.db.execute("INSERT INTO events (ts, kind, data) VALUES (?, 'meeting_confirmed', ?)",
                            (self.clock.t, json.dumps({"partner": "Vera"})))
        self.run_for(17 * HOUR, step=5, on_tick=self.walk_to_town)     # через полночь
        text = [m["text"] for m in self.mem.top_memories(50) if m["text"].startswith("Дневник 2026-10-02")]
        self.assertEqual(len(text), 1, text)
        self.assertIn("победил 5 монстров", text[0])
        self.assertIn("достиг 42 уровня", text[0])
        self.assertIn("встречался с Vera", text[0])


if __name__ == "__main__":
    unittest.main()

