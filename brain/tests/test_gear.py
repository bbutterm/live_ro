"""Снаряжение (gear.py, ORG-097, ТЗ Т-44): бюджет сверх keep и копилки, совет «что купить», надеть из рюкзака.

Настоящий Mind (Arkady, Swordman, мечта job2 — копилка 30 000z) с поддельным телом; время — подменные часы.
Запуск: cd brain && python3 -m unittest -v tests.test_gear
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import gear as G
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.dream import Dream
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.modules import MODULES
from live_brain.routine import load_world
from live_brain.safety import SafetyPolicy
from live_brain.savings import Savings

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
LOW = dict.fromkeys(("bravery", "sociability", "greed", "curiosity", "diligence", "generosity", "patience",
                     "whimsy"), 0.1)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


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
        self.world = json.loads(json.dumps(WORLD))
        self.world["gear"] = dict(self.world.get("gear") or {}, enabled=True)
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=self.world)
        self.clock = Clock(time.time())
        self.mind.needs.t = dict(LOW, diligence=0.9)
        self.mind.dream = Dream(self.mind, self.world, clock=self.clock, rng=random.Random(1))
        self.mind.savings = Savings(self.mind, self.world, clock=self.clock)
        self.gear = G.Gear(self.mind, self.world, clock=self.clock)
        self.mind.gear = self.gear
        self.state(zeny=12000)
        self.mind.dream.tick()                               # мечта «стать Knight», копилка 30 000
        self.assertEqual(self.mind.dream.save_target(), (30000, "стать Knight"))
        self.mind.routine.st["mode"] = "town"

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 40,
             "job": "Swordman", "job_lv": 30, "dead": False, "weight_pct": 20, "zeny": 60000, "players": [],
             "items": {"501": 30}, "equip": {"weapon": 1107, "Armor": 2301}, "equip_bag": []}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def tick(self, advance=0):
        self.clock.t += advance
        self.gear.next_tick = 0
        self.gear.last_pick = 0
        asyncio.run(self.gear.tick())

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ? ORDER BY id",
                                                              (kind,))]

    def acts(self, kind):
        return [a for a in self.sent if a.get("action") == kind]


class BudgetTest(Base):
    def test_budget_keeps_reserve_and_savings(self):
        self.state(zeny=60000)
        # 60 000 − keep 5 000 − копилка 30 000 = 25 000; жадность 0.1 → / 1.05
        self.assertEqual(self.gear.budget(), int(25000 / 1.05))
        self.state(zeny=34000)
        self.assertEqual(self.gear.budget(), 0)                          # ниже keep + копилки — ничего
        self.assertIsNone(self.gear.pick())

    def test_greedy_waits_longer(self):
        self.state(zeny=60000)
        modest = self.gear.budget()
        self.mind.needs.t["greed"] = 0.9
        self.assertLess(self.gear.budget(), modest)

    def test_pick_upgrade_within_budget(self):
        self.state(zeny=60000)
        p = self.gear.pick()
        self.assertEqual((p["name"], p["slot"], p["price"], p["gain"]), ("Scimitar", "weapon", 17000, 32))
        self.assertEqual(p["shop"]["map"], "prt_in")

    def test_no_equip_no_guess(self):
        self.state(zeny=60000, equip=None)
        self.assertIsNone(self.gear.pick())
        self.tick()
        self.assertEqual(self.events("gear_wish"), [])


class AdviceTest(Base):
    def test_wish_once_per_item(self):
        self.state(zeny=60000)
        self.tick()
        self.tick(400)
        ev = self.events("gear_wish")
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["name"], ev[0]["price"], ev[0]["shop"]), ("Scimitar", 17000, "prt_in Weapon Dealer"))
        self.assertIn("Scimitar", self.gear.summary())
        self.assertIn("Scimitar", LINES["gear_wish"](ev[0]))
        self.assertEqual(self.acts("equip"), [])                        # совет ничего не покупает
        self.state(zeny=60000, equip={"weapon": 1113, "Armor": 2301})   # надел Scimitar — дальше броня
        self.tick(400)
        self.assertEqual([e["name"] for e in self.events("gear_wish")], ["Scimitar", "Coat"])

    def test_poor_no_summary(self):
        self.state(zeny=30000)
        self.tick()
        self.assertIsNone(self.gear.summary())


class WearTest(Base):
    def test_wear_better_from_bag(self):
        self.state(equip_bag=[1104, 1113])                   # Falchion хуже Blade, Scimitar лучше
        self.tick()
        self.assertEqual([a["item"] for a in self.acts("equip")], [1113])
        self.assertEqual(self.events("gear_worn"), [])      # ack — не факт
        self.state(equip={"weapon": 1113, "Armor": 2301}, equip_bag=[1104, 1107])
        self.tick(5)
        self.assertEqual([e["name"] for e in self.events("gear_worn")], ["Scimitar"])
        self.tick(700)
        self.assertEqual(len(self.acts("equip")), 1)        # хуже надетого — не надевает

    def test_wrong_job_or_level(self):
        self.state(lv=10, equip_bag=[1113])                  # Scimitar с 14 уровня
        self.tick()
        self.assertEqual(self.acts("equip"), [])
        self.state(lv=40, equip_bag=[1601])                  # Rod — не меч мечника
        self.tick(700)
        self.assertEqual(self.acts("equip"), [])

    def test_timeout_and_busy(self):
        self.state(equip_bag=[1113])
        self.mind.routine.st["mode"] = "hunt"
        self.tick()
        self.assertEqual(self.acts("equip"), [])             # вне отдыха — нет
        self.mind.routine.st["mode"] = "town"
        self.tick(700)
        self.assertEqual(len(self.acts("equip")), 1)
        self.tick(G.WEAR_TIMEOUT + 1)
        self.assertEqual(len(self.events("gear_wear_failed")), 1)


class SwitchTest(unittest.TestCase):
    def test_off_by_default_and_registered(self):
        self.assertIn(G.Gear, MODULES)
        self.assertFalse(WORLD["gear"]["enabled"])
        self.assertFalse(G.Gear.ENABLED)

    def test_safety_equip(self):
        sp = SafetyPolicy(["prt_fild08"])
        self.assertEqual(sp.check({"action": "equip", "item": 1113}, {"map": "prontera"}, protocol=True)[0],
                         {"action": "equip", "item": 1113})
        self.assertIsNone(sp.check({"action": "equip", "item": "1113; quit"}, {}, protocol=True)[0])
        self.assertIsNone(sp.check({"action": "equip", "item": 1113}, {"dead": True}, protocol=True)[0])
        self.assertIsNone(sp.check({"action": "equip", "item": 1113}, {"map": "prontera"})[0])   # модель — нет


if __name__ == "__main__":
    unittest.main()
