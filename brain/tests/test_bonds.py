"""Связи жителей (bonds.py, ORG-023/024/025): встреча с продолжением, дружба, забота.

Запуск: cd brain && python3 -m unittest -v tests.test_bonds
"""
import asyncio
import json
from tests.persona_fixture import prontera_persona
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain.bonds import FRIEND_AFFINITY, MISS_HOURS, TOGETHER_MIN, Bonds
from live_brain.memory import Memory

BRAIN_DIR = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class FakeSocial:
    def __init__(self):
        self.st, self.said, self.next_walk = {"pairs": {"Vera": 123}}, [], None

    async def say(self, peer, topic, step, now=None):
        self.said.append((peer, topic, step))
        return True


class BondsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.sent, self.dec = [], []

        async def execute(actions, source, reason, protocol=False):
            self.sent.extend(actions)

        self.routine = SimpleNamespace(st={"mode": "hunt"}, last_sent=5, save=lambda: None)
        self.social = FakeSocial()
        self.mind = SimpleNamespace(
            mem=self.mem, fresh_state=True, execute=execute, write_decision=self.dec.append,
            persona=prontera_persona(),
            ctx=SimpleNamespace(peers={"Vera"}, last={}), routine=self.routine, social=self.social,
            state={"name": "Arkady", "dead": False, "players": [], "friends": []})
        self.clock = Clock()
        self.b = Bonds(self.mind, clock=self.clock)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def kinds(self):
        return [d.get("event") for d in self.dec]

    def test_meeting_on_hunt_map_hunts_together(self):
        asyncio.run(self.b.after_meeting({"partner": "Vera", "map": "prt_fild07"}))
        self.assertEqual(self.routine.st["prefer_map"], "prt_fild07")
        self.assertEqual(self.routine.last_sent, 0)
        self.assertIn("bonds_hunt_together", self.kinds())

    def test_meeting_in_town_sits_and_talks_then_proof(self):
        self.routine.st["mode"] = "town"
        asyncio.run(self.b.after_meeting({"partner": "Vera", "map": "prontera"}))
        self.assertEqual(self.social.st["pairs"]["Vera"], 0, "поговорить сразу")
        self.assertGreater(self.social.next_walk, self.clock.t + TOGETHER_MIN * 60 - 5)
        self.clock.t += TOGETHER_MIN * 60 + 1
        self.mind.state["players"] = [{"name": "Vera", "x": 150, "y": 180}]
        asyncio.run(self.b.tick())
        self.assertIn("bonds_together_done", self.kinds())
        self.assertEqual(self.mem.relation("Vera")["affinity"], 1)

    def test_friend_request_only_when_close_and_visible(self):
        self.mem.update_relation("Vera", 2)
        self.mind.state["players"] = [{"name": "Vera", "x": 1, "y": 1}]
        asyncio.run(self.b.tick())
        self.assertEqual(self.sent, [], "отношение ниже порога")
        self.mem.update_relation("Vera", FRIEND_AFFINITY - 2)
        asyncio.run(self.b.tick())
        asyncio.run(self.b.tick())
        self.assertEqual(self.sent, [{"action": "friend_request", "to": "Vera"}], "раз в сутки")
        self.mind.state["friends"] = [{"name": "Vera", "online": True}]
        self.clock.t += 86401
        self.sent.clear()
        asyncio.run(self.b.tick())
        self.assertEqual([a for a in self.sent if a["action"] == "friend_request"], [], "уже друзья — по серверу")

    def test_job_change_congrats(self):
        self.mind.state["players"] = [{"name": "Vera", "x": 1, "y": 1, "job": "Acolyte"}]
        asyncio.run(self.b.tick())
        self.mind.state["players"] = [{"name": "Vera", "x": 1, "y": 1, "job": "Priest"}]
        asyncio.run(self.b.tick())
        self.assertIn(("Vera", "congrats", 4), self.social.said)
        self.assertIn("bonds_job_changed", self.kinds())

    def test_miss_friend_once_per_day(self):
        self.mind.state["friends"] = [{"name": "Vera", "online": True}]
        self.mind.ctx.last["talk:Vera"] = self.clock.t - (MISS_HOURS + 1) * 3600
        asyncio.run(self.b.tick())
        asyncio.run(self.b.tick())
        self.assertEqual(self.social.said, [("Vera", "hello", 1)])


if __name__ == "__main__":
    unittest.main()
