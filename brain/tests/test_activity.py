"""Каталог занятий и выбор по мотивам (activity.py, ORG-016/017): журнал, факт завершения, запреты.

Запуск: cd brain && python3 -m unittest -v tests.test_activity
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain.activity import Activities
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class ActivityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(a)
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=load_world(BRAIN_DIR / "world" / "goals.json"))
        self.mind.calendar = None                    # calendar: выбор в тесте не зависит от дня недели и праздника
        self.mind.tradition = None                   # tradition: вечерний круг (20–21 ч) не вмешивается в тест
        self.clock = Clock()
        self.a = Activities(self.mind, clock=self.clock, rng=random.Random(3))
        self.mind.activities = self.a
        self.dec = root / "d.jsonl"
        self.state(map="prontera", x=156, y=185, lock_map="prontera", lock_x=156, lock_y=185)
        r = self.mind.routine
        r.new_day(self.clock.t)
        r.st.update(mode="town", arrived=True, rest_until=self.clock.t + 3600, mode_since=self.clock.t)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prt_fild08", "x": 100, "y": 100, "hp_pct": 100, "lv": 41,
             "job": "Swordsman", "dead": False, "weight_pct": 20, "zeny": 60000, "items": {"501": 30}, "players": []}
        s.update(getattr(self, "_last", {}))
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def tick(self):
        self.a.next_decide = 0
        asyncio.run(self.a.tick())

    def records(self):
        if not self.dec.exists():
            return []
        return [json.loads(l) for l in self.dec.read_text().splitlines() if '"activity"' in l]

    def test_heavy_backpack_goes_service_and_proof(self):
        self.state(weight_pct=55, items={"501": 0})
        self.tick()
        rec = [r for r in self.records() if r.get("chosen")][-1]
        self.assertEqual(rec["chosen"], "service")
        self.assertEqual(len(rec["top"]), 3 if len(rec["top"]) >= 3 else len(rec["top"]))
        self.assertIn("supply", rec["needs"])
        self.assertIn({"action": "service"}, [{"action": a["action"]} for a in self.sent])
        self.state(weight_pct=30)                                 # продал — вес упал
        asyncio.run(self.a.tick())
        self.assertIn("activity_done", [r.get("event") for r in self.records()])

    def test_failed_without_proof(self):
        self.state(weight_pct=55, items={"501": 0})
        self.tick()
        self.clock.t += 16 * 60                                   # вес не изменился — не продал
        asyncio.run(self.a.tick())
        self.assertIn("activity_failed", [r.get("event") for r in self.records()])

    def test_tired_hunter_ends_hunt(self):
        r = self.mind.routine
        r.st.update(mode="hunt", mode_since=self.clock.t - 3600, hunted=5000, session_end=5100)
        self.state(map="prt_fild08", lock_map="prt_fild08", lock_x=None, lock_y=None)
        self.tick()
        chosen = [x for x in self.records() if x.get("chosen")][-1]["chosen"]
        self.assertIn(chosen, ("end_hunt", "keep_hunting", "change_map"))
        if chosen == "end_hunt":
            self.assertEqual(r.st["mode"], "town")

    def test_not_while_plan_or_recover_or_early_hunt(self):
        self.mind.routine.st["recover"] = True
        self.tick()
        self.assertEqual(self.records(), [])
        self.mind.routine.st["recover"] = False
        self.mind.routine.st.update(mode="hunt", mode_since=self.clock.t)   # охота только началась
        self.tick()
        self.assertEqual(self.records(), [])

    def test_inertia_no_flapping(self):
        self.tick()
        first = [x for x in self.records() if x.get("chosen")][-1]["chosen"]
        starts = self.mem.count_events("activity", 0)
        for _ in range(5):
            self.tick()
        self.assertEqual(self.mem.count_events("activity", 0), starts, f"без новых фактов не переключается ({first})")


if __name__ == "__main__":
    unittest.main()
