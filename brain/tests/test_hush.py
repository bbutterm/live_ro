"""Тихие дни мира и режиссёр, который уважает тишину (ORG-110, Т-39): quiet_day детерминирован и одинаков у всех,
доля ≈ per_week/7, рынок/праздник/день рождения не тихие; режиссёр — новые умолчания (8 ч, шанс 0.5 одним броском
на окно, 1 в сутки, без contest/gathering), в тихий день только последствия; вывеска, подначка, приветствие и бюджет
внимания в тихий день; личные тихие часы; летопись; BRAIN_DISABLE=calendar.

Запуск: cd brain && python3 -m unittest -v tests.test_hush
"""
import asyncio
import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain import world_bus
from live_brain import world_calendar as wc
from live_brain.config import Settings
from live_brain.director import Director
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZ_H = WORLD.get("timezone_offset_hours", 0)
TZ = timezone(timedelta(hours=TZ_H))
CAL = wc.load()
ALWAYS = {"per_week": 7, "never": ["market", "holiday", "birthday"], "market_weekdays": [6]}   # каждый обычный день
NEVER = {"per_week": 0}


def at(hour, minute=0, day=8, month=10):
    """2026-10-08 — четверг без праздника (как REF_DAY tests/worldtime.py)."""
    return datetime(2026, month, day, hour, minute, tzinfo=TZ).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class QuietDayTest(unittest.TestCase):
    def test_deterministic_and_shared(self):
        days = [at(12, day=d) for d in range(1, 29)]
        a = [wc.quiet_day(t, TZ_H, cal=CAL, roster={}) for t in days]
        b = [wc.quiet_day(t, TZ_H, cal=json.loads(json.dumps(CAL)), roster={}) for t in days]
        self.assertEqual(a, b, "одинаково у двух «жителей» без общей БД")
        self.assertEqual(a, [wc.quiet_day(t, TZ_H, cal=CAL, roster={}) for t in days], "повторяемо")
        # весь день один и тот же ответ (дата мира, а не час)
        t = next(t for t, q in zip(days, a) if q)
        self.assertTrue(all(wc.quiet_day(t - 12 * 3600 + h * 3600 + 1, TZ_H, cal=CAL, roster={}) for h in range(24)))

    def test_share_per_week(self):
        start = at(12, day=1, month=1)
        n = sum(wc.quiet_day(start + i * 86400, TZ_H, cal=CAL, roster={}) for i in range(1000))
        self.assertAlmostEqual(n / 1000, CAL["quiet"]["per_week"] / 7, delta=0.03)

    def test_never_market_holiday_birthday(self):
        sat = at(12, day=10)                      # 2026-10-10 — суббота, рыночный день
        self.assertEqual(datetime.fromtimestamp(sat, TZ).isoweekday(), 6)
        self.assertFalse(wc.quiet_day(sat, TZ_H, ALWAYS, cal=CAL, roster={}))
        self.assertTrue(wc.quiet_day(at(12), TZ_H, ALWAYS, cal=CAL, roster={}), "обычный четверг — тихий при 7/нед")
        harvest = datetime(2026, 9, 23, 12, tzinfo=TZ).timestamp()
        self.assertFalse(wc.quiet_day(harvest, TZ_H, ALWAYS, cal=CAL, roster={}), "праздник урожая")
        roster = {"residents": {"bot02": {"name": "Vera", "born": "2025-10-08"}}}
        self.assertFalse(wc.quiet_day(at(12), TZ_H, ALWAYS, cal=CAL, roster=roster), "день рождения жителя")
        self.assertFalse(wc.quiet_day(at(12), TZ_H, NEVER, cal=CAL, roster={}))

    def test_chronicle_header(self):
        cal = dict(CAL, quiet=ALWAYS)
        self.assertIn("тихий день", wc.header_for("2026-10-08", TZ_H, cal, roster={}))
        self.assertNotIn("тихий день", wc.header_for("2026-10-10", TZ_H, cal, roster={}))
        self.assertNotIn("тихий день", wc.header_for("2026-10-08", TZ_H, dict(CAL, quiet=NEVER), roster={}))


class MindCase(unittest.TestCase):
    bot = "bot01"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock(at(14))
        self.buses = []

    def tearDown(self):
        for b in self.buses:
            b.close()
        for m in getattr(self, "minds", []):
            m.mem.close()
        self.tmp.cleanup()

    def make(self, bot=None, env=None, world=WORLD, quiet=ALWAYS, bus=False, keep_sleep=False):
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / f"{bot or self.bot}.json").read_text())
        if not keep_sleep:
            persona.pop("sleep", None)
        self.minds = getattr(self, "minds", [])
        n = len(self.minds)
        self.decisions = self.root / f"d{n}.jsonl"
        kw = {}
        if bus:
            self.path = self.root / "state" / "shared" / "world.sqlite"
            self.bus = world_bus.WorldBus(self.path, "Arkady")
            self.others = {name: world_bus.WorldBus(self.path, name) for name in ("Vera", "Bram")}
            self.buses += [self.bus, *self.others.values()]
            kw["world_bus_db"] = self.bus
        mind = Mind(Settings.from_env(env or {}), persona, Memory(self.root / f"m{n}.sqlite"), send, self.decisions,
                    RuleGate(), peers={"Arkady", "Vera", "Bram"}, world=world, **kw)
        self.minds.append(mind)
        if mind.calendar is not None:
            mind.calendar.clock = self.clock
            mind.calendar.cal = dict(mind.calendar.cal, quiet=quiet)
            mind.calendar.roster = {}
        mind.state = {"name": persona["name"], "map": "prontera", "x": 156, "y": 185, "dead": False, "lv": 41,
                      "hp_pct": 100, "players": []}
        mind.fresh_state = True
        for attr in ("social", "attention", "mood", "rivalry", "society", "strangers"):
            mod = getattr(mind, attr, None)
            if mod is not None and hasattr(mod, "clock"):
                mod.clock = self.clock
        if mind.social is not None:
            mind.social.grammar = None
            mind.social.rng = random.Random(2)
        return mind

    def whispers(self):
        return [a for a in self.sent if a.get("action") == "whisper"]

    def decisions_of(self, typ, event):
        if not self.decisions.exists():
            return []
        rows = [json.loads(x) for x in self.decisions.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [r for r in rows if r.get("type") == typ and r.get("event") == event]


class DirectorHushTest(MindCase):
    def director(self, quiet=NEVER, world=WORLD, env=None, seed=1):
        self.mind = self.make(world=world, quiet=quiet, bus=True, env=env)
        self.d = Director(self.mind, world, clock=self.clock, rng=random.Random(seed))
        self.d.started = self.clock.t - 24 * 3600
        self.d.cfg["cache_seconds"] = 0
        self.mind.director = self.d
        if self.mind.crowd is not None:
            self.mind.crowd.clock = self.clock
        if self.mind.routine is not None and isinstance(self.mind.routine.st, dict):
            self.mind.routine.st["mode"] = "town"
        return self.d

    def tick(self):
        self.d.next_check = 0.0
        asyncio.run(self.d.tick())

    def incidents(self):
        return [r["data"]["incident"] for r in self.bus.recent("director", 0)]

    def test_new_defaults(self):
        d = self.director()
        self.assertEqual((d.cfg["quiet_hours"], d.cfg["max_per_day"], d.cfg["gap_hours"], d.cfg["stir_chance"]),
                         (8, 1, 12, 0.5))
        self.assertNotIn("contest", d.kinds())
        self.assertNotIn("gathering", d.kinds())

    def test_quiet_day_no_stir_but_consequences(self):
        d = self.director(quiet=ALWAYS)
        d.cfg["stir_chance"] = 1.0
        self.assertTrue(self.mind.calendar.quiet())
        self.tick()
        self.assertEqual(self.incidents(), [], "тихий день — без поводов тишины")
        self.assertIn("тихий день", self.decisions_of("director", "let_quiet")[-1]["why"])
        self.others["Vera"].publish("death_report", {"map": "prt_fild07"}, 4, now=self.clock.t - 3600)
        self.others["Bram"].publish("death_report", {"map": "prt_fild07"}, 4, now=self.clock.t - 1800)
        self.others["Bram"].publish("death_report", {"map": "prt_fild08"}, 4, now=self.clock.t - 1700)
        self.tick()
        self.assertEqual(self.incidents(), ["caution"], "после 3 смертей — день осторожности и в тихий день")

    def test_help_on_quiet_day(self):
        d = self.director(quiet=ALWAYS)
        self.others["Vera"].publish("death_report", {"map": "prt_fild08"}, 4, now=self.clock.t - 1800)
        self.tick()
        self.assertEqual(self.incidents(), ["help"])

    def test_contest_gathering_only_if_listed(self):
        d = self.director()
        d.cfg["stir_chance"] = 1.0
        for _ in range(3):
            self.assertNotIn("contest", [o[0] for o in d.stir_options(self.clock.t)])
        d.cfg["stir_kinds"] = ["contest"]
        self.others["Bram"].replace("presence", {"map": "prontera"}, 1, now=self.clock.t - 60)
        self.assertEqual([o[0] for o in d.stir_options(self.clock.t)], ["contest"])
        self.tick()
        self.assertEqual(self.incidents(), ["contest"])

    def test_gathering_when_listed(self):
        d = self.director()
        if self.mind.tradition is None:
            self.skipTest("традиция выключена")
        self.clock.t = at(18, 30)
        self.assertNotIn("gathering", [o[0] for o in d.stir_options(self.clock.t)])
        d.cfg["stir_kinds"] = ["gathering"]
        self.assertEqual([o[0] for o in d.stir_options(self.clock.t)], ["gathering"])

    def test_chance_zero_lets_quiet(self):
        d = self.director()
        d.cfg["stir_chance"] = 0.0
        self.tick()
        self.assertEqual(self.incidents(), [])
        self.assertEqual(len(self.decisions_of("director", "let_quiet")), 1)

    def test_one_roll_per_quiet_window(self):
        d = self.director()
        d.cfg["stir_chance"] = 0.5
        rolls = []
        real = d.rng.random
        d.rng.random = lambda: rolls.append(1) or 0.9           # бросок «не в этот раз»
        for _ in range(4):
            self.tick()
            self.clock.t += 1800
        self.assertEqual(len(rolls), 1, "один бросок на окно тишины, не каждые 30 мин")
        self.assertEqual(self.incidents(), [])
        self.others["Vera"].publish("level_up", {"level": 20}, 3, now=self.clock.t - 9 * 3600)   # новое окно
        d.rng.random = lambda: rolls.append(1) or 0.1
        self.tick()
        self.assertEqual(len(rolls), 2)
        self.assertEqual(len(self.incidents()), 1)
        d.rng.random = real

    def test_quiet_hours_eight(self):
        d = self.director()
        d.cfg["stir_chance"] = 1.0
        self.others["Vera"].publish("level_up", {"level": 20}, 3, now=self.clock.t - 5 * 3600)
        self.tick()
        self.assertEqual(self.incidents(), [], "5 ч тишины — ещё не повод")
        self.clock.t += 3 * 3600 + 60
        self.tick()
        self.assertEqual(len(self.incidents()), 1)

    def test_calendar_disabled(self):
        d = self.director(quiet=ALWAYS, env={"BRAIN_DISABLE": "calendar"})
        self.assertIsNone(self.mind.calendar)
        self.assertFalse(d.quiet_day(self.clock.t), "без календаря тихих дней нет")
        self.assertEqual(d.cfg["quiet_hours"], 8, "режиссёр — по новым параметрам")
        d.cfg["stir_chance"] = 1.0
        self.tick()
        self.assertEqual(len(self.incidents()), 1)


class ResidentsHushTest(MindCase):
    def test_no_tease_on_quiet_day(self):
        m = self.make(quiet=ALWAYS)
        if m.rivalry is None:
            self.skipTest("rivalry выключен")
        self.assertFalse(asyncio.run(m.rivalry.tease(self.clock.t, "Vera", "rival_lead", mine=5, theirs=1)))
        self.assertEqual(self.whispers(), [])
        m.calendar.cal = dict(m.calendar.cal, quiet=NEVER)
        self.assertTrue(asyncio.run(m.rivalry.tease(self.clock.t, "Vera", "rival_lead", mine=5, theirs=1)))

    def test_no_sign_on_quiet_day(self):
        m = self.make(quiet=ALWAYS)
        if m.society is None:
            self.skipTest("society выключен")
        s = m.society
        s.cfg["room_chance"] = 1.0
        s.next_room = 0
        if m.routine is not None:
            m.routine.st = dict(m.routine.st or {}, mode="town", arrived=True, rest_until=self.clock.t + 3 * 3600,
                                day=m.routine.today(self.clock.t))
        asyncio.run(s.rooms(self.clock.t, m.state))
        self.assertFalse([a for a in self.sent if a.get("action") == "chat_room"], "тихий день — без вывесок")
        m.calendar.cal = dict(m.calendar.cal, quiet=NEVER)
        s.next_room = 0
        asyncio.run(s.rooms(self.clock.t, m.state))
        opened = [a for a in self.sent if a.get("action") == "chat_room"]
        self.assertIsNone(s.close_reason(self.clock.t, m.state))
        self.assertTrue(opened, "в обычный день вывеска ставится")

    def test_strangers_wave_once_on_quiet_day(self):
        m = self.make(quiet=ALWAYS)
        if m.strangers is None:
            self.skipTest("strangers выключен")
        st = m.strangers
        p = {"seen": 1, "first": self.clock.t, "maps": [], "hours": []}
        asyncio.run(st.wave("Human", p, self.clock.t, m.state))
        self.assertEqual(len([a for a in self.sent if a.get("action") == "emote"]), 1, "первая встреча — машет")
        self.clock.t += 2 * 3600
        asyncio.run(st.wave("Human", p, self.clock.t, m.state))
        self.assertEqual(len([a for a in self.sent if a.get("action") == "emote"]), 1, "тихий день — раз в день")
        m.calendar.cal = dict(m.calendar.cal, quiet=NEVER)
        asyncio.run(st.wave("Human", p, self.clock.t, m.state))
        self.assertEqual(len([a for a in self.sent if a.get("action") == "emote"]), 2)

    def test_attention_half_on_quiet_day(self):
        m = self.make(quiet=NEVER)
        if m.attention is None:
            self.skipTest("attention выключен")
        base = m.attention.budget(self.clock.t)["base"]
        m.calendar.cal = dict(m.calendar.cal, quiet=ALWAYS)
        self.assertAlmostEqual(m.attention.budget(self.clock.t)["base"], base * 0.5, places=2)

    def test_own_quiet_hours(self):
        m = self.make("bot02", quiet=NEVER, keep_sleep=True)       # Vera: сон 23:30, 7–8.5 ч (середина 7.75)
        s = m.social
        self.assertTrue(s.own_quiet(at(22, 45)), "последний час до сна")
        self.assertFalse(s.own_quiet(at(22, 20)))
        self.assertFalse(s.own_quiet(at(15)))
        self.assertTrue(s.own_quiet(at(7, 30, day=9)), "первый час после пробуждения (7:15)")
        self.assertFalse(s.own_quiet(at(8, 30, day=9)))
        self.clock.t = at(22, 45)
        m.state["name"] = "Arkady"                                 # меньшее имя заговаривает первым
        m.state["players"] = [{"name": "Bram", "x": 157, "y": 185, "lv": 30}]
        s.near_since = {"Bram": self.clock.t - 120}
        asyncio.run(s.chat(self.clock.t, m.state))
        self.assertEqual(self.whispers(), [], "тихий час — первым не заговаривает")
        self.clock.t = at(15)
        s.near_since = {"Bram": self.clock.t - 120}
        asyncio.run(s.chat(self.clock.t, m.state))
        self.assertTrue(self.whispers(), "днём — как обычно")

    def test_no_sleep_no_quiet_hours(self):
        m = self.make(quiet=NEVER)
        self.assertFalse(m.social.own_quiet(at(23)))
        self.assertFalse(m.social.own_quiet(at(8)))


if __name__ == "__main__":
    unittest.main()
