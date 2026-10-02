"""Вечерний круг у фонтана (tradition.py, ORG-058): окно, сила традиции, один писатель, ступени, занятие gathering.

Настоящие Mind (Arkady и Vera) с общей шиной мира во временном каталоге и поддельным телом; часы подменные.
Запуск: cd brain && python3 -m unittest -v tests.test_tradition
"""
import asyncio
import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain import world_bus
from live_brain.activity import Activities
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.tradition import GAIN, LOSS, START, Tradition, stage

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
WORLD["tradition"] = dict(WORLD.get("tradition") or {}, enabled=True, point="fountain", hours=[20, 21], minutes=40)
TZ = timezone(timedelta(hours=WORLD["timezone_offset_hours"]))
FOUNTAIN = WORLD["social"]["points"]["fountain"]


def at(day, hour, minute=0):
    base = datetime(2026, 10, 5, tzinfo=TZ) + timedelta(days=day)
    return base.replace(hour=hour, minute=minute).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Resident:
    """Житель: настоящий Mind с поддельным телом и общей шиной."""

    def __init__(self, root, bot, name, bus_path, clock):
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        persona.pop("sleep", None)
        self.name = name
        self.mem = Memory(root / f"{bot}.sqlite")
        self.bus = world_bus.WorldBus(bus_path, name, clock=clock)
        self.dec = root / f"{bot}.jsonl"
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, self.dec, RuleGate(),
                         peers={"Arkady", "Vera"}, world=WORLD, world_bus_db=self.bus)
        self.mind.tradition.clock = clock
        self.mind.world.pump()                                   # курсор шины: история не переносится
        self.clock = clock

    def state(self, x, y, others=(), **kw):
        s = {"type": "state", "name": self.name, "map": "prontera", "x": x, "y": y, "hp_pct": 100, "lv": 30,
             "dead": False, "weight_pct": 10, "zeny": 5000, "items": {"501": 20},
             "players": [{"name": n, "x": ox, "y": oy, "lv": 30} for n, ox, oy in others]}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def tick(self):
        self.mind.tradition.tick()

    def decisions(self, kind="tradition"):
        if not self.dec.exists():
            return []
        return [json.loads(l) for l in self.dec.read_text().splitlines() if f'"{kind}"' in l]

    def close(self):
        self.mem.close()
        self.bus.close()


class TraditionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock(at(0, 14))
        self.bus_path = self.root / "shared" / "world.sqlite"
        self.a = Resident(self.root, "bot01", "Arkady", self.bus_path, self.clock)
        self.v = Resident(self.root, "bot02", "Vera", self.bus_path, self.clock)

    def tearDown(self):
        self.a.close()
        self.v.close()
        self.tmp.cleanup()

    def evening(self, day, together=True, who=("a", "v")):
        """Вечер: в 20:10 жители у фонтана (вместе или поодиночке), в 21:05 окно закрыто."""
        fx, fy = FOUNTAIN["x"], FOUNTAIN["y"]
        self.clock.t = at(day, 20, 10)
        if "a" in who:
            self.a.state(fx, fy, [("Vera", fx + 2, fy + 1)] if together else [])
            self.a.tick()
        if "v" in who:
            self.v.state(fx + 2, fy + 1, [("Arkady", fx, fy)] if together else [])
            self.v.tick()
        self.clock.t = at(day, 21, 5)
        for r in (self.a, self.v):
            r.tick()

    def bus_kinds(self):
        return [e["kind"] for e in self.a.bus.read()]

    def test_window_by_hours_and_sleep(self):
        t = self.a.mind.tradition
        self.assertFalse(t.window_open(at(0, 19, 59)))
        self.assertTrue(t.window_open(at(0, 20, 0)))
        self.assertTrue(t.window_open(at(0, 20, 59)))
        self.assertFalse(t.window_open(at(0, 21, 0)))
        self.assertFalse(t.window_open(at(0, 3)))
        self.a.mind.routine.st["mode"] = "sleep"
        self.assertFalse(t.window_open(at(0, 20, 30)), "спящий не идёт")

    def test_strength_steps_and_limits(self):
        t = self.a.mind.tradition
        self.assertEqual(t.strength(), START)
        self.assertEqual(t.on_window_end(at(0, 21), 1, "2026-10-05"), round(START + GAIN, 3))
        self.assertIsNone(t.on_window_end(at(0, 21), 1, "2026-10-05"), "вечер уже записан")
        self.assertEqual(t.on_window_end(at(1, 21), 0, "2026-10-06"), round(START + GAIN - LOSS, 3))
        for d in range(2, 12):
            t.on_window_end(at(d, 21), 1, f"2026-10-{d + 5:02d}")
        self.assertEqual(t.strength(at(11, 21)), 1.0)
        for d in range(12, 30):
            t.on_window_end(at(d, 21), 0, (datetime(2026, 10, 5) + timedelta(days=d)).strftime("%Y-%m-%d"))
        self.assertEqual(t.strength(at(29, 21)), 0.0)
        self.assertEqual(stage(0.19)[0], "forgotten")
        self.assertEqual(stage(0.2)[0], "sometimes")
        self.assertEqual(stage(0.6)[0], "tradition")

    def test_decay_without_gatherings(self):
        t = self.a.mind.tradition
        t.on_window_end(at(0, 21), 1, "2026-10-05")
        self.assertEqual(t.strength(at(1, 21)), 0.45, "первые сутки без сбора — без угасания")
        self.assertLess(t.strength(at(5, 21)), 0.45)

    def test_three_evenings_become_tradition(self):
        for day in range(3):
            self.evening(day)
        self.assertGreaterEqual(self.a.mind.tradition.strength(), 0.6)
        self.assertEqual(self.v.mind.tradition.strength(), self.a.mind.tradition.strength(), "общая сила из шины")
        writes = [e for e in self.a.bus.read() if e["kind"] == "tradition_strength"]
        self.assertEqual(len(writes), 3, "по одной записи за вечер")
        self.assertEqual({e["bot"] for e in writes}, {"Arkady"}, "пишет минимальное имя")
        self.assertEqual(self.v.mind.mem.count_events("tradition_gathering", 0), 0)
        self.assertIn("seen", [d.get("event") for d in self.v.decisions()])
        self.a.mind.world.pump()
        tr = [e for e in self.a.bus.read() if e["kind"] == "tradition"]
        self.assertEqual(len(tr), 1, "смена ступени публикуется один раз")
        self.assertEqual(tr[0]["importance"], 3)
        self.assertIn("собираются у фонтана", world_bus.describe("tradition", tr[0]["data"]))
        self.assertIn("собираются у фонтана", LINES["tradition_stage"](tr[0]["data"]))

    def test_lonely_evening_weakens(self):
        self.evening(0, together=False, who=("v",))
        self.assertEqual(self.v.mind.tradition.strength(), round(START - LOSS, 3))
        self.evening(1, together=False, who=("v",))
        self.a.mind.world.pump()
        self.v.mind.world.pump()
        self.assertEqual(self.v.mind.tradition.strength(), round(START - 2 * LOSS, 3))
        tr = [e for e in self.a.bus.read() if e["kind"] == "tradition"]
        self.assertEqual([e["data"]["stage"] for e in tr], ["forgotten"])

    def test_restart_closes_missed_window(self):
        fx, fy = FOUNTAIN["x"], FOUNTAIN["y"]
        self.clock.t = at(0, 20, 30)
        self.a.state(fx, fy, [("Vera", fx + 1, fy)])
        self.a.tick()
        t2 = Tradition(self.a.mind, WORLD["tradition"], clock=self.clock, world=WORLD)   # рестарт мозга
        self.clock.t = at(1, 9)
        t2.tick()
        self.assertEqual(t2.strength(), round(START + GAIN, 3))


class GatheringActivityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.dec = root / "d.jsonl"
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, self.dec, RuleGate(),
                         peers={"Arkady", "Vera"}, world=WORLD)
        self.clock = Clock(at(0, 20, 5))
        self.mind.tradition.clock = self.clock
        self.mind.social.clock = self.clock
        self.a = Activities(self.mind, clock=self.clock, rng=random.Random(3))
        self.mind.activities = self.a
        self.state(x=150, y=150)
        r = self.mind.routine
        r.new_day(self.clock.t)
        r.st.update(mode="town", arrived=True, rest_until=self.clock.t + 3600, mode_since=self.clock.t)
        self.mind.needs.weighted = lambda: {"social": 0.8, "rest": 0.3, "curiosity": 0.1, "supply": 0.0,
                                            "progress": 0.1, "wealth": 0.1, "safety": 0.0, "care": 0.0}

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 41,
             "job": "Swordsman", "dead": False, "weight_pct": 20, "zeny": 60000, "items": {"501": 30}, "players": []}
        s.update(getattr(self, "_last", {}))
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def score(self, strength):
        self.mind.tradition.strength = lambda now=None: strength
        self.a.rng = random.Random(0)
        return self.a.scores(self.mind.state)[0].get("gathering")

    def test_only_in_window(self):
        self.assertIsNotNone(self.score(0.3))
        self.clock.t = at(0, 15)
        self.assertIsNone(self.score(0.3), "днём круга нет")
        self.clock.t = at(0, 23)
        self.assertIsNone(self.score(0.3))

    def test_weight_grows_with_strength(self):
        self.assertGreater(self.score(0.9), self.score(0.2))

    def test_choose_go_and_proof(self):
        self.mind.tradition.strength = lambda now=None: 0.9
        self.a.next_decide = 0
        asyncio.run(self.a.tick())
        self.assertEqual(self.a.st.get("name"), "gathering")
        moves = [a for a in self.sent if a.get("action") == "meet_point"]
        self.assertEqual((moves[-1]["x"], moves[-1]["y"]), (FOUNTAIN["x"], FOUNTAIN["y"]))
        self.assertNotIn("sit", [a.get("action") for a in self.sent], "садится тело само (sitAuto_idle)")
        self.assertGreaterEqual(self.mind.social.next_walk, self.clock.t + 39 * 60, "не уходит гулять в круге")
        self.state(x=FOUNTAIN["x"], y=FOUNTAIN["y"], players=[{"name": "Vera", "x": FOUNTAIN["x"] + 3,
                                                               "y": FOUNTAIN["y"], "lv": 30}])
        asyncio.run(self.a.tick())
        self.assertTrue(self.a.st.get("proved"))
        self.assertEqual(self.mem.count_events("activity_done", 0), 1)

    def test_alone_not_proved(self):
        self.mind.tradition.strength = lambda now=None: 0.9
        self.a.next_decide = 0
        asyncio.run(self.a.tick())
        self.state(x=FOUNTAIN["x"], y=FOUNTAIN["y"], players=[])
        self.clock.t += 41 * 60
        asyncio.run(self.a.tick())
        self.assertIs(self.a.st.get("proved"), False)

    def test_blocked_by_meeting_plan(self):
        self.mind.plans.store.active = lambda: {"id": 1}
        self.a.next_decide = 0
        asyncio.run(self.a.tick())
        self.assertNotEqual(self.a.st.get("name"), "gathering")
        self.assertEqual(self.a.blocked(self.mind.state), "план встречи")

    def test_switch_off(self):
        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        off = Mind(Settings.from_env({"BRAIN_DISABLE": "tradition"}), persona, self.mem, None, self.dec,
                   RuleGate(), peers={"Vera"}, world=WORLD)
        self.assertIsNone(off.tradition)
        off2 = Mind(Settings.from_env({}), persona, self.mem, None, self.dec, RuleGate(), peers={"Vera"},
                    world=dict(WORLD, tradition=dict(WORLD["tradition"], enabled=False)))
        self.assertIsNone(off2.tradition)
        self.mind.tradition = None                                  # без модуля занятие недоступно
        self.assertNotIn("gathering", self.a.scores(self.mind.state)[0])


if __name__ == "__main__":
    unittest.main()
