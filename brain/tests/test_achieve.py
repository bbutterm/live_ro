"""Достижения сервера (achieve.py, ORG-080, ТЗ Т-31): список и обновления от моста, объявление нового, тема, соперник.

Настоящие Mind (Arkady, Vera) с общей шиной мира во временном каталоге; события achievement/achievement_list
подаются как от моста (brainBridge.pl, пакеты 0A23/0A24). Генератор справочника сверяется с upstream rAthena
(LIVE_RO_RATHENA или upstream/rathena; нет — проверка пропускается).
Запуск: cd brain && python3 -m unittest -v tests.test_achieve
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import world_bus
from live_brain.achieve import TABLE
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.safety import SafetyPolicy

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT = BRAIN_DIR.parent
RATHENA = Path(os.environ.get("LIVE_RO_RATHENA") or ROOT / "upstream" / "rathena")
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class Resident:
    def __init__(self, root, bot, name, clock, bus_path, world=WORLD, env=None):
        self.name = name
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(Path(root) / f"{bot}.sqlite")
        self.bus = world_bus.WorldBus(bus_path, name)
        self.mind = Mind(Settings.from_env(env or {}), persona, self.mem, send, Path(root) / f"{bot}.jsonl",
                         RuleGate(), peers={"Arkady", "Vera"} - {name}, world=world, world_bus_db=self.bus)
        self.ach = self.mind.achieve
        if self.ach:
            self.ach.clock = clock
        self.clock = clock
        asyncio.run(self.mind.on_message({"type": "state", "name": name, "map": "prontera", "x": 156, "y": 185,
                                          "hp_pct": 100, "lv": 35, "dead": False, "players": []}))
        self.mind.world.pump()                            # курсор шины: дальше публикуются новые события

    def event(self, kind, **data):
        asyncio.run(self.mind.on_message({"type": "event", "kind": kind, **data}))

    def tick(self):
        self.ach.next_tick = 0
        self.ach.next_snap = 0
        asyncio.run(self.ach.tick())

    def events(self, kind):
        return [json.loads(r[0]) for r in self.mem.db.execute("SELECT data FROM events WHERE kind = ?", (kind,))]

    def close(self):
        self.bus.close()
        self.mem.close()


class AchieveBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.bus_path = Path(self.tmp.name) / "world.sqlite"
        self.a = Resident(self.tmp.name, "bot01", "Arkady", self.clock, self.bus_path)
        self.v = Resident(self.tmp.name, "bot02", "Vera", self.clock, self.bus_path)

    def tearDown(self):
        self.a.close()
        self.v.close()
        self.tmp.cleanup()


class TestServerData(AchieveBase):
    def test_first_list_silent_then_new(self):
        self.a.event("achievement_list", points=20, rank=1, done=[[200005, 1700000000, 0], [220005, 1700000100, 1]])
        self.assertEqual(self.a.ach.count(), 2)
        self.assertEqual(self.a.events("achievement_done"), [])         # история не объявляется
        self.assertEqual(self.a.ach.st["unrewarded"], [200005])
        # новое из обновления
        self.a.event("achievement", id=220004, at=1700000500, reward=0, points=30, rank=1, title="X")
        done = self.a.events("achievement_done")
        self.assertEqual(done[-1]["name"], "A competition of popularity")   # название из achievements.json
        self.assertEqual(done[-1]["group"], "Add_Friend")
        self.assertEqual(self.a.ach.st["points"], 30)
        mem = [m["text"] for m in self.a.mem.top_memories(5)]
        self.assertIn("Получил(а) достижение «A competition of popularity» — по данным сервера.", mem)
        # повтор не объявляется; новое из следующего списка (переподключение) — объявляется
        self.a.event("achievement", id=220004, at=1700000500, reward=1, points=30, rank=1)
        self.a.event("achievement_list", points=40, rank=1,
                     done=[[200005, 1, 0], [220005, 1, 1], [220004, 1, 1], [220003, 1700000900, 0]])
        self.assertEqual([d["id"] for d in self.a.events("achievement_done")], [220004, 220003])
        self.assertEqual(self.a.ach.st["unrewarded"], [200005, 220003])
        # шина и летопись
        self.a.mind.world.pump()
        rows = [e for e in self.a.bus.read() if e["kind"] == "achievement"]
        self.assertEqual([e["data"]["name"] for e in rows], ["A competition of popularity", "My friend's friend~"])
        self.assertEqual(world_bus.describe("achievement", {"name": "Let's Party~"}), "получил(а) достижение «Let's Party~»")
        self.assertEqual(LINES["achievement_done"]({"name": "Official Adventurer"}),
                         "получил(а) достижение «Official Adventurer»")

    def test_new_without_list(self):
        """Персонажу без записей сервер не шлёт 0A23 (clif.cpp:21831) — первое обновление всё равно новое."""
        self.a.event("achievement", id=200005, at=1700000000, reward=0, points=10, rank=1, title="Official Adventurer")
        self.assertEqual(self.a.events("achievement_done")[-1]["name"], "Official Adventurer")

    def test_unknown_id_uses_title(self):
        self.a.event("achievement", id=999999, at=1, reward=0, points=1, rank=1, title="Secret")
        self.assertEqual(self.a.events("achievement_done")[-1]["name"], "Secret")
        self.a.event("achievement", id=999998, at=1, reward=0)
        self.assertEqual(self.a.events("achievement_done")[-1]["name"], "№ 999998")

    def test_events_owned(self):
        self.a.event("achievement", id=200005, at=1, reward=0)
        self.a.event("achievement_reward", id=200005, ok=True)
        self.assertEqual(self.a.ach.st["unrewarded"], [])
        self.assertIsNone(self.a.mind.pending)                           # не повод для LLM

    def test_topic_and_rival(self):
        self.a.event("achievement", id=200005, at=1, reward=0)
        f = self.a.ach.facts("Vera", self.clock())
        self.assertEqual(f["ach"], "Official Adventurer")
        self.a.ach.said("Vera", f, self.clock())
        self.assertIsNone(self.a.ach.facts("Vera", self.clock()))       # раз каждому
        social = self.a.mind.social
        self.assertIn("achieve", social.topics)
        self.assertEqual(social.phrase("achieve", f).count("«Official Adventurer»"), 1)
        # соперник: снимки в шине, у меня больше — тема «счёт»
        self.a.mind.rivalry.st["rival"] = "Vera"
        self.v.event("achievement", id=220005, at=1, reward=0)
        self.a.event("achievement", id=220004, at=1, reward=0)
        self.a.ach.st["told"]["Vera"] = [200005, 220004]
        self.a.tick()
        self.v.tick()
        f = self.a.ach.facts("Vera", self.clock())
        self.assertEqual((f["mine"], f["theirs"], f["_key"]), (2, 1, "achieve_lead"))
        self.a.ach.said("Vera", f, self.clock())
        self.assertIsNone(self.a.ach.facts("Vera", self.clock()))       # раз в день
        self.assertEqual(self.a.mind.rivalry.score()["achieve"], 2)
        self.assertEqual(len([e for e in self.a.bus.read() if e["kind"] == "achieve_known"]), 2)   # по снимку на жителя
        quiet = [e for e in world_bus.read_period(self.bus_path, 0, time.time() + 10) if e["kind"] == "achieve_known"]
        self.assertEqual(quiet, [])                                      # снимок тихий: не в летописи

    def test_claim_rewards(self):
        self.a.event("achievement", id=200005, at=1, reward=0)
        self.a.tick()
        self.assertEqual([s for s in self.a.sent if s.get("action") == "achieve_reward"], [])   # по умолчанию нет
        self.a.ach.cfg["claim_rewards"] = True
        self.a.tick()
        self.assertEqual([s["id"] for s in self.a.sent if s.get("action") == "achieve_reward"], [200005])
        self.a.tick()                                                        # не чаще claim_gap_seconds
        self.assertEqual(len([s for s in self.a.sent if s.get("action") == "achieve_reward"]), 1)

    def test_summary(self):
        self.assertIsNone(self.a.ach.summary())
        self.a.event("achievement", id=200005, at=1, reward=0, points=10, rank=1)
        self.assertEqual(self.a.ach.summary(), {"достижений": 1, "очки": 10, "последнее": "Official Adventurer"})


class TestSafetyAndSwitch(unittest.TestCase):
    def test_safety(self):
        s = SafetyPolicy(["prt_fild08"])
        self.assertEqual(s.check({"action": "achieve_reward", "id": 200005}, {}, protocol=True),
                         ({"action": "achieve_reward", "id": 200005}, None))
        self.assertIsNotNone(s.check({"action": "achieve_reward", "id": "1;quit"}, {}, protocol=True)[1])
        self.assertEqual(s.check({"action": "achieve_reward", "id": 1}, {})[1], "неизвестное действие")

    def test_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Resident(tmp, "bot01", "Arkady", Clock(), Path(tmp) / "w.sqlite", env={"BRAIN_DISABLE": "achieve"})
            try:
                self.assertIsNone(r.mind.achieve)
                r.event("achievement", id=200005, at=1, reward=0)
                self.assertEqual(r.events("achievement_done"), [])
                self.assertIsNone(r.mind.pending)                        # вид принадлежит модулю и выключенному
            finally:
                r.close()


class TestGenerator(unittest.TestCase):
    @unittest.skipUnless((RATHENA / "db" / "re" / "achievement_db.yml").exists(), "нет upstream rAthena")
    def test_matches_upstream(self):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_achievements.py"), str(RATHENA)],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), json.loads(TABLE.read_text(encoding="utf-8")))

    def test_table(self):
        data = json.loads(TABLE.read_text(encoding="utf-8"))["achievements"]
        self.assertGreater(len(data), 300)
        self.assertEqual(data["200005"], {"name": "Official Adventurer", "group": "Job_Change", "score": 10})
        self.assertTrue(all(len(v["name"]) <= 60 for v in data.values()))


if __name__ == "__main__":
    unittest.main()
