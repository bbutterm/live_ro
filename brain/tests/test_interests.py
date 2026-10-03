"""Интересы (interests.py, ORG-103, ТЗ Т-42): у жителя 1–3 увлечения; инициатива и разговор о хобби — у увлечённых.

Запуск: cd brain && python3 -m unittest -v tests.test_interests
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain import census
from live_brain import interests as im
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.interests import CATALOG, Interests, derive
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.pets import Pets
from live_brain.routine import load_world
from tests.test_pets import DATA, MAPS
from tests.test_social import Clock, FakeMind, at_hour

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT = BRAIN_DIR.parent
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")


def persona(bot):
    return json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())


class DeriveTest(unittest.TestCase):
    def test_deterministic_two_different(self):
        for p in [persona("bot01"), persona("bot02")] + [
                json.loads(f.read_text()) for f in sorted((ROOT / "bots" / "templates").glob("*/persona.json"))]:
            a = derive(p["traits"], p["name"])
            self.assertEqual(a, derive(p["traits"], p["name"]))
            self.assertEqual(len(a), 2)
            self.assertEqual(len(set(a)), 2)
            self.assertTrue(set(a) <= set(CATALOG))

    def test_traits_drive(self):
        self.assertIn("people", derive({"sociability": 0.95, "greed": 0.1, "curiosity": 0.1, "whimsy": 0.1,
                                        "diligence": 0.1}, "X"))
        greedy = derive({"greed": 0.95, "sociability": 0.1, "curiosity": 0.1, "whimsy": 0.1, "diligence": 0.1}, "Y")
        self.assertTrue({"trade", "cards"} & set(greedy))

    def test_tie_by_name(self):
        flat = {t: 0.5 for t in ("greed", "curiosity", "sociability", "whimsy", "diligence")}
        outs = {tuple(derive(flat, n)) for n in ("Ann", "Bob", "Cid", "Dee", "Eve", "Fay", "Gus", "Hal")}
        self.assertGreater(len(outs), 1, "при равных чертах имена дают разные интересы")

    def test_persona_value_checked(self):
        p = {"name": "Q", "interests": ["cards", "dragons", "cards"], "traits": {}}
        with self.assertLogs("interests", "WARNING"):
            self.assertEqual(im.interests(p), ["cards"])
        with self.assertLogs("interests", "WARNING"):
            self.assertEqual(im.interests({"name": "Q", "interests": ["nope"], "traits": {"sociability": 0.9}})[0],
                             "people")                                # ничего годного — вывод из черт

    def test_personas(self):
        self.assertEqual(im.interests(persona("bot01")), ["achieve", "explore"])
        self.assertEqual(im.interests(persona("bot02")), ["people", "pets"])
        seen = set()
        for f in sorted((ROOT / "bots" / "templates").glob("*/persona.json")):
            got = tuple(im.interests(json.loads(f.read_text())))
            self.assertTrue(1 <= len(got) <= 3, f)
            self.assertNotIn(got, seen, f"{f}: шаблоны — с разными интересами")
            seen.add(got)


class TopicTest(unittest.TestCase):
    """Тема хобби у не увлечённого звучит в ~5 раз реже (шанс × other 0.2)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock(at_hour(14))
        self.mem = Memory(Path(self.tmp.name) / "a.sqlite")
        self.a = FakeMind("bot01", "Vera", self.mem, self.clock)
        s = self.a.social
        s.phrases = dict(s.phrases, hobby=["Смотри, {thing}!"])
        s.register_topic("hobby", lambda peer, now: {"thing": "карта"}, chance=1.0, interest="cards")

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def count(self, n=1000):
        s = self.a.social
        s.rng = random.Random(5)
        return sum("hobby" in s.registry_topics("Vera", self.clock.t) for _ in range(n))

    def test_card_rarer_for_arkady(self):
        self.assertEqual(self.count(), 1000, "без модуля интересов — как раньше")
        self.a.interests = Interests(self.a, WORLD)
        self.assertFalse(self.a.interests.has("cards"))
        arkady = self.count()
        self.a.interests.list = ["cards"]
        fan = self.count()
        self.assertEqual(fan, 1000)
        self.assertTrue(150 <= arkady <= 250, arkady)
        self.assertAlmostEqual(fan / arkady, 5, delta=1.2)


class MindTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, env=None, bot="bot01"):
        async def send(a):
            return 1
        mem = Memory(self.root / f"{bot}-{len(list(self.root.iterdir()))}.sqlite")
        self.addCleanup(mem.close)
        return Mind(Settings.from_env(env or {}), persona(bot), mem, send, self.root / "d.jsonl", RuleGate(),
                    peers={"Arkady", "Vera"}, world=WORLD)

    def test_registry_and_topics(self):
        m = self.make()
        self.assertEqual(m.interests.list, ["achieve", "explore"])
        self.assertEqual(m.social.topics["card"]["interest"], "cards")
        self.assertEqual(m.social.topics["bestiary"]["interest"], "bestiary")
        self.assertEqual(m.social.topics["achieve"]["interest"], "achieve")
        self.assertEqual(m.social.topics["place"]["interest"], "places")
        self.assertEqual(im.weight(m, "cards"), 0.2)
        self.assertEqual(im.weight(m, "explore", "explore_other"), 1.0)
        self.assertIsNotNone(m.collection, "факты коллекции пишутся у всех — модуль не выключается")

    def test_card_facts_for_everyone(self):
        m = self.make()
        self.assertFalse(m.interests.has("cards"))
        m.collection.card_found(time.time(), "4001")
        self.assertEqual(m.mem.count_events("card_found", 0), 1)

    def test_switch_off(self):
        m = self.make({"BRAIN_DISABLE": "interests"})
        self.assertIsNone(m.interests)
        self.assertEqual(im.weight(m, "cards"), 1.0)
        self.assertEqual(im.weight(SimpleNamespace(interests=None), "pets", "tame_other"), 1.0)

    def test_dream_weighs_interest(self):
        m = self.make()                                      # Arkady: жадность 0.6, любопытство 0.4
        state = {"name": "Arkady", "lv": 30, "job": "Swordsman"}
        m.dream.rng = SimpleNamespace(uniform=lambda a, b: 0.0)
        kinds = [d["kind"] for d in m.dream.choose(state)]
        self.assertLess(kinds.index("explorer"), kinds.index("cards"), "увлечён путешествиями, не картами")
        m.interests = None
        kinds_off = [d["kind"] for d in m.dream.choose(state)]
        self.assertLess(kinds_off.index("cards"), kinds_off.index("explorer"), "без интересов — по чертам")


class PetsInterestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.sent, self.dec = [], []

        async def execute(actions, source, reason, protocol=False):
            self.sent += actions

        p = persona("bot01")
        p["hunt_maps"] = ["prt_fild08"]
        self.mind = SimpleNamespace(
            mem=self.mem, fresh_state=True, execute=execute, write_decision=self.dec.append, epoch=1, persona=p,
            needs=SimpleNamespace(t={"generosity": 0.7}), routine=SimpleNamespace(st={"mode": "hunt"}),
            state={"name": "Arkady", "lv": 10, "hp_pct": 100, "dead": False,
                   "pet": {"has": False, "eggs": [], "items": {"619": 1}, "near": {"1002": 3}}})
        self.mind.interests = Interests(self.mind, WORLD)
        self.t = time.time()
        self.p = Pets(self.mind, clock=lambda: self.t, data=DATA, atlas_maps=MAPS)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def tames(self, n=200):
        for _ in range(n):
            self.t += 3600
            self.p.st["tries"] = []
            asyncio.run(self.p.tick())
        return len([a for a in self.sent if a["action"] == "pet_tame"])

    def test_not_fan_tames_rarely(self):
        self.p.rng = random.Random(3)
        rare = self.tames()
        self.assertTrue(20 <= rare <= 60, rare)              # ~0.2 × 200
        self.assertIn("tame_skip", [d.get("event") for d in self.dec])
        self.sent.clear()
        self.mind.interests.list = ["pets"]
        self.assertEqual(self.tames(50), 50)

    def test_existing_pet_kept(self):
        self.mind.state["pet"] = {"has": True, "type": 1002, "name": "Poring", "hungry": 50}
        self.t += 3600
        asyncio.run(self.p.tick())
        self.assertEqual(self.p.st["pet"]["name"], "Poring")


class CensusTest(unittest.TestCase):
    def test_census_shows_interests(self):
        rs = census.residents(ROOT, None, ["bot01", "bot02"], time.time())
        by = {r["bot"]: r for r in rs}
        self.assertEqual(by["bot01"]["interests"], ["achieve", "explore"])
        self.assertEqual(by["bot02"]["interests"], ["people", "pets"])
        text = census.render(census.collect(ROOT, None, ["bot01", "bot02"]))
        self.assertIn("интересы: люди, питомцы", text)


if __name__ == "__main__":
    unittest.main()
