"""Относительная бедность (wealth.py, ORG-100 часть 1, ТЗ Т-43): мотив wealth от копилки и медианы мира.

Запуск: cd brain && python3 -m unittest -v tests.test_wealth
"""
import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from live_brain import world_bus
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.wealth import Wealth, median, report_line

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")


class Bus:
    def __init__(self, rows):
        self.rows, self.reads = rows, 0

    def latest(self, kind, since=0.0):
        self.reads += 1
        assert kind == "presence"
        return {b: {"ts": since + 1, "data": {"map": "prontera", "zeny": z}} for b, z in self.rows.items()}


class WealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.now = 1_000_000_000.0
        self.bus = Bus({"bot02": 200000})
        self.mind = SimpleNamespace(mem=self.mem, state={"zeny": 200000}, needs=SimpleNamespace(t={"greed": 0.5}),
                                    world=SimpleNamespace(bus=self.bus), dream=None)
        self.w = Wealth(self.mind, WORLD, clock=lambda: self.now)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def fresh(self):
        self.w.cache = (0.0, [])

    def test_median(self):
        self.assertIsNone(median([]))
        self.assertEqual(median([3, 1, 2]), 2)
        self.assertEqual(median([1, 2, 3, 10]), 2.5)

    def test_rich_world_keeps_motive(self):
        old = max(0.0, 1 - 200000 / 50000)
        self.assertEqual(old, 0.0)
        v = self.w.value()
        self.assertGreater(v, 0.3)                       # 1 − 200k / (1.5 × 200k) ≈ 0.33
        t = self.w.target()
        self.assertEqual(t["median"], 200000)
        self.assertEqual(t["target"], 300000)

    def test_poorer_than_median_wants_more(self):
        self.bus.rows = {"bot02": 400000, "bot03": 300000}
        self.mind.state["zeny"] = 50000
        poor = self.w.value()
        self.fresh()
        self.mind.state["zeny"] = 400000
        rich = self.w.value()
        self.assertGreater(poor, 0.8)
        self.assertLess(rich, poor)

    def test_greedy_above_generous(self):
        self.mind.needs.t["greed"] = 0.9
        greedy = self.w.value()
        self.fresh()
        self.mind.needs.t["greed"] = 0.1
        generous = self.w.value()
        self.assertGreater(greedy, generous)

    def test_goal_raises_target_and_bank_counts(self):
        self.mind.world = None                           # нет шины — только порог и копилка
        self.mind.state["zeny"] = 20000
        self.assertEqual(self.w.target()["target"], 5000)
        self.assertEqual(self.w.value(), 0.0)
        self.mind.dream = SimpleNamespace(save_target=lambda: (30000, "стать Knight"))
        self.assertAlmostEqual(self.w.value(), round(1 - 20000 / 30000, 2))
        self.mem.set("savings", {"bank": 10000})
        self.assertEqual(self.w.value(), 0.0)            # банк копилки — тоже мои зени

    def test_cache(self):
        self.w.value()
        self.w.value()
        self.assertEqual(self.bus.reads, 1)
        self.now += WORLD["wealth"]["cache_seconds"] + 1
        self.w.value()
        self.assertEqual(self.bus.reads, 2)

    def test_report_snapshot(self):
        self.w.value()
        line = report_line(self.mem.get("wealth"))
        self.assertIn("достаток: 200000 из 300000 z", line)
        self.assertIn("медиана мира 200000", line)
        self.assertIsNone(report_line(None))


class MindWealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, env=None, bus=None):
        async def send(a):
            return 1
        mem = Memory(self.root / f"m{len(list(self.root.iterdir()))}.sqlite")
        self.addCleanup(mem.close)
        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        m = Mind(Settings.from_env(env or {}), persona, mem, send, self.root / "d.jsonl", RuleGate(),
                 peers={"Arkady", "Vera"}, world=WORLD, world_bus_db=bus)
        m.state = {"name": "Arkady", "map": "prontera", "x": 1, "y": 1, "zeny": 120000, "hp_pct": 100, "lv": 30}
        return m

    def test_switch_off_old_formula(self):
        m = self.make({"BRAIN_DISABLE": "wealth"})
        self.assertIsNone(m.wealth)
        self.assertEqual(m.needs.values()["wealth"], 0.0)             # 1 − 120000/50000 -> 0
        m.state["zeny"] = 10000
        self.assertEqual(m.needs.values()["wealth"], 0.8)

    def test_presence_carries_zeny_and_median(self):
        a_bus = world_bus.WorldBus(self.root / "world.sqlite", "bot01")
        v_bus = world_bus.WorldBus(self.root / "world.sqlite", "bot02")
        self.addCleanup(a_bus.close)
        self.addCleanup(v_bus.close)
        m = self.make(bus=a_bus)
        self.assertEqual(m.crowd.presence()["zeny"], 120000)
        v_bus.replace("presence", {"map": "prontera", "zeny": 300000}, 1, now=time.time())
        t = m.wealth.target()
        self.assertEqual(t["median"], 210000)                          # (120000 + 300000) / 2
        self.assertGreater(m.needs.values()["wealth"], 0.5)

    def test_report_line(self):
        from live_brain.__main__ import report
        m = self.make()
        m.needs.values()
        args = SimpleNamespace(bot="bot01", persona=str(BRAIN_DIR / "personas" / "bot01.json"))
        out = io.StringIO()
        with redirect_stdout(out):
            report(args, m.mem, self.root)
        self.assertIn("достаток: 120000 из", out.getvalue())


if __name__ == "__main__":
    unittest.main()
