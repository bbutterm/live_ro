"""Разбор смерти, опасные карты и монстры (postmortem.py, AUT-009/010/011/043).

Запуск: cd brain && python3 -m unittest -v tests.test_postmortem
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain.memory import Memory
from live_brain.postmortem import Postmortem

BRAIN_DIR = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class PostmortemTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.dec = []
        self.mind = SimpleNamespace(mem=self.mem, persona=json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text()),
                                    write_decision=self.dec.append, state={})
        self.clock = Clock()
        self.pm = Postmortem(self.mind, clock=self.clock)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def fight(self, monsters, potions=0, weight=40):
        self.pm.on_state({"hp_pct": 30, "weight_pct": weight, "map": "prt_fild08", "items": {"501": potions}})
        for m in monsters:
            self.pm.observe("attack", {"monster": m})

    def test_report_only_observed_facts(self):
        self.fight(["Poring", "Lunatic", "Lunatic"], potions=0, weight=72)
        self.pm.observe("survival", {"action": "potion"})
        rep = self.pm.report({"kind": "died", "map": "prt_fild08"})
        self.assertEqual(rep["cause"], "Lunatic")
        self.assertEqual(rep["potions_left"], 0)
        self.assertEqual(rep["weight_pct"], 72)
        self.assertEqual(rep["unknown"], [])
        text = self.mem.top_memories(5)[0]["text"]
        self.assertIn("Lunatic×2", text)
        self.assertIn("зелий оставалось 0", text)
        self.assertEqual(self.pm.risky_monsters()["Lunatic"]["killed_me"], 1)

    def test_unknown_is_marked_not_invented(self):
        rep = self.pm.report({"kind": "died", "map": "prt_fild08"})
        self.assertIsNone(rep["cause"])
        self.assertIn("кто бил", rep["unknown"])
        self.assertIn("неизвестно", self.mem.top_memories(5)[0]["text"])

    def test_old_fight_not_blamed(self):
        self.fight(["Poring"])
        self.clock.t += 60                                   # бой был минуту назад
        rep = self.pm.report({"kind": "died", "map": "prt_fild08"})
        self.assertIsNone(rep["cause"])

    def test_two_deaths_ban_map(self):
        self.fight(["Lunatic"])
        self.pm.report({"kind": "died", "map": "prt_fild08"})
        self.assertEqual(self.pm.bans(), {})
        self.clock.t += 600
        self.fight(["Lunatic"])
        self.pm.report({"kind": "died", "map": "prt_fild08"})
        self.assertIn("prt_fild08", self.pm.bans())
        self.clock.t += 7300
        self.assertEqual(self.pm.bans(), {}, "исключение временное")

    def test_kills_counted_and_resurrect_check(self):
        self.pm.on_kill("Poring")
        self.pm.on_kill("Poring")
        self.assertEqual(self.mem.get("monster_risk")["Poring"]["beaten"], 2)
        self.assertEqual(self.pm.risky_monsters(), {}, "кого только побеждали — не опасен")
        self.assertFalse(self.pm.can_resurrect())
        self.mind.state = {"combat": {"applied": {"party": ["AL_HEAL", "ALL_RESURRECTION"]}}}
        self.assertTrue(self.pm.can_resurrect())


if __name__ == "__main__":
    unittest.main()
