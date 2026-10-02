"""Жизнь группы (crew.py, ORG-053): карта решается вместе, чат группы, прогулка за лидером.

Запуск: cd brain && python3 -m unittest -v tests.test_crew
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain.crew import CHAT_GAP, PREF_GAP, STREAK_N, WALK_GAP, Crew
from live_brain.memory import Memory
from live_brain.safety import SafetyPolicy

BRAIN_DIR = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class FakeMaps:
    def __init__(self, scores, unsafe=()):
        self.scores, self.unsafe = scores, set(unsafe)

    def score(self, m):
        return self.scores.get(m)

    def safe_for_level(self, maps, level, max_risk=None):
        return [m for m in maps if not (m in self.unsafe and level < 30)] or maps

    def choose(self, maps, bans, level=None, needs=None, rng=None):
        allowed = [m for m in maps if m not in bans]
        return max(allowed, key=lambda m: self.scores.get(m, 0)), "по опыту"


class FakeParty:
    def __init__(self, me, leader, members):
        self.me, self.leader, self._members = me, leader, members
        self.st = {"confirmed": True}

    @property
    def is_leader(self):
        return self.me == self.leader

    def mates(self):
        return {m["name"] for m in self._members}

    def member(self, name):
        return next((m for m in self._members if m["name"] == name), None)


class CrewTest(unittest.TestCase):
    def make(self, me, leader, members, scores=None, unsafe=()):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        mem = Memory(Path(self.tmp.name) / f"{me}.sqlite")
        self.addCleanup(mem.close)
        self.sent = []
        policy = SafetyPolicy(["prt_fild08", "prt_fild01", "gef_fild02"], peers={"Arkady", "Vera"})

        async def execute(actions, source, reason, protocol=False, extra=None):
            for a in actions:
                ok, why = policy.check(a, mind.state, protocol=protocol)
                self.assertIsNotNone(ok, f"safety отклонил {a}: {why}")
                self.sent.append(ok)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona["hunt_maps"] = ["prt_fild08", "prt_fild01", "gef_fild02"]
        mind = SimpleNamespace(
            mem=mem, fresh_state=True, execute=execute, write_decision=lambda d: None, persona=persona,
            ctx=SimpleNamespace(peers={"Arkady", "Vera"} - {me}), needs=SimpleNamespace(t={"sociability": 1.0},
                                                                                       risk_tolerance=lambda: 1.0),
            maps=FakeMaps(scores or {"prt_fild08": 50, "prt_fild01": 40, "gef_fild02": 10}, unsafe),
            routine=SimpleNamespace(st={"mode": "town"}, bans=lambda: set()),
            party=FakeParty(me, leader, members), may_move=lambda owner: (True, None),
            state={"name": me, "lv": 40, "dead": False, "party": "LR_Arkady", "map": "prontera", "x": 150, "y": 150})
        self.clock = Clock()
        self.mind = mind
        return Crew(mind, clock=self.clock, rng=random.Random(1))

    def tick(self, crew, dt=5):
        self.clock.t += dt
        asyncio.run(crew.tick())

    def test_member_sends_preference_to_leader(self):
        c = self.make("Vera", "Arkady", [{"name": "Arkady", "online": True, "map": "prontera", "x": 150, "y": 150}])
        self.tick(c)
        prefs = [a for a in self.sent if a["action"] == "whisper"]
        self.assertEqual(prefs, [{"action": "whisper", "to": "Arkady", "text": "[crew:pref:prt_fild08:40]"}])
        self.tick(c)
        self.assertEqual(len([a for a in self.sent if a["action"] == "whisper"]), 1, f"не чаще {PREF_GAP} с")

    def test_leader_weighs_votes_and_weakest(self):
        c = self.make("Arkady", "Arkady", [{"name": "Vera", "online": True}],
                      scores={"prt_fild08": 50, "prt_fild01": 40, "gef_fild02": 10})
        asyncio.run(c.on_tag("Vera", "[crew:pref:prt_fild01:41]"))
        best, why = c.group_choice("prt_fild08", "по опыту")
        self.assertEqual(best, "prt_fild08", "голоса поровну — решает опыт карты")
        best, why = c.group_choice("prt_fild08", "по опыту")
        self.assertEqual(best, "prt_fild01", "по очереди: в этот раз желание Vera весит вдвое")
        self.assertIn("вместе", why)
        self.assertEqual(c.st["owed"], "Arkady", "теперь очередь лидера")
        # слабый товарищ: карта не по силам — отсекается, даже с голосом
        c2 = self.make("Arkady", "Arkady", [{"name": "Vera", "online": True}],
                       scores={"prt_fild08": 10, "gef_fild02": 50}, unsafe={"gef_fild02"})
        asyncio.run(c2.on_tag("Vera", "[crew:pref:gef_fild02:12]"))
        best, _ = c2.group_choice("gef_fild02", "по опыту")
        self.assertNotEqual(best, "gef_fild02", "слабейшему 12 ур. туда рано")

    def test_no_prefs_keeps_own_choice(self):
        c = self.make("Arkady", "Arkady", [{"name": "Vera", "online": True}])
        self.assertEqual(c.group_choice("prt_fild08", "по опыту"), ("prt_fild08", "по опыту"))

    def test_foreign_tag_ignored(self):
        c = self.make("Arkady", "Arkady", [{"name": "Vera", "online": True}])
        asyncio.run(c.on_tag("Stranger", "[crew:pref:gef_fild02:99]"))
        self.assertEqual(c.st.get("prefs"), {})

    def test_party_chat_arrival_streak_and_limits(self):
        c = self.make("Arkady", "Arkady", [{"name": "Vera", "online": True}])
        self.tick(c)
        self.mind.routine.st["mode"] = "hunt"
        self.mind.state["map"] = "prt_fild08"
        self.tick(c)
        said = [a for a in self.sent if a["action"] == "party_say"]
        self.assertEqual(len(said), 1)
        self.assertIn("prt_fild08", said[0]["text"])
        for _ in range(STREAK_N):
            self.mind.mem.add_event("kill", {})
        self.tick(c)
        self.assertEqual(len([a for a in self.sent if a["action"] == "party_say"]), 1, f"не чаще {CHAT_GAP} с")
        self.tick(c, CHAT_GAP)
        self.assertEqual(len([a for a in self.sent if a["action"] == "party_say"]), 2, "серия побед")

    def test_events_thanks_and_danger(self):
        c = self.make("Arkady", "Arkady", [{"name": "Vera", "online": True}])
        asyncio.run(c.on_event("support", {"skill": "AL_HEAL", "from": "Vera", "to": "Arkady", "amount": 120}))
        self.assertIn("Vera", self.sent[-1]["text"])
        self.clock.t += CHAT_GAP
        asyncio.run(c.on_event("support", {"skill": "AL_HEAL", "from": "Stranger", "to": "Arkady", "amount": 50}))
        self.assertEqual(len(self.sent), 1, "чужому не благодарю в чат группы")
        asyncio.run(c.on_event("danger", {}))
        self.assertEqual(self.sent[-1]["action"], "party_say")

    def test_no_group_no_chat(self):
        c = self.make("Arkady", "Arkady", [{"name": "Vera", "online": True}])
        self.mind.party.st["confirmed"] = False
        asyncio.run(c.on_event("level_up", {"level": 41}))
        self.assertEqual(self.sent, [])

    def test_member_walks_with_leader_in_town(self):
        c = self.make("Vera", "Arkady", [{"name": "Arkady", "online": True, "map": "prontera", "x": 155, "y": 152}])
        self.tick(c)
        follows = [a for a in self.sent if a["action"] == "follow"]
        self.assertEqual(follows, [{"action": "follow", "to": "Arkady"}])
        self.assertTrue(c.walking())
        self.mind.state["follow"] = "Arkady"
        self.tick(c, 9 * 60)
        self.assertEqual(self.sent[-1]["action"], "unfollow", "прогулка окончена")
        self.tick(c)
        self.assertEqual(len([a for a in self.sent if a["action"] == "follow"]), 1, f"не чаще {WALK_GAP} с")

    def test_walk_ends_when_leader_leaves_map(self):
        """review2: лидер ушёл с карты (экспедиция, без меня) — прогулка за ним кончается сразу, а не через
        WALK_MIN минут follow в поле за чужой экспедицией."""
        c = self.make("Vera", "Arkady", [{"name": "Arkady", "online": True, "map": "prontera", "x": 155, "y": 152}])
        self.tick(c)
        self.assertTrue(c.walking())
        self.mind.state["follow"] = "Arkady"
        self.mind.party.member("Arkady").update(map="prt_fild01", x=200, y=200)
        self.tick(c, 30)
        self.assertEqual(self.sent[-1]["action"], "unfollow", "лидер на другой карте — не идти следом")
        self.assertFalse(c.walking())

    def test_walk_ends_when_expedition_starts(self):
        """review2: участник пошёл в экспедицию лидера посреди прогулки — follow снять (иначе lockMap не работает)."""
        c = self.make("Vera", "Arkady", [{"name": "Arkady", "online": True, "map": "prontera", "x": 155, "y": 152}])
        self.tick(c)
        self.mind.state["follow"] = "Arkady"
        self.mind.explorer = SimpleNamespace(busy=lambda: True)
        self.tick(c, 5)
        self.assertEqual(self.sent[-1]["action"], "unfollow")
        self.assertFalse(c.walking())


if __name__ == "__main__":
    unittest.main()
