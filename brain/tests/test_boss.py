"""Мини-босс группой (boss.py, ORG-079): оценка по атласу, согласие по характеру, протокол [boss:...], поход
экспедицией, победа по факту kill, поражение и бан, лимиты, по умолчанию выключено.

Два настоящих Mind (Arkady — лидер группы LR_Arkady, Vera — участник) и общая шина мира во временном каталоге;
шёпоты между ними передаются тестом. Время — поддельные часы. Запуск: cd brain && python3 -m unittest -v tests.test_boss
"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import chronicle, world_bus
from live_brain.boss import TAG, Boss
from live_brain.config import Settings
from live_brain.explore import Explorer
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
ON = json.loads(json.dumps(WORLD))
ON["boss"]["enabled"] = True
CFG = dict(ON["explore"], min_curiosity=0.0)


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class Dice:
    """Предсказуемый «характер»: random() — заданное число, uniform — нижняя граница, choice — первый."""

    def __init__(self, x=0.0):
        self.x = x

    def random(self):
        return self.x

    def uniform(self, lo, hi):
        return lo

    def choice(self, seq):
        return seq[0]


class Resident:
    def __init__(self, root, bot, name, clock, bus_path, world=ON, peers=("Arkady", "Vera")):
        self.name = name
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(Path(root) / f"{bot}.sqlite")
        self.bus = world_bus.WorldBus(bus_path, name)
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, Path(root) / f"{bot}.jsonl", RuleGate(),
                         peers=set(peers), world=world, world_bus_db=self.bus)
        m = self.mind
        if m.social:
            m.social.is_night = lambda now: False         # тест не зависит от времени суток на машине
        m.director = None                                 # режиссёр здесь не участвует (своё в test_director)
        self.ex = Explorer(m, CFG, clock=clock)
        m.explorer = self.ex
        if m.boss is not None:
            self.boss = Boss(m, world, clock=clock, rng=Dice(0.0))
            m.boss = self.boss
        m.routine.new_day(clock.t)
        m.routine.st.update(mode="town", arrived=True, rest_until=clock.t + 3600, mode_since=clock.t)
        m.party.st["confirmed"] = True
        self._last = {}

    def state(self, **kw):
        s = {"type": "state", "name": self.name, "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 30,
             "job": "Swordsman", "dead": False, "weight_pct": 20, "zeny": 60000, "items": {"501": 30},
             "party": "LR_Arkady", "players": []}
        s.update(self._last)
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def hear(self, sender, text):
        asyncio.run(self.mind.on_message({"type": "event", "kind": "chat_private", "from": sender, "text": text}))

    def event(self, kind, **data):
        asyncio.run(self.mind.on_message({"type": "event", "kind": kind, **data}))

    def whispers(self, to=None):
        return [a["text"] for a in self.sent if a.get("action") == "whisper" and (to is None or a.get("to") == to)]

    def events(self, kind):
        return [json.loads(r[0]) for r in self.mem.db.execute("SELECT data FROM events WHERE kind = ?", (kind,))]

    def close(self):
        self.bus.close()
        self.mem.close()


class BossBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.bus_path = Path(self.tmp.name) / "world.sqlite"
        self.a = Resident(self.tmp.name, "bot01", "Arkady", self.clock, self.bus_path)
        self.v = Resident(self.tmp.name, "bot02", "Vera", self.clock, self.bus_path)
        self.a.state(lv=30, party_members=[{"name": "Vera", "online": True, "map": "prontera", "hp_pct": 100}])
        self.v.state(lv=25, party_members=[{"name": "Arkady", "online": True, "map": "prontera", "hp_pct": 100}])
        self.a.mind.needs.t["bravery"] = 0.7
        self.v.mind.needs.t["bravery"] = 0.6

    def tearDown(self):
        self.a.close()
        self.v.close()
        self.tmp.cleanup()

    def tick(self, who, dt=1):
        self.clock.t += dt
        asyncio.run(who.boss.tick())

    def relay(self, src, dst):
        """Передать новые шёпоты src → dst (метки [boss:...])."""
        sent = [a for a in src.sent if a.get("action") == "whisper" and a.get("to") == dst.name and not a.get("_relayed")]
        for a in sent:
            a["_relayed"] = True
            dst.hear(src.name, a["text"])

    def go(self):
        """Полный протокол: ask → yes → go. Возвращает после старта похода у обоих."""
        self.tick(self.a)
        self.assertIn("[boss:ask:Vocal:prt_fild07]", self.a.whispers("Vera"))
        self.relay(self.a, self.v)
        self.assertTrue(any(t.startswith("[boss:yes:25:100:30:0]") for t in self.v.whispers("Arkady")))
        self.relay(self.v, self.a)
        self.tick(self.a)
        self.assertIn("[boss:go:Vocal:prt_fild07]", self.a.whispers("Vera"))
        self.relay(self.a, self.v)


class AssessTest(BossBase):
    def team(self, *specs):
        return [{"name": f"m{i}", "lv": lv, "hp": hp, "pots": pots, "heal": heal}
                for i, (lv, hp, pots, heal) in enumerate(specs)]

    def test_targets_from_atlas(self):
        b = self.a.boss
        vocal, eclipse = (b.target_info(t) for t in b.cfg["targets"])
        self.assertEqual((vocal["level"], vocal["hp"]), (18, 3317), "mob_db.yml: Vocal ур. 18, HP 3317")
        self.assertEqual((eclipse["level"], eclipse["hp"], eclipse["boss"]), (31, 625, True))

    def test_assess_rules(self):
        b, vocal = self.a.boss, self.a.boss.target("Vocal")
        ok, why = b.assess(vocal, "prt_fild07", self.team((30, 100, 30, 0)))
        self.assertFalse(ok)
        self.assertIn("не меньше 2", why, "одиночка не идёт")
        self.assertTrue(b.assess(vocal, "prt_fild07", self.team((30, 100, 30, 0), (25, 100, 12, 0)))[0])
        self.assertIn("сумма уровней", b.assess(vocal, "prt_fild07", self.team((17, 100, 30, 0), (16, 100, 30, 0)))[1])
        self.assertIn("слабейшему", b.assess(vocal, "prt_fild07", self.team((40, 100, 30, 0), (12, 100, 30, 0)))[1])
        self.assertIn("HP", b.assess(vocal, "prt_fild07", self.team((30, 100, 30, 0), (25, 60, 30, 0)))[1])
        self.assertIn("зелий", b.assess(vocal, "prt_fild07", self.team((30, 100, 30, 0), (25, 100, 2, 0)))[1])
        self.assertTrue(b.assess(vocal, "prt_fild07", self.team((30, 100, 30, 0), (25, 100, 2, 1)))[0], "хилер")

    def test_eclipse_risk_without_boss_itself(self):
        b, ecl = self.a.boss, self.a.boss.target("Eclipse")
        info = b.target_info(ecl)
        raw, _ = b.atlas.danger_for("prt_fild02", 30)
        risk, _ = b.map_risk("prt_fild02", 30, info)
        self.assertLess(risk, raw, "вклад самого босса оценивает assess, не риск карты")
        self.assertFalse(b.assess(ecl, "prt_fild02", self.team((30, 100, 30, 0), (30, 100, 30, 0)))[0], "60 < 62")
        self.assertTrue(b.assess(ecl, "prt_fild02", self.team((33, 100, 30, 0), (31, 100, 30, 0)))[0])

    def test_tag(self):
        self.assertEqual(TAG.search("[boss:yes:25:100:30:0]").group(1), "yes")
        self.assertLessEqual(len("[boss:ask:Eclipse:prt_fild02]"), 78)


class ConsentTest(BossBase):
    def test_cautious_refuses(self):
        self.v.mind.needs.t["bravery"] = 0.1
        self.v.hear("Arkady", "[boss:ask:Vocal:prt_fild07]")
        self.assertEqual(self.v.whispers("Arkady"), ["[boss:no:fear]"])

    def test_low_hp_refuses(self):
        self.v.state(hp_pct=50)
        self.v.hear("Arkady", "[boss:ask:Vocal:prt_fild07]")
        self.assertEqual(self.v.whispers("Arkady"), ["[boss:no:hp]"])

    def test_not_leader_ignored(self):
        self.v.hear("Bob", "[boss:ask:Vocal:prt_fild07]")
        self.a.hear("Vera", "[boss:ask:Vocal:prt_fild07]")
        self.assertEqual(self.v.whispers(), [])
        self.assertEqual(self.a.whispers(), [], "лидер не отвечает участнику")

    def test_refusal_means_bypass(self):
        self.v.mind.needs.t["bravery"] = 0.1
        self.tick(self.a)
        self.relay(self.a, self.v)
        self.relay(self.v, self.a)
        self.tick(self.a)
        self.assertFalse(self.a.ex.busy())
        self.assertTrue(self.a.events("boss_declined"))
        self.assertEqual(self.a.events("boss_declined")[0]["refused"], {"Vera": "fear"})


class TripTest(BossBase):
    def test_protocol_starts_trip_for_both(self):
        self.go()
        self.assertTrue(self.a.ex.busy())
        self.assertEqual(self.a.ex.trip["map"], "prt_fild07")
        self.assertEqual(self.a.ex.trip["stay"], 40 * 60, "стоянка покрывает респаун 30–50 мин")
        self.assertNotIn("[explore:trip:prt_fild07]", self.a.whispers(), "лидер похода зовёт только согласившихся")
        self.assertTrue(self.v.ex.busy())
        self.assertEqual(self.v.ex.trip["led_by"], "Arkady")
        self.assertEqual([a["map"] for a in self.a.sent if a["action"] == "explore"], ["prt_fild07"])
        self.assertEqual(self.a.events("boss_start")[0]["team"], ["Arkady", "Vera"])

    def test_victory_once_on_bus(self):
        self.go()
        self.a.state(map="prt_fild07", lock_map="prt_fild07")
        self.v.state(map="prt_fild07", lock_map="prt_fild07")
        self.a.event("kill", monster="Rocker", map="prt_fild07")
        self.assertFalse(self.a.events("boss_killed"), "чужой монстр — не победа")
        self.a.event("kill", monster="Vocal", map="prt_fild07")
        self.v.event("kill", monster="Vocal", map="prt_fild07")
        self.assertEqual(len(self.a.events("boss_killed")), 1)
        self.assertEqual(len(self.v.events("boss_killed")), 1)
        rows = self.a.bus.recent("boss_victory", 0)
        self.assertEqual(len(rows), 1, "одна запись шины на победу группы")
        imp = self.a.bus.db.execute("SELECT importance FROM world_events WHERE kind = 'boss_victory'").fetchone()[0]
        self.assertEqual(imp, 5, "победа над мини-боссом — событие недели")
        self.assertEqual(self.a.mind.collection.st["bosses"]["Vocal"]["n"], 1, "трофей")
        self.assertTrue(self.a.events("trophy_boss"))
        self.assertIn("[boss:done:won]", self.a.whispers("Vera"))
        self.assertIn("[boss:won:Vocal]", self.v.whispers("Arkady"))
        self.assertTrue(any(a.get("action") == "party_say" and "Vocal" in a["text"] for a in self.a.sent))
        self.assertFalse(self.a.ex.busy(), "после победы — домой")
        self.assertIn("победили Vocal", chronicle.LINES["boss_killed"](self.a.events("boss_killed")[0]))
        texts = [world_bus.describe(e["kind"], e["data"]) for e in world_bus.read_period(self.bus_path, 0, self.clock.t + 10)]
        self.assertTrue(any("победа группой над Vocal" in t for t in texts))
        self.tick(self.a)
        self.assertIsNone(self.a.boss.st.get("hunt"))
        self.assertFalse(self.a.events("boss_missed"))

    def test_member_victory_reported_by_tag(self):
        self.go()
        self.v.state(map="prt_fild07", lock_map="prt_fild07")
        self.v.event("kill", monster="Vocal", map="prt_fild07")
        self.relay(self.v, self.a)
        self.assertEqual(self.a.events("boss_killed")[0]["by"], "Vera")
        self.assertEqual(len(self.a.bus.recent("boss_victory", 0)), 1)

    def test_death_bans_boss(self):
        self.go()
        self.a.state(map="prt_fild07", lock_map="prt_fild07")
        self.a.event("died", map="prt_fild07")
        self.clock.t += 1
        asyncio.run(self.a.ex.tick())                     # экспедиция видит смерть — возврат
        self.assertFalse(self.a.ex.busy())
        self.tick(self.a)
        self.assertTrue(self.a.events("boss_failed"))
        self.assertTrue(self.a.boss.banned("Vocal", self.clock.t))
        self.assertIn("[boss:done:back]", self.a.whispers("Vera"))
        self.relay(self.a, self.v)
        self.assertFalse(self.v.ex.busy(), "лидер увёл группу — участник тоже назад")
        self.assertTrue(any(r["kind"] == "boss_failed" for r in world_bus.read_period(self.bus_path, 0, self.clock.t + 1)))
        self.clock.t += 3 * 3600
        self.assertFalse(self.a.boss.banned("Vocal", self.clock.t), "бан на fail_ban_hours")

    def test_missed_and_daily_limit(self):
        self.go()
        asyncio.run(self.a.ex.finish(self.clock.t, ok=True, why="осмотрелся"))
        self.tick(self.a)
        self.assertEqual(self.a.events("boss_missed")[0]["why"], "осмотрелся")
        n = len(self.a.whispers("Vera"))
        self.a.state(map="prontera")
        self.a.boss.next_check = 0
        self.tick(self.a, dt=7 * 3600)
        self.assertEqual(len([t for t in self.a.whispers("Vera")[n:] if t.startswith("[boss:ask")]), 0,
                         "max_per_day = 1")

    def test_alone_no_ask(self):
        self.a.state(party_members=[{"name": "Vera", "online": False, "map": "prontera"}])
        self.tick(self.a)
        self.assertEqual(self.a.whispers(), [])


class SwitchTest(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(WORLD["boss"]["enabled"], "мини-босс опасен — по умолчанию выключено")
        with tempfile.TemporaryDirectory() as tmp:
            clock = Clock()
            r = Resident(tmp, "bot01", "Arkady", clock, Path(tmp) / "w.sqlite", world=WORLD)
            try:
                self.assertIsNone(r.mind.boss)
            finally:
                r.close()
            r = Resident(tmp, "bot01", "Arkady", clock, Path(tmp) / "w.sqlite")
            try:
                self.assertIsInstance(r.mind.boss, Boss)
            finally:
                r.close()


if __name__ == "__main__":
    unittest.main()
