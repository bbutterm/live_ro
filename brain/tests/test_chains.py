"""Цепочки предусловий, GOAP-лайт (activity.py, ORG-018): исправитель перед недоступным лучшим занятием.

Запуск: cd brain && python3 -m unittest -v tests.test_chains
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
NEEDS = {"safety": 0.0, "supply": 0.4, "progress": 0.1, "social": 1.2, "curiosity": 0.1, "rest": 0.0,
         "wealth": 0.0, "care": 0.0}


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class ChainTest(unittest.TestCase):
    def setUp(self, catalog=None):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(a)
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        persona["traits"] = {"whimsy": 0.0}                       # шум выбора минимальный
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({"BRAIN_DISABLE": "home"}), persona, self.mem, send, root / "d.jsonl",
                         RuleGate(), peers={"Arkady", "Vera"},
                         world=load_world(BRAIN_DIR / "world" / "goals.json"))
        self.clock = Clock()
        kw = {"path": catalog} if catalog else {}
        self.a = Activities(self.mind, clock=self.clock, rng=random.Random(3), **kw)
        self.mind.activities = self.a
        self.needs = dict(NEEDS)
        self.mind.needs.weighted = lambda: dict(self.needs)
        self.dec = root / "d.jsonl"
        self.state(map="prontera", x=156, y=185, lock_map="prontera", lock_x=156, lock_y=185)
        r = self.mind.routine
        r.new_day(self.clock.t)
        r.st.update(mode="town", arrived=True, rest_until=self.clock.t + 3600, mode_since=self.clock.t)
        self.mind.social.is_night = lambda now: False

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 41,
             "job": "Swordsman", "dead": False, "weight_pct": 20, "zeny": 60000, "items": {"501": 30},
             "players": []}
        s.update(getattr(self, "_last", {}))
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def tick(self):
        self.a.next_decide = 0
        asyncio.run(self.a.tick())

    def records(self, event=None):
        if not self.dec.exists():
            return []
        out = [json.loads(l) for l in self.dec.read_text().splitlines() if '"activity"' in l]
        return [r for r in out if event is None or r.get("event") == event]

    def test_heavy_socialize_goes_service_first(self):
        """Готово ORG-018: вес 60%, лучшее — навестить жителя, но сначала поездка по делам; видно в журнале."""
        self.state(weight_pct=60, players=[{"name": "Vera", "x": 160, "y": 185}])
        self.tick()
        start = self.records("chain_start")
        self.assertEqual(len(start), 1)
        self.assertEqual(start[0]["target"], "socialize")
        self.assertEqual(start[0]["plan"], "service → socialize")
        self.assertIn("light", start[0]["missing"])
        self.assertIn("service", [a["action"] for a in self.sent])
        self.assertEqual(self.a.st["name"], "service")
        self.tick()                                              # вес не упал — цель не начинается
        self.assertEqual(self.a.st["name"], "service")
        self.state(weight_pct=25)                                # продал/сдал — легко
        self.tick()
        self.assertEqual([r["step"] for r in self.records("chain_step_done")], ["service"])
        self.assertEqual(self.records("chain_done")[0]["target"], "socialize")
        self.assertEqual(self.a.st["name"], "socialize")
        self.assertNotIn("chain", self.a.st)
        self.assertEqual(self.mem.count_events("activity_chain", 0), 1)

    def test_step_timeout_fails_and_ttl_blocks_repeat(self):
        self.state(weight_pct=60, players=[{"name": "Vera", "x": 160, "y": 185}])
        self.tick()
        self.assertTrue(self.a.st.get("chain"))
        self.clock.t += 21 * 60                                  # вес так и не упал
        self.tick()
        failed = self.records("chain_failed")
        self.assertEqual(len(failed), 1)
        self.assertIn("service", failed[0]["why"])
        self.assertNotIn("chain", self.a.st)
        self.tick()                                              # та же ситуация — без новой цепочки (TTL)
        self.assertEqual(len(self.records("chain_start")), 1)
        self.clock.t += 61 * 60                                  # TTL прошёл, перезарядка service тоже
        self.tick()
        self.assertEqual(len(self.records("chain_start")), 2)

    def test_mode_change_aborts(self):
        self.state(weight_pct=60, players=[{"name": "Vera", "x": 160, "y": 185}])
        self.tick()
        self.mind.routine.st.update(mode="hunt", mode_since=self.clock.t - 3600)
        self.tick()
        self.assertEqual(self.records("chain_failed")[0]["why"], "режим распорядка сменился")

    def test_night_is_unfixable(self):
        self.mind.social.is_night = lambda now: True
        self.state(players=[{"name": "Vera", "x": 160, "y": 185}])
        self.tick()
        imp = {r["target"]: r for r in self.records("chain_impossible")}
        self.assertIn("socialize", imp)
        self.assertIn("ночь", imp["socialize"]["why"])
        self.assertEqual(self.records("chain_start"), [])
        n = len(self.records("chain_impossible"))
        self.tick()
        self.assertEqual(len(self.records("chain_impossible")), n, "повтор той же невозможной цепочки — после TTL")

    def test_no_potions_then_hunt_early(self):
        """Нет зелий: «пойти на охоту раньше» требует potions_min — сначала по делам (закупка)."""
        self.needs.update(social=0.0, progress=1.5, supply=0.5)
        self.state(items={"501": 0})
        self.tick()
        start = self.records("chain_start")
        self.assertEqual(start[0]["target"], "hunt_early")
        self.assertEqual([s["name"] for s in start[0]["steps"]], ["service"])
        self.assertEqual(start[0]["steps"][0]["fixes"], {"potions_min": 5})
        self.state(items={"501": 20})
        self.tick()
        self.assertEqual(self.records("chain_done")[0]["target"], "hunt_early")


class PlanTest(unittest.TestCase):
    """plan_chain на своём каталоге: глубина, слияние условий, длина, отказ без зацикливания."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cat = json.loads((BRAIN_DIR / "world" / "activities.json").read_text(encoding="utf-8"))
        acts = cat["activities"]
        acts["visit_shop"] = {"label": "к торговцу", "modes": ["town"], "satisfies": {"wealth": 0.1},
                              "requires": {"light": True}, "provides": ["zeny_min"], "cooldown_minutes": 0,
                              "proof": "none"}
        acts["buy_gift"] = {"label": "купить подарок", "modes": ["town"], "satisfies": {"care": 3.0},
                            "requires": {"zeny_min": 100000, "light": True}, "cooldown_minutes": 0, "proof": "none"}
        acts["loop_a"] = {"label": "a", "modes": ["town"], "satisfies": {}, "requires": {"cond_b": True},
                          "provides": ["cond_a"], "proof": "none"}
        acts["loop_b"] = {"label": "b", "modes": ["town"], "satisfies": {}, "requires": {"cond_a": True},
                          "provides": ["cond_b"], "proof": "none"}
        self.path = Path(self.tmp.name) / "acts.json"
        self.path.write_text(json.dumps(cat), encoding="utf-8")
        self.t = ChainTest("test_night_is_unfixable")
        self.t.setUp(catalog=self.path)

    def tearDown(self):
        self.t.tearDown()
        self.tmp.cleanup()

    def plan(self, target, **state):
        self.t.state(**state)
        a = self.t.a
        needs = self.t.needs
        miss = a.missing(a.catalog[target]["requires"], self.t.mind.state, needs)
        return a.plan_chain(target, miss, self.t.mind.state, needs, self.t.clock.t)

    def test_service_fixes_both_conditions_once(self):
        steps, why = self.plan("buy_gift", weight_pct=60, zeny=10)
        self.assertIsNone(why)
        self.assertEqual([s["name"] for s in steps], ["service"])
        self.assertEqual(steps[0]["fixes"], {"zeny_min": 100000, "light": True})

    def test_depth_fixer_with_own_precondition(self):
        cat = self.t.a.catalog
        cat["service"]["provides"] = ["light"]                   # деньги даёт только visit_shop (нужен лёгкий рюкзак)
        steps, why = self.plan("buy_gift", weight_pct=60, zeny=10)
        self.assertIsNone(why)
        self.assertEqual([s["name"] for s in steps], ["service", "visit_shop"])

    def test_too_long_and_cycle_refused(self):
        steps, why = self.plan("loop_a")
        self.assertIsNone(steps)
        self.assertTrue(why)

    def test_cooldown_fixer_not_used(self):
        a = self.t.a
        a.st.setdefault("last", {})["service"] = self.t.clock.t
        steps, why = self.plan("socialize", weight_pct=60, players=[{"name": "Vera", "x": 160, "y": 185}])
        self.assertIsNone(steps)
        self.assertIn("light", why)


if __name__ == "__main__":
    unittest.main()
