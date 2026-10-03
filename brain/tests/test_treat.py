"""Угощение по поводу (treat.py, ORG-099, ТЗ Т-45): повод, условия, лимиты, итог treat_given через economy.

Настоящий Mind (Arkady) с поддельным телом; Vera — рядом. Запуск: cd brain && python3 -m unittest -v tests.test_treat
"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import treat as T
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.modules import MODULES
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
VERA = {"name": "Vera", "x": 158, "y": 186}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeCalendar:
    def __init__(self, birthdays=()):
        self.birthdays = list(birthdays)

    def day(self, now=None):
        return {"date": "2026-10-03", "birthdays": self.birthdays}

    def factor(self, need, now=None):
        return 1.0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        world = json.loads(json.dumps(WORLD))
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=world)
        self.mind.dream = None
        self.mind.savings = None
        self.clock = Clock(time.time())
        self.mind.needs.t = dict(self.mind.needs.t, generosity=0.8)
        self.mind.calendar = FakeCalendar()
        self.tr = T.Treat(self.mind, world, clock=self.clock)
        self.mind.treat = self.tr
        self.mind.routine.st["mode"] = "town"
        self.state()

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 40,
             "job": "Swordman", "job_lv": 30, "dead": False, "weight_pct": 20, "zeny": 50000, "players": [VERA],
             "items": {"501": 30, "502": 10}}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def tick(self, advance=0):
        self.clock.t += advance
        self.tr.next_tick = 0
        asyncio.run(self.tr.tick())

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ? ORDER BY id",
                                                              (kind,))]

    def gives(self):
        return [a for a in self.sent if a.get("action") == "give"]


class OccasionTest(Base):
    def test_birthday(self):
        self.mind.calendar.birthdays = ["Vera"]
        self.tick()
        g = self.gives()
        self.assertEqual(len(g), 1)
        self.assertEqual((g[0]["to"], g[0]["item"], g[0]["amount"]), ("Vera", 502, 3))   # 503 нет — Orange
        self.assertTrue(any(a.get("action") == "whisper" and "днём рождения" in a["text"] for a in self.sent))
        self.assertEqual(self.mind.economy.giving["treat"], "birthday")
        self.assertEqual(self.mind.economy.giving["zeny"], 150)                         # 3 × 50z NPC

    def test_reconciled_and_graduated(self):
        self.mem.add_event("society_reconciled", {"peer": "Vera", "occasion": "встреча"})
        self.tick()
        self.assertEqual(len(self.gives()), 1)
        self.tr.st["last"] = 0                                          # без кулдауна — следующий повод
        self.mem.add_event("mentor_graduated", {"peer": "Vera", "mentee": "Vera", "lv": 25})
        self.mind.economy.giving = None
        self.tick()
        self.assertEqual(len(self.gives()), 2)

    def test_same_occasion_once(self):
        self.mind.calendar.birthdays = ["Vera"]
        self.tick()
        self.tr.st["last"] = 0
        self.mind.economy.giving = None
        self.tick()
        self.assertEqual(len(self.gives()), 1)

    def test_no_occasion_far_busy(self):
        self.tick()
        self.assertEqual(self.gives(), [])                              # повода нет
        self.mind.calendar.birthdays = ["Vera"]
        self.state(players=[{"name": "Vera", "x": 190, "y": 185}])
        self.tick()
        self.assertEqual(self.gives(), [])                              # далеко
        self.state()
        self.mind.routine.st["mode"] = "hunt"
        self.tick()
        self.assertEqual(self.gives(), [])                              # не отдых
        self.mind.routine.st["mode"] = "town"
        self.mind.economy.giving = {"id": "x", "to": "Vera", "item": "501", "amount": 1, "since": self.clock()}
        self.tick()
        self.assertEqual(self.gives(), [])                              # уже передаю


class LimitTest(Base):
    def test_cooldown(self):
        self.mind.calendar.birthdays = ["Vera"]
        self.tick()
        self.mind.economy.giving = None
        self.mem.add_event("society_reconciled", {"peer": "Vera", "occasion": "встреча"})
        self.tick(3600)
        self.assertEqual(len(self.gives()), 1)                          # неделя не прошла

    def test_stingy_poor_and_few_potions(self):
        self.mind.calendar.birthdays = ["Vera"]
        self.mind.needs.t["generosity"] = 0.1
        self.tick()
        self.assertEqual(self.gives(), [])                              # скупой
        self.mind.needs.t["generosity"] = 0.8
        self.state(zeny=6000)                                           # 5 % от 1000 сверх keep = 50 < 150
        self.tick()
        self.assertEqual([(g["item"], g["amount"]) for g in self.gives()], [(501, 3)])   # Red Potion по 10z
        self.tr.st.update(last=0, done=[])
        self.mind.economy.giving = None
        self.state(items={"501": 21, "502": 5})                         # 501 держу 20, 502 держу 5
        self.tick()
        self.assertEqual([(g["item"], g["amount"]) for g in self.gives()][1:], [(501, 1)])


class ResultTest(Base):
    def test_treat_given_on_server_ok(self):
        self.mind.calendar.birthdays = ["Vera"]
        self.tick()
        self.mind.economy.on_give_result({"ok": True, "to": "Vera", "item": 502, "amount": 3})
        ev = self.events("treat_given")
        self.assertEqual(ev, [{"peer": "Vera", "item": "502", "amount": 3, "zeny": 150, "occasion": "birthday"}])
        self.assertEqual(len(self.events("gift_given")), 1)
        self.assertIn("день рождения", LINES["treat_given"](ev[0]))

    def test_no_event_on_fail(self):
        self.mind.calendar.birthdays = ["Vera"]
        self.tick()
        self.mind.economy.on_give_result({"ok": False, "to": "Vera", "item": 502, "amount": 3, "reason": "далеко"})
        self.assertEqual(self.events("treat_given"), [])


class SwitchTest(unittest.TestCase):
    def test_registered_and_on(self):
        self.assertIn(T.Treat, MODULES)
        self.assertTrue(WORLD["treat"]["enabled"])


if __name__ == "__main__":
    unittest.main()
