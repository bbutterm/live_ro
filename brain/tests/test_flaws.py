"""Крайние черты и изъян (flaws.py, ORG-101, ТЗ Т-49).

Запуск: cd brain && python3 -m unittest -v tests.test_flaws
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from live_brain.config import Settings
from live_brain.flaws import CATALOG, Flaw, flaw_of, label
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT = BRAIN_DIR.parent
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TEMPLATES = sorted((ROOT / "bots" / "templates").glob("*/persona.json"))


def traits(p):
    return {k: v for k, v in (p.get("traits") or {}).items() if not k.startswith("_")}


class TemplatesTest(unittest.TestCase):
    def test_each_template_one_extreme_and_flaw(self):
        self.assertGreaterEqual(len(TEMPLATES), 4)
        flaws = set()
        for path in TEMPLATES:
            p = json.loads(path.read_text(encoding="utf-8"))
            extreme = [k for k, v in traits(p).items() if v <= 0.1 or v >= 0.9]
            self.assertEqual(len(extreme), 1, f"{path}: {extreme}")
            self.assertIn(p.get("flaw"), CATALOG, path)
            flaws.add(p["flaw"])
        self.assertEqual(len(flaws), len(TEMPLATES))                 # у всех разные изъяны

    def test_base_residents_untouched(self):
        for bot in ("bot01", "bot02"):
            p = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text(encoding="utf-8"))
            self.assertIsNone(p.get("flaw"))

    def test_unknown_flaw_dropped(self):
        with self.assertLogs("flaws", "WARNING"):
            self.assertIsNone(flaw_of({"name": "X", "flaw": "lazy"}))
        self.assertEqual(label({"flaw": "miser"}), "изъян: скряга")
        self.assertIsNone(label({}))


class MindFlawTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, flaw=None, env=None):
        async def send(a):
            return 1
        mem = Memory(self.root / f"m{len(list(self.root.iterdir()))}.sqlite")
        self.addCleanup(mem.close)
        p = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        if flaw:
            p["flaw"] = flaw
        m = Mind(Settings.from_env(env or {}), p, mem, send, self.root / "d.jsonl", RuleGate(),
                 peers={"Arkady", "Vera"}, world=WORLD)
        m.state = {"name": "Arkady", "map": "prontera", "lv": 20, "hp_pct": 100, "zeny": 100000,
                   "items": {"501": 50}}
        return m

    def test_no_flaw_no_module(self):
        self.assertIsNone(self.make().flaw)

    def test_chatterbox_and_silent_budget(self):
        now = time.time()
        base = self.make().attention.base(now)
        self.assertAlmostEqual(self.make("chatterbox").attention.base(now), base * 2, places=5)
        self.assertAlmostEqual(self.make("silent").attention.base(now), base * 0.3, places=5)

    def test_coward_risk(self):
        plain = self.make().needs.risk_tolerance()
        self.assertAlmostEqual(self.make("coward").needs.risk_tolerance(), plain * 0.7, places=5)

    def test_miser_refuses_zeny_shares_potions(self):
        m = self.make("miser")
        self.assertEqual(m.economy.refuse_reason("Vera", "z", 100), "деньги не даю")
        self.assertNotEqual(m.economy.refuse_reason("Vera", "501", 1), "деньги не даю")
        plain = self.make()
        self.assertNotEqual(plain.economy.refuse_reason("Vera", "z", 100), "деньги не даю")
        self.assertAlmostEqual(m.economy.greed(), min(1.0, plain.economy.greed() + 0.2))

    def test_homebody_hops(self):
        f = Flaw(None, "homebody")
        self.assertEqual(f.cap("hops", 4), 1)
        self.assertEqual(Flaw(None, "miser").cap("hops", 4), 4)
        m = self.make("homebody")
        if m.explorer is None:
            self.skipTest("explore выключен в goals.json")
        m.explorer.reach_maps = lambda: {"prt_fild01": {"hops": 2, "x": 1, "y": 1}}
        self.assertEqual(m.explorer.check_target("prt_fild01", 20)[1], "дальше 1 переходов")

    def test_switch_off(self):
        m = self.make("chatterbox", {"BRAIN_DISABLE": "flaw"})
        self.assertIsNone(m.flaw)
        self.assertAlmostEqual(m.attention.base(time.time()), self.make().attention.base(time.time()), places=5)


if __name__ == "__main__":
    unittest.main()
