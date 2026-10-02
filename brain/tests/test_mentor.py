"""Наставничество новичков (mentor.py, ORG-057): кто новичок, предложение, советы по фактам, подарок, охота, выпуск.

Настоящие Mind (Arkady 41 — наставник, синтетический новичок Bram, Vera) с общей шиной мира во временном каталоге и
поддельным телом; часы модуля подменные. Шёпоты между жителями передаются вручную (тело их только записывает).
Запуск: cd brain && python3 -m unittest -v tests.test_mentor
"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import atlas, world_bus
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
PEERS = {"Arkady", "Vera", "Bram"}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Resident:
    def __init__(self, root, bot, name, bus_path, clock, env=None):
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        persona.pop("sleep", None)
        persona["name"] = name
        self.name = name
        self.mem = Memory(root / f"{name}.sqlite")
        self.bus = world_bus.WorldBus(bus_path, name, clock=clock) if bus_path else None
        self.mind = Mind(Settings.from_env(env or {}), persona, self.mem, send, root / f"{name}.jsonl", RuleGate(),
                         peers=PEERS - {name}, world=WORLD, world_bus_db=self.bus)
        self.clock = clock
        if self.mind.world:
            self.mind.world.pump()
        if self.mind.economy:
            self.mind.economy.clock = clock
        self.m = self.mind.mentor
        if self.m:
            self.m.clock = clock
            self.m.born_ts = None
        self.mind.routine.st.update(mode="town", arrived=True)

    def state(self, lv=41, job="Swordsman", job_lv=30, items=None, others=(), **kw):
        s = {"type": "state", "name": self.name, "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": lv,
             "job": job, "job_lv": job_lv, "dead": False, "weight_pct": 10, "zeny": 60000,
             "items": dict({"501": 30}, **(items or {})),
             "players": [dict({"name": n, "x": 158, "y": 185}, **(o if isinstance(o, dict) else {}))
                         for n, o in ((o, {}) if isinstance(o, str) else (o["name"], o) for o in others)]}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def tick(self):
        self.m.next_tick = 0
        asyncio.run(self.m.tick())

    def hear(self, sender, text):
        asyncio.run(self.mind.on_event({"kind": "chat_private", "from": sender, "text": text}))

    def whispers(self, to=None, tag="[mentor:"):
        return [a["text"] for a in self.sent if a.get("action") == "whisper" and (to is None or a["to"] == to)
                and tag in a["text"]]

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ? ORDER BY id",
                                                              (kind,))]

    def close(self):
        self.mem.close()
        if self.bus:
            self.bus.close()


class MentorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock(time.time())
        self.bus_path = self.root / "shared" / "world.sqlite"
        self.a = Resident(self.root, "bot01", "Arkady", self.bus_path, self.clock)
        self.b = Resident(self.root, "bot02", "Bram", self.bus_path, self.clock)
        self.a.state()
        self.b.state(lv=5, job="Novice", job_lv=3, items={"501": 2})

    def tearDown(self):
        self.a.close()
        self.b.close()
        self.tmp.cleanup()

    def pair(self):
        self.b.tick()                                     # снимок mentee_seek
        self.a.tick()                                     # предложение
        offer = self.a.whispers("Bram")[-1]
        self.b.hear("Arkady", offer)
        for text in self.b.whispers("Arkady"):            # [mentor:ok], затем [mentor:lv:...]
            self.a.hear("Bram", text)
        self.assertEqual(self.a.m.role, "mentor")
        self.assertEqual(self.b.m.role, "mentee")

    # ---------- кто новичок ----------

    def test_sleeps_without_newbies(self):
        v = Resident(self.root, "bot02", "Vera", self.bus_path, self.clock)
        try:
            v.state(lv=30, job="Acolyte", job_lv=30)
            self.b.state(lv=41, job="Swordsman", job_lv=40)      # «Bram» не новичок — в мире только опытные
            for r in (self.a, self.b, v):
                r.tick()
            self.assertEqual(self.a.whispers() + self.b.whispers() + v.whispers(), [])
            self.assertEqual([e for e in self.a.bus.read() if e["kind"] == "mentee_seek"], [])
            self.assertIsNone(self.a.m.summary())
        finally:
            v.close()

    def test_newbie_by_born_and_level(self):
        m = self.b.m
        self.assertTrue(m.newbie())                                   # 5 ур.
        self.b.state(lv=22, job="Swordsman", job_lv=10)
        self.assertFalse(m.newbie())                                  # 22 ур., born неизвестен
        m.born_ts = self.clock() - 2 * 86400
        self.assertTrue(m.newbie())                                   # родился 2 дня назад
        m.born_ts = self.clock() - 30 * 86400
        self.assertFalse(m.newbie())
        m.born_ts = self.clock() - 86400
        self.b.state(lv=25, job="Swordsman", job_lv=10)
        self.assertFalse(m.newbie())                                  # выпускной уровень
        self.assertFalse(self.a.m.newbie())                           # Arkady 41

    def test_seek_snapshot_quiet(self):
        self.b.tick()
        seek = [e for e in self.a.bus.read() if e["kind"] == "mentee_seek"]
        self.assertEqual(len(seek), 1)
        self.assertEqual(seek[0]["data"]["lv"], 5)
        self.assertEqual(world_bus.read_period(self.bus_path, 0, self.clock() + 10), [])   # не в летопись

    # ---------- предложение ----------

    def test_offer_and_accept(self):
        self.pair()
        self.assertIn("[mentor:offer]", self.a.whispers("Bram")[0])
        self.assertEqual(self.a.mem.get("mentor")["peer"], "Bram")
        self.assertEqual(self.b.mem.get("mentor")["peer"], "Arkady")
        self.assertEqual(self.a.m.st["lv"], 5)
        self.assertEqual(self.a.m.st["job"], "Novice")
        self.assertEqual(self.a.mem.relation("Bram")["affinity"], 1)
        self.assertEqual(self.b.mem.relation("Arkady")["affinity"], 1)
        self.assertEqual(self.a.events("mentor_start")[0]["mentee"], "Bram")
        self.assertEqual(self.b.events("mentor_found")[0]["mentor"], "Arkady")
        self.assertEqual(LINES["mentor_start"](self.a.events("mentor_start")[0]), "взял(а) под крыло Bram")
        self.a.mind.world.pump()
        kinds = {e["kind"]: e for e in self.a.bus.read()}
        self.assertEqual(kinds["mentor_start"]["importance"], 3)
        self.assertNotIn("mentee_seek", kinds)                        # ученик больше не ищет
        self.assertEqual(self.a.m.summary()["с_кем"], "Bram")

    def test_no_offer_without_gap_generosity_or_in_quarrel(self):
        self.b.tick()
        self.a.state(lv=18, job="Swordsman", job_lv=20)
        self.a.tick()
        self.assertEqual(self.a.whispers(), [])                       # 18 < 5 + 15
        self.a.state()
        self.a.mind.needs.t["generosity"] = 0.2
        self.a.tick()
        self.assertEqual(self.a.whispers(), [])
        self.a.mind.needs.t["generosity"] = 0.9
        self.a.mind.society.st["quarrel"]["Bram"] = {"cause": "тест", "since": self.clock()}
        self.a.tick()
        self.assertEqual(self.a.whispers(), [])
        del self.a.mind.society.st["quarrel"]["Bram"]
        self.a.tick()
        self.assertEqual(len(self.a.whispers("Bram")), 1)
        self.a.m.st["pending"] = None
        self.a.tick()
        self.assertEqual(len(self.a.whispers("Bram")), 1)             # одному — не чаще offer_gap_hours

    def test_visible_newbie_without_bus(self):
        (self.root / "nobus").mkdir()
        a = Resident(self.root / "nobus", "bot01", "Arkady", None, self.clock)
        try:
            self.assertIsNone(a.mind.world)
            a.state(others=[{"name": "Bram", "lv": 6}])
            a.tick()
            self.assertEqual(len(a.whispers("Bram")), 1)
        finally:
            a.close()

    def test_mentee_declines_when_not_newbie(self):
        self.b.state(lv=30, job="Swordsman", job_lv=30)
        self.b.hear("Arkady", "Bram, давай помогу освоиться? [mentor:offer]")
        self.assertIn("[mentor:no]", self.b.whispers("Arkady")[0])
        self.assertIsNone(self.b.m.role)

    # ---------- советы ----------

    def test_tips_from_facts(self):
        self.pair()
        for _ in range(5):
            self.a.m.st["last_tip"] = 0
            self.a.tick()
        texts = self.a.whispers("Bram", "[mentor:tip:")
        best = atlas.default().suitable_maps(5, "melee", top=1)[0]["map"]
        self.assertIn(f"Начни с {best}", texts[0])
        self.assertTrue(any("Tool Dealer" in t and "[mentor:tip:potion]" in t for t in texts))
        self.assertTrue(any("Kafra" in t for t in texts))
        self.assertEqual(len(texts), 3)                               # Novice без цели в реестре — без совета о пути
        for t in texts:
            self.assertLessEqual(len(t), 78)
        self.b.hear("Arkady", texts[0])
        self.assertEqual(self.b.events("mentor_tip")[0]["topic"], "hunt")
        self.assertIn(f"Arkady советует: Начни с {best}", str(self.b.mem.top_memories(20)))
        # новый уровень ученика (следующая ступень) и профессия Swordsman — новый совет об охоте и о пути
        self.a.hear("Bram", "[mentor:lv:11:40:Swordsman]")
        for _ in range(3):
            self.a.m.st["last_tip"] = 0
            self.a.tick()
        more = self.a.whispers("Bram", "[mentor:tip:")[3:]
        self.assertTrue(any("[mentor:tip:hunt]" in t for t in more))
        self.assertTrue(any("Chivalry Captain" in t and "Knight" in t for t in more))

    # ---------- подарок ----------

    def test_gift_through_economy(self):
        self.pair()
        self.a.m.st["tips"] = {k: m for k, m, _ in self.a.m.tips()}  # советы уже даны
        self.a.state(items={"501": 50})
        self.a.tick()
        gift = self.a.whispers("Bram", "[mentor:gift:")
        self.assertEqual(len(gift), 1)
        self.assertIn("[mentor:gift:501:15]", gift[0])
        self.b.state(lv=5, job="Novice", job_lv=3, items={"501": 2}, others=["Arkady"])
        self.b.hear("Arkady", gift[0])
        need = self.b.whispers("Arkady", "[need:")
        self.assertEqual(len(need), 1)
        self.assertTrue(need[0].endswith(":501:15]"))
        self.assertEqual(self.b.mind.economy.req["peer"], "Arkady")
        self.a.tick()
        self.assertEqual(len(self.a.whispers("Bram", "[mentor:gift:")), 1)   # раз в gift_gap_hours

    def test_gift_skipped_when_enough(self):
        self.pair()
        self.b.state(lv=5, job="Novice", job_lv=3, items={"501": 30}, others=["Arkady"])
        self.b.hear("Arkady", "Зелья нужны? Поделюсь. [mentor:gift:501:15]")
        self.assertEqual(self.b.whispers("Arkady", "[need:"), [])

    def test_no_gift_when_mentor_short(self):
        self.pair()
        self.a.m.st["tips"] = {k: m for k, m, _ in self.a.m.tips()}
        self.a.tick()                                                # 30 < keep 20 + ask 15
        self.assertEqual(self.a.whispers("Bram", "[mentor:gift:"), [])

    # ---------- охота, вехи, выпуск ----------

    def test_joint_hunt(self):
        self.pair()
        self.a.m.st["tips"] = {k: m for k, m, _ in self.a.m.tips()}
        self.a.state(map="prt_fild08", party="LR_Arkady",
                     party_members=[{"name": "Bram", "map": "prt_fild08", "online": True}])
        self.a.mind.party.st["confirmed"] = True
        self.a.mind.routine.st["mode"] = "hunt"
        self.a.tick()
        self.a.tick()
        hunts = self.a.events("mentor_hunt")
        self.assertEqual(len(hunts), 1)                               # раз в сутки
        self.assertEqual(hunts[0]["map"], "prt_fild08")

    def test_milestones_and_graduation(self):
        self.pair()
        self.a.hear("Bram", "[mentor:lv:10:9:Novice]")
        self.a.hear("Bram", "[mentor:lv:10:10:Novice]")
        cheers = self.a.whispers("Bram", "[mentor:cheer]")
        self.assertEqual(len(cheers), 1)
        self.a.hear("Bram", "[mentor:lv:11:1:Swordsman]")
        self.assertEqual(len(self.a.whispers("Bram", "[mentor:cheer]")), 2)   # смена профессии
        self.b.hear("Arkady", self.a.whispers("Bram", "[mentor:cheer]")[-1])
        self.assertEqual(len(self.b.events("mentor_cheer")), 1)
        self.a.hear("Bram", "[mentor:lv:25:20:Swordsman]")
        grad = self.a.whispers("Bram", "[mentor:grad]")
        self.assertEqual(len(grad), 1)
        self.assertIsNone(self.a.m.role)
        self.assertEqual(self.a.events("mentor_graduated")[0]["mentee"], "Bram")
        self.assertEqual(self.a.mem.relation("Bram")["affinity"], 3)
        self.b.hear("Arkady", grad[0])
        self.assertIsNone(self.b.m.role)
        self.assertEqual(self.b.events("mentor_done")[0]["peer"], "Arkady")
        self.a.mind.world.pump()
        g = [e for e in self.a.bus.read() if e["kind"] == "mentor_graduated"]
        self.assertEqual(g[0]["importance"], 4)
        self.assertIn("выпуск ученика Bram", world_bus.describe("mentor_graduated", g[0]["data"]))

    def test_term_end(self):
        self.pair()
        self.clock.t += 11 * 86400
        self.a.state()
        self.a.tick()
        self.assertEqual(len(self.a.whispers("Bram", "[mentor:end]")), 1)
        self.assertEqual(self.a.events("mentor_end")[0]["why"], "срок")
        self.b.hear("Arkady", self.a.whispers("Bram", "[mentor:end]")[0])
        self.assertIsNone(self.b.m.role)

    def test_mentee_reports_new_level(self):
        self.pair()
        n = len(self.b.whispers("Arkady", "[mentor:lv:"))
        self.b.tick()
        self.assertEqual(len(self.b.whispers("Arkady", "[mentor:lv:")), n)    # без изменений — молчит
        self.b.state(lv=6, job="Novice", job_lv=4)
        self.b.tick()
        self.assertIn("[mentor:lv:6:4:Novice]", self.b.whispers("Arkady", "[mentor:lv:")[-1])

    def test_disabled(self):
        r = Resident(self.root, "bot01", "Arkady2", None, self.clock, env={"BRAIN_DISABLE": "mentor"})
        try:
            self.assertIsNone(r.mind.mentor)
        finally:
            r.close()


if __name__ == "__main__":
    unittest.main()
