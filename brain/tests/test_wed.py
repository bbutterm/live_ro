"""Помолвка и свадьба (wed.py, dream.py вид wedding; ORG-062, ТЗ Т-35): ступени по фактам, ритуал, разрыв, мечта.

Два настоящих Mind (Arkady — Male, Vera — Female) с общей шиной мира во временном каталоге; шёпоты передаются
вручную (relay). Часы и кости модуля подменные. Сверка шагов обряда со скриптом — при наличии upstream/rathena.
Запуск: cd brain && python3 -m unittest -v tests.test_wed
"""
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import world_bus
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memoir import chapter
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.wed import CEREMONY, Wed

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT = BRAIN_DIR.parent
RATHENA = Path(os.environ.get("LIVE_RO_RATHENA") or ROOT / "upstream" / "rathena")
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
WORLD["wed"]["min_residents"] = 2          # habit2: механику пары проверяем на двоих (по умолчанию wed спит при < 4)


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class Dice:
    def __init__(self, x=0.0):
        self.x = x

    def random(self):
        return self.x

    def choice(self, seq):
        return seq[0]

    def uniform(self, a, b):
        return a


class Resident:
    def __init__(self, root, bot, name, other, clock, bus_path, world=WORLD, sex="Male"):
        self.name, self.other, self.sex = name, other, sex
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(Path(root) / f"{bot}.sqlite")
        self.bus = world_bus.WorldBus(bus_path, name)
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, Path(root) / f"{bot}.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"} - {name}, world=world, world_bus_db=self.bus)
        m = self.mind
        if m.social:
            m.social.is_night = lambda now: False
        m.director = None
        if m.wed is not None:
            m.wed = Wed(m, world, clock=clock, rng=Dice(0.0))
            m.wed.bond = lambda peer: 25
        m.routine.new_day(clock.t)
        m.routine.st.update(mode="town", arrived=True, rest_until=clock.t + 3600, mode_since=clock.t)
        self.clock = clock
        self._last = {}

    @property
    def w(self):
        return self.mind.wed

    def state(self, **kw):
        other_sex = "Female" if self.sex == "Male" else "Male"
        s = {"type": "state", "name": self.name, "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 40,
             "job": "Swordsman", "sex": self.sex, "dead": False, "weight_pct": 20, "zeny": 20000, "items": {},
             "players": [{"name": self.other, "x": 158, "y": 185, "sex": other_sex, "job": "Acolyte", "lv": 40}]}
        s.update(self._last)
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def hear(self, sender, text):
        asyncio.run(self.mind.on_message({"type": "event", "kind": "chat_private", "from": sender, "text": text}))

    def tick(self, dt=1):
        self.clock.t += dt
        self.w.next_tick = 0
        asyncio.run(self.w.tick())

    def whispers(self, to=None):
        return [a["text"] for a in self.sent if a.get("action") == "whisper" and (to is None or a.get("to") == to)]

    def actions(self, kind):
        return [a for a in self.sent if a.get("action") == kind]

    def events(self, kind):
        return [json.loads(r[0]) for r in self.mem.db.execute("SELECT data FROM events WHERE kind = ?", (kind,))]

    def love(self, peer, n=8):
        for _ in range(n // 2):
            self.mem.update_relation(peer, 2)

    def close(self):
        self.bus.close()
        self.mem.close()


class WedBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.bus_path = Path(self.tmp.name) / "world.sqlite"
        self.a = Resident(self.tmp.name, "bot01", "Arkady", "Vera", self.clock, self.bus_path, sex="Male")
        self.v = Resident(self.tmp.name, "bot02", "Vera", "Arkady", self.clock, self.bus_path, sex="Female")
        self.a.state()
        self.v.state()
        self.a.mind.world.pump()
        self.v.mind.world.pump()

    def tearDown(self):
        self.a.close()
        self.v.close()
        self.tmp.cleanup()

    def relay(self, src, dst):
        for a in [a for a in src.sent if a.get("action") == "whisper" and a.get("to") == dst.name
                  and not a.get("_relayed")]:
            a["_relayed"] = True
            dst.hear(src.name, a["text"])

    def engage(self):
        self.a.love("Vera")
        self.v.love("Arkady")
        self.a.tick()
        self.assertTrue(self.a.whispers("Vera")[-1].endswith("[wed:ask]"))
        self.relay(self.a, self.v)
        self.assertTrue(self.v.whispers("Arkady")[-1].endswith("[wed:yes]"))
        self.relay(self.v, self.a)


class TestStages(WedBase):
    def test_friends_then_close(self):
        self.assertEqual(self.a.w.stage("Vera"), "none")
        self.a.love("Vera", 4)
        self.a.tick()
        self.assertEqual(self.a.w.stage("Vera"), "friends")
        self.a.love("Vera", 4)                                   # 8, вес эпизодов 25
        self.a.tick()
        self.assertEqual(self.a.w.stage("Vera"), "close")
        self.assertEqual([e["stage"] for e in self.a.events("wed_stage")], ["friends", "close"])
        self.a.w.bond = lambda peer: 19                          # общей истории мало (< 20) — только друзья
        self.assertEqual(self.a.w.stage("Vera"), "friends")

    def test_no_ask_without_close(self):
        self.a.love("Vera", 6)
        self.a.tick()
        self.assertFalse(self.a.whispers("Vera"))


class TestEngagement(WedBase):
    def test_ritual(self):
        self.engage()
        self.assertEqual(self.a.w.fiance, "Vera")
        self.assertEqual(self.v.w.fiance, "Arkady")
        self.assertEqual(self.a.events("wed_engaged")[-1]["peer"], "Vera")
        self.assertEqual(self.v.events("wed_accepted")[-1]["peer"], "Arkady")
        self.assertEqual(self.a.mem.relation("Vera")["affinity"], 10)
        # подарок письмом и сердце рядом (следующий такт)
        self.a.tick()
        self.v.tick()
        mail = self.a.actions("mail_send")[-1]
        self.assertEqual((mail["to"], mail["zeny"]), ("Vera", 500))
        self.assertEqual(self.v.actions("mail_send")[-1]["to"], "Arkady")
        self.assertEqual(self.a.actions("emote")[-1]["id"], 3)
        self.a.tick()
        self.assertEqual(len(self.a.actions("mail_send")), 1)    # подарок — один раз
        # шина: помолвка одна (пишет предложивший), летопись, мемуары
        self.a.mind.world.pump()
        self.v.mind.world.pump()
        rows = [e for e in self.a.bus.read() if e["kind"] == "wed_engaged"]
        self.assertEqual([(e["bot"], e["data"]["peer"]) for e in rows], [("Arkady", "Vera")])
        self.assertEqual(LINES["wed_engaged"]({"peer": "Vera"}), "обручился(ась) с Vera")
        ch = chapter([{"ts": self.clock.t, "kind": "wed_engaged", "data": {"peer": "Vera"}}], "Male",
                     self.clock.t - 10, self.clock.t + 10, 1)
        self.assertIn("Обручился с Vera", ch["lines"][0])
        # тема разговора: третьему жителю, не невесте
        self.assertIsNone(self.a.w.facts("Vera", self.clock.t))
        self.assertEqual(self.a.w.facts("Rook", self.clock.t), {"who": "Vera", "_key": "wed_engaged"})
        self.a.w.said("Rook", {"who": "Vera", "_key": "wed_engaged"}, self.clock.t)
        self.assertIsNone(self.a.w.facts("Rook", self.clock.t))
        self.assertEqual(self.a.w.summary()["помолвка с"], "Vera")

    def test_decline_by_heart_and_no_pressure(self):
        self.a.love("Vera")
        self.v.love("Arkady")
        self.v.w.rng = Dice(0.99)
        self.a.tick()
        self.relay(self.a, self.v)
        self.assertTrue(self.v.whispers("Arkady")[-1].endswith("[wed:no:heart]"))
        self.relay(self.v, self.a)
        self.assertIsNone(self.a.w.fiance)
        self.assertEqual(self.a.events("wed_declined")[-1]["why"], "heart")
        self.a.tick(3600 * 13)
        self.assertEqual(len(self.a.whispers("Vera")), 1)       # не чаще ask_gap_days
        self.a.tick(86400 * 15)
        self.assertEqual(len(self.a.whispers("Vera")), 2)
        self.relay(self.a, self.v)
        self.relay(self.v, self.a)
        self.a.tick(86400 * 30)
        self.assertEqual(len(self.a.whispers("Vera")), 2)       # max_asks — больше не настаивает
        self.assertIn("не буду настаивать", self.a.mem.db.execute(
            "SELECT text FROM memories ORDER BY id DESC LIMIT 1").fetchone()[0])

    def test_refuse_when_not_close(self):
        self.a.love("Vera")
        self.v.love("Arkady", 4)                                 # у Vera только дружба
        self.a.tick()
        self.relay(self.a, self.v)
        self.assertTrue(self.v.whispers("Arkady")[-1].endswith("[wed:no:stage]"))

    def test_shy_resident_waits(self):
        self.a.love("Vera")
        self.a.w.rng = Dice(0.99)                                # не решился — следующая попытка через check_hours
        self.a.tick()
        self.assertFalse(self.a.whispers("Vera"))
        self.a.w.rng = Dice(0.0)
        self.a.tick(60)
        self.assertFalse(self.a.whispers("Vera"))
        self.a.tick(3600 * 12)
        self.assertTrue(self.a.whispers("Vera"))

    def test_break_on_quarrel(self):
        self.engage()
        self.a.mind.society.st["quarrel"]["Vera"] = {"since": self.clock.t, "cause": "тест"}
        self.a.tick()
        self.assertTrue(self.a.whispers("Vera")[-1].endswith("[wed:off:quarrel]"))
        self.assertIsNone(self.a.w.fiance)
        self.relay(self.a, self.v)
        self.assertIsNone(self.v.w.fiance)
        self.assertEqual(self.v.events("wed_broken")[-1]["why"], "quarrel")
        self.assertEqual(self.a.events("wed_broken")[-1]["why"], "quarrel")


class TestWeddingDream(WedBase):
    def test_wedding_dream_and_savings(self):
        d = self.a.mind.dream
        d.next_tick = 0
        d.tick()
        first = d.st["kind"]
        self.assertNotEqual(first, "wedding")
        self.engage()
        d.next_tick = 0
        d.tick()                                                 # помолвка: прежняя мечта уступает
        self.assertEqual(self.a.events("dream_changed")[-1]["why"], "помолвка с Vera — теперь общая мечта: свадьба")
        d.next_tick = 0
        d.tick()
        self.assertEqual(d.st["kind"], "wedding")
        self.assertEqual([s["metric"] for s in d.st["stages"]], ["lv", "wealth", "married"])
        self.assertEqual(d.st["stages"][1]["target"], 1300000 + 45000 + 43000)
        self.assertEqual(d.save_target(), (1388000, "свадьба с Vera"))
        self.assertEqual(self.v.mind.wed.cost(), 1200000 + 45000 + 43000)
        d.next_tick = 0
        d.tick()
        self.assertEqual(d.st["kind"], "wedding")                # не сбрасывается сама

    def test_same_sex_no_wedding(self):
        self.engage()
        self.a.mem.set("known_players", {"Vera": {"sex": "Male"}})
        self.assertIsNone(self.a.w.terms())
        self.assertIsNone(self.a.mind.dream.make("wedding", self.a.mind.state))

    def test_break_drops_wedding(self):
        self.engage()
        d = self.a.mind.dream
        d.save({})
        d.next_tick = 0
        d.tick()
        self.assertEqual(d.st["kind"], "wedding")
        self.a.mind.society.st["quarrel"]["Vera"] = {"since": self.clock.t, "cause": "тест"}
        self.a.tick()
        d.next_tick = 0
        d.tick()
        self.assertEqual(self.a.events("dream_changed")[-1]["why"], "помолвки больше нет")

    def test_ready_and_married_by_ring(self):
        self.engage()
        d = self.a.mind.dream
        d.save({})
        self.a.state(lv=46, zeny=1400000)
        self.a.tick()
        self.assertEqual(self.a.events("wed_ready")[-1]["cost"], 1388000)
        self.assertFalse(self.a.events("wed_ready")[-1]["ceremony"])
        self.a.tick()
        self.assertEqual(len(self.a.events("wed_ready")), 1)
        self.a.state(items={"2634": 1})
        self.a.tick()
        self.assertEqual(self.a.w.spouse, "Vera")
        self.assertEqual(self.a.events("wed_married")[-1]["via"], "ring")
        self.assertEqual(d.value("married", self.a.mind.state), 1)

    def test_married_by_announce(self):
        self.engage()
        self.v.mind.rumors.on_world_msg({"text": "I now pronounce you, Arkady and Vera, husband and wife.",
                                         "source": "sys"})
        self.assertEqual(self.v.w.spouse, "Arkady")
        self.assertEqual(self.v.events("wed_married")[-1]["via"], "announce")
        self.assertFalse(self.a.w.on_announce("I now pronounce you, Bob and Ann, husband and wife."))


class TestSwitches(unittest.TestCase):
    def test_disabled(self):
        w = json.loads(json.dumps(WORLD))
        w["wed"]["enabled"] = False
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(Path(tmp) / "m.sqlite")

            async def send(a):
                return 1
            persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
            m = Mind(Settings.from_env({}), persona, mem, send, Path(tmp) / "d.jsonl", RuleGate(),
                     peers={"Vera"}, world=w)
            self.assertIsNone(m.wed)
            m2 = Mind(Settings.from_env({"BRAIN_DISABLE": "wed"}), persona, mem, send, Path(tmp) / "d.jsonl",
                      RuleGate(), peers={"Vera"}, world=WORLD)
            self.assertIsNone(m2.wed)
            self.assertFalse(WORLD["wed"]["ceremony"])
            mem.close()


@unittest.skipUnless((RATHENA / "npc/other/marriage.txt").exists(), "нет upstream/rathena")
class TestScript(unittest.TestCase):
    """Шаги обряда и условия — из npc/other/marriage.txt (сверка строк)."""

    def line(self, ref):
        path, n = ref.rsplit(":", 1)
        return (RATHENA / path).read_text(encoding="utf-8", errors="replace").splitlines()[int(n) - 1]

    def test_ceremony_refs(self):
        self.assertIn("Wedding Staff#w", self.line(CEREMONY["apply"]["src"]))
        self.assertIn("prt_church,97,100", self.line(CEREMONY["apply"]["src"]))
        self.assertIn("Bishop#w", self.line(CEREMONY["bishop"]["src"]))
        self.assertIn("prt_church,100,128", self.line(CEREMONY["bishop"]["src"]))
        self.assertIn("2338", self.line(CEREMONY["outfit"]["src"]))
        self.assertIn("7170", self.line(CEREMONY["outfit"]["src"]))
        self.assertIn("2613", self.line(CEREMONY["ring"]["src"]))
        self.assertIn("getpartymember", self.line(CEREMONY["party"]["src"]))

    def test_terms_match_script(self):
        text = (RATHENA / "npc/other/marriage.txt").read_text(encoding="utf-8", errors="replace")
        self.assertIn("BaseLevel < 45", text)
        self.assertIn("Zeny < 1200000", text)
        self.assertIn("Zeny < 1300000", text)
        self.assertIn("husband and wife.\",bc_map", text)
        self.assertIn("Currently does not support same-Sex marriages", text)
        self.assertEqual(WORLD["wed"]["fee"], {"m": 1300000, "f": 1200000})
        self.assertEqual(WORLD["wed"]["level"], 45)
        plan = Wed.ceremony_plan(type("W", (), {"cfg": WORLD["wed"], "my_sex": lambda s: "m"})(), "f")
        self.assertEqual([s["step"] for s in plan], ["outfit", "ring", "apply", "party", "bishop"])
        self.assertIn("Wedding Dress", plan[0]["what"])


if __name__ == "__main__":
    unittest.main()
