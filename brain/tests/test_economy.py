"""Взаимопомощь жителей (economy.py) без сети и процессов: два мозга обмениваются шёпотом.

Запуск: cd brain && python3 -m unittest -v tests.test_economy
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain.economy import Economy
from live_brain.memory import Memory
from live_brain.routine import Routine, load_world
from live_brain.safety import SafetyPolicy

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")


class Clock:
    def __init__(self, t=None):
        self.t = t if t is not None else time.time()   # события памяти пишутся по настоящему времени

    def __call__(self):
        return self.t


class FakeMind:
    def __init__(self, name, peer, mem, state):
        self.mem = mem
        self.state = dict(name=name, map="prontera", x=156, y=185, dead=False, **state)
        self.state["players"] = [{"name": peer, "x": 157, "y": 186}]
        self.ctx = SimpleNamespace(peers={peer})
        self.fresh_state = True
        self.routine = SimpleNamespace(in_town_mode=True)
        self.active_plan = None
        self.plans = SimpleNamespace(store=SimpleNamespace(active=lambda: self.active_plan))
        self.safety = SafetyPolicy(["prt_fild08"], peers={peer})
        self.out, self.decisions = [], []

    async def execute(self, actions, source, reason, protocol=False):
        for a in actions:
            clean, why = self.safety.check(a, self.state, protocol=protocol)
            if why:
                self.economy.on_rejected(a, why)
            else:
                self.out.append(clean)

    def write_decision(self, rec):
        self.decisions.append(rec)


class EconomyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        cfg = WORLD["economy"]
        self.a = FakeMind("Arkady", "Vera", Memory(Path(self.tmp.name) / "a.sqlite"),
                          {"zeny": 30000, "items": {"501": 40, "601": 0}})
        self.v = FakeMind("Vera", "Arkady", Memory(Path(self.tmp.name) / "v.sqlite"),
                          {"zeny": 3000, "items": {"501": 2, "601": 0}})
        for m in (self.a, self.v):
            m.economy = Economy(m, cfg, clock=self.clock)

    def tearDown(self):
        self.a.mem.close()
        self.v.mem.close()
        self.tmp.cleanup()

    def run_(self, coro):
        return asyncio.run(coro)

    def deliver(self, src, dst):
        """Шёпоты src -> dst доходят до мозга dst; остальные действия остаются в src.out."""
        sent = [a for a in src.out if a["action"] == "whisper"]
        src.out = [a for a in src.out if a["action"] != "whisper"]
        for w in sent:
            self.assertLessEqual(len(w["text"]), 100)
            self.run_(dst.economy.on_tag(src.state["name"], w["text"]))
        return sent

    def kinds(self, mind):
        return [r[0] for r in mind.mem.db.execute("SELECT kind FROM events ORDER BY id")]

    def test_full_gift(self):
        self.run_(self.v.economy.tick())                     # Vera: 2 зелья < 5 — просит
        ask = self.deliver(self.v, self.a)
        self.assertEqual(len(ask), 1)
        self.assertIn("[need:", ask[0]["text"])
        self.assertEqual(ask[0]["to"], "Arkady")
        give = [a for a in self.a.out if a["action"] == "give"]
        self.assertEqual(give, [{"action": "give", "to": "Vera", "item": 501, "amount": 15}])
        self.deliver(self.a, self.v)                         # ok доходит до Vera
        self.assertEqual(self.v.economy.req["status"], "accepted")
        # слово «ok» — не доказательство: пока зелий не прибавилось, Vera ждёт
        self.clock.t += 10
        self.run_(self.v.economy.tick())
        self.assertIsNotNone(self.v.economy.req)
        # тело Arkady завершило сделку: сервер подтвердил, у Vera +15
        self.a.economy.on_give_result({"kind": "give_result", "to": "Vera", "item": 501, "amount": 15, "ok": True})
        self.a.state["items"]["501"] = 25
        self.v.state["items"]["501"] = 17
        self.run_(self.v.economy.tick())
        self.assertIsNone(self.v.economy.req)
        self.assertIn("gift_received", self.kinds(self.v))
        self.assertIn("gift_given", self.kinds(self.a))
        self.assertEqual(self.v.mem.relation("Arkady")["affinity"], 1)

    def test_refuse_when_short(self):
        self.a.state["items"]["501"] = 25                    # 25 - 15 < keep 20
        self.run_(self.v.economy.tick())
        self.deliver(self.v, self.a)
        self.assertFalse([a for a in self.a.out if a["action"] == "give"])
        self.deliver(self.a, self.v)
        self.assertIsNone(self.v.economy.req)
        self.assertIn("gift_failed", self.kinds(self.v))
        # повторно не просит раньше ask_gap_minutes
        self.clock.t += 60
        self.run_(self.v.economy.tick())
        self.assertFalse(self.v.out)
        self.clock.t += WORLD["economy"]["ask_gap_minutes"] * 60
        self.run_(self.v.economy.tick())
        self.assertTrue(self.v.out)

    def test_no_ask_on_hunt_or_far_or_plan(self):
        self.v.routine.in_town_mode = False
        self.run_(self.v.economy.tick())
        self.assertFalse(self.v.out)
        self.v.routine.in_town_mode = True
        self.v.state["players"] = [{"name": "Arkady", "x": 190, "y": 185}]
        self.run_(self.v.economy.tick())
        self.assertFalse(self.v.out, "житель далеко — не прошу")
        self.v.state["players"] = [{"name": "Arkady", "x": 157, "y": 186}]
        self.v.active_plan = {"id": "x"}
        self.run_(self.v.economy.tick())
        self.assertFalse(self.v.out, "идёт план встречи — не прошу")

    def test_zeny_need(self):
        self.v.state["items"]["501"] = 30
        self.v.state["zeny"] = 100
        self.run_(self.v.economy.tick())
        self.deliver(self.v, self.a)
        give = [a for a in self.a.out if a["action"] == "give"]
        self.assertEqual(give, [{"action": "give", "to": "Vera", "item": "zeny", "amount": 2000}])

    def test_stranger_and_limits(self):
        self.run_(self.a.economy.on_tag("Stranger", "[need:abcd:501:5]"))
        # mind пропускает только жителей, но и сам Economy не отдаёт чужим: safety отклонит give
        self.assertFalse([a for a in self.a.out if a["action"] == "give"])
        self.assertIsNone(self.a.economy.giving)
        self.a.out.clear()
        self.run_(self.a.economy.on_tag("Vera", "[need:abcd:501:99]"))
        self.assertFalse([a for a in self.a.out if a["action"] == "give"], "больше ask — отказ")
        self.run_(self.a.economy.on_tag("Vera", "[need:abce:4001:1]"))
        self.assertFalse([a for a in self.a.out if a["action"] == "give"], "карты не раздаёт")
        for i in range(WORLD["economy"]["gifts_per_day"]):
            self.a.mem.add_event("gift_given", {"peer": "Vera"})
        self.run_(self.a.economy.on_tag("Vera", "[need:abcf:501:5]"))
        self.assertFalse([a for a in self.a.out if a["action"] == "give"], "дневной лимит")

    def test_give_result_timeout_frees(self):
        self.run_(self.a.economy.on_tag("Vera", "[need:abcd:501:5]"))
        self.assertIsNotNone(self.a.economy.giving)
        self.clock.t += 200
        self.run_(self.a.economy.tick())
        self.assertIsNone(self.a.economy.giving)

    def test_operator_ask(self):
        self.v.state["items"]["501"] = 30                    # нехватки нет, но оператор просит для проверки
        self.assertIsNone(self.run_(self.v.economy.ask("501", 10, force=True)))
        self.deliver(self.v, self.a)
        self.assertEqual([a for a in self.a.out if a["action"] == "give"],
                         [{"action": "give", "to": "Vera", "item": 501, "amount": 10}])
        self.assertEqual(self.run_(self.v.economy.ask("501", 10, force=True)), "уже жду ответа на просьбу")
        self.assertEqual(self.run_(self.a.economy.ask("501; quit", 1, force=True)), "неверный предмет или количество")
        self.a.state["players"] = []
        self.assertEqual(self.run_(self.a.economy.ask("501", 1, force=True)), "рядом нет жителя")

    def test_tag_fits_long_name(self):
        long = "Leonardo_the_Wanderer_X"                    # 23 символа — максимум RO
        self.v.ctx.peers = {long}
        self.v.safety.peers = {long}
        self.v.state["players"] = [{"name": long, "x": 157, "y": 186}]
        self.v.state["zeny"] = 1
        self.v.state["items"]["501"] = 30
        self.run_(self.v.economy.ask("z", 1234567, force=True))
        w = self.v.out[0]
        self.assertLessEqual(len(w["text"]), 100)
        self.assertTrue(w["text"].endswith(f"[need:{self.v.economy.req['id']}:z:1234567]"))

    def test_answer_timeout(self):
        self.run_(self.v.economy.tick())
        self.clock.t += 130
        self.run_(self.v.economy.tick())
        self.assertIsNone(self.v.economy.req)
        self.assertIn("gift_failed", self.kinds(self.v))


class SafetyGiveTest(unittest.TestCase):
    def test_give_rules(self):
        s = SafetyPolicy(["prt_fild08"], peers={"Vera"})
        ok = {"action": "give", "to": "Vera", "item": 501, "amount": 5}
        self.assertEqual(s.check(ok, {})[1], "неизвестное действие", "модель give не получает")
        self.assertIsNone(s.check(ok, {}, protocol=True)[1])
        self.assertIsNone(s.check(dict(ok, item="zeny", amount=1000), {}, protocol=True)[1])
        self.assertIsNotNone(s.check(dict(ok, to="Stranger"), {}, protocol=True)[1])
        self.assertIsNotNone(s.check(dict(ok, item="501; quit"), {}, protocol=True)[1])
        self.assertIsNotNone(s.check(dict(ok, amount=0), {}, protocol=True)[1])
        self.assertIsNotNone(s.check(ok, {"dead": True}, protocol=True)[1])
        self.assertIsNone(s.check({"action": "shop_close"}, {"dead": True}, protocol=True)[1])


class RoutineVendTest(unittest.TestCase):
    def test_open_in_town_close_before_hunt(self):
        tmp = tempfile.TemporaryDirectory()
        mem = Memory(Path(tmp.name) / "m.sqlite")
        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        sent = []
        state = {"name": "Arkady", "map": "prontera", "x": 156, "y": 185, "lock_map": "prontera",
                 "lock_x": 156, "lock_y": 185, "dead": False, "vend": {"can": 1, "open": 0}}

        async def execute(actions, source, reason, protocol=False):
            sent.extend(a["action"] for a in actions)

        mind = SimpleNamespace(persona=persona, mem=mem, state=state, fresh_state=True, execute=execute,
                               write_decision=lambda r: None,
                               plans=SimpleNamespace(store=SimpleNamespace(active=lambda: None)))
        clock = Clock()
        r = Routine(mind, WORLD, rng=random.Random(1), clock=clock)
        r.new_day(clock.t)
        r.st.update(mode="town", rest_until=clock.t + 600, arrived=False)
        asyncio.run(r.tick())
        self.assertIn("shop_open", sent)
        self.assertNotIn("sit", sent, "с лавкой не садится")
        state["vend"]["open"] = 1
        sent.clear()
        clock.t += 700                                      # перерыв закончился — на охоту
        asyncio.run(r.tick())
        self.assertEqual(sent, ["shop_close"], "сначала закрыть лавку, потом идти")
        state["vend"]["open"] = 0
        sent.clear()
        clock.t += 61
        asyncio.run(r.tick())
        self.assertEqual(sent, ["hunt"])
        mem.close()
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
