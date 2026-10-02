"""Недельные цели (aims.py, ORG-038): выбор по характеру, прогресс по фактам, итог недели, замена, boost.

Запуск: cd brain && python3 -m unittest -v tests.test_aims
"""
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain.aims import MAX_BOOST, WEEK, Aims
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind

BRAIN_DIR = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class AimsTest(unittest.TestCase):
    def make(self, traits):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)

        async def send(a):
            return 1

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona["traits"] = dict({k: 0.1 for k in persona["traits"] if not k.startswith("_")}, **traits)
        self.mem = Memory(root / "m.sqlite")
        self.addCleanup(self.mem.close)
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"})
        self.mind.ctx.name = "Arkady"
        self.mind.fresh_state = True
        self.mind.state = {"name": "Arkady", "lv": 30, "job": "Swordsman", "job_lv": 20, "zeny": 50000}
        self.clock = Clock()
        self.aims = Aims(self.mind, clock=self.clock, rng=random.Random(1))
        self.mind.aims = self.aims

    def tick(self, dt=0):
        self.clock.t += dt
        self.aims.next_tick = 0
        self.aims.tick()

    def kinds(self):
        return [a["kind"] for a in self.aims.st["items"]]

    def test_choice_by_character(self):
        self.make({"greed": 0.95, "generosity": 0.9})
        self.tick()
        self.assertEqual(sorted(self.kinds()), ["help", "zeny"], "жадный и щедрый — зени и помощь")
        self.assertEqual(self.mem.count_events("aim_new", 0), 2)
        summary = self.mind.build_prompt("тест", {})[1]["content"]
        self.assertIn("цели_недели", summary)
        self.assertIn("накопить ещё 15000 зени", summary)

    def test_job_aim_only_when_ready(self):
        self.make({"diligence": 0.95})
        self.tick()
        self.assertNotIn("job", self.kinds(), "job_lv 20 — рано")
        self.mem.set("aims", {})
        self.mind.state.update(job_lv=40)
        self.tick()
        self.assertIn("job", self.kinds())
        self.mind.state.update(job="Knight", job_lv=1)
        self.tick()
        self.assertEqual(self.mem.count_events("job_changed", 0), 1, "смена профессии по данным игры")
        job = [a for a in self.aims.st["items"] if a["kind"] == "job"][0]
        self.assertTrue(job["done"])

    def test_progress_done_and_boost(self):
        self.make({"diligence": 0.95, "sociability": 0.9})
        self.tick()
        self.assertEqual(sorted(self.kinds()), ["friend", "level"])
        self.assertGreater(self.aims.boost("progress"), 1.0)
        self.assertLessEqual(self.aims.boost("progress"), MAX_BOOST)
        self.assertEqual(self.aims.boost("wealth"), 1.0, "нет цели — мотив без изменений")
        early = self.aims.boost("social")
        self.clock.t += WEEK * 0.4
        self.assertGreater(self.aims.boost("social"), early, "ближе к концу недели — сильнее")
        self.mind.state["lv"] = 33
        self.tick()
        level = [a for a in self.aims.st["items"] if a["kind"] == "level"][0]
        self.assertTrue(level["done"])
        self.assertEqual(self.aims.boost("progress"), 1.0, "выполнено — не толкает")
        self.assertEqual(self.mem.count_events("aim_done", 0), 1)
        self.mem.update_relation("Vera", 2)
        self.tick()
        self.assertEqual(self.mem.count_events("aim_done", 0), 2, "дружба +2 — по отношениям в памяти")

    def test_replace_unreachable_and_week_result(self):
        self.make({"generosity": 0.95, "greed": 0.9})
        self.tick()
        self.assertIn("help", self.kinds())
        self.tick(WEEK * 0.6)                                        # полнедели — ни одной помощи
        self.assertNotIn("help", self.kinds(), "недостижимая цель заменена")
        self.assertEqual(self.mem.count_events("aim_replaced", 0), 2, "и зени тоже не копились")
        replaced = [a for a in self.aims.st["items"] if a.get("replaced")]
        self.assertEqual(len(replaced), 2)
        self.tick(WEEK * 0.1)
        self.assertEqual(self.mem.count_events("aim_replaced", 0), 2, "замена — один раз")
        self.tick(WEEK * 0.4)                                        # неделя кончилась
        self.assertEqual(self.mem.count_events("aim_result", 0), 2)
        results = [m for m in self.mem.top_memories(50) if m["text"].startswith("Итог недели")]
        self.assertEqual(len(results), 2)
        self.assertEqual(self.mem.count_events("aim_new", 0), 4, "новая неделя — новые цели (замены — aim_replaced)")

    def test_help_counts_facts(self):
        self.make({"generosity": 0.95})
        self.tick()
        self.assertIn("help", self.kinds())
        for _ in range(2):
            self.mem.add_event("heal_given", {"from": "Arkady", "to": "Vera", "amount": 50})
        self.mem.add_event("gift_given", {"peer": "Vera"})
        self.tick()
        help_ = [a for a in self.aims.st["items"] if a["kind"] == "help"][0]
        self.assertEqual(help_["progress"], 3)
        self.assertTrue(help_["done"])


if __name__ == "__main__":
    unittest.main()
