"""Копилка мечты и банк (savings.py, ORG-073): отложено на мечту, отказы economy, вехи, банк по правилам.

Настоящий Mind (Arkady, мечта job2 «стать Knight», цель копилки 30 000z из goals.json dream.save) с поддельным
телом; время модуля — подменные часы. Запуск: cd brain && python3 -m unittest -v tests.test_savings
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.dream import Dream
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.safety import BANK_PER_DAY, MAX_BANK_OP, SafetyPolicy
from live_brain.savings import BANK_TIMEOUT, MAX_FAILS, Savings

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
    BANK = False

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
        world["savings"] = dict(world.get("savings") or {}, bank=self.BANK)
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=world)
        self.clock = Clock(time.time())
        self.mind.needs.t = dict(LOW, diligence=0.9)
        self.mind.dream = Dream(self.mind, world, clock=self.clock, rng=random.Random(1))
        self.sv = Savings(self.mind, world, clock=self.clock)
        self.mind.savings = self.sv
        self.state(zeny=12000)
        self.mind.dream.tick()                               # мечта «стать Knight»
        self.assertEqual(self.mind.dream.save_target(), (30000, "стать Knight"))

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 40,
             "job": "Swordman", "job_lv": 30, "dead": False, "weight_pct": 20, "zeny": 12000, "players": [],
             "items": {"501": 30}}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def tick(self, advance=0):
        self.clock.t += advance
        self.sv.next_tick = 0
        asyncio.run(self.sv.tick())

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ? ORDER BY id",
                                                              (kind,))]

    def acts(self, kind):
        return [a for a in self.sent if a.get("action") == kind]


class PiggyTest(Base):
    def test_reserve_and_progress(self):
        self.assertEqual(self.sv.reserve(), 12000)
        self.assertEqual(self.sv.progress()["pct"], 40)
        self.state(zeny=50000)
        self.assertEqual(self.sv.reserve(), 30000)                      # не больше цели
        self.assertEqual(self.sv.summary(), {"копит_на": "стать Knight", "отложено": "30000/30000 (100 %)"})
        self.mind.dream = None
        self.assertEqual(self.sv.reserve(), 0)                          # нет мечты — нет копилки

    def test_economy_keeps_savings(self):
        econ = self.mind.economy
        self.state(zeny=40000)
        self.assertEqual(econ.refuse_reason("Vera", "z", 2000), "коплю на мечту")   # 38 000 < 15 000 + 30 000
        self.state(zeny=34000)
        self.assertEqual(econ.offer_refuse_reason("Vera", "984", 1, 300), "коплю на мечту")   # перепродажа
        self.state(zeny=34000, items={"501": 2})
        self.assertIsNone(econ.offer_refuse_reason("Vera", "501", 5, 30))         # зелий мало — покупаю
        self.mind.savings = None
        self.assertIsNone(econ.offer_refuse_reason("Vera", "984", 1, 300))
        self.state(zeny=40000)
        self.assertIsNone(econ.refuse_reason("Vera", "z", 2000))

    def test_milestones(self):
        self.tick()                                                      # новая цель: пройденное (25 %) — молча
        self.assertEqual(self.events("savings_progress"), [])
        self.state(zeny=16000)
        self.tick()
        self.assertEqual([e["pct"] for e in self.events("savings_progress")], [50])
        self.state(zeny=31000)
        self.tick()
        self.assertEqual([e["pct"] for e in self.events("savings_progress")], [50, 100])
        self.assertTrue(any("Накопил(а) на мечту" in m["text"] for m in self.mem.top_memories(10)))
        self.assertIn("100 %", LINES["savings_progress"](self.events("savings_progress")[-1]))
        self.tick()
        self.assertEqual(len(self.events("savings_progress")), 2)

    def test_bank_off_by_default(self):
        self.state(zeny=90000)
        self.mind.routine.st["mode"] = "town"
        self.tick()
        self.tick(advance=3600)
        self.assertEqual([a for a in self.sent if a["action"].startswith("bank")], [])


class BankTest(Base):
    BANK = True

    def setUp(self):
        super().setUp()
        self.mind.routine.st["mode"] = "town"

    def balance(self, vault):
        asyncio.run(self.mind.on_event({"type": "event", "kind": "bank_balance", "vault": vault}))

    def result(self, op, ok, vault, zeny=0, reason=0):
        asyncio.run(self.mind.on_event({"type": "event", "kind": "bank_result", "op": op, "ok": ok,
                                        "reason": reason, "vault": vault, "zeny": zeny}))

    def test_check_deposit_withdraw(self):
        self.state(zeny=60000)
        self.tick()
        self.assertEqual(self.acts("bank_check"), [{"action": "bank_check"}])
        self.balance(0)
        self.tick(advance=60)
        self.assertEqual(self.acts("bank_deposit"), [], "не чаще bank_gap_minutes")
        self.tick(advance=600)
        dep = self.acts("bank_deposit")
        self.assertEqual(dep[0]["zeny"], 30000)                            # излишек сверх 10 000, до цели
        self.result("deposit", True, 30000, zeny=30000)
        self.assertEqual(self.events("bank_deposit")[0]["zeny"], 30000)
        self.assertEqual(self.sv.bank(), 30000)
        self.state(zeny=30000)
        self.assertEqual(self.sv.reserve(), 0)                             # цель в банке — карман свободен
        self.assertEqual(self.sv.progress()["pct"], 100)
        self.tick(advance=700)
        self.assertEqual(len(self.acts("bank_deposit")), 1, "цель уже в банке — больше не кладу")
        self.state(zeny=3000)                                              # бедность: на зелья
        self.tick(advance=700)
        self.assertEqual(self.acts("bank_withdraw")[0]["zeny"], 7000)
        self.result("withdraw", True, 23000, zeny=10000)
        self.assertEqual(self.events("bank_withdraw")[0]["zeny"], 7000)
        self.assertIn("банк", LINES["bank_deposit"](self.events("bank_deposit")[0]))

    def test_body_busy_waits(self):
        self.state(zeny=60000, map="prt_fild08")
        self.tick()
        self.state(zeny=60000, give={"to": "Vera"})
        self.tick(advance=700)
        self.mind.routine.st["mode"] = "hunt"
        self.state(zeny=60000)
        self.tick(advance=700)
        self.assertEqual(self.sent, [])

    def test_failures_pause(self):
        self.state(zeny=60000)
        self.tick()
        self.balance(0)
        for i in range(MAX_FAILS):
            self.tick(advance=700)
            self.assertEqual(len(self.acts("bank_deposit")), i + 1)
            self.result("deposit", False, 0, reason=1)
        self.tick(advance=700)
        self.assertEqual(len(self.acts("bank_deposit")), MAX_FAILS, "три отказа — пауза на сутки")
        self.assertTrue(any("Банк не отвечает" in m["text"] for m in self.mem.top_memories(10)))
        self.tick(advance=86400)
        self.assertEqual(len(self.acts("bank_deposit")), MAX_FAILS + 1)

    def test_pending_timeout(self):
        self.state(zeny=60000)
        self.tick()
        self.tick(advance=BANK_TIMEOUT + 1)
        self.assertEqual(self.events("bank_failed")[0]["fails"], 1)

    def test_events_are_owned(self):
        self.balance(4242)
        self.assertEqual(self.sv.bank(), 4242)
        self.assertEqual(self.mind.reasons, [])                            # не повод для модели


class BankSafetyTest(unittest.TestCase):
    def test_safety(self):
        s = SafetyPolicy(["prt_fild08"], peers={"Vera"})
        st = {"zeny": 1000}
        self.assertEqual(s.check({"action": "bank_check"}, st)[1], "неизвестное действие")   # модель не может
        self.assertEqual(s.check({"action": "bank_deposit", "zeny": 500}, st, protocol=True),
                         ({"action": "bank_deposit", "zeny": 500}, None))
        self.assertEqual(s.check({"action": "bank_deposit", "zeny": 5000}, st, protocol=True)[1], "в кармане меньше")
        for bad in (0, True, "100", MAX_BANK_OP + 1):
            self.assertIsNotNone(s.check({"action": "bank_withdraw", "zeny": bad}, st, protocol=True)[1])
        self.assertEqual(s.check({"action": "bank_withdraw", "zeny": 9000}, st, protocol=True)[1], None)
        self.assertIsNotNone(s.check({"action": "bank_check"}, {"dead": True}, protocol=True)[1])
        for _ in range(BANK_PER_DAY):
            s.check({"action": "bank_check"}, st, protocol=True)
        self.assertIn("лимит", s.check({"action": "bank_check"}, st, protocol=True)[1])


if __name__ == "__main__":
    unittest.main()
