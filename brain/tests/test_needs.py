"""Мотивы и характер числами (needs.py, ORG-015/019) и выбор карты с характером (ORG-010).

Запуск: cd brain && python3 -m unittest -v tests.test_needs
"""
import json
import random
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain.maps import EXPLORE_MIN, MapStats
from live_brain.memory import Memory
from live_brain.needs import Needs

BRAIN_DIR = Path(__file__).resolve().parents[1]


def persona(bot):
    return json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())


class NeedsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.mind = SimpleNamespace(mem=self.mem, persona=persona("bot01"), routine=None,
                                    ctx=SimpleNamespace(last={}),
                                    state={"hp_pct": 100, "items": {"501": 30}, "weight_pct": 20, "zeny": 60000})
        self.n = Needs(self.mind)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_facts_raise_needs(self):
        base = self.n.values()
        self.assertLess(base["safety"], 0.1)
        self.assertLess(base["supply"], 0.1)
        self.mem.add_event("died", {})
        self.mem.add_event("died", {})
        self.mind.state.update(hp_pct=20, items={"501": 0}, weight_pct=55)
        v = self.n.values()
        self.assertGreater(v["safety"], 0.7, "смерти и низкий HP")
        self.assertEqual(v["supply"], 1.0, "нет зелий и тяжело")

    def test_week_aims_boost_motive(self):
        """ORG-038: невыполненная цель недели усиливает свой мотив, остальные не трогает."""
        plain = self.n.weighted()
        self.mind.aims = SimpleNamespace(boost=lambda need: 1.5 if need == "social" else 1.0)
        boosted = self.n.weighted()
        self.assertAlmostEqual(boosted["social"], round(plain["social"] * 1.5, 2), delta=0.011)
        self.assertEqual(boosted["safety"], plain["safety"])

    def test_characters_differ(self):
        arkady = Needs(SimpleNamespace(mem=self.mem, persona=persona("bot01"), routine=None,
                                       ctx=SimpleNamespace(last={}), state={}))
        vera = Needs(SimpleNamespace(mem=self.mem, persona=persona("bot02"), routine=None,
                                     ctx=SimpleNamespace(last={}), state={}))
        self.assertGreater(vera.weight("social"), arkady.weight("social"))
        self.assertGreater(vera.weight("care"), arkady.weight("care"))
        self.assertGreater(arkady.risk_tolerance(), vera.risk_tolerance())
        self.assertGreater(arkady.session_factor(), vera.session_factor())
        self.assertGreater(vera.noise(), arkady.noise())

    def test_map_choice_uses_experience_and_noise(self):
        clock = SimpleNamespace(t=1000.0)
        ms = MapStats(SimpleNamespace(mem=self.mem), clock=lambda: clock.t)
        for name, exp, zeny in (("prt_fild08", 2.0, 1000), ("prt_fild07", 6.0, 3000)):
            for i in range(int(EXPLORE_MIN * 12) + 1):
                clock.t += 5
                ms.tick({"map": name, "exp_pct": 10 + exp * i / (EXPLORE_MIN * 12), "zeny": zeny * i}, hunting=True)
            ms.last = None
        self.assertEqual(ms.choose(["prt_fild08", "prt_fild07"], {})[0], "prt_fild07", "больше опыта в час")
        whimsical = SimpleNamespace(noise=lambda: 0.9, risk_tolerance=lambda: 0.5, t={"curiosity": 0.5})
        picks = {ms.choose(["prt_fild08", "prt_fild07"], {}, needs=whimsical, rng=random.Random(s))[0]
                 for s in range(40)}
        self.assertEqual(picks, {"prt_fild08", "prt_fild07"}, "причудливый иногда выбирает не лучшее")


if __name__ == "__main__":
    unittest.main()
