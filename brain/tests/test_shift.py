"""ORG-044: мягкая смена — сторож просит мозг уснуть раньше (команда sleep в inbox), флаг сна для сторожа,
пробуждение в своё время и после смены дня.

Запуск: cd brain && python3 -m unittest -v tests.test_shift
"""
import asyncio
import json
import os
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
from live_brain.routine import Routine
from tests.worldtime import shift_time

from tests.test_routine import WORLD, Clock, FakeMind

TZ = timezone(timedelta(hours=WORLD["timezone_offset_hours"]))
BRAIN_DIR = Path(__file__).resolve().parents[1]


class ShiftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.mem = Memory(root / "m.sqlite")
        self.mind = FakeMind(self.mem)
        self.mind.inbox_path = str(root / "bot01.inbox")
        self.flag = root / "bot01.asleep"
        self.mind.persona["sleep"] = {"start": "02:30", "hours": [7.0, 7.0]}
        self.clock = Clock(datetime(2026, 10, 2, 12, 0, tzinfo=TZ).timestamp())
        self.r = Routine(self.mind, WORLD, rng=random.Random(1), clock=self.clock)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def go(self, seconds, step=10.0, online=lambda: True):
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

    def sleeps(self):
        return [a for a in self.mind.sent if a["action"] == "sleep"]

    def test_nap_goes_to_town_sleeps_and_wakes_with_flag(self):
        self.go(60)                                          # охотится
        self.assertEqual(self.r.st["mode"], "hunt")
        self.assertIsNone(self.r.request_sleep(2))
        self.go(5 * 60)                                      # тело в игре < SLEEP_RETRY — без повтора
        self.assertEqual(self.r.st["mode"], "sleep", "дошёл до города и уснул")
        self.assertEqual(len(self.sleeps()), 1)
        self.assertAlmostEqual(self.sleeps()[0]["seconds"], 2 * 3600, delta=5 * 60)
        wake_at = int(self.flag.read_text())
        self.assertEqual(wake_at, int(self.r.st["wake_at"]))
        self.assertIn("routine_shift", [d.get("event") for d in self.mind.decisions])
        self.go(2 * 3600, step=60, online=lambda: False)    # тело остановлено сторожем
        self.go(60)                                          # сторож поднял тело после wake_at
        self.assertIn(self.r.st["mode"], ("town", "hunt"))
        self.assertFalse(self.flag.exists(), "проснулся — флага нет")
        self.assertNotIn("nap_until", self.r.st)

    def test_nap_extends_to_own_night(self):
        self.clock.t = datetime(2026, 10, 3, 1, 30, tzinfo=TZ).timestamp()
        self.r.st = {}
        self.assertIsNone(self.r.request_sleep(2))           # до 03:30, а своя ночь 02:30–09:30
        own_end = datetime(2026, 10, 3, 9, 30, tzinfo=TZ).timestamp()
        self.assertAlmostEqual(self.r.st["nap_until"], own_end, delta=1)

    def test_already_sleeping_and_bad_hours(self):
        self.go(60)
        self.r.st["mode"] = "sleep"
        self.assertEqual(self.r.request_sleep(1), "уже спит")
        self.r.st["mode"] = "town"
        self.assertIsNone(self.r.request_sleep("много"))     # мусор — по умолчанию NAP_HOURS
        self.assertAlmostEqual(self.r.st["nap_until"] - self.clock.t, 2 * 3600, delta=1)

    def test_day_change_during_sleep_keeps_wake_time(self):
        """Граница дня — середина диапазона сна; длинная ночь не обрывается в момент смены дня."""
        self.clock.t = datetime(2026, 10, 2, 22, 0, tzinfo=TZ).timestamp()
        self.go(4.6 * 3600)                                   # 02:36 — уснул
        self.assertEqual(self.r.st["mode"], "sleep")
        self.r.st["wake_at"] += 3 * 3600                      # ночь длиннее середины диапазона
        day0 = self.r.st["day"]
        self.go(9 * 3600, step=60)                            # тело в игре (relog), граница дня 09:30
        self.assertNotEqual(self.r.st["day"], day0, "новый день наступил")
        self.assertEqual(self.r.st["mode"], "sleep", "но житель спит до своего часа")
        self.go(2 * 3600, step=60)
        self.assertNotEqual(self.r.st["mode"], "sleep")


class InboxSleepTest(unittest.TestCase):
    """Команда сторожа sleep в run/brain/<bot>.inbox доходит до распорядка."""

    def test_sleep_command(self):
        shift_time(self)                     # timefix: полдень мира — ночью распорядок сам уводит в сон
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inbox = root / "bot01.inbox"
            mem = Memory(root / "m.sqlite")

            async def send(action):
                return 1
            persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
            mind = Mind(Settings.from_env({}), persona, mem, send, root / "decisions.jsonl", RuleGate(),
                        peers={"Arkady", "Vera"}, inbox_path=str(inbox),
                        world=json.loads((BRAIN_DIR / "world" / "goals.json").read_text()))
            try:
                asyncio.run(mind.on_message({"type": "state", "name": "Arkady", "map": "prt_fild08", "x": 100,
                                             "y": 100, "hp_pct": 100, "lock_map": "prt_fild08", "dead": False}))
                mind.fresh_state = True
                inbox.write_text(json.dumps({"cmd": "sleep", "hours": 1.5, "ts": time.time()}) + "\n")
                asyncio.run(mind.read_inbox())           # забрал (rename), исполнит на следующем тике
                asyncio.run(mind.read_inbox())
                self.assertIn("nap_until", mind.routine.st)
                self.assertAlmostEqual(mind.routine.st["nap_until"] - time.time(), 1.5 * 3600, delta=60)
                ops = [json.loads(l) for l in (root / "decisions.jsonl").read_text().splitlines()
                       if json.loads(l).get("type") == "operator"]
                self.assertEqual(ops[-1]["result"], "ok")
                self.assertEqual(mind.routine.sleep_flag(), os.path.join(d, "bot01.asleep"))
            finally:
                mem.close()


if __name__ == "__main__":
    unittest.main()
