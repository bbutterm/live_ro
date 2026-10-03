"""Шрамы, страх места и реванш (scars.py, ORG-093, ТЗ Т-48).

Запуск: cd brain && python3 -m unittest -v tests.test_scars
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain import chronicle
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.maps import MapStats
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.scars import Scars

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
DAY = 86400
MAPS = ["prt_fild08", "prt_fild07", "prt_fild05"]


class ScarsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.now = time.time()
        self.decisions = []
        self.mind = SimpleNamespace(mem=self.mem, persona={"name": "Arkady", "hunt_maps": list(MAPS)},
                                    state={"lv": 20, "hp_pct": 100, "map": "prt_fild07"},
                                    needs=SimpleNamespace(t={"bravery": 0.3}), write_decision=self.decisions.append)
        self.s = Scars(self.mind, WORLD, clock=lambda: self.now)
        self.mind.scars = self.s
        self.maps = MapStats(self.mind, clock=lambda: self.now)
        for m in MAPS:                                     # все карты изведаны, лучшая — prt_fild07
            self.maps.stats()[m] = {"minutes": 60, "kills": 50, "deaths": 0,
                                    "exp": 30.0 if m == "prt_fild07" else 10.0, "zeny": 0}

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def die(self, hmap="prt_fild07", mob="Vocal", ago=0):
        self.mem.add_event("death_report", {"map": hmap, "cause": mob})
        t, self.now = self.now, self.now - ago
        self.s.on_died({"map": hmap})
        self.now = t

    def choose(self):
        return self.maps.choose(MAPS, {}, rng=random.Random(1))

    def test_death_writes_scar(self):
        self.die()
        sc = self.mem.get("scars")
        self.assertEqual((sc[0]["map"], sc[0]["mob"], sc[0]["lv"]), ("prt_fild07", "Vocal", 20))
        self.die()
        self.assertEqual(len(self.mem.get("scars")), 1)
        self.assertEqual(self.mem.get("scars")[0]["deaths"], 2)

    def test_cautious_avoids_two_weeks(self):
        self.assertEqual(self.choose()[0], "prt_fild07")
        self.die(ago=10 * DAY)
        self.assertNotEqual(self.choose()[0], "prt_fild07")
        self.s.items[0]["ts"] = self.now - 15 * DAY
        self.assertEqual(self.choose()[0], "prt_fild07")

    def test_cautious_all_scarred_keeps_list(self):
        for m in MAPS:
            self.die(hmap=m, ago=DAY)
        self.assertIn(self.choose()[0], MAPS)

    def test_brave_takes_revenge_when_stronger(self):
        self.mind.needs.t["bravery"] = 0.7
        self.die(ago=3 * DAY)
        self.mind.state["lv"] = 21                         # +1 — рано
        self.assertNotIn("реванш", self.choose()[1])
        self.mind.state["lv"] = 22
        self.mind.state["hp_pct"] = 80                     # HP ниже 90 % — нет
        self.assertNotIn("реванш", self.choose()[1])
        self.mind.state["hp_pct"] = 95
        self.maps.stats()["prt_fild05"]["exp"] = 90.0       # другая карта лучше — реванш всё равно важнее
        hmap, why = self.choose()
        self.assertEqual(hmap, "prt_fild07")
        self.assertIn("реванш: здесь меня убил Vocal 3 дня назад", why)

    def test_brave_not_too_early_and_tries_limit(self):
        self.mind.needs.t["bravery"] = 0.7
        self.die(ago=DAY)
        self.mind.state["lv"] = 25
        self.assertNotIn("реванш", self.choose()[1])       # прошло меньше 2 дней
        self.s.items[0]["ts"] = self.now - 3 * DAY
        for i in range(3):
            self.assertIn("реванш", self.choose()[1])
            self.now += 7 * 3600                           # следующая попытка — через 6+ ч
        self.assertEqual(self.s.items[0]["tries"], 3)
        self.assertNotIn("реванш", self.choose()[1])

    def test_cautious_never_revenge(self):
        self.die(ago=3 * DAY)
        self.mind.state["lv"] = 30
        self.assertIsNone(self.s.revenge(MAPS))

    def test_kill_same_mob_closes_scar(self):
        self.die(ago=3 * DAY)
        self.assertIsNone(self.s.on_kill({"monster": "Poring", "map": "prt_fild07"}))
        self.assertIsNone(self.s.on_kill({"monster": "Vocal", "map": "prt_fild08"}))
        d = self.s.on_kill({"monster": "Vocal", "map": "prt_fild07"})
        self.assertEqual(d["days"], 3)
        self.assertEqual(self.mem.count_events("revenge", 0), 1)
        self.assertEqual(self.s.open(), [])
        self.assertEqual(self.s.fear("prt_fild07"), 0.0)
        self.assertEqual(self.decisions[-1]["type"], "revenge")
        line = chronicle.LINES["revenge"](d)
        self.assertEqual(line, "взял(а) реванш у Vocal на prt_fild07 (через 3 дня)")
        self.assertIsNone(self.s.on_kill({"monster": "Vocal", "map": "prt_fild07"}))   # шрам закрыт

    def test_kill_right_after_death_not_revenge(self):
        self.die()
        self.assertIsNone(self.s.on_kill({"monster": "Vocal", "map": "prt_fild07"}))

    def test_fear_decays(self):
        self.die(ago=7 * DAY)
        self.assertAlmostEqual(self.s.fear("prt_fild07"), 0.5, places=2)
        self.assertEqual(self.s.fear("prt_fild05"), 0.0)


class MindScarsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, env=None):
        async def send(a):
            return 1
        mem = Memory(self.root / f"m{len(list(self.root.iterdir()))}.sqlite")
        self.addCleanup(mem.close)
        p = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        m = Mind(Settings.from_env(env or {}), p, mem, send, self.root / "d.jsonl", RuleGate(),
                 peers={"Arkady", "Vera"}, world=WORLD)
        m.state = {"name": "Arkady", "map": "prt_fild08", "lv": 20, "hp_pct": 100}
        return m

    def test_death_event_through_mind(self):
        m = self.make()
        self.assertIsNotNone(m.scars)
        hmap = m.persona["hunt_maps"][0]
        m.postmortem.observe("attack", {"monster": "Vocal"})
        asyncio.run(m.on_message({"type": "event", "kind": "died", "map": hmap}))
        sc = m.mem.get("scars")
        self.assertEqual((sc[0]["map"], sc[0]["mob"]), (hmap, "Vocal"))

    def test_switch_off(self):
        m = self.make({"BRAIN_DISABLE": "scars"})
        self.assertIsNone(m.scars)
        asyncio.run(m.on_message({"type": "event", "kind": "died", "map": "prt_fild08"}))
        self.assertIsNone(m.mem.get("scars"))


if __name__ == "__main__":
    unittest.main()
