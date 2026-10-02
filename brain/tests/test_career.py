"""Карьера жителя (career.py): цель в памяти, этап квеста только по флагу, повтор и пауза; порядок навыков.

Запуск: cd brain && python3 -m unittest -v tests.test_career
"""
import asyncio
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain.career import MAX_FAILS, Career
from live_brain.memory import Memory

ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class CareerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.sent, self.dec = [], []

        async def execute(actions, source, reason, protocol=False):
            self.sent.extend(actions)

        self.mind = SimpleNamespace(
            mem=self.mem, fresh_state=True, execute=execute, write_decision=self.dec.append,
            state={"name": "Vera", "job": "Acolyte", "lv": 45, "job_lv": 40, "zeny": 5000, "items": {},
                   "job_change": {"running": False, "skill_points": 0, "quests": [], "items": {}}},
            routine=SimpleNamespace(in_town_mode=True, st={"arrived": True}),
            plans=SimpleNamespace(store=SimpleNamespace(active=lambda: None)),
            may_move=lambda owner: (True, None), alert=lambda *a, **k: self.dec.append({"alert": a}))
        self.clock = Clock()

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_goal_summary_without_auto(self):
        c = Career(self.mind, {"auto_job_change": False}, clock=self.clock)
        asyncio.run(c.tick())
        self.assertIn("Acolyte -> Priest", self.mem.get("career")["text"])
        self.assertEqual(self.sent, [], "без флага этапы не начинаются")

    def test_stage_start_only_in_town_and_retry_gap(self):
        c = Career(self.mind, {"auto_job_change": True}, clock=self.clock)
        self.mind.routine.in_town_mode = False
        asyncio.run(c.tick())
        self.assertEqual(self.sent, [], "на охоте — нет")
        self.mind.routine.in_town_mode = True
        asyncio.run(c.tick())
        self.assertEqual([a["action"] for a in self.sent], ["job_change"])
        self.assertEqual(self.sent[0]["path"], "priest")
        asyncio.run(c.tick())
        self.assertEqual(len(self.sent), 1, "повтор не раньше чем через час")

    def test_failures_pause_and_alert(self):
        c = Career(self.mind, {"auto_job_change": True}, clock=self.clock)
        for _ in range(MAX_FAILS):
            c.on_result({"kind": "job_change_result", "path": "priest", "stage": "apply", "ok": False, "reason": "x"})
        self.assertTrue(any("alert" in d for d in self.dec))
        self.assertGreater(c.st["next_try"], self.clock.t + 80000)
        c.on_result({"kind": "job_change_result", "path": "priest", "stage": "apply", "ok": True})
        self.assertEqual(c.st["done"], ["apply"])


@unittest.skipUnless((ROOT / "upstream" / "rathena" / "db" / "re" / "skill_tree.yml").exists()
                     and importlib.util.find_spec("yaml"), "нужны сабмодуль rAthena и PyYAML")
class SkillOrderTest(unittest.TestCase):
    def test_skill_lists_respect_prerequisites(self):
        """raiseSkill отключается навсегда на навыке с невыполненным требованием — порядок должен быть верным."""
        spec = importlib.util.spec_from_file_location("check_skill_lists", ROOT / "scripts" / "check_skill_lists.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.check(ROOT / "upstream" / "rathena"), [])


if __name__ == "__main__":
    unittest.main()
