"""Стигмергия занятий и карт (crowd.py, ORG-089): presence в шине, штрафы людных карт и занятий, повторы, метрика.

Настоящий Mind (Arkady) и шина мира во временном каталоге; Vera (не в группе) и Bram (в группе Arkady: группы по
три по порядку имён) — другие писатели той же шины. Запуск: cd brain && python3 -m unittest -v tests.test_crowd
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import world_bus
from live_brain.__main__ import organic_metrics
from live_brain.config import Settings
from live_brain.crowd import Crowd
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
A, B = "prt_fild08", "prt_fild07"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class CrowdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.path = root / "world.sqlite"
        self.bus = world_bus.WorldBus(self.path, "Arkady")
        self.others = {n: world_bus.WorldBus(self.path, n) for n in ("Vera", "Bram")}
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera", "Bram", "Ilsa"}, world=WORLD, world_bus_db=self.bus)
        self.clock = Clock(time.time())
        self.c = Crowd(self.mind, WORLD, clock=self.clock)
        self.c.cfg["cache_seconds"] = 0
        self.mind.crowd = self.c
        asyncio.run(self.mind.on_message({"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185,
                                          "hp_pct": 100, "lv": 41, "dead": False, "players": []}))

    def tearDown(self):
        for b in [self.bus, *self.others.values()]:
            b.close()
        self.mem.close()
        self.tmp.cleanup()

    def there(self, who, hmap, activity=None):
        self.others[who].replace("presence", {"map": hmap, "activity": activity, "mode": "hunt"}, 1, now=self.clock.t)

    def equal_maps(self):
        st = {m: {"minutes": 60.0, "kills": 100, "deaths": 0, "exp": 10.0, "zeny": 0} for m in (A, B)}
        self.mem.set("map_stats", st)
        self.mind.maps.cache = None

    def choose(self):
        return self.mind.maps.choose([A, B], set(), rng=random.Random(1))

    # ---------- шина ----------

    def test_presence_one_per_bot_and_quiet(self):
        self.mind.fresh_state = True
        self.c.tick()
        self.clock.t += 6 * 60
        self.c.tick()
        n = self.bus.db.execute("SELECT COUNT(*) FROM world_events WHERE kind = 'presence' AND bot = 'Arkady'"
                                ).fetchone()[0]
        self.assertEqual(n, 1, "затирание: одна запись на жителя")
        self.assertEqual(self.others["Vera"].latest("presence")["Arkady"]["data"]["map"], "prontera")
        self.assertFalse([e for e in world_bus.read_period(self.path, 0, self.clock.t + 10) if e["kind"] == "presence"],
                         "presence не попадает в летопись и дашборд")

    def test_no_bus_does_not_fail(self):
        self.mind.world = None
        self.c.tick()
        self.assertEqual(self.c.others(), {})
        self.assertEqual(self.c.map_penalty(A), 0.0)

    # ---------- карты ----------

    def test_crowded_map_loses_on_equal_experience(self):
        self.equal_maps()
        self.assertEqual(self.choose()[0], A, "без толпы — первая из равных")
        self.there("Vera", A)
        choice, why = self.choose()
        self.assertEqual(choice, B)
        self.assertIn("людно", why)

    def test_own_group_is_not_a_crowd(self):
        self.assertIn("Bram", self.mind.party.mates())
        self.equal_maps()
        self.there("Bram", A)
        self.assertEqual(self.c.map_share(A), 0.0)
        self.assertEqual(self.choose()[0], A)

    def test_cautious_penalizes_less(self):
        self.there("Vera", A)
        self.mind.needs.t["bravery"] = 0.9
        brave = self.c.map_penalty(A)
        self.mind.needs.t["bravery"] = 0.1
        cautious = self.c.map_penalty(A)
        self.assertGreater(brave, cautious)
        self.assertGreater(cautious, 0)

    def test_visible_strangers_on_my_map(self):
        self.mind.state["players"] = [{"name": "Человек", "x": 1, "y": 1}, {"name": "Bram", "x": 2, "y": 2}]
        self.assertGreater(self.c.map_share("prontera"), 0)
        self.assertEqual(self.c.map_share(A), 0.0, "видимые игроки — только на моей карте")

    def test_unexplored_less_crowded_first(self):
        self.there("Vera", A)
        self.assertEqual(self.choose()[0], B, "оба места не изведаны — сначала менее людное")

    # ---------- занятия ----------

    def test_activity_crowd_and_repeats(self):
        self.assertEqual(self.c.activity_penalty("rest", "town"), 0.0)
        self.there("Vera", A, activity="rest")
        crowd = self.c.activity_penalty("rest", "hunt")
        self.assertAlmostEqual(crowd, self.c.cfg["weight"])
        self.assertLess(self.c.activity_penalty("rest", "town"), crowd, "общительному толпа в городе мешает меньше")
        self.mem.add_event("activity", {"name": "stroll"})
        self.mem.add_event("activity", {"name": "stroll"})
        self.assertAlmostEqual(self.c.activity_penalty("stroll", "town"), 2 * self.c.cfg["repeat"])
        self.assertEqual(self.c.activity_penalty("stroll", "town", current="stroll"), 0.0, "текущее — без штрафа")

    def test_scores_use_penalty(self):
        acts = self.mind.activities
        self.mind.routine.st.update(mode="town", arrived=True)
        acts.rng = random.Random(7)
        before, _ = acts.scores(self.mind.state)
        self.there("Vera", A, activity="rest")
        acts.rng = random.Random(7)
        after, _ = acts.scores(self.mind.state)
        self.assertLess(after["rest"], before["rest"])

    def test_metric_variety(self):
        for name in ("rest", "rest", "stroll", "rest"):
            self.mem.add_event("activity", {"name": name})
        m = organic_metrics(self.mem, time.time() - 3600)
        self.assertEqual(m["разнообразие занятий"], 0.5)


if __name__ == "__main__":
    unittest.main()
