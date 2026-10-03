"""Распорядок дня без сети и без процессов: поддельные часы прокручивают сутки.

Запуск: cd brain && python3 -m unittest -v tests.test_routine
"""
import asyncio
import json
from tests.persona_fixture import prontera_persona
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
        self.persona = prontera_persona()
        self.persona.pop("sleep", None)            # сон — отдельные тесты ниже (SleepTest)
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
            self.r.on_combat(self.clock.t)                 # на охоте фейковый бот дерётся, а не стоит

    def test_daily_budget_between_4_and_5_hours(self):
        self.run_for(2)
        self.assertTrue(4 * HOUR <= self.r.st["budget"] <= 5 * HOUR)
        lo, hi = self.r.cfg["session_minutes"]                  # с учётом привычек характера
        self.assertTrue(lo * 60 <= self.r.st["session_end"] <= hi * 60)

    def test_session_tired_town_sit_then_hunt_again(self):
        self.run_for((self.r.cfg["session_minutes"][1] + 1) * 60, step=5, on_tick=self.walk_to_town)
        self.assertEqual(self.r.st["mode"], "town")
        # фейковый бот «охотится» неподвижно — правило застревания тоже срабатывает, здесь оно не важно
        kinds = [d["event"] for d in self.mind.decisions if d["type"] == "routine" and d["event"] != "routine_stuck"]
        self.assertEqual(kinds[:2], ["routine_town", "routine_arrived"])
        self.assertIn({"action": "sit"}, self.mind.sent)
        self.assertEqual(self.mind.state["lock_map"], "prontera")
        self.run_for((self.r.cfg["break_minutes"][1] + 1) * 60, step=5, on_tick=self.walk_to_town)  # перерыв не дольше нормы
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
        self.run_for((self.r.cfg["session_minutes"][1] + 1) * 60, step=5, on_tick=self.walk_to_town)
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
        self.assertEqual([a for a in self.mind.sent if a["action"] == "unstuck"], [{"action": "unstuck", "radius": 10}])
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
        self.assertIn("prt_fild07", self.mem.get("map_bans"))
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

    # ---------- AUT-008/026/037/086/098 ----------

    def test_death_recover_town_then_hunt_only_with_hp(self):
        self.run_for(2)
        self.mind.state.update(dead=True, hp_pct=0)
        asyncio.run(self.r.on_death(self.clock.t))
        self.assertEqual(self.r.st["mode"], "town")
        self.assertTrue(self.r.st["recover"])
        self.assertEqual(self.mem.get("goal"), "отдыхаю в prontera и восстанавливаюсь")
        # возродился в городе с 3% HP: перерыв прошёл, но на охоту не идёт
        self.mind.state.update(dead=False, hp_pct=3)
        self.run_for(15 * 60, step=5, on_tick=self.walk_to_town)
        self.assertEqual(self.r.st["mode"], "town")
        self.assertEqual(sum(1 for d in self.mind.decisions if d.get("event") == "routine_wait_hp"), 1)
        self.assertIn("HP", asyncio.run(self.r.force("hunt")))          # и оператор не отправит больного
        self.mind.state.update(hp_pct=85)
        self.run_for(5, on_tick=self.walk_to_town)
        self.assertEqual(self.r.st["mode"], "hunt")
        self.assertFalse(self.r.st["recover"])
        self.assertEqual(self.mem.get("goal"), "охочусь на prt_fild08")

    def test_low_hp_without_potions_goes_to_town(self):
        self.run_for(2)
        self.mind.state.update(hp_pct=20, items={"501": 3})
        self.run_for(60, step=5, on_tick=lambda: self.r.on_combat(self.clock.t))
        self.assertEqual(self.r.st["mode"], "hunt", "есть зелья — тело лечится само")
        self.mind.state.update(items={"501": 0})
        self.run_for(10, step=5)
        self.assertEqual(self.r.st["mode"], "hunt", "короткий провал HP — не паника")
        self.run_for(20, step=5)
        self.assertEqual(self.r.st["mode"], "town")
        self.assertTrue(self.r.st["recover"])

    def test_no_stuck_while_sitting_or_trading(self):
        self.run_for(2)
        self.mind.state.update(sitting=True)
        self.run_for(400, step=5)
        self.mind.state.update(sitting=False, activity="sellAuto")
        self.run_for(400, step=5)
        self.assertEqual([a for a in self.mind.sent if a["action"] == "unstuck"], [])
        self.mind.state.update(activity="route")
        self.run_for(301, step=5)
        self.assertEqual([a for a in self.mind.sent if a["action"] == "unstuck"], [{"action": "unstuck", "radius": 10}])


    def test_escape_wing_goes_to_recover(self):
        self.run_for(2)
        asyncio.run(self.r.on_escape(self.clock.t))
        self.assertEqual(self.r.st["mode"], "town")
        self.assertTrue(self.r.st["recover"])


    def test_recover_blocked_by_weight_noted_once(self):
        self.run_for(2)
        asyncio.run(self.r.force("rest"))
        self.mind.state.update(hp_pct=10, weight_pct=72, items={"501": 0})
        self.run_for(20 * 60, step=5, on_tick=self.walk_to_town)
        self.assertEqual(sum(1 for d in self.mind.decisions if d.get("event") == "recover_blocked"), 1)


    def test_stuck_ladder_relocate_then_blocked(self):
        self.run_for(2)
        for _ in range(2):
            self.run_for(301, step=5)
        self.assertEqual([a.get("radius") for a in self.mind.sent if a["action"] == "unstuck"], [10, 25])
        self.run_for(301, step=5)
        self.assertEqual(self.r.st["mode"], "town", "третья попытка — уйти в город")
        self.assertIn("prt_fild08", self.mem.get("map_bans"))
        self.assertNotIn("blocked", self.r.st)
        # снова на охоте и снова застрял — тупик
        self.r.st.update(mode="hunt", rest_until=0)
        self.mind.state.update(map="prt_fild07", lock_map="prt_fild07")
        self.run_for(301, step=5)
        self.assertIn("blocked", self.r.st)
        self.assertIn("routine_blocked", [d.get("event") for d in self.mind.decisions])


    def test_service_run_when_heavy(self):
        self.run_for(2)
        asyncio.run(self.r.force("rest"))
        self.mind.state.update(weight_pct=45, items={"501": 30})
        self.run_for(120, step=5, on_tick=self.walk_to_town)
        self.assertEqual([a for a in self.mind.sent if a["action"] == "service"], [{"action": "service"}])
        self.run_for(600, step=5, on_tick=self.walk_to_town)
        self.assertEqual(len([a for a in self.mind.sent if a["action"] == "service"]), 1, "не чаще раза в 30 мин")


class SleepTest(RoutineTest.__bases__[0]):
    """ORG-012: сон в своё время, выход из игры, новый день с пробуждения."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.mind = FakeMind(self.mem)
        self.mind.persona["sleep"] = {"start": "02:30", "hours": [7.0, 7.0]}
        tz = timezone(timedelta(hours=WORLD["timezone_offset_hours"]))
        self.clock = Clock(datetime(2026, 10, 2, 22, 0, tzinfo=tz).timestamp())
        self.r = Routine(self.mind, WORLD, rng=random.Random(1), clock=self.clock)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def go(self, seconds, step=5.0, online=lambda: True):
        async def run():
            end = self.clock.t + seconds
            while self.clock.t < end:
                self.clock.t += step
                self.mind.fresh_state = online()
                town = WORLD["routine"]["town"]
                if self.mind.state.get("lock_map") == town["map"]:
                    self.mind.state.update(map=town["map"], x=town["x"], y=town["y"])
                if self.mind.fresh_state:
                    await self.r.tick()
        asyncio.run(run())

    def test_sleep_then_new_day_on_wake(self):
        self.go(60)
        day0 = self.r.st["day"]
        self.go(4.6 * 3600, step=10)                         # до 02:36: пора спать
        sleeps = [a for a in self.mind.sent if a["action"] == "sleep"]
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0]["seconds"], 7 * 3600 - 6 * 60, delta=600)
        self.assertEqual(self.r.st["mode"], "sleep")
        self.go(6.8 * 3600, step=60, online=lambda: False)   # спит: тело офлайн
        self.go(30 * 60, step=10)                            # 09:30 + вход в игру
        self.assertEqual(self.r.st["mode"] in ("town", "hunt"), True)
        self.assertNotEqual(self.r.st["day"], day0, "новый день — с пробуждения")
        self.assertIn("routine_wake", [d.get("event") for d in self.mind.decisions])

    def test_not_sleeping_body_retried(self):
        self.go(4.6 * 3600, step=10)
        self.go(SLEEP_RETRY_S + 30, step=10)                 # тело осталось в игре
        self.assertEqual(len([a for a in self.mind.sent if a["action"] == "sleep"]), 2)


SLEEP_RETRY_S = 600


if __name__ == "__main__":
    unittest.main()

