"""Бестиарий и первооткрыватели (bestiary.py, ORG-077): счёт видов, «первый из жителей», места, тема, дашборд.

Настоящие Mind (Arkady, Vera) в раскладке лаборатории (state/<bot>/memory.sqlite, state/shared/world.sqlite) во
временном каталоге; события kill подаются как от тела, часы модуля подменные.
Запуск: cd brain && python3 -m unittest -v tests.test_bestiary
"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import dashboard, world_bus
from live_brain.__main__ import organic_metrics
from live_brain.bestiary import world_bestiary
from live_brain.chronicle import LINES
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


class Resident:
    def __init__(self, root, bot, name, clock, bus=True, history=(), env=None):
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        persona.pop("sleep", None)
        self.name = name
        (root / "state" / bot).mkdir(parents=True, exist_ok=True)
        self.mem = Memory(root / "state" / bot / "memory.sqlite")
        for mob in history:                                       # прошлые победы до запуска модуля
            self.mem.add_event("kill", {"monster": mob})
        self.bus = world_bus.WorldBus(root / "state" / "shared" / "world.sqlite", name, clock=clock) if bus else None
        self.mind = Mind(Settings.from_env(env or {}), persona, self.mem, send, root / f"{bot}.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"} - {name}, world=WORLD, world_bus_db=self.bus)
        self.b = self.mind.bestiary
        if self.b:
            self.b.clock = clock
        self.clock = clock

    def state(self, map_="prontera", **kw):
        s = {"type": "state", "name": self.name, "map": map_, "x": 156, "y": 185, "hp_pct": 100, "lv": 30,
             "dead": False, "weight_pct": 10, "zeny": 1000, "items": {"501": 30}, "players": []}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def kill(self, mob, map_=None):
        if map_:
            self.state(map_)
        asyncio.run(self.mind.on_event({"kind": "kill", "monster": mob}))

    def tick(self):
        self.b.next_tick = 0
        self.b.next_snap = 0
        self.b.known_cache = (0.0, None)
        self.b.tick()

    def visit(self, hmap):
        places = self.mem.get("places") or {}
        places[hmap] = {"source": "seen", "first": self.clock(), "last": self.clock()}
        self.mem.set("places", places)
        self.tick()

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ? ORDER BY id",
                                                              (kind,))]

    def close(self):
        self.mem.close()
        if self.bus:
            self.bus.close()


class BestiaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock(time.time())
        self.a = Resident(self.root, "bot01", "Arkady", self.clock, history=("Fabre", "Fabre", "Lunatic"))
        self.v = Resident(self.root, "bot02", "Vera", self.clock)
        self.a.state()
        self.v.state()

    def tearDown(self):
        self.a.close()
        self.v.close()
        self.tmp.cleanup()

    def bus(self, kind):
        return [e for e in self.a.bus.read() if e["kind"] == kind]

    def test_seed_is_silent(self):
        self.assertEqual(self.a.b.st["kills"]["Fabre"]["n"], 2)
        self.assertTrue(self.a.b.st["seeded"])
        self.a.tick()
        self.assertEqual(self.a.events("bestiary_first"), [])
        self.assertEqual(self.bus("monster_first"), [])

    def test_first_monster_in_world(self):
        self.a.kill("Poring", "prt_fild08")
        first = self.bus("monster_first")
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["importance"], 3)
        self.assertEqual(first[0]["data"]["map"], "prt_fild08")
        ev = self.a.events("bestiary_first")[0]
        self.assertEqual(LINES["bestiary_first"](ev), "первым(ой) из жителей победил(а) Poring")
        self.assertEqual(world_bus.describe("monster_first", first[0]["data"]), LINES["bestiary_first"](ev))
        self.a.kill("Poring")
        self.assertEqual(len(self.bus("monster_first")), 1)            # повтор — только счёт
        self.assertEqual(self.a.b.st["kills"]["Poring"]["n"], 2)
        self.v.kill("Poring", "prt_fild08")                            # Vera — уже не первая
        self.assertEqual(len(self.bus("monster_first")), 1)
        self.assertEqual(self.v.events("bestiary_first"), [])
        self.assertEqual(self.v.b.st["kills"]["Poring"]["n"], 1)

    def test_mini_boss_importance(self):
        self.v.kill("Vocal", "prt_fild07")
        first = self.bus("monster_first")[0]
        self.assertEqual(first["importance"], 4)
        self.assertTrue(first["data"]["boss"])
        self.assertIn("(мини-босс)", world_bus.describe("monster_first", first["data"]))

    def test_known_from_snapshot(self):
        self.a.tick()                                                  # снимок: Fabre, Lunatic
        snap = [e for e in self.v.bus.read() if e["kind"] == "bestiary_known"]
        self.assertEqual(snap[0]["data"]["m"], ["Fabre", "Lunatic"])
        self.assertEqual([e for e in world_bus.read_period(self.root / "state" / "shared" / "world.sqlite", 0,
                                                           self.clock() + 1) if e["kind"] == "bestiary_known"], [])
        self.v.kill("Fabre", "prt_fild08")
        self.assertEqual(self.bus("monster_first"), [])                # мир знал Fabre по снимку Arkady
        self.a.tick()
        self.assertEqual(len([e for e in self.v.bus.read() if e["kind"] == "bestiary_known"]), 1)   # затирание

    def test_first_place(self):
        self.a.visit("gef_fild01")
        first = self.bus("place_first")
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["importance"], 4)
        self.assertEqual(first[0]["data"]["name"], "тропа Arkady")
        self.assertIn("«тропа Arkady»", LINES["bestiary_first"](self.a.events("bestiary_first")[0]))
        self.a.visit("geffen")                                         # город — не открытие
        self.v.visit("gef_fild01")                                     # Vera — уже не первая
        self.assertEqual(len(self.bus("place_first")), 1)
        self.v.visit("pay_dun00")
        self.assertEqual(self.bus("place_first")[-1]["data"]["name"], "ход Vera")
        for r in (self.a, self.v):                                     # данные событий не ломают recent_events/промпт
            self.assertTrue(r.mem.recent_events(20))
            self.assertIn("бестиарий", json.dumps(r.mind.build_prompt("тест", {}), ensure_ascii=False))

    def test_no_bus_no_claims(self):
        r = Resident(self.root / "x", "bot01", "Arkady", self.clock, bus=False)
        try:
            self.assertIsNone(r.mind.world)
            r.state()
            r.kill("Poring", "prt_fild08")
            r.visit("gef_fild01")
            self.assertEqual(r.events("bestiary_first"), [])
            self.assertEqual(r.b.st["kills"]["Poring"]["n"], 1)
        finally:
            r.close()

    def test_topic(self):
        self.a.kill("Poring", "prt_fild08")
        f = self.a.b.facts("Vera", self.clock())
        self.assertEqual(f["mob"], "Poring")
        self.assertIn("bestiary", self.a.mind.social.topics)
        self.assertTrue(self.a.mind.social.can_say("bestiary", f))
        self.a.b.said("Vera", f, self.clock())
        self.assertIsNone(self.a.b.facts("Vera", self.clock()))
        self.a.visit("gef_fild01")
        f = self.a.b.facts("Vera", self.clock())
        self.assertEqual((f["place"], f["_key"]), ("gef_fild01", "bestiary_place"))
        self.assertTrue(self.a.mind.social.can_say("bestiary_place", f))
        self.clock.t += 8 * 86400
        self.assertIsNone(self.a.b.facts("Arkady2", self.clock()))       # старое открытие — не хвастается

    def test_dashboard_and_metric(self):
        self.a.kill("Poring", "prt_fild08")
        self.v.kill("Poring", "prt_fild08")
        self.v.kill("Vocal", "prt_fild07")
        self.a.visit("gef_fild01")
        best = world_bestiary(self.root, ["bot01", "bot02"], now=self.clock())
        poring = next(m for m in best["monsters"] if m["monster"] == "Poring")
        self.assertEqual((poring["kills"], poring["first_by"]), (2, "Arkady"))
        self.assertEqual(poring["by"], {"Arkady": 1, "Vera": 1})
        fabre = next(m for m in best["monsters"] if m["monster"] == "Fabre")
        self.assertTrue(fabre["guess"])                                # история без шины — по памяти
        self.assertEqual(best["places"][0]["name"], "тропа Arkady")
        page = dashboard.render(dashboard.collect(self.root, ["bot01", "bot02"], now=self.clock()))
        self.assertIn("<h2>Бестиарий</h2>", page)
        self.assertIn("Vocal", page)
        self.assertIn("тропа Arkady", page)
        m = organic_metrics(self.a.mem, self.clock() - 3600, self.clock())
        self.assertEqual(m["видов в бестиарии"], 3)                    # Fabre, Lunatic, Poring
        self.assertEqual(m["открытий первым"], 2)                      # Poring, gef_fild01

    def test_disabled(self):
        r = Resident(self.root / "y", "bot01", "Arkady", self.clock, bus=False, env={"BRAIN_DISABLE": "bestiary"})
        try:
            self.assertIsNone(r.mind.bestiary)
        finally:
            r.close()


if __name__ == "__main__":
    unittest.main()
