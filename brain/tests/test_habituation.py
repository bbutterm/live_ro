"""Привыкание (ORG-096, ТЗ Т-41): близость растёт от поступков, а не от сидения рядом.

social.together даёт +1 только до together_cap и не чаще раза в together_every_days; Episodes.weight_with —
сумма весов эпизодов пары с затуханием; wed.stage «близкие» — по весу общей истории; wed спит при < min_residents.
Запуск: cd brain && python3 -m unittest -v tests.test_habituation
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain.episodes import HALF_LIFE_DAYS, Episodes
from live_brain.memory import Memory
from live_brain.routine import load_world
from live_brain.wed import Wed
from tests.test_social import Clock, FakeMind, at_hour

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
DAY = 86400


class TogetherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock(at_hour(14))
        self.mem = Memory(Path(self.tmp.name) / "a.sqlite")
        self.a = FakeMind("bot01", "Vera", self.mem, self.clock)
        self.a.state["players"] = [{"name": "Vera", "x": 158, "y": 186, "lv": 30}]

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def day_together(self, minutes=31):
        """Один день: minutes рядом с Vera (шаг 5 с), затем часы — на следующие сутки."""
        start = self.clock.t
        for _ in range(minutes * 60 // 5):
            self.clock.t += 5
            self.a.social.together(self.clock.t, self.a.state, 5)
        self.clock.t = start + DAY

    def aff(self):
        return (self.mem.relation("Vera") or {}).get("affinity", 0)

    def test_two_weeks_without_deeds_stop_at_cap(self):
        for _ in range(14):
            self.day_together()
        self.assertEqual(self.aff(), 4)                     # 1 + раз в 3 дня: 1, 4, 7, 10 -> 4; дальше — потолок
        gains = [d["gain"] for d in (self.a.decisions or []) if d.get("event") == "together"]
        self.assertEqual(len(gains), 14)                    # запись — каждый день, как прежде
        self.assertEqual(sum(gains), 4)
        log = self.mem.relation_log("Vera")
        self.assertEqual(len([r for r in log if r["note"] == "провели время вместе"]), 4)   # только при gain 1

    def test_every_days(self):
        self.day_together()
        self.assertEqual(self.aff(), 1)
        self.day_together()
        self.day_together()
        self.assertEqual(self.aff(), 1)                     # дни 2 и 3 — без прибавки
        self.day_together()
        self.assertEqual(self.aff(), 2)                     # прошло 3 дня

    def test_deeds_above_cap_still_work(self):
        self.mem.update_relation("Vera", 2, "a")
        self.mem.update_relation("Vera", 2, "b")
        self.assertEqual(self.aff(), 4)
        self.day_together()
        self.assertEqual(self.aff(), 4)                     # «вместе» выше потолка не растит
        self.mem.update_relation("Vera", 1, "подарок")
        self.mem.update_relation("Vera", 1, "вылечил(а) меня")
        self.assertEqual(self.aff(), 6)                     # поступки — как прежде
        self.day_together()
        self.assertEqual(self.aff(), 6)                     # и привыкание не уменьшает отношение

    def test_config_restores_old_rule(self):
        self.a.social.cfg.update(together_cap=10, together_every_days=0)
        for _ in range(6):
            self.day_together()
        self.assertEqual(self.aff(), 6)                     # прежнее поведение: +1 каждый день


class WeightWithTest(unittest.TestCase):
    def test_weight_with_decays(self):
        now = 1_000_000_000.0
        kv = {"episodes": [
            {"id": "heal:Vera:1", "ts": now, "kind": "heal", "peer": "Vera", "weight": 5, "times": 1},
            {"id": "gift:Vera:2", "ts": now - HALF_LIFE_DAYS * DAY, "kind": "gift", "peer": "Vera", "weight": 4,
             "times": 1},
            {"id": "gift:Rook:3", "ts": now, "kind": "gift", "peer": "Rook", "weight": 3, "times": 1},
        ]}
        mind = SimpleNamespace(mem=SimpleNamespace(get=kv.get))
        ep = Episodes(mind, clock=lambda: now)
        self.assertAlmostEqual(ep.weight_with("Vera"), 5 + 2, places=2)    # второй — за полураспад вдвое легче
        self.assertAlmostEqual(ep.weight_with("Rook", now), 3, places=2)
        self.assertEqual(ep.weight_with("Ilsa"), 0)


class WedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "w.sqlite")
        self.now = 1_000_000_000.0
        self.weight = {"Vera": 0.0}
        self.decisions = []
        self.mind = SimpleNamespace(
            mem=self.mem, persona={"name": "Arkady"}, state={"name": "Arkady", "lv": 40, "map": "prontera"},
            fresh_state=True, ctx=SimpleNamespace(peers={"Vera"}), needs=None,
            episodes=SimpleNamespace(weight_with=lambda peer, now=None: self.weight.get(peer, 0.0)),
            write_decision=self.decisions.append)
        self.w = Wed(self.mind, WORLD, clock=lambda: self.now)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def tick(self):
        self.now += 60
        asyncio.run(self.w.tick())

    def test_close_by_weight(self):
        self.mem.update_relation("Vera", 2)
        self.mem.update_relation("Vera", 2)
        self.mem.update_relation("Vera", 2)
        self.mem.update_relation("Vera", 2)                 # 8 ≥ close_min
        self.weight["Vera"] = 19.9
        self.assertEqual(self.w.stage("Vera"), "friends")
        self.weight["Vera"] = 20
        self.assertEqual(self.w.stage("Vera"), "close")
        self.assertEqual(WORLD["wed"]["close_weight"], 20)
        self.assertNotIn("close_episodes", WORLD["wed"])

    def test_asleep_with_two_residents(self):
        self.assertEqual(WORLD["wed"]["min_residents"], 4)
        self.mem.update_relation("Vera", 2, "x")
        self.tick()
        self.assertTrue(self.w.asleep())
        self.assertFalse(self.w.st["pairs"])               # ступени не считаются
        self.assertEqual([d["event"] for d in self.decisions], ["wed_asleep"])
        self.tick()
        self.assertEqual(len(self.decisions), 1)            # отметка — не чаще раза в час
        self.assertEqual(self.w.refuse_why("Vera"), "few")

    def test_works_with_four(self):
        self.mind.ctx.peers = {"Vera", "Rook", "Ilsa"}
        self.mem.update_relation("Vera", 2, "x")
        self.mem.update_relation("Vera", 2, "y")
        self.tick()
        self.assertFalse(self.w.asleep())
        self.assertEqual(self.w.st["pairs"]["Vera"]["stage"], "friends")

    def test_existing_engagement_kept(self):
        self.w.st["engaged"] = {"peer": "Vera", "since": self.now}
        self.w.st["gift"] = {"peer": "Vera", "sent": True}
        for _ in range(4):
            self.mem.update_relation("Vera", 2, "x")
        self.assertFalse(self.w.asleep())                   # двое, но помолвка уже есть
        self.tick()
        self.assertEqual(self.w.fiance, "Vera")
        self.assertNotIn("wed_broken", [d.get("event") for d in self.decisions])


if __name__ == "__main__":
    unittest.main()
