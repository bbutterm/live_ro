"""Скупка и снаряжение торговца (buying.py, ORG-036, ТЗ Т-38): правила мозга, безопасность, данные NPC.

Данные (меню Mr. Hugh и Kafra, клетка у NPC) сверяются со скриптами rAthena и полями OpenKore, если рядом есть
сабмодули или заданы LIVE_RO_RATHENA / LIVE_RO_OPENKORE; иначе эта часть пропускается. Это проверка правил мозга,
а не скупки в игре.

Запуск: cd brain && python3 -m unittest -v tests.test_buying
"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import buying as B
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

from tests.test_newborn import HAVE_BOTH, HAVE_RATHENA, RATHENA, ok_walkable, rathena_walkable, reach

BRAIN_DIR = Path(__file__).resolve().parents[1]
HOMES = json.loads((BRAIN_DIR / "world" / "homes.json").read_text(encoding="utf-8"))


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


def buyer(**kw):
    b = {"can": 1, "skill": 1, "permits": 3, "shabby": 0, "slots": 5, "vending": 3, "pushcart": 3, "cart": 1,
         "open": 0, "stores": []}
    b.update(kw)
    return b


class FakeOrders:
    """orders.open_orders: чужие открытые заказы шины."""
    def __init__(self, orders):
        self.orders = orders

    def open_orders(self, now):
        return list(self.orders)

    def reserved(self):
        return []


class BuyingCase(unittest.TestCase):
    def make(self, enabled=True, patch=None):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(a)
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        world = load_world(BRAIN_DIR / "world" / "goals.json")
        world["buying"] = dict(world.get("buying") or {}, enabled=enabled, **(patch or {}))
        self.mem = Memory(root / "m.sqlite")
        self.alerts = []
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=world)
        self.mind.alert = lambda kind, text, every=3600: self.alerts.append(kind)
        self.clock = Clock()
        self.b = self.mind.buying
        if self.b:
            self.b.clock = self.clock
        town = self.mind.routine.town
        self.state(map=town["map"], x=town["x"], y=town["y"], lock_map=town["map"], lock_x=town["x"],
                   lock_y=town["y"])
        r = self.mind.routine
        r.new_day(self.clock.t)
        r.st.update(mode="town", arrived=True, rest_until=self.clock.t + 3600, mode_since=self.clock.t)
        r.sleep_window = lambda now: None
        if self.mind.social:
            self.mind.social.is_night = lambda now: False

    def tearDown(self):
        if hasattr(self, "mem"):
            self.mem.close()
            self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 40,
             "job": "Merchant", "dead": False, "weight_pct": 20, "zeny": 60000, "sp": 80,
             "items": {"501": 30, "984": 1, "1010": 2}, "players": [], "buyer": buyer(),
             "vend": {"can": 1, "open": 0, "overcharge": 0, "discount": 0},
             "craft": {"items": {}, "kept": [], "weight_free": 3000}}
        s.update(getattr(self, "_last", {}))
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def tick(self, minutes=1):
        self.clock.t += minutes * 60
        asyncio.run(self.b.tick())

    def actions(self, kind):
        return [a for a in self.sent if a.get("action") == kind]

    def event(self, kind, **kw):
        asyncio.run(self.mind.on_event({"type": "event", "kind": kind, **kw}))

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ?", (kind,))]


class BuyerTest(BuyingCase):
    def test_disabled_by_default(self):
        world = load_world(BRAIN_DIR / "world" / "goals.json")
        self.assertFalse(world["buying"]["enabled"])
        self.assertFalse(world["buying"]["setup"]["license"])
        self.make(enabled=False)
        self.assertIsNone(self.mind.buying)
        self.event("buyer_bought", item=984, amount=2, zeny=1000)            # own: не в gate и не в LLM
        self.assertEqual(self.events("buying_bought"), [])

    def test_plan_prices_and_budget(self):
        self.make()
        st = self.mind.state
        plan = self.b.plan(self.clock.t, st)
        self.assertTrue(plan)
        ids = [p["id"] for p in plan]
        self.assertEqual(ids[:2], [984, 1010], "с образцом в рюкзаке — первыми")
        p = self.b.prices
        for it in plan:
            self.assertGreater(it["price"], p.npc_sell(it["id"]), "дороже NPC-продажи — житель продаст мне")
            self.assertLessEqual(it["price"], p.buy_limit(it["id"]))
        self.assertLessEqual(len(plan), 5)
        self.assertLessEqual(sum(i["price"] * i["amount"] for i in plan), self.b.budget(st))
        self.assertEqual(self.b.budget(st), int((60000 - 5000) * 0.3))

    def test_budget_too_small(self):
        self.make()
        self.state(zeny=5600)
        self.assertEqual(self.b.plan(self.clock.t, self.mind.state), [])

    def test_orders_first_with_margin(self):
        self.make()
        self.mind.orders = FakeOrders([{"id": "o1", "item": "1011", "n": 4, "reward": 3000, "customer": "Vera"}])
        self.state(items={"501": 30, "984": 1, "1011": 1})
        plan = self.b.plan(self.clock.t, self.mind.state)
        first = plan[0]
        self.assertEqual(first["id"], 1011)
        self.assertEqual(first["why"], "заказ Vera")
        self.assertLessEqual(first["price"], int(3000 / 4 * 0.9))
        self.assertGreaterEqual(first["amount"], 4)

    def test_open_close_cycle(self):
        self.make()
        self.tick()
        opens = self.actions("buyer_open")
        self.assertEqual(len(opens), 1)
        self.assertTrue(opens[0]["title"].startswith("Arkady"))
        self.event("buyer_result", ok=True, items=[{"id": 984, "price": 660, "amount": 4}], limit=2640)
        self.assertEqual(len(self.events("buying_opened")), 1)
        self.state(buyer=buyer(open=1, items=[{"id": 984, "price": 660, "amount": 4}]))
        self.event("buyer_bought", item=984, amount=2, zeny=1320, price=660)
        got = self.events("buying_bought")
        self.assertEqual((got[0]["item"], got[0]["amount"], got[0]["zeny"]), (984, 2, 1320))
        self.tick(10)
        self.assertEqual(self.actions("buyer_close"), [], "ещё не время закрывать")
        self.tick(40)
        self.assertEqual(len(self.actions("buyer_close")), 1, "open_minutes вышли")
        self.event("buyer_closed", why="закрыл сам", bought={"984": 2}, spent=1320)
        self.assertIsNone(self.b.st["open"])
        self.assertEqual(self.b.st["stats"]["bought"], 2)
        self.state(buyer=buyer(open=0))
        self.tick(30)
        self.assertEqual(len(self.actions("buyer_open")), 1, "не чаще open_gap_minutes")

    def test_close_when_going_to_hunt(self):
        self.make()
        self.state(buyer=buyer(open=1))
        self.b.st["open"] = {"since": self.clock.t, "items": [], "limit": 0}
        self.mind.routine.st["mode"] = "hunt"
        self.tick()
        self.assertEqual(len(self.actions("buyer_close")), 1)

    def test_open_failed_retry_later(self):
        self.make()
        self.tick()
        self.event("buyer_result", ok=False, reason="вес покупок больше допустимого")
        self.assertEqual(len(self.events("buying_open_failed")), 1)
        self.b.st["last_open"] = 0
        self.tick()
        self.assertEqual(len(self.actions("buyer_open")), 1, "пауза retry_minutes после отказа")

    def test_not_while_busy_or_vending(self):
        self.make()
        self.mind.routine.st["arrived"] = False
        self.tick()
        self.assertEqual(self.actions("buyer_open"), [], "не дошёл до места отдыха")
        self.mind.routine.st["arrived"] = True
        self.state(vend={"can": 1, "open": 1, "overcharge": 0, "discount": 0})
        self.tick()
        self.assertEqual(self.actions("buyer_open") + self.actions("shop_close"), [], "лавка — сначала её очередь")
        self.tick(61)
        self.assertEqual(len(self.actions("shop_close")), 1, "очередь скупки: закрыть лавку")
        self.assertGreater(self.mind.routine.last_vend, self.clock.t, "распорядок не откроет лавку в мою очередь")
        self.state(vend={"can": 1, "open": 0, "overcharge": 0, "discount": 0})
        self.tick()
        self.assertEqual(len(self.actions("buyer_open")), 1)

    def test_low_sp_waits(self):
        self.make()
        self.state(sp=10)
        self.tick()
        self.assertEqual(self.actions("buyer_open"), [])

    def test_economy_refuses_deals_while_open(self):
        self.make()
        self.state(buyer=buyer(open=1))
        self.assertEqual(self.mind.economy.body_elsewhere(), "стою со скупкой")


class SellerTest(BuyingCase):
    def seller(self, **kw):
        self.make()
        self.state(job="Swordsman", buyer=buyer(can=0, skill=0, permits=0, slots=0, vending=0, pushcart=0, cart=0,
                                                stores=[{"name": "Vera", "title": "Vera: куплю"}]),
                   items={"501": 30, "1010": 3, "4001": 1, "909": 40}, **kw)

    def test_sell_spare(self):
        self.seller()
        self.mind.collection = None                     # альбом карт (ORG-074) держит карты — здесь без него
        self.tick()
        acts = self.actions("buyer_sell")
        self.assertEqual(len(acts), 1)
        a = acts[0]
        self.assertEqual(a["from"], "Vera")
        by = {i["id"]: i for i in a["items"]}
        self.assertNotIn(501, by, "зелья не продаю (share и NO_SELL)")
        self.assertEqual(by[1010]["min"], self.b.prices.npc_sell(1010) + 1)
        self.assertGreaterEqual(by[4001]["min"], 1000, "карта — не дешевле value")
        self.assertIn(507, by, "товары жителей (травы) — плагин проверит рюкзак сам")

    def test_album_cards_kept(self):
        self.seller()
        if not self.mind.collection:
            self.skipTest("модуль collection выключен")
        self.tick()
        self.assertNotIn(4001, [i["id"] for i in self.actions("buyer_sell")[0]["items"]], "карта альбома")
        self.tick(5)
        self.assertEqual(len(self.actions("buyer_sell")), 1, "жду итога, к той же скупке не чаще sell_gap")

    def test_sell_result_verified(self):
        self.seller()
        self.tick()
        self.event("buyer_sell_result", ok=True, **{"from": "Vera"}, sold=[{"item": 1010, "amount": 3, "price": 110}],
                   zeny_gain=330)
        self.assertEqual(len(self.events("buying_sold")), 1)
        self.assertEqual(self.b.st["stats"]["earned"], 330)

    def test_sell_result_unverified(self):
        self.seller()
        self.tick()
        self.event("buyer_sell_result", ok=True, **{"from": "Vera"}, sold=[{"item": 1010, "amount": 3, "price": 110}],
                   zeny_gain=100)
        self.assertEqual(self.events("buying_sold"), [])
        self.assertEqual(len(self.events("buying_unverified")), 1)

    def test_no_sell_to_stranger_or_out_of_town(self):
        self.seller()
        self.state(buyer=buyer(can=0, slots=0, stores=[{"name": "Stranger", "title": "x"}]))
        self.tick()
        self.assertEqual(self.actions("buyer_sell"), [])
        self.state(map="prt_fild08", buyer=buyer(can=0, slots=0, stores=[{"name": "Vera", "title": "x"}]))
        self.tick()
        self.assertEqual(self.actions("buyer_sell"), [])


class SetupTest(BuyingCase):
    def test_cart_at_home_kafra(self):
        self.make(patch={"setup": {"cart": True, "license": False}})
        self.state(buyer=buyer(cart=0, skill=0, permits=0, can=0, slots=0))
        self.tick()
        jc = [a for a in self.actions("job_change") if a.get("path") == "buying"]
        self.assertEqual(len(jc), 1)
        k = HOMES["towns"]["prontera"]
        steps = jc[0]["steps"]
        self.assertEqual((steps[0]["x"], steps[0]["y"]), (k["stand"]["x"], k["stand"]["y"]))
        self.assertEqual([a["text"] for a in steps[1]["answers"]], list(B.CART_ANSWERS))
        self.assertTrue(steps[1]["ordered"])
        self.event("job_change_result", path="buying", stage="cart", ok=True)
        self.state(buyer=buyer(cart=1, skill=0, permits=0, can=0, slots=0))
        self.tick()
        self.assertEqual(len(self.events("buying_setup_done")), 1)
        self.assertEqual(self.mind.career.st.get("done", []) if self.mind.career else [], [], "итог не в career")

    def test_cart_needs_zeny(self):
        self.make()
        self.state(zeny=1000, buyer=buyer(cart=0, skill=0, permits=0, can=0, slots=0))
        self.tick()
        self.assertEqual(self.actions("job_change"), [])

    def test_license_trip(self):
        self.make(patch={"setup": {"cart": True, "license": True}})
        self.state(zeny=20000, buyer=buyer(skill=0, permits=0, can=0, slots=0))
        self.tick()
        jc = self.actions("job_change")
        self.assertEqual(len(jc), 1)
        steps = jc[0]["steps"]
        self.assertEqual((steps[-2]["map"], steps[-2]["x"], steps[-2]["y"]), ("alberta_in", 58, 49))
        talk = steps[-1]
        self.assertEqual(talk["input_text"], "Arkady")
        self.assertEqual(jc[0]["success"]["text"], B.LICENSE_PROOF)
        self.event("job_change_result", path="buying", stage="license", ok=False, reason="таймаут")
        self.assertEqual(len(self.events("buying_setup_failed")), 1)
        self.tick()
        self.assertEqual(len(self.actions("job_change")), 1, "пауза setup_retry_hours")

    def test_license_needs_free_weight(self):
        self.make(patch={"setup": {"license": True}})
        self.state(zeny=20000, buyer=buyer(skill=0, permits=0, can=0, slots=0),
                   craft={"items": {}, "kept": [], "weight_free": 1000})
        self.tick()
        self.assertEqual(self.actions("job_change"), [])

    def test_permits_trip(self):
        self.make(patch={"setup": {"permits": True}})
        self.state(buyer=buyer(permits=0, can=0, slots=0))
        self.tick()
        talk = self.actions("job_change")[0]["steps"][-1]
        self.assertEqual(talk["input_number"], 10)
        self.assertEqual([a["text"] for a in talk["answers"]], list(B.PERMITS_ANSWERS))


class SafetyTest(BuyingCase):
    def test_safety(self):
        self.make()
        s = self.mind.safety
        st = {"zeny": 1000}
        ok, why = s.check({"action": "buyer_open", "title": "a#b", "items": [{"id": 984, "price": 100, "amount": 5}]},
                          st, protocol=True)
        self.assertEqual(ok["title"], "ab")
        _, why = s.check({"action": "buyer_open", "title": "a", "items": [{"id": 984, "price": 300, "amount": 5}]},
                         st, protocol=True)
        self.assertIn("лимит", why)
        _, why = s.check({"action": "buyer_open", "title": "a", "items": [{"id": 984, "price": 1, "amount": 1}] * 6},
                         st, protocol=True)
        self.assertIsNotNone(why)
        _, why = s.check({"action": "buyer_sell", "from": "Stranger", "items": [{"id": 984, "min": 1}]}, st,
                         protocol=True)
        self.assertIn("жителю", why)
        _, why = s.check({"action": "buyer_open", "title": "a", "items": [{"id": 984, "price": 1, "amount": 1}]}, st)
        self.assertEqual(why, "неизвестное действие", "модель скупку не открывает")
        ok, _ = s.check({"action": "buyer_close"}, {"dead": True}, protocol=True)
        self.assertEqual(ok, {"action": "buyer_close"}, "закрыть — всегда")


@unittest.skipUnless(HAVE_RATHENA, "нет upstream/rathena")
class DataTest(unittest.TestCase):
    def line(self, rel, n):
        return (RATHENA / rel).read_text(encoding="utf-8", errors="replace").splitlines()[n - 1]

    def test_hugh_script(self):
        f = "npc/merchants/buying_shops.txt"
        self.assertIn("alberta_in,58,52", self.line(f, 104))
        self.assertIn("2400", self.line(f, 105))
        for text, n in zip(B.LICENSE_ANSWERS, (154, 175, 205)):
            self.assertIn(text, self.line(f, n))
        self.assertIn(B.LICENSE_PROOF, self.line(f, 225))
        self.assertIn("input .@name$", self.line(f, 221))
        self.assertIn(B.PERMITS_ANSWERS[0], self.line(f, 115))
        self.assertIn(B.PERMITS_PROOF, self.line(f, 140))
        self.assertIn("10,000", self.line(f, 198))
        self.assertIn(f"{B.PERMIT_PRICE} zeny", self.line(f, 122))

    def test_kafra_cart(self):
        f = "npc/kafras/functions_kafras.txt"
        self.assertIn('"Rent a Pushcart","Check Other Information","Cancel"', self.line(f, 142))
        self.assertIn('"Rent a Pushcart.:Cancel"', self.line(f, 379))
        self.assertIn("F_Kafra\",5,0,0,40,800", self.line("npc/kafras/kafras.txt", 298))

    def test_skill_and_items(self):
        skills = (RATHENA / "db/re/skill_db.yml").read_text(encoding="utf-8")
        i = skills.index("Name: ALL_BUYING_STORE")
        self.assertIn("Buy_Market_Permit", skills[i:i + 600])
        usable = (RATHENA / "db/re/item_db_usable.yml").read_text(encoding="utf-8")
        i = usable.index(f"Id: {B.SHABBY}\n")
        self.assertIn("buyingstore 2;", usable[i:i + 300])

    @unittest.skipUnless(HAVE_BOTH, "нет полей OpenKore")
    def test_hugh_stand_walkable(self):
        x, y = B.HUGH["stand"]
        self.assertTrue(rathena_walkable("alberta_in", x, y) and ok_walkable("alberta_in", x, y))
        self.assertTrue(reach("alberta_in", (56, 43), (x, y), 1), "от точки гильдии торговцев (этап first_job)")


class MerchantProfileTest(unittest.TestCase):
    """Разрыв цепочки торговца: raiseSkill должен выучить MC_PUSHCART и MC_VENDING (лавка ORG-034, скупка ORG-036)."""

    def test_vending_in_skill_list(self):
        classes = json.loads((BRAIN_DIR.parent / "bots/combat/classes.json").read_text(encoding="utf-8"))["classes"]
        steps = [x.split() for x in classes["Merchant"]["skills"].split(",")]
        final = {}
        for name, lv in steps:
            final[name] = max(final.get(name, 0), int(lv))
        self.assertGreaterEqual(final.get("MC_VENDING", 0), 1)
        self.assertGreaterEqual(final.get("MC_PUSHCART", 0), 3, "skill_tree.yml: MC_VENDING требует MC_PUSHCART 3")
        self.assertLessEqual(sum(final.values()), 49, "очков навыков Merchant — 49 (job 50)")
        self.assertNotIn("MC_LOUD", final, "квестовый навык (skill_db IsQuest) очками не учится")
        first_vending = next(i for i, (n, _) in enumerate(steps) if n == "MC_VENDING")
        self.assertLessEqual(sum(int(lv) for _, lv in steps[:first_vending + 1]), 11, "лавка — к job 12")


if __name__ == "__main__":
    unittest.main()
