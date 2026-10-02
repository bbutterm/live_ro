"""Коллекции: карты и трофеи (collection.py, ORG-074) — альбом по фактам loot/kill, хвастовство, не продаёт.

Настоящий Mind (Arkady) с поддельным телом, настоящая шина мира во временном каталоге; время модуля — подменные
часы (события старше окна сделки TRADE_SEC). Запуск: cd brain && python3 -m unittest -v tests.test_collection
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import world_bus
from live_brain.__main__ import organic_metrics
from live_brain.chronicle import LINES
from live_brain.collection import TRADE_SEC, Collection
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class CollectionTest(unittest.TestCase):
    def setUp(self, env=None):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.bus = world_bus.WorldBus(root / "world.sqlite", "Arkady")
        self.mind = Mind(Settings.from_env(env or {}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=WORLD, world_bus_db=self.bus)
        self.clock = Clock(time.time() + TRADE_SEC + 30)
        self.state(items={"501": 30, "4001": 1})
        self.mem.add_event("kill", {"monster": "Poring"})              # победа до модуля — трофей молча
        if self.mind.collection:
            self.c = Collection(self.mind, WORLD, clock=self.clock, rng=random.Random(1))
            self.mind.collection = self.c
            self.mind.world.pump()                                     # курсор шины на конец истории
            self.tick()                                                # первый запуск: история молча

    def tearDown(self):
        self.bus.close()
        self.mem.close()
        self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 30,
             "job": "Swordman", "dead": False, "weight_pct": 20, "zeny": 60000, "players": []}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def tick(self):
        self.c.next_tick = 0
        self.c.tick()

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ? ORDER BY id",
                                                              (kind,))]

    def drop(self, item, monster="Fabre"):
        self.mem.add_event("kill", {"monster": monster})
        self.mem.add_event("loot", {"item": item, "amount": 1})

    # ---------- альбом ----------

    def test_first_run_is_silent(self):
        self.assertEqual(self.c.album()["4001"]["src"], "inventory")   # карта рюкзака — в альбоме
        self.assertIn("Poring", self.c.st["trophies"])
        self.assertEqual(self.events("card_found") + self.events("trophy_first"), [])

    def test_card_drop_first_and_next(self):
        self.drop("Fabre Card")
        self.tick()
        ev = self.events("card_found")
        self.assertEqual(ev, [{"id": "4002", "name": "Fabre Card", "first": True, "n": 2}])
        self.assertEqual(self.events("trophy_first"), [{"monster": "Fabre"}])
        self.assertIn("первая карта", LINES["card_found"](ev[0]))
        self.drop("Pupa Card", monster="Pupa")
        self.tick()
        self.assertFalse(self.events("card_found")[1]["first"])
        self.mind.world.pump()
        rows = {r["data"]["name"]: r["importance"] for r in self.bus.read() if r["kind"] == "card_found"}
        self.assertEqual(rows, {"Fabre Card": 5, "Pupa Card": 3})      # первая в жизни — важность 5
        self.drop("Fabre Card")                                        # дубликат — без события
        self.tick()
        self.assertEqual(len(self.events("card_found")), 2)

    def test_not_a_drop_is_silent(self):
        self.mem.add_event("loot", {"item": "Fabre Card", "amount": 1})          # без победы (склад, NPC)
        self.mem.add_event("kill", {"monster": "Pupa"})
        self.mem.add_event("deal_complete", {})
        self.mem.add_event("loot", {"item": "Pupa Card", "amount": 1})           # сделка рядом
        self.tick()
        self.assertEqual(self.events("card_found"), [])
        self.assertEqual({k: v["src"] for k, v in self.c.album().items()},
                         {"4001": "inventory", "4002": "inventory", "4003": "inventory"})

    def test_waits_trade_window(self):
        self.clock.t = time.time()                                     # loot только что — сделка ещё может прийти
        self.drop("Fabre Card")
        self.tick()
        self.assertEqual(self.events("card_found"), [])
        self.clock.t += TRADE_SEC + 5
        self.tick()
        self.assertEqual(len(self.events("card_found")), 1)

    def test_rare_trophy(self):
        self.drop("Knife", monster="Lunatic")
        self.drop("Jellopy", monster="Lunatic")
        self.tick()
        self.assertEqual(self.events("trophy_rare"), [{"id": "1201", "name": "Knife"}])
        self.assertEqual(self.events("trophy_first"), [{"monster": "Lunatic"}])
        self.drop("Knife", monster="Lunatic")
        self.tick()
        self.assertEqual(len(self.events("trophy_rare")), 1)
        m = organic_metrics(self.mem, time.time() - 3600)
        self.assertEqual((m["карт в альбоме"], m["трофеев за период"]), (1, 2))

    # ---------- экономика: карты альбома не продаются ----------

    def test_album_cards_not_for_sale(self):
        self.drop("Fabre Card")
        self.tick()
        econ = self.mind.economy
        lots = {i: n for i, n, _ in econ.for_sale({"items": {"4001": 2, "4002": 1, "4005": 1}})}
        self.assertEqual(lots, {"4001": 1, "4005": 1})                 # дубликат и чужая карта — можно
        self.assertEqual(self.c.sellable({"4002": 1, "501": 5}), {"4002": 0, "501": 5})

    # ---------- хвастовство: тема разговора ----------

    def test_brag_topic(self):
        social = self.mind.social
        self.assertIn("card", social.topics)
        now = self.clock()
        self.assertIsNone(social.provide("card", "Vera", now))         # карта со склада — не хвастается
        self.drop("Fabre Card")
        self.tick()
        f = social.provide("card", "Vera", now)
        self.assertEqual((f["card"], f["cards"], f["_key"]), ("Fabre Card", 2, "card_first"))
        text, extra = social.compose("Vera", "card", None, now)
        self.assertIn("Fabre Card", text)
        self.assertLessEqual(len(text), 60)
        asyncio.run(social.say("Vera", "card", 1, now))
        self.assertIn("[chat:card:1]", self.sent[-1]["text"])
        self.assertIsNone(social.provide("card", "Vera", now))         # этому жителю уже рассказал
        self.assertIsNotNone(social.provide("card", "Arkady2", now))
        self.assertIsNone(social.provide("card", "Vera", now + 8 * 86400))
        self.assertTrue(social.phrases.get("card_re"))                 # ответ собеседника на тему

    def test_disabled(self):
        self.tearDown()
        self.setUp(env={"BRAIN_DISABLE": "collection"})
        self.assertIsNone(self.mind.collection)
        self.assertNotIn("card", self.mind.social.topics)


if __name__ == "__main__":
    unittest.main()
