"""Спарринг на арене PvP (spar.py, ORG-061, ТЗ Т-30): согласие, протокол шёпота, итог по событиям тела, арбитр.

Два настоящих Mind (Arkady, Vera) с общей шиной мира во временном каталоге; события тела (spar_step, spar_result)
подаются как от плагина spar, шёпоты передаются между жителями вручную (relay). Часы модуля подменные.
Запуск: cd brain && python3 -m unittest -v tests.test_spar
"""
import asyncio
import json
from tests.persona_fixture import prontera_persona
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import world_bus
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.safety import SafetyPolicy
from live_brain.spar import Spar

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
ON = json.loads(json.dumps(WORLD))
ON["spar"]["enabled"] = True


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


class Resident:
    def __init__(self, root, bot, name, clock, bus_path, world=ON):
        self.name = name
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = prontera_persona(bot)
        persona.pop("sleep", None)
        self.mem = Memory(Path(root) / f"{bot}.sqlite")
        self.bus = world_bus.WorldBus(bus_path, name)
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, Path(root) / f"{bot}.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"} - {name}, world=world, world_bus_db=self.bus)
        m = self.mind
        if m.social:
            m.social.is_night = lambda now: False         # тест не зависит от времени суток на машине
        m.director = None
        if m.spar is not None:
            m.spar = Spar(m, world, clock=clock, rng=Dice(0.0))
        m.routine.new_day(clock.t)
        m.routine.st.update(mode="town", arrived=True, rest_until=clock.t + 3600, mode_since=clock.t)
        self.clock = clock
        self._last = {}

    @property
    def sp(self):
        return self.mind.spar

    def state(self, **kw):
        s = {"type": "state", "name": self.name, "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 35,
             "job": "Swordsman", "dead": False, "weight_pct": 20, "zeny": 5000, "items": {"501": 30, "602": 2},
             "players": []}
        s.update(self._last)
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def hear(self, sender, text):
        asyncio.run(self.mind.on_message({"type": "event", "kind": "chat_private", "from": sender, "text": text}))

    def event(self, kind, **data):
        asyncio.run(self.mind.on_message({"type": "event", "kind": kind, **data}))

    def tick(self, dt=1):
        self.clock.t += dt
        self.sp.next_tick = 0
        asyncio.run(self.sp.tick())

    def whispers(self, to=None):
        return [a["text"] for a in self.sent if a.get("action") == "whisper" and (to is None or a.get("to") == to)]

    def actions(self, kind):
        return [a for a in self.sent if a.get("action") == kind]

    def events(self, kind):
        return [json.loads(r[0]) for r in self.mem.db.execute("SELECT data FROM events WHERE kind = ?", (kind,))]

    def close(self):
        self.bus.close()
        self.mem.close()


class SparBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.bus_path = Path(self.tmp.name) / "world.sqlite"
        self.a = Resident(self.tmp.name, "bot01", "Arkady", self.clock, self.bus_path)
        self.v = Resident(self.tmp.name, "bot02", "Vera", self.clock, self.bus_path)
        self.a.state()
        self.v.state()
        self.a.mind.needs.t["bravery"] = 0.7
        self.v.mind.needs.t["bravery"] = 0.6
        self.a.mind.rivalry.st["rival"] = "Vera"          # соперники недели (ORG-060)
        self.v.mind.rivalry.st["rival"] = "Arkady"
        self.a.mind.world.pump()                          # курсор шины: дальше публикуются новые события
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

    def to_arena(self):
        """ask → yes → spar first → arena → [spar:in:] → spar second → fight у обоих."""
        self.a.tick()
        self.assertTrue(any(t.endswith("[spar:ask]") for t in self.a.whispers("Vera")))
        self.relay(self.a, self.v)
        self.assertTrue(any(t.endswith("[spar:yes]") for t in self.v.whispers("Arkady")))
        self.relay(self.v, self.a)
        self.assertEqual(self.a.actions("spar")[-1]["role"], "first")
        self.a.event("spar_step", phase="arena", room="Prontera", to="Vera")
        self.assertIn("[spar:in:Prontera]", self.a.whispers("Vera")[-1])
        self.relay(self.a, self.v)
        self.assertEqual(self.v.actions("spar")[-1]["role"], "second")
        self.a.event("spar_step", phase="fight", room="Prontera", to="Vera")
        self.v.event("spar_step", phase="fight", room="Prontera", to="Arkady")


class TestProtocol(SparBase):
    def test_full_bout_yield(self):
        self.to_arena()
        a = self.a.actions("spar")[-1]
        self.assertEqual((a["to"], a["room"]), ("Vera", "Prontera"))
        self.assertEqual(self.v.actions("spar")[-1]["room"], "Prontera")
        # пока идёт бой — тело у спарринга: распорядок не двигает
        self.assertFalse(self.a.mind.may_move("routine")[0])
        self.assertTrue(self.a.sp.busy())
        # Vera: HP < 30 % — сдаётся
        self.v.event("spar_result", outcome="yield", reason="HP 27% < 30%", to="Arkady", hp_pct=27, wing="used")
        self.assertTrue(any(t.endswith("[spar:yield]") for t in self.v.whispers("Arkady")))
        self.relay(self.v, self.a)
        self.assertEqual(self.a.actions("spar_stop")[-1]["action"], "spar_stop")
        self.a.event("spar_result", outcome="stopped", reason="Vera сдалась", to="Vera", hp_pct=60, wing="used")
        self.assertIsNone(self.a.sp.cur)
        self.assertIsNone(self.v.sp.cur)
        self.assertEqual(self.a.events("spar_won")[-1]["loser"], "Vera")
        self.assertEqual(self.v.events("spar_bout")[-1]["outcome"], "lost")
        self.assertEqual(self.a.events("spar_bout")[-1]["outcome"], "won")
        self.assertEqual(self.a.sp.st["score"]["Vera"]["w"], 1)
        self.assertEqual(self.v.sp.st["score"]["Arkady"]["l"], 1)
        self.assertEqual(self.a.mem.relation("Vera")["affinity"], 1)            # честный бой сближает
        self.assertTrue(self.a.actions("emote") and self.v.actions("emote"))
        self.assertTrue(any("[chat:spar:4]" in t for t in self.a.whispers("Vera")))
        self.assertTrue(self.a.mind.may_move("routine")[0])
        # шина: победа одна (пишет только победитель), летопись
        self.a.mind.world.pump()
        self.v.mind.world.pump()
        rows = [e for e in self.a.bus.read() if e["kind"] == "spar_won"]
        self.assertEqual([(e["bot"], e["data"]["loser"]) for e in rows], [("Arkady", "Vera")])
        self.assertEqual(LINES["spar_won"]({"loser": "Vera"}), "победил(а) Vera в спарринге на арене")
        self.assertEqual(world_bus.describe("spar_won", {"loser": "Vera"}), "победил(а) Vera в спарринге на арене")
        # соперничество: победы недели в спарринге — метрика
        self.assertEqual(self.a.mind.rivalry.score()["spar"], 1)
        self.assertEqual(self.v.mind.rivalry.score()["spar"], 0)

    def test_opponent_down_is_win(self):
        self.to_arena()
        self.a.event("spar_result", outcome="won", reason="соперник упал", to="Vera", hp_pct=70, wing="used")
        self.v.event("spar_result", outcome="down", reason="упал(а) на арене", to="Arkady", hp_pct=0, wing="none")
        self.assertEqual(self.a.events("spar_bout")[-1]["outcome"], "won")
        self.assertEqual(self.v.events("spar_bout")[-1]["outcome"], "lost")
        self.relay(self.v, self.a)                         # поздний [spar:yield] — без двойного итога
        self.assertEqual(len(self.a.events("spar_bout")), 1)

    def test_draw_only_first_publishes(self):
        self.to_arena()
        self.a.event("spar_result", outcome="draw", reason="лимит боя", to="Vera")
        self.v.event("spar_result", outcome="draw", reason="лимит боя", to="Arkady")
        self.assertEqual(len(self.a.events("spar_draw")), 1)
        self.assertEqual(self.v.events("spar_draw"), [])
        self.assertEqual(self.a.sp.st["score"]["Vera"]["d"], 1)

    def test_stranger_aborts_both(self):
        self.to_arena()
        self.a.event("spar_result", outcome="aborted", reason="stranger: Bob", to="Vera", wing="used")
        self.assertIn("[spar:off:stranger]", self.a.whispers("Vera")[-1])
        self.assertEqual(self.a.events("spar_bout")[-1]["outcome"], "aborted")
        self.relay(self.a, self.v)
        self.assertTrue(self.v.actions("spar_stop"))
        self.v.event("spar_result", outcome="stopped", reason="Arkady отменил", to="Arkady")
        self.assertEqual(self.v.events("spar_bout")[-1]["outcome"], "aborted")
        self.assertEqual(self.v.events("spar_won"), [])
        self.assertNotIn("Arkady", self.v.sp.st["score"])

    def test_gone_then_yield(self):
        self.to_arena()
        self.a.event("spar_result", outcome="gone", reason="соперник пропал с арены", to="Vera")
        self.assertEqual(self.a.sp.cur["phase"], "after")
        self.a.hear("Vera", "Сдаюсь! [spar:yield]")
        self.assertEqual(self.a.events("spar_bout")[-1]["outcome"], "won")

    def test_watchdog(self):
        self.to_arena()
        self.a.tick(dt=21 * 60)
        self.assertTrue(self.a.actions("spar_stop"))
        self.assertIn("[spar:off:timeout]", self.a.whispers("Vera")[-1])
        self.assertEqual(self.a.events("spar_bout")[-1]["outcome"], "aborted")

    def test_max_per_day(self):
        self.to_arena()
        self.a.event("spar_result", outcome="draw", reason="лимит", to="Vera")
        self.a.sp.next_check = 0
        n = len(self.a.whispers("Vera"))
        self.a.tick(dt=3600)
        self.assertEqual(len(self.a.whispers("Vera")), n)
        self.assertEqual(self.a.sp.blocker(self.clock.t), "today")


class TestConsent(SparBase):
    def ask(self):
        self.v.hear("Arkady", "Разомнёмся на арене? [spar:ask]")
        return self.v.whispers("Arkady")[-1]

    def test_quarrel_refuses(self):
        self.v.mind.society.st["quarrel"]["Arkady"] = {"cause": "тест", "since": time.time()}
        self.assertIn("[spar:no:quarrel]", self.ask())
        self.assertIsNone(self.v.sp.cur)

    def test_coward_refuses(self):
        self.v.mind.needs.t["bravery"] = 0.2
        self.assertIn("[spar:no:fear]", self.ask())

    def test_level_wing_hp(self):
        self.v.state(lv=30)
        self.assertIn("[spar:no:level]", self.ask())
        self.v.state(lv=35, items={"501": 30})
        self.assertIn("[spar:no:wing]", self.ask())
        self.v.state(items={"602": 1}, hp_pct=60)
        self.assertIn("[spar:no:hp]", self.ask())
        self.v.state(hp_pct=100, zeny=900)
        self.assertIn("[spar:no:zeny]", self.ask())

    def test_not_friend_nor_rival(self):
        self.v.mind.rivalry.st["rival"] = None
        self.assertIn("[spar:no:who]", self.ask())
        self.a.mind.rivalry.st["rival"] = None
        self.a.tick()
        self.assertEqual(self.a.whispers("Vera"), [])        # звать некого
        for _ in range(2):
            self.a.mem.update_relation("Vera", 2)
        self.a.sp.next_check = 0
        self.a.tick()
        self.assertTrue(self.a.whispers("Vera")[-1].endswith("[spar:ask]"))   # друг (affinity ≥ 3)

    def test_out_of_town_or_dead(self):
        self.v.state(map="prt_fild08")
        self.assertIn("[spar:no:town]", self.ask())
        self.v.state(map="prontera")
        self.v.mem.add_event("died", {"map": "prt_fild08"})
        self.assertIn("[spar:no:hurt]", self.ask())

    def test_peace_after_reconcile(self):
        self.a.mem.add_event("society_reconciled", {"peer": "Vera", "occasion": "подарок"})
        self.a.tick()
        self.assertIn(self.a.whispers("Vera")[-1].split(" [")[0], ["Мир? Тогда разомнёмся на арене!",
                                                                 "Помирились — давай на арену, по-дружески?"])
        self.assertTrue(self.a.sp.cur["peace"])

    def test_declined_and_expired(self):
        self.v.mind.needs.t["bravery"] = 0.2
        self.a.tick()
        self.relay(self.a, self.v)
        self.relay(self.v, self.a)
        self.assertIsNone(self.a.sp.cur)
        self.assertEqual(self.a.events("spar_declined")[-1]["why"], "fear")
        self.assertEqual(self.a.actions("spar"), [])
        # без ответа — истекает
        self.a.sp.next_check = 0
        self.a.sp.st["history"] = []
        self.a.tick(dt=7 * 3600)
        self.assertEqual(self.a.sp.cur["phase"], "asked")
        self.a.tick(dt=200)
        self.assertIsNone(self.a.sp.cur)

    def test_stranger_whisper_ignored(self):
        self.v.hear("Bob", "[spar:ask]")
        self.assertEqual(self.v.whispers("Bob"), [])
        self.assertIsNone(self.v.sp.cur)


class TestSafetyAndDefault(unittest.TestCase):
    def test_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Resident(tmp, "bot01", "Arkady", Clock(), Path(tmp) / "w.sqlite", world=WORLD)
            try:
                self.assertIsNone(r.mind.spar)
                r.hear("Vera", "[spar:ask]")
                self.assertEqual(r.actions("spar"), [])
            finally:
                r.close()
        self.assertFalse(WORLD["spar"]["enabled"])

    def test_safety(self):
        s = SafetyPolicy(["prt_fild08"], peers=["Vera"])
        st = {"lv": 35, "hp_pct": 100}
        ok, why = s.check({"action": "spar", "to": "Vera", "role": "first", "room": "Prontera"}, st, protocol=True)
        self.assertIsNone(why)
        self.assertEqual(ok, {"action": "spar", "to": "Vera", "role": "first", "room": "Prontera"})
        for bad, text in (({"to": "Bob"}, "жител"), ({"room": "Nightmare"}, "комната"), ({"role": "x"}, "роль")):
            a = dict({"action": "spar", "to": "Vera", "role": "first", "room": "Prontera"}, **bad)
            self.assertIn(text, s.check(a, st, protocol=True)[1])
        self.assertIn("уровень", s.check({"action": "spar", "to": "Vera", "role": "first", "room": "Prontera"},
                                         {"lv": 30}, protocol=True)[1])
        self.assertEqual(s.check({"action": "spar", "to": "Vera", "role": "first", "room": "Prontera"}, st)[1],
                         "неизвестное действие")                     # модель (без protocol) не может
        self.assertEqual(s.check({"action": "spar_stop"}, {"dead": True}, protocol=True)[0]["action"], "spar_stop")


if __name__ == "__main__":
    unittest.main()
