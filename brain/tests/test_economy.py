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

from live_brain.economy import Economy, economy_metrics, metrics_from_rows
from live_brain.memory import Memory
from live_brain.routine import Routine, load_world
from live_brain.safety import SafetyPolicy
from tests.worldtime import shift_time

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
        self.assertIsNotNone(self.v.economy.req, "рост запаса без сделки (могла быть покупка у NPC) — не подарок")
        self.v.economy.on_deal_complete({"kind": "deal_complete", "with": "Arkady"})
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


class MarketTest(unittest.TestCase):
    """ORG-033: предложение лута жителю до NPC, двусторонняя сделка, честность при недоплате."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        cfg = WORLD["economy"]
        self.a = FakeMind("Arkady", "Vera", Memory(Path(self.tmp.name) / "a.sqlite"),
                          {"zeny": 30000, "items": {"501": 40, "601": 0, "4001": 1}})
        self.v = FakeMind("Vera", "Arkady", Memory(Path(self.tmp.name) / "v.sqlite"),
                          {"zeny": 20000, "items": {"501": 30, "601": 0}})
        for m in (self.a, self.v):
            m.economy = Economy(m, cfg, clock=self.clock)
        self.v.economy.market["wish"] = ["4001"]            # Vera ищет Poring Card (список желаний)

    def tearDown(self):
        self.a.mem.close()
        self.v.mem.close()
        self.tmp.cleanup()

    run_ = EconomyTest.run_
    deliver = EconomyTest.deliver
    kinds = EconomyTest.kinds

    def acts(self, mind, kind):
        return [a for a in mind.out if a["action"] == kind]

    def test_sell_to_resident(self):
        asyncio.run(self.a.economy.tick())
        offer = self.deliver(self.a, self.v)
        self.assertEqual(len(offer), 1)
        self.assertRegex(offer[0]["text"], r"\[offer:[a-z0-9]+:4001:1:1250\]$", "карта 1000 × (1 + 0.5 × 0.5)")
        self.assertEqual(self.acts(self.v, "offer_buy"),
                         [{"action": "offer_buy", "from": "Arkady", "item": 4001, "amount": 1, "price": 1250}])
        self.deliver(self.v, self.a)
        self.assertEqual(self.acts(self.a, "offer_sell"),
                         [{"action": "offer_sell", "to": "Vera", "item": 4001, "amount": 1, "price": 1250}])
        # тела: сделка завершена сервером, у продавца оплата в окне сделки, у покупателя товар
        self.a.economy.on_give_result({"to": "Vera", "item": 4001, "amount": 1, "ok": True, "price": 1250, "paid": 1250})
        self.assertIn("trade_sold", self.kinds(self.a))
        self.assertIsNone(self.a.economy.offer)
        self.v.economy.on_buy_result({"from": "Arkady", "item": 4001, "amount": 1, "price": 1250, "ok": True})
        asyncio.run(self.v.economy.tick())
        self.assertNotIn("trade_bought", self.kinds(self.v), "без роста рюкзака — ещё не «купил»")
        self.v.state["items"]["4001"] = 1
        asyncio.run(self.v.economy.tick())
        self.assertIn("trade_bought", self.kinds(self.v))
        self.assertEqual(self.v.mem.relation("Arkady")["affinity"], 1)
        m = economy_metrics(self.a.mem, 0)
        self.assertEqual((m["сделок с жителями"], m["продал жителям, z"]), (1, 1250))

    def test_refuse_not_needed_and_no_repeat(self):
        self.v.economy.market["wish"] = []
        asyncio.run(self.a.economy.tick())
        self.deliver(self.a, self.v)
        self.assertFalse(self.acts(self.v, "offer_buy"), "не нужно и перепродать NPC невыгодно")
        self.deliver(self.v, self.a)
        self.assertIn("offer_refused", self.kinds(self.a))
        self.clock.t += 31 * 60
        asyncio.run(self.a.economy.tick())
        self.assertFalse(self.a.out, "отказанный лот не предлагаю снова decline_hours")

    def test_refuse_without_reserve(self):
        self.v.state["zeny"] = 6000                          # 6000 - 1250 < keep_zeny 5000
        asyncio.run(self.a.economy.tick())
        self.deliver(self.a, self.v)
        self.assertFalse(self.acts(self.v, "offer_buy"))
        self.assertIn("offer_declined", self.kinds(self.v))

    def test_no_offer_on_hunt_or_alone(self):
        self.a.routine.in_town_mode = False
        asyncio.run(self.a.economy.tick())
        self.assertFalse(self.a.out)
        self.a.routine.in_town_mode = True
        self.a.state["players"] = []
        asyncio.run(self.a.economy.tick())
        self.assertFalse(self.a.out, "рядом нет жителя — сдаст NPC как обычно")

    def test_underpaid_is_debt(self):
        asyncio.run(self.a.economy.tick())
        self.deliver(self.a, self.v)
        self.deliver(self.v, self.a)
        self.a.economy.on_give_result({"to": "Vera", "item": 4001, "amount": 1, "ok": True, "price": 1250, "paid": 1000})
        self.assertEqual(self.a.mem.get("market_debts"), {"Vera": 250})
        self.assertEqual(self.a.mem.relation("Vera")["affinity"], -1)
        self.assertIn("trade_debt", self.kinds(self.a))

    def test_timeouts_free(self):
        asyncio.run(self.a.economy.tick())
        self.clock.t += 130
        asyncio.run(self.a.economy.tick())
        self.assertIsNone(self.a.economy.offer)
        self.run_(self.v.economy.on_tag("Arkady", "[offer:abcd:4001:1:1250]"))
        self.assertIsNotNone(self.v.economy.buying)
        self.clock.t += 400
        asyncio.run(self.v.economy.tick())
        self.assertIsNone(self.v.economy.buying)
        self.assertIn("trade_failed", self.kinds(self.v))

    def test_buyer_buy_result_failed(self):
        self.run_(self.v.economy.on_tag("Arkady", "[offer:abcd:4001:1:1250]"))
        self.v.economy.on_buy_result({"from": "Arkady", "ok": False, "reason": "продавец положил 0 из 1"})
        self.assertIsNone(self.v.economy.buying)
        self.assertEqual(self.v.mem.relation("Arkady"), None, "неудача сделки отношения не меняет")


class MailTest(unittest.TestCase):
    """ORG-035: подарок почтой жителю не рядом, забрать вложение, итог недели."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        cfg = WORLD["economy"]
        self.a = FakeMind("Arkady", "Vera", Memory(Path(self.tmp.name) / "a.sqlite"),
                          {"zeny": 30000, "items": {"501": 40, "601": 0}})
        self.v = FakeMind("Vera", "Arkady", Memory(Path(self.tmp.name) / "v.sqlite"),
                          {"zeny": 3000, "items": {"501": 2, "601": 0}})
        for m in (self.a, self.v):
            m.state["players"] = []                          # не видят друг друга
            m.economy = Economy(m, cfg, clock=self.clock)

    def tearDown(self):
        self.a.mem.close()
        self.v.mem.close()
        self.tmp.cleanup()

    deliver = EconomyTest.deliver
    kinds = EconomyTest.kinds
    run_ = EconomyTest.run_

    def test_gift_by_mail(self):
        self.run_(self.v.economy.tick())
        ask = self.deliver(self.v, self.a)
        self.assertEqual(len(ask), 1, "рядом никого — просит друга издалека")
        mail = [a for a in self.a.out if a["action"] == "mail_send"]
        self.assertEqual(len(mail), 1)
        self.assertEqual({k: mail[0][k] for k in ("to", "item", "amount", "zeny")},
                         {"to": "Vera", "item": 501, "amount": 15, "zeny": 0})
        self.assertFalse([a for a in self.a.out if a["action"] == "give"])
        self.deliver(self.a, self.v)
        self.assertEqual(self.v.economy.req["status"], "accepted")
        self.a.economy.on_mail_result({"to": "Vera", "title": "Подарок", "item": 501, "amount": 15, "zeny": 0,
                                       "ok": True})
        self.assertIn("gift_given", self.kinds(self.a))
        self.assertIn("mail_sent", self.kinds(self.a))
        self.run_(self.v.economy.on_mail_received({"from": "Arkady", "mail_id": 7, "title": "Подарок", "attach": "i"}))
        self.assertEqual([a for a in self.v.out if a["action"] == "mail_take"], [{"action": "mail_take", "mail_id": 7}])
        self.v.state["items"]["501"] = 17
        self.run_(self.v.economy.tick())
        self.assertIsNotNone(self.v.economy.req, "рост запаса без забранного письма — не подарок")
        self.v.economy.on_mail_taken({"from": "Arkady", "mail_id": 7, "ok": True, "zeny": 0,
                                      "items": [{"id": 501, "amount": 15}]})
        self.run_(self.v.economy.tick())
        self.assertIsNone(self.v.economy.req)
        self.assertIn("gift_received", self.kinds(self.v))
        m = economy_metrics(self.v.mem, 0)
        self.assertEqual((m["подарков получил"], m["писем получил"]), (1, 1))

    def test_remote_ask_rare_and_not_when_visible(self):
        self.v.state["players"] = [{"name": "Arkady", "x": 190, "y": 185}]
        self.run_(self.v.economy.tick())
        self.assertFalse(self.v.out, "житель виден, хоть и далеко — почтой не прошу")
        self.v.state["players"] = []
        self.run_(self.v.economy.tick())
        self.assertTrue(self.v.out)
        self.v.out.clear()
        self.v.economy.req = None
        self.clock.t += WORLD["economy"]["ask_gap_minutes"] * 60 + 1
        self.run_(self.v.economy.tick())
        self.assertFalse(self.v.out, "почтой — не чаще remote_gap_minutes")

    def test_mail_from_stranger_ignored(self):
        self.run_(self.v.economy.on_mail_received({"from": "Stranger", "mail_id": 9, "attach": "z"}))
        self.assertFalse(self.v.out)

    def test_week_summary(self):
        self.a.mem.update_relation("Vera", 2)
        self.a.mem.update_relation("Vera", 2)
        self.run_(self.a.economy.tick())                     # первый запуск: только отсчёт недели
        self.assertFalse(self.a.out)
        self.a.mem.set("econ_week_mail", self.clock.t - 8 * 86400)
        self.run_(self.a.economy.tick())
        mail = [a for a in self.a.out if a["action"] == "mail_send"]
        self.assertEqual(len(mail), 1)
        self.assertEqual((mail[0]["to"], mail[0]["title"]), ("Vera", "Итог недели"))
        self.assertLessEqual(len(mail[0]["body"]), 200)

    def test_week_summary_needs_friend(self):
        self.a.mem.set("econ_week_mail", self.clock.t - 8 * 86400)
        self.run_(self.a.economy.tick())
        self.assertFalse([a for a in self.a.out if a["action"] == "mail_send"], "нет друга — письма нет")


class ShopTest(unittest.TestCase):
    """ORG-034: товары лавки жителя-Merchant из prices.json (спящая функция без Merchant в мире)."""

    def test_offer_shop(self):
        tmp = tempfile.TemporaryDirectory()
        clock = Clock()
        a = FakeMind("Arkady", "Vera", Memory(Path(tmp.name) / "a.sqlite"),
                     {"zeny": 30000, "items": {"501": 40, "601": 0, "984": 3},
                      "vend": {"can": 1, "open": 0, "overcharge": 5, "cart": {"4001": 1}, "slots": 5}})
        a.state["players"] = []
        a.economy = Economy(a, WORLD["economy"], clock=clock)
        asyncio.run(a.economy.tick())
        shop = [x for x in a.out if x["action"] == "offer_shop"]
        self.assertEqual(len(shop), 1)
        self.assertEqual([i["id"] for i in shop[0]["items"]], [984, 4001])
        self.assertEqual(shop[0]["items"][0]["amount"], 3)
        a.out.clear()
        clock.t += 60
        asyncio.run(a.economy.tick())
        self.assertFalse(a.out, "не чаще shop_hours")
        a.state["vend"]["can"] = 0
        a.mem.set("econ_shop_ts", 0)
        asyncio.run(a.economy.tick())
        self.assertFalse(a.out, "без навыка лавки — ничего")
        a.mem.close()
        tmp.cleanup()


class MetricsTest(unittest.TestCase):
    def test_metrics(self):
        rows = [("trade_sold", {"price": 1000, "paid": 1000}), ("trade_bought", '{"price": 300}'),
                ("gift_given", {"item": "z", "amount": 2000}), ("gift_received", {"item": "501", "got": 15}),
                ("npc_sold", {"zeny": 3000}), ("vend_sold", {"zeny": 0}), ("mail_sent", {}),
                ("trade_debt", {"price": 500, "paid": 200})]
        m = metrics_from_rows(rows, {"zeny": 12345})
        self.assertEqual(m["зени"], 12345)
        self.assertEqual(m["сделок с жителями"], 2)
        self.assertEqual(m["оборот между жителями, z"], 1000 + 300 + 2000)
        self.assertEqual(m["доля продаж жителям"], 0.25)
        self.assertEqual(m["долги жителей, z"], 300)
        self.assertIsNone(metrics_from_rows([])["доля продаж жителям"])


class SafetyMarketTest(unittest.TestCase):
    def test_market_rules(self):
        s = SafetyPolicy(["prt_fild08"], peers={"Vera"})
        sell = {"action": "offer_sell", "to": "Vera", "item": 4001, "amount": 1, "price": 1250}
        self.assertEqual(s.check(sell, {})[1], "неизвестное действие", "модель торговлю не получает")
        self.assertEqual(s.check(sell, {}, protocol=True), (sell, None))
        self.assertIsNotNone(s.check(dict(sell, to="Stranger"), {}, protocol=True)[1])
        self.assertIsNotNone(s.check(dict(sell, price=0), {}, protocol=True)[1])
        self.assertIsNotNone(s.check(dict(sell, item="4001; quit"), {}, protocol=True)[1])
        self.assertIsNotNone(s.check(sell, {"dead": True}, protocol=True)[1])
        buy = {"action": "offer_buy", "from": "Vera", "item": 4001, "amount": 1, "price": 1250}
        self.assertIsNone(s.check(buy, {"zeny": 2000}, protocol=True)[1])
        self.assertIsNotNone(s.check(buy, {"zeny": 1000}, protocol=True)[1], "не хватает зени")
        mail = {"action": "mail_send", "to": "Vera", "title": "Подарок", "body": "Держи", "zeny": 100}
        self.assertIsNone(s.check(mail, {}, protocol=True)[1])
        self.assertIsNotNone(s.check(dict(mail, title="Хай"), {}, protocol=True)[1], "заголовок короче 4")
        self.assertIsNotNone(s.check(dict(mail, body="x" * 201), {}, protocol=True)[1])
        self.assertIsNotNone(s.check(dict(mail, to="Stranger"), {}, protocol=True)[1])
        self.assertIsNotNone(s.check(dict(mail, item=501), {}, protocol=True)[1], "предмет без количества")
        for _ in range(4):
            s.check(mail, {}, protocol=True)
        self.assertIn("лимит писем", s.check(mail, {}, protocol=True)[1])
        self.assertEqual(s.check({"action": "mail_take", "mail_id": 7}, {}, protocol=True)[0],
                         {"action": "mail_take", "mail_id": 7})
        self.assertIsNotNone(s.check({"action": "mail_take", "mail_id": "7"}, {}, protocol=True)[1])
        shop = {"action": "offer_shop", "title": "Лавка #1", "items": [{"id": 984, "price": 700, "amount": 3}]}
        self.assertEqual(s.check(shop, {}, protocol=True)[0]["title"], "Лавка 1")
        self.assertIsNotNone(s.check(dict(shop, items=[{"id": 984, "price": -1, "amount": 3}]), {}, protocol=True)[1])
        self.assertIsNotNone(s.check(dict(shop, items=[]), {}, protocol=True)[1])


class MindRoutingTest(unittest.TestCase):
    """mind.py: метка [offer:...] и события почты доходят до economy, поле промпта «рынок»."""

    def test_routing(self):
        from live_brain.config import Settings
        from live_brain.gate import RuleGate
        from live_brain.mind import Mind
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        sent = []

        async def send(action):
            sent.append(action)
            return len(sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        mem = Memory(root / "m.sqlite")
        mind = Mind(Settings.from_env({}), persona, mem, send, root / "decisions.jsonl", RuleGate(),
                    peers={"Arkady", "Vera"}, world=WORLD)
        asyncio.run(mind.on_message({"type": "state", "name": "Arkady", "map": "prontera", "x": 150, "y": 150,
                                     "hp_pct": 100, "zeny": 30000, "items": {"501": 40}, "dead": False}))
        asyncio.run(mind.on_message({"type": "event", "kind": "chat_private", "from": "Vera",
                                     "text": "Есть карта [offer:abcd:4001:1:1250]"}))
        self.assertTrue(any(a["action"] == "whisper" and a["text"].endswith("[offer:abcd:no]") for a in sent),
                        "предложение дошло до economy, ответ — метка")
        asyncio.run(mind.on_message({"type": "event", "kind": "mail_received", "from": "Vera", "mail_id": 7,
                                     "title": "Подарок", "attach": "z"}))
        self.assertIn({"action": "mail_take", "mail_id": 7}, [{k: a[k] for k in a if k != "id"} for a in sent])
        asyncio.run(mind.on_message({"type": "event", "kind": "mail_taken", "from": "Vera", "mail_id": 7,
                                     "ok": True, "zeny": 500, "items": []}))
        asyncio.run(mind.on_message({"type": "event", "kind": "npc_sold", "zeny": 700}))
        m = economy_metrics(mem, 0)
        self.assertEqual((m["писем получил"], m["продажи NPC, z"]), (1, 700))
        prompt = mind.build_prompt("тест", {})[1]["content"]
        self.assertIn("рынок", prompt)
        mem.close()
        tmp.cleanup()


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
        shift_time(self)                     # timefix: полдень мира — ночью распорядок уводит в сон, а не в лавку
        tmp = tempfile.TemporaryDirectory()
        mem = Memory(Path(tmp.name) / "m.sqlite")
        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        sent = []
        rest = WORLD["routine"]["town"]      # точка отдыха распорядка (R2: не у фонтана)
        state = {"name": "Arkady", "map": "prontera", "x": rest["x"], "y": rest["y"], "lock_map": "prontera",
                 "lock_x": rest["x"], "lock_y": rest["y"], "dead": False, "vend": {"can": 1, "open": 0}}

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
