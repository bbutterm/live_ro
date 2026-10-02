"""Заказы между жителями (orders.py, ORG-070): публикация в шину, взятие шёпотом, доставка сделкой рынка, факты.

Настоящие Mind (Vera — заказчик, Arkady — исполнитель) с общей шиной мира во временном каталоге и поддельным телом;
часы модуля подменные. Шёпоты между жителями передаются вручную (поддельное тело их только записывает).
Запуск: cd brain && python3 -m unittest -v tests.test_orders
"""
import asyncio
import copy
import json
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
from live_brain.orders import Orders
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
JELLOPY, MUCUS = "909", "938"                     # не продаются у NPC (atlas.item_shops)


def world(wish=(JELLOPY,)):
    w = copy.deepcopy(WORLD)
    w["economy"].setdefault("market", {})["wish"] = list(wish)
    w["pets"] = dict(w.get("pets") or {}, enabled=False)        # желания питомца (приручение) — не в этих тестах
    return w


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Resident:
    def __init__(self, root, bot, name, bus_path, clock, wish=()):
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        persona.pop("sleep", None)
        self.name = name
        self.mem = Memory(root / f"{bot}.sqlite")
        self.bus = world_bus.WorldBus(bus_path, name, clock=clock)
        self.w = world(wish)
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / f"{bot}.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=self.w, world_bus_db=self.bus)
        self.clock = clock
        self.mind.world.pump()
        self.mind.economy.clock = clock
        self.o = Orders(self.mind, self.w, clock=clock)
        self.mind.orders = self.o
        self.mind.routine.st.update(mode="town", arrived=True)

    def state(self, items=None, others=(), **kw):
        s = {"type": "state", "name": self.name, "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 30,
             "dead": False, "weight_pct": 10, "zeny": 60000, "items": dict({"501": 30}, **(items or {})),
             "players": [{"name": n, "x": 158, "y": 185, "lv": 30} for n in others]}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def tick(self):
        self.o.next_check = 0
        self.o.next_scan = 0
        asyncio.run(self.o.tick())

    def hear(self, sender, text):
        asyncio.run(self.mind.on_event({"kind": "chat_private", "from": sender, "text": text}))

    def whispers(self, to=None):
        return [a for a in self.sent if a.get("action") == "whisper" and (to is None or a["to"] == to)]

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ? ORDER BY id",
                                                              (kind,))]

    def close(self):
        self.mem.close()
        self.bus.close()


class OrdersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock(time.time())
        bus = self.root / "shared" / "world.sqlite"
        self.v = Resident(self.root, "bot02", "Vera", bus, self.clock, wish=(JELLOPY,))
        self.a = Resident(self.root, "bot01", "Arkady", bus, self.clock)
        self.v.state()
        self.a.state()

    def tearDown(self):
        self.v.close()
        self.a.close()
        self.tmp.cleanup()

    def posted(self):
        self.v.tick()
        return [r for r in self.a.bus.read() if r["kind"] == "order"]

    def taken(self):
        """Vera заказала Jellopy, у Arkady они есть: он берётся, она соглашается."""
        self.posted()
        self.a.state(items={JELLOPY: 5})
        self.a.tick()
        take = self.a.whispers("Vera")[-1]["text"]
        self.v.hear("Arkady", take)
        ok = self.v.whispers("Arkady")[-1]["text"]
        self.a.hear("Vera", ok)
        return take, ok

    # ---------- заказчик ----------

    def test_candidates(self):
        r = Resident(self.root, "bot02", "Vera2", self.root / "w2.sqlite", self.clock, wish=(JELLOPY, "501", "611"))
        try:
            r.state()
            got = [i for i, _ in r.o.candidates(r.mind.state)]
            self.assertIn(JELLOPY, got)
            self.assertNotIn("501", got)                  # запасы share — просьба [need:], не заказ
            self.assertNotIn("611", got)                  # Magnifier продаёт NPC
        finally:
            r.close()

    def test_post_and_gap(self):
        rows = self.posted()
        self.assertEqual(len(rows), 1)
        d = rows[0]["data"]
        self.assertEqual((d["item"], d["n"], d["name"]), (JELLOPY, 1, "Jellopy"))
        self.assertEqual(d["reward"], self.v.mind.economy.prices.buy_limit(JELLOPY, 1))
        self.assertEqual(rows[0]["importance"], 2)
        self.assertIn("Jellopy", LINES["order_posted"](self.v.events("order_posted")[0]))
        self.v.o.st.pop("mine")                            # закрыт — но интервал публикаций
        self.v.tick()
        self.assertEqual(len([r for r in self.a.bus.read() if r["kind"] == "order"]), 1)

    def test_no_money_no_order(self):
        self.v.state(zeny=100)
        self.assertEqual(self.posted(), [])

    # ---------- исполнитель ----------

    def test_take_with_item(self):
        take, ok = self.taken()
        self.assertTrue(take.endswith(":take]"))
        self.assertTrue(ok.endswith(":ok]"))
        self.assertEqual(self.a.o.st["job"]["status"], "taken")
        self.assertEqual(self.v.o.st["mine"]["taken_by"], "Arkady")
        self.assertIn("order_taken", [r["kind"] for r in self.a.bus.read()])
        self.assertEqual(self.a.o.open_orders(self.clock.t), [])            # взятый — больше не открыт
        self.assertEqual(self.a.o.reserved(), {JELLOPY: 1})

    def test_not_take_without_item_or_loot(self):
        self.posted()
        self.a.tick()
        self.assertEqual(self.a.whispers("Vera"), [])
        self.a.mem.add_event("loot", {"item": "Jellopy", "amount": 2})    # добывал сам — берёт под добычу
        self.a.tick()
        self.assertTrue(self.a.whispers("Vera")[-1]["text"].endswith(":take]"))

    def test_second_taker_refused(self):
        self.taken()
        oid = self.v.o.st["mine"]["id"]
        self.v.hear("Arkady", f"[order:{oid}:take]")                       # повтор / другой исполнитель
        self.assertTrue(self.v.whispers("Arkady")[-1]["text"].endswith(":no]"))
        self.assertEqual(self.v.o.st["mine"]["taken_by"], "Arkady")

    def test_deliver_by_market_offer(self):
        self.taken()
        self.a.tick()
        self.assertFalse([w for w in self.a.whispers("Vera") if "[offer:" in w["text"]])   # Vera не видна
        self.a.state(items={JELLOPY: 5}, others=("Vera",))
        self.a.tick()
        offer = [w for w in self.a.whispers("Vera") if "[offer:" in w["text"]][-1]["text"]
        reward = self.a.o.st["job"]["reward"]
        self.assertIn(f":{JELLOPY}:1:{reward}]", offer)                    # цена — награда заказа
        self.v.state(others=("Arkady",))
        self.v.hear("Arkady", offer)                                        # экономика заказчика согласна
        self.assertTrue(self.v.whispers("Arkady")[-1]["text"].endswith(":ok]"))
        buy = [a for a in self.v.sent if a.get("action") == "offer_buy"][-1]
        self.assertEqual((buy["item"], buy["price"]), (int(JELLOPY), reward))

    def test_done_by_trade_facts(self):
        self.taken()
        reward = self.a.o.st["job"]["reward"]
        self.a.mem.add_event("trade_sold", {"peer": "Vera", "item": JELLOPY, "amount": 1, "price": reward,
                                            "paid": reward, "role": "seller"})
        self.a.tick()
        self.assertIsNone(self.a.o.st["job"])
        self.assertEqual(self.a.o.st["rep"]["done"], 1)
        done = self.a.events("order_done")[0]
        self.assertIn("выполнил", LINES["order_done"](done))
        self.assertIn("order_done", [r["kind"] for r in self.v.bus.read()])
        self.v.mem.add_event("trade_bought", {"peer": "Arkady", "item": JELLOPY, "amount": 1, "price": reward})
        self.v.tick()
        self.assertIsNone(self.v.o.st.get("mine"))
        self.assertEqual(self.v.events("order_closed")[0]["why"], "done")

    def test_cancel_when_not_needed(self):
        self.taken()
        self.v.mind.economy.market["wish"] = []
        self.v.tick()
        self.assertTrue(self.v.whispers("Arkady")[-1]["text"].endswith(":cancel]"))
        self.assertEqual(self.v.events("order_closed")[0]["why"], "cancel")
        self.a.hear("Vera", self.v.whispers("Arkady")[-1]["text"])
        self.assertIsNone(self.a.o.st["job"])

    def test_expiry(self):
        self.taken()
        self.clock.t += 3 * 86400
        self.v.tick()
        self.assertEqual(self.v.events("order_closed")[0]["why"], "failed")
        self.a.tick()
        self.assertEqual(self.a.o.st["rep"]["failed"], 1)
        self.assertEqual(self.a.events("order_failed")[0]["customer"], "Vera")

    def test_for_sale_keeps_reserved(self):
        self.a.mind.economy.prices.cfg["valuable_lot"] = 1                 # Jellopy — «ценный» лот для теста
        self.a.state(items={JELLOPY: 5})
        self.assertIn(JELLOPY, [i for i, *_ in self.a.mind.economy.for_sale()])
        self.taken()
        self.assertNotIn(JELLOPY, [i for i, *_ in self.a.mind.economy.for_sale()])

    def test_disabled(self):
        mem = Memory(self.root / "x.sqlite")
        try:
            m = Mind(Settings.from_env({"BRAIN_DISABLE": "orders"}),
                     json.loads((BRAIN_DIR / "personas" / "bot02.json").read_text()), mem, None,
                     self.root / "x.jsonl", RuleGate(), peers={"Arkady", "Vera"}, world=WORLD)
            self.assertIsNone(m.orders)
        finally:
            mem.close()


if __name__ == "__main__":
    unittest.main()
