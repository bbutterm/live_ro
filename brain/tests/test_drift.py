"""Дрейф характера (drift.py, ORG-092, ТЗ Т-47): черты сдвигаются от фактов недели в пределах ±0.2.

Запуск: cd brain && python3 -m unittest -v tests.test_drift
"""
import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from live_brain import chronicle
from live_brain.config import Settings
from live_brain.drift import TraitDrift, report_line
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
WEEK = 7 * 86400


def persona():
    return {"name": "Arkady", "hunt_maps": ["prt_fild08"],
            "traits": {"bravery": 0.6, "sociability": 0.5, "generosity": 0.4, "curiosity": 0.5, "_comment": "x"}}


class DriftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.now = time.time()
        self.decisions = []
        self.mind = SimpleNamespace(mem=self.mem, persona=persona(), state={"lv": 20},
                                    needs=SimpleNamespace(t={}), write_decision=self.decisions.append)
        self.d = TraitDrift(self.mind, WORLD, clock=lambda: self.now)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def start_week(self, ago=WEEK + 60):
        self.d.st.update(week=self.now - ago, minutes0=self.d.hunt_minutes())

    def talk(self, n=1):
        for _ in range(n):
            self.mem.add_event("social_said", {"peer": "Vera", "topic": "hunt"})

    def test_first_tick_starts_week_only(self):
        self.assertIsNone(self.d.tick())
        self.assertAlmostEqual(self.mem.get("traits_drift")["week"], self.now)
        self.assertEqual(self.d.effective()["bravery"], 0.6)

    def test_week_not_passed_nothing(self):
        self.start_week(ago=3 * 86400)
        for _ in range(3):
            self.mem.add_event("died", {"map": "prt_fild08"})
        self.assertIsNone(self.d.tick())
        self.assertEqual(self.d.drift()["bravery"], 0.0)

    def test_deaths_lower_bravery_step_capped(self):
        self.start_week()
        self.talk()
        for _ in range(3):
            self.mem.add_event("died", {"map": "prt_fild08"})
        ch = self.d.tick()
        self.assertEqual(ch["bravery"], [0.6, 0.55])                 # −0.12 → шаг не больше 0.05
        self.assertEqual(self.mind.persona["traits"]["bravery"], 0.55)
        self.assertEqual(self.mind.persona["traits_innate"]["bravery"], 0.6)
        self.assertEqual(self.mind.needs.t["bravery"], 0.55)
        self.assertEqual(self.mind.persona["traits"]["_comment"], "x")
        self.assertEqual(self.mem.count_events("trait_shift", 0), 1)
        line = chronicle.LINES["trait_shift"](json.loads(self.mem.db.execute(
            "SELECT data FROM events WHERE kind = 'trait_shift'").fetchone()[0]))
        self.assertIn("осторожнее (3 смерти за неделю)", line)
        self.assertEqual(self.decisions[-1]["type"], "trait_shift")

    def test_hunting_without_death_raises_bravery(self):
        self.start_week()
        self.talk()
        self.mem.set("map_stats", {"prt_fild08": {"minutes": 20 * 60 + 5, "kills": 0, "deaths": 0}})
        self.assertEqual(self.d.tick()["bravery"], [0.6, 0.64])

    def test_strong_wins_raise_bravery(self):
        self.start_week()
        self.talk()
        self.mem.set("monster_risk", {"Vocal": {"killed_me": 1, "beaten": 0, "last": 0}})
        self.mem.add_event("kill", {"monster": "Vocal", "map": "prt_fild07"})
        self.mem.add_event("kill", {"monster": "Vocal", "map": "prt_fild07"})
        self.assertEqual(self.d.tick()["bravery"], [0.6, 0.62])

    def test_gifts_heals_generosity_and_loneliness(self):
        self.start_week()
        self.mem.add_event("gift_given", {"peer": "Vera"})
        self.mem.add_event("heal_given", {"to": "Vera"})
        ch = self.d.tick()
        self.assertEqual(ch["generosity"], [0.4, 0.42])
        self.assertEqual(ch["sociability"], [0.5, 0.48])             # ни одного разговора за неделю

    def test_many_talks_and_new_places(self):
        self.start_week()
        self.talk(14)
        self.mem.set("places", {"a": {"source": "seen", "first": self.now - 100},
                                "b": {"source": "seen", "first": self.now - 200},
                                "c": {"source": "told", "first": self.now}})
        ch = self.d.tick()
        self.assertEqual(ch["sociability"], [0.5, 0.52])
        self.assertEqual(ch["curiosity"], [0.5, 0.52])

    def test_limit_and_anchor(self):
        for _ in range(3):
            self.mem.add_event("died", {"map": "prt_fild08"})
        self.talk()
        for _ in range(12):                                            # 12 недель подряд по 3 смерти
            self.start_week()
            self.d.tick()
        self.assertGreaterEqual(self.d.drift()["bravery"], -0.2)
        self.assertLess(self.d.drift()["bravery"], -0.15)
        self.mem.db.execute("DELETE FROM events WHERE kind = 'died'")
        before = self.d.drift()["bravery"]
        self.start_week()
        self.d.tick()                                                  # неделя без смертей и охоты: только якорь
        self.assertAlmostEqual(self.d.drift()["bravery"], round(before * 0.9, 4), places=3)

    def test_stays_in_bounds(self):
        p = persona()
        p["traits"]["bravery"] = 0.02
        d = TraitDrift(SimpleNamespace(mem=self.mem, persona=p, state={}, needs=SimpleNamespace(t={})), WORLD,
                       clock=lambda: self.now)
        d.st.update(week=self.now - WEEK - 60, minutes0=0)
        self.mem.add_event("died", {})
        d.tick()
        self.assertEqual(d.effective()["bravery"], 0.0)
        self.assertAlmostEqual(d.drift()["bravery"], -0.02, places=3)

    def test_long_pause_restarts_week(self):
        self.start_week(ago=3 * WEEK)
        self.mem.add_event("died", {})
        self.assertIsNone(self.d.tick())
        self.assertEqual(self.d.drift()["bravery"], 0.0)

    def test_restored_on_restart_and_innate_kept(self):
        self.start_week()
        self.talk()
        self.mem.add_event("died", {})
        self.d.tick()
        p = self.mind.persona                                          # тот же словарь (повторное создание)
        d2 = TraitDrift(SimpleNamespace(mem=self.mem, persona=p, state={}, needs=SimpleNamespace(t={})), WORLD)
        self.assertEqual(d2.innate["bravery"], 0.6)
        self.assertEqual(d2.effective()["bravery"], 0.56)
        fresh = persona()
        d3 = TraitDrift(SimpleNamespace(mem=self.mem, persona=fresh, state={}, needs=SimpleNamespace(t={})), WORLD)
        self.assertEqual(fresh["traits"]["bravery"], 0.56)

    def test_report_line(self):
        self.assertEqual(report_line({"drift": {}, "innate": {}}), "характер: как врождённый")
        line = report_line({"drift": {"bravery": -0.08}, "innate": {"bravery": 0.6}})
        self.assertEqual(line, "характер: смелость 0.52 (врожд. 0.60, -0.08)")
        self.assertIsNone(report_line(None))


class MindDriftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, env=None, mem=None):
        async def send(a):
            return 1
        mem = mem or Memory(self.root / "m.sqlite")
        self.addCleanup(mem.close)
        p = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        return Mind(Settings.from_env(env or {}), p, mem, send, self.root / "d.jsonl", RuleGate(),
                    peers={"Arkady", "Vera"}, world=WORLD), p

    def test_needs_read_effective_traits_file_untouched(self):
        path = BRAIN_DIR / "personas" / "bot01.json"
        raw = path.read_text()
        innate = json.loads(raw)["traits"]["bravery"]
        mem = Memory(self.root / "m.sqlite")
        mem.set("traits_drift", {"drift": {"bravery": -0.1}, "week": time.time()})
        m, p = self.make(mem=mem)
        self.assertIsNotNone(m.drift)
        self.assertAlmostEqual(m.needs.t["bravery"], round(innate - 0.1, 3))
        self.assertAlmostEqual(p["traits"]["bravery"], round(innate - 0.1, 3))
        self.assertEqual(path.read_text(), raw)

    def test_switch_off(self):
        mem = Memory(self.root / "m.sqlite")
        mem.set("traits_drift", {"drift": {"bravery": -0.1}, "week": time.time()})
        m, p = self.make({"BRAIN_DISABLE": "drift"}, mem=mem)
        self.assertIsNone(m.drift)
        self.assertNotIn("traits_innate", p)
        self.assertEqual(m.needs.t["bravery"], json.loads((BRAIN_DIR / "personas" / "bot01.json")
                                                          .read_text())["traits"]["bravery"])

    def test_report_prints_character(self):
        from live_brain.__main__ import report
        mem = Memory(self.root / "r.sqlite")
        self.addCleanup(mem.close)
        mem.set("traits_drift", {"drift": {"bravery": -0.08}, "innate": {"bravery": 0.6}})
        args = SimpleNamespace(bot="bot01", persona=str(BRAIN_DIR / "personas" / "bot01.json"))
        out = io.StringIO()
        with redirect_stdout(out):
            report(args, mem, self.root)
        self.assertIn("характер: смелость 0.52", out.getvalue())


if __name__ == "__main__":
    unittest.main()
