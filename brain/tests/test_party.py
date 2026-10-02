"""Группа жителей (party.py, AUT-055–061, 066) без сети: два мозга и их состояния.

Запуск: cd brain && python3 -m unittest -v tests.test_party
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain.memory import Memory
from live_brain.party import WAIT_MAX, Party
from live_brain.routine import Routine, load_world
from live_brain.safety import SafetyPolicy

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class FakeMind:
    def __init__(self, bot, peer, mem, clock):
        self.persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        name = self.persona["name"]
        self.mem = mem
        self.state = {"name": name, "map": "prt_fild08", "x": 100, "y": 100, "hp_pct": 100, "dead": False,
                      "lock_map": "prt_fild08", "lock_x": None, "lock_y": None, "party": None, "party_members": []}
        self.ctx = SimpleNamespace(peers={peer}, name=name)
        self.fresh_state = True
        self.plans = SimpleNamespace(store=SimpleNamespace(active=lambda: None))
        self.safety = SafetyPolicy(self.persona["hunt_maps"], peers={peer}, extra_point_maps=["prontera"])
        self.out, self.decisions = [], []
        self.routine = Routine(self, WORLD, rng=random.Random(1), clock=clock)
        self.party = Party(self, {}, clock=clock)

    async def execute(self, actions, source, reason, protocol=False):
        for a in actions:
            clean, why = self.safety.check(a, self.state, protocol=protocol)
            if not why:
                self.out.append(clean)
                k = clean["action"]
                if k == "follow":
                    self.state["follow"] = clean["to"]
                elif k == "unfollow":
                    self.state["follow"] = None
                elif k == "hunt":
                    self.state.update(lock_map=clean["map"], lock_x=None, lock_y=None)
                elif k == "meet_point":
                    self.state.update(lock_map=clean["map"], lock_x=clean["x"], lock_y=clean["y"])

    def write_decision(self, rec):
        self.decisions.append(rec)

    def actions(self, kind=None):
        return [a for a in self.out if kind is None or a["action"] == kind]


class PartyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.a = FakeMind("bot01", "Vera", Memory(Path(self.tmp.name) / "a.sqlite"), self.clock)
        self.v = FakeMind("bot02", "Arkady", Memory(Path(self.tmp.name) / "v.sqlite"), self.clock)

    def tearDown(self):
        self.a.mem.close()
        self.v.mem.close()
        self.tmp.cleanup()

    def tick(self, *minds):
        for m in minds:                                          # как в mind.run: распорядок, затем группа
            asyncio.run(m.routine.tick())
            asyncio.run(m.party.tick())

    def deliver(self, src, dst):
        for w in [a for a in src.out if a["action"] == "whisper"]:
            asyncio.run(dst.party.on_tag(src.state["name"], w["text"]))
        src.out = [a for a in src.out if a["action"] != "whisper"]

    def form(self, vera_map="prt_fild08", vx=101, vy=101, online=True, hp=100):
        self.a.state.update(party="LR_Arkady", party_members=[
            {"name": "Vera", "online": online, "hp_pct": hp, "map": vera_map, "x": vx, "y": vy}])
        self.v.state.update(party="LR_Arkady", party_members=[
            {"name": "Arkady", "online": True, "hp_pct": 100, "map": "prt_fild08", "x": 100, "y": 100, "leader": True}])

    def test_roles_and_formation(self):
        self.assertTrue(self.a.party.is_leader)
        self.assertFalse(self.v.party.is_leader)
        self.tick(self.a, self.v)
        self.assertEqual(self.a.actions("party_create"), [{"action": "party_create", "name": "LR_Arkady"}])
        self.assertEqual(self.v.actions(), [], "участник сам группу не создаёт")
        self.a.state["party"] = "LR_Arkady"                     # сервер создал группу, Vera ещё не в ней
        self.clock.t += 1
        self.tick(self.a)
        self.assertEqual(self.a.actions("party_invite"), [{"action": "party_invite", "to": "Vera"}])
        self.tick(self.a)
        self.assertEqual(len(self.a.actions("party_invite")), 1, "не спамит приглашениями")
        self.assertFalse(self.a.party.st.get("confirmed"), "без состава от сервера группы нет")

    def test_confirmed_by_server_and_mode_sync(self):
        self.form()
        self.tick(self.a, self.v)
        self.assertIn("party_confirmed", [r[0] for r in self.a.mem.db.execute("SELECT kind FROM events")])
        self.assertTrue(self.v.party.st["confirmed"])
        self.deliver(self.a, self.v)                             # лидер объявил режим
        self.assertEqual(self.v.party.leader_wants(), ("hunt", "prt_fild08"))
        self.tick(self.v)
        self.assertEqual(self.v.actions("follow"), [{"action": "follow", "to": "Arkady"}])

    def test_member_follows_leader_to_town_and_back(self):
        self.form()
        self.tick(self.a, self.v)
        asyncio.run(self.a.routine.force("rest"))               # лидер ушёл отдыхать
        self.tick(self.a)
        self.deliver(self.a, self.v)
        self.assertEqual(self.v.party.leader_wants()[0], "town")
        asyncio.run(self.v.routine.tick())
        self.assertEqual(self.v.routine.st["mode"], "town")
        self.assertEqual(self.v.state["lock_map"], "prontera")
        asyncio.run(self.a.routine.force("hunt"))
        self.clock.t += 1
        self.tick(self.a)
        self.deliver(self.a, self.v)
        self.v.state["hp_pct"] = 40                               # участнику плохо — не идёт, просит подождать
        asyncio.run(self.v.routine.tick())
        self.assertEqual(self.v.routine.st["mode"], "town")
        self.assertIn("[party:recover:]", [a["text"] for a in self.v.actions("whisper")])
        self.deliver(self.v, self.a)
        self.assertEqual(self.a.routine.st["mode"], "town", "темп по слабому: лидер тоже отдыхает")
        self.v.state["hp_pct"] = 95
        asyncio.run(self.a.routine.force("hunt"))
        self.clock.t += 1
        self.tick(self.a)
        self.deliver(self.a, self.v)
        asyncio.run(self.v.routine.tick())
        self.assertEqual(self.v.routine.st["mode"], "hunt")

    def test_leash_wait_and_timeout(self):
        self.form(vera_map="prontera")
        self.tick(self.a)
        self.assertEqual(self.a.actions("pause"), [{"action": "pause"}], "участник на другой карте — жду")
        self.form(vx=103, vy=102)
        self.tick(self.a)
        self.assertEqual(self.a.actions("resume"), [{"action": "resume"}], "подошёл — продолжаю")
        self.a.out.clear()
        self.form(vx=140, vy=100)
        self.tick(self.a)
        self.clock.t += WAIT_MAX + 1
        self.tick(self.a)
        self.assertEqual([a["action"] for a in self.a.out if a["action"] in ("pause", "resume")], ["pause", "resume"])
        self.assertIn("party_wait_timeout", [r[0] for r in self.a.mem.db.execute("SELECT kind FROM events")])

    def test_offline_or_dead_member_not_awaited(self):
        self.form(vera_map="prontera", online=False)
        self.tick(self.a)
        self.assertEqual(self.a.actions("pause"), [])
        self.form(vera_map="prontera", hp=0)
        self.tick(self.a)
        self.assertEqual(self.a.actions("pause"), [])

    def test_danger_brings_leader(self):
        self.form(vx=110, vy=100)
        self.tick(self.a, self.v)
        self.a.out.clear()
        asyncio.run(self.v.party.on_danger({"kind": "danger"}))
        asyncio.run(self.v.party.on_danger({"kind": "danger"}))
        self.assertEqual([a["text"] for a in self.v.actions("whisper") if "danger" in a["text"]], ["[party:danger:]"],
                         "срочный сигнал — один раз за SIGNAL_GAP")
        self.deliver(self.v, self.a)
        self.assertEqual(self.a.actions("follow"), [{"action": "follow", "to": "Vera"}])

    def test_heal_confirmed_by_server_packet(self):
        self.a.party.on_support({"kind": "support", "skill": "AL_HEAL", "from": "Vera", "to": "Arkady", "amount": 120})
        self.a.party.on_support({"kind": "support", "skill": "AL_HEAL", "from": "Vera", "to": "Arkady", "amount": 80})
        heals = [json.loads(r[0]) for r in self.a.mem.db.execute("SELECT data FROM events WHERE kind='heal_confirmed'")]
        self.assertEqual([h["amount"] for h in heals], [120, 80])
        mems = [m["text"] for m in self.a.mem.top_memories(20) if "Heal" in m["text"]]
        self.assertEqual(len(mems), 1, "воспоминание не чаще раза в час")
        self.assertEqual(self.a.mem.relation("Vera")["affinity"], 1)
        self.a.party.on_support({"kind": "support", "skill": "AL_HEAL", "from": "Arkady", "to": "Arkady", "amount": 50})
        self.assertEqual(len(heals), 2)

    def test_death_signal_no_false_promise(self):
        self.form(vera_map="prontera")
        self.tick(self.a, self.v)
        self.assertEqual(self.a.actions("pause"), [{"action": "pause"}])
        asyncio.run(self.v.party.on_my_death({"kind": "died", "map": "prt_fild08"}))
        self.deliver(self.v, self.a)
        self.assertEqual(self.a.actions("resume"), [{"action": "resume"}], "погибшего не ждут")
        note = [d for d in self.a.decisions if d.get("event") == "party_member_dead"][0]
        self.assertFalse(note["can_resurrect"])
        self.assertIn("Воскресить не могу", note["text"])

    def test_stranger_tags_ignored(self):
        asyncio.run(self.v.party.on_tag("Stranger", "[party:town:]"))
        self.assertIsNone(self.v.party.st.get("leader_mode"))


if __name__ == "__main__":
    unittest.main()
