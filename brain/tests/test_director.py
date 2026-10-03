"""Рассказчик мира — режиссёр без LLM (director.py, ORG-086): кто режиссёр, тишина → повод, тяжёлый день → затишье,
лимиты, правдивые слухи, влияние на мотивы и допуск риска, закулисье шины (дашборд — да, летопись — нет).

Настоящий Mind (Arkady) и шина мира во временном каталоге; Vera, Bram и Abel — другие писатели той же шины.
Время — поддельные часы (день мира по timezone_offset_hours goals.json).
Запуск: cd brain && python3 -m unittest -v tests.test_director
"""
import asyncio
import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain import dashboard, world_bus
from live_brain.config import Settings
from live_brain.director import STIR, Director
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZ = timezone(timedelta(hours=WORLD.get("timezone_offset_hours", 0)))
# hush: механика режиссёра (лимиты, чередование, факты поводов) проверяется на прежних числах ORG-086 — частый
# режиссёр без броска и тихих дней; новые умолчания ORG-110 (8 ч, шанс 0.5, 1 в сутки, 12 ч, тихий день) — test_hush.
LEGACY = json.loads(json.dumps(WORLD))
LEGACY["director"].update(quiet_hours=3, max_per_day=2, gap_hours=3, stir_kinds=list(STIR), stir_chance=1.0)


def at(hour, minute=0, day=1):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class DirectorTest(unittest.TestCase):
    def setUp(self, peers=("Arkady", "Vera", "Bram"), env=None, world=LEGACY):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.path = root / "state" / "shared" / "world.sqlite"      # раскладка лаборатории — для дашборда
        self.bus = world_bus.WorldBus(self.path, "Arkady")
        self.others = {n: world_bus.WorldBus(self.path, n) for n in ("Vera", "Bram", "Abel")}
        self.decisions = root / "d.jsonl"
        self.mind = Mind(Settings.from_env(env or {}), persona, self.mem, send, self.decisions, RuleGate(),
                         peers=set(peers), world=world, world_bus_db=self.bus)
        self.clock = Clock(at(14))
        if self.mind.director is not None:
            self.d = Director(self.mind, world, clock=self.clock, rng=random.Random(1))
            self.d.started = self.clock.t - 5 * 3600
            self.d.cfg["cache_seconds"] = 0
            self.mind.director = self.d
        if self.mind.crowd is not None:
            self.mind.crowd.clock = self.clock
        if self.mind.calendar is not None:                         # hush: без тихих дней мира (их — test_hush)
            self.mind.calendar.cal = dict(self.mind.calendar.cal, quiet={"per_week": 0})
        asyncio.run(self.mind.on_message({"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185,
                                          "hp_pct": 100, "lv": 41, "dead": False, "players": []}))
        if self.mind.routine is not None and isinstance(self.mind.routine.st, dict):
            self.mind.routine.st["mode"] = "town"

    def tearDown(self):
        for b in [self.bus, *self.others.values()]:
            b.close()
        self.mem.close()
        self.tmp.cleanup()

    # ---------- помощники ----------

    def tick(self):
        self.d.next_check = 0.0
        asyncio.run(self.d.tick())

    def incidents(self):
        return [r["data"] for r in self.bus.recent("director", 0)]

    def decisions_of(self, event):
        if not self.decisions.exists():
            return []
        rows = [json.loads(line) for line in self.decisions.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r for r in rows if r.get("type") == "director" and r.get("event") == event]

    def present(self, who, minutes_ago=1):
        self.others[who].replace("presence", {"map": "prontera"}, 1, now=self.clock.t - minutes_ago * 60)

    def incident_by(self, who, kind, hours=3, needs=None, risk=1.0, ago=0):
        from live_brain.director import KINDS
        ts = self.clock.t - ago
        self.others[who].publish("director", {"incident": kind, "label": KINDS[kind]["label"], "until": ts + hours * 3600,
                                              "needs": needs or KINDS[kind]["needs"], "risk": risk, "params": {},
                                              "why": "тест"}, 1, now=ts)

    # ---------- кто режиссёр ----------

    def test_director_is_min_name_online(self):
        self.present("Bram")
        self.assertTrue(self.d.is_director())
        self.mind.ctx.peers.add("Abel")
        self.present("Abel")
        self.assertFalse(self.d.is_director(), "Abel онлайн — имя меньше")
        self.present("Abel", minutes_ago=30)
        self.assertTrue(self.d.is_director(), "запись Abel устарела — не онлайн")
        self.mind.routine.st["mode"] = "sleep"
        self.assertFalse(self.d.is_director(), "спящий — не режиссёр")

    def test_sleeping_peer_is_not_online(self):
        """review3: житель с именем меньше спит (presence mode sleep, relog не удался — состояние свежее) —
        сам он не режиссёр (awake), и другие не должны уступать ему роль: иначе режиссёра нет ни у кого."""
        self.mind.ctx.peers.add("Abel")
        self.others["Abel"].replace("presence", {"map": "prontera", "mode": "sleep"}, 1, now=self.clock.t - 60)
        self.assertNotIn("Abel", self.d.online())
        self.assertTrue(self.d.is_director(), "спящий Abel — не онлайн, режиссёр — Arkady")

    # ---------- тишина → повод ----------

    def test_quiet_world_gets_a_nudge_backstage(self):
        self.tick()
        inc = self.incidents()
        self.assertEqual(len(inc), 1)
        self.assertIn(inc[0]["incident"], STIR)
        if self.mind.explorer is not None:
            self.assertEqual(inc[0]["incident"], "expedition", "без фактов о богатстве и гибели — экспедиция")
        self.assertFalse([e for e in world_bus.read_period(self.path, 0, self.clock.t + 1) if e["kind"] == "director"],
                         "закулисье: летопись и серия режиссёра не видят")
        self.assertTrue([e for e in world_bus.read_period(self.path, 0, self.clock.t + 1, backstage=True)
                         if e["kind"] == "director"])
        self.assertEqual(self.bus.read(min_importance=2), [], "важность 1: не в новостях промпта")
        data = dashboard.collect(self.root, [], day=datetime.fromtimestamp(self.clock.t, TZ).strftime("%Y-%m-%d"),
                                 tz_hours=WORLD.get("timezone_offset_hours", 0), now=self.clock.t)
        self.assertTrue([e for e in data["events"] if e["src"] == "режиссёр" and "режиссёр:" in e["text"]])
        self.assertTrue(self.decisions_of("incident"))

    def test_curiosity_boost_follows_character(self):
        self.incident_by("Vera", "expedition")
        self.mind.needs.t["curiosity"] = 1.0
        hi = self.d.boost("curiosity")
        self.mind.needs.t["curiosity"] = 0.2
        lo = self.d.boost("curiosity")
        self.assertGreater(hi, lo)
        self.assertGreater(lo, 1.0)
        self.assertEqual(self.d.boost("wealth"), 1.0, "мотив вне повода не трогается")
        self.assertLessEqual(hi, 1.5)

    def test_busy_world_no_nudge(self):
        self.others["Vera"].publish("level_up", {"level": 20}, 3, now=self.clock.t - 1800)
        self.tick()
        self.assertEqual(self.incidents(), [], "событие полчаса назад — не тишина")

    def test_fresh_brain_does_not_see_quiet(self):
        self.d.started = self.clock.t - 600
        self.tick()
        self.assertEqual(self.incidents(), [])
        self.assertTrue(self.decisions_of("skip"))

    def test_no_nudges_at_night(self):
        self.clock.t = at(3)
        self.d.started = self.clock.t - 5 * 3600
        self.tick()
        self.assertEqual(self.incidents(), [])
        self.assertIn("ночь", self.decisions_of("skip")[-1]["why"])

    def test_not_director_does_nothing(self):
        self.mind.ctx.peers.add("Abel")
        self.present("Abel")
        self.tick()
        self.assertEqual(self.incidents(), [])

    # ---------- лимиты ----------

    def test_limits_one_active_gap_and_per_day(self):
        self.tick()
        self.assertEqual(len(self.incidents()), 1)
        self.clock.t += 40 * 60
        self.tick()
        self.assertEqual(len(self.incidents()), 1, "действует повод — нового нет")
        self.clock.t += 3 * 3600
        self.tick()
        self.assertEqual(len(self.incidents()), 2, "повод кончился, прошло gap_hours — можно")
        self.clock.t += 4 * 3600
        self.tick()
        self.assertEqual(len(self.incidents()), 2, "max_per_day = 2")
        self.assertIn("за сутки", self.decisions_of("skip")[-1]["why"])

    def test_kinds_rotate(self):
        self.present("Bram")
        self.tick()
        self.clock.t += 3 * 3600 + 60
        self.present("Bram")
        self.tick()
        kinds = [i["incident"] for i in self.incidents()]
        self.assertEqual(len(kinds), 2)
        self.assertNotEqual(kinds[0], kinds[1], "давно не бывший вид — раньше")

    def test_limits_counted_across_directors(self):
        self.incident_by("Abel", "contest", hours=1, ago=7200)
        self.incident_by("Bram", "expedition", hours=1, ago=5 * 3600)
        self.tick()
        self.assertEqual(self.incidents()[-1]["incident"], "expedition", "свои записи не появились")
        self.assertEqual(len(self.bus.recent("director", 0)), 2, "чужие решения считаются в лимитах")

    # ---------- тяжёлый день → затишье ----------

    def heavy_day(self):
        self.others["Vera"].publish("death_report", {"map": "prt_fild07"}, 4, now=self.clock.t - 3600)
        self.others["Bram"].publish("death_report", {"map": "prt_fild07"}, 4, now=self.clock.t - 1800)
        self.others["Bram"].publish("map_banned", {"map": "prt_fild07"}, 4, now=self.clock.t - 1700)

    def test_heavy_day_brings_caution(self):
        base = self.mind.needs.risk_tolerance()
        self.heavy_day()
        self.assertEqual(self.d.tension()["score"], 3)
        self.tick()
        inc = self.incidents()[-1]
        self.assertEqual(inc["incident"], "caution")
        self.assertAlmostEqual(self.mind.needs.risk_tolerance(), base * 0.8, places=6)
        self.mind.needs.t["bravery"] = 0.1
        timid = self.d.boost("safety")
        self.mind.needs.t["bravery"] = 0.9
        brave = self.d.boost("safety")
        self.assertGreater(timid, brave, "осторожного затишье трогает сильнее")
        self.assertLess(self.d.boost("curiosity"), 1.0)
        self.clock.t += 7 * 3600
        self.assertEqual(self.d.risk_factor(), 1.0, "после until — как обычно")

    def test_caution_not_repeated(self):
        self.heavy_day()
        self.tick()
        self.clock.t += 40 * 60
        self.tick()
        self.assertEqual([i["incident"] for i in self.incidents()], ["caution"])

    # ---------- поводы по фактам ----------

    def test_help_after_death(self):
        self.others["Vera"].publish("death_report", {"map": "prt_fild08"}, 4, now=self.clock.t - 1800)
        self.tick()
        inc = self.incidents()[-1]
        self.assertEqual(inc["incident"], "help")
        self.assertEqual(inc["params"]["who"], "Vera")
        self.mind.needs.t["generosity"] = 1.0
        self.assertEqual(self.d.boost("care"), 1.5)

    def test_gathering_before_evening_circle(self):
        if self.mind.tradition is None:
            self.skipTest("традиция выключена")
        self.clock.t = at(18, 30)
        self.d.started = self.clock.t - 5 * 3600
        self.tick()
        inc = self.incidents()[-1]
        self.assertEqual(inc["incident"], "gathering")
        lo, hi = self.mind.tradition.window(self.mind.tradition.date(self.clock.t))
        self.assertEqual(inc["until"], hi, "до конца вечернего круга")

    def test_rich_rumor_only_from_facts(self):
        self.assertIsNone(self.d.rich_fact(self.clock.t), "фактов нет — слуха нет")
        opts = [o[0] for o in self.d.stir_options(self.clock.t)]
        self.assertNotIn("rich_rumor", opts)
        self.mem.set("map_stats", {"prt_fild08": {"minutes": 90.0, "kills": 300, "deaths": 0, "exp": 30.0,
                                                  "zeny": 0}})
        self.mind.maps.cache = None
        self.tick()
        inc = self.incidents()[-1]
        self.assertEqual(inc["incident"], "rich_rumor")
        texts = [a.get("text") for a in self.sent if a.get("action") == "whisper"]
        self.assertIn("[info:rich:prt_fild08]", texts, "свой опыт — свой слух")

    def test_rich_rumor_retells_checked_fact(self):
        self.mem.set("map_stats", {"prt_fild08": {"minutes": 90.0, "kills": 10, "deaths": 2, "exp": 1.0, "zeny": 0}})
        self.mind.maps.cache = None
        self.others["Vera"].publish("rumor_checked", {"what": "rich", "map": "prt_fild05", "ok": True}, 3,
                                    now=self.clock.t - 5 * 3600)
        self.tick()
        self.assertEqual(self.incidents()[-1]["incident"], "rich_rumor")
        whispers = [a for a in self.sent if a.get("action") == "whisper"]
        self.assertEqual({a["to"] for a in whispers}, {"Bram"}, "автору слух не пересказывают")
        self.assertEqual(whispers[0]["text"], "[info:rich:prt_fild05:1:Vera]")

    def test_refuted_rumor_is_not_a_fact(self):
        self.others["Vera"].publish("rumor_checked", {"what": "rich", "map": "prt_fild05", "ok": False}, 3,
                                    now=self.clock.t - 3600 * 5)
        self.assertIsNone(self.d.rich_fact(self.clock.t))

    # ---------- житель-слушатель ----------

    def test_follower_hears_once_and_expires(self):
        self.incident_by("Vera", "gathering", hours=1)
        self.mind.needs.t["sociability"] = 1.0
        self.assertEqual(self.d.boost("social"), 1.4)
        self.d.next_check = self.clock.t + 3600
        asyncio.run(self.d.tick())
        asyncio.run(self.d.tick())
        self.assertEqual(len(self.decisions_of("heard")), 1)
        before = self.mind.needs.weighted()["social"]
        self.clock.t += 2 * 3600
        self.assertEqual(self.d.boost("social"), 1.0)
        self.assertLess(self.mind.needs.weighted()["social"], before + 1e-9)

    def test_needs_weighted_uses_director(self):
        plain = self.mind.needs.weighted()["curiosity"]
        self.incident_by("Vera", "expedition")
        self.mind.needs.t["curiosity"] = 1.0
        boosted = self.mind.needs.weighted()["curiosity"]
        if plain:
            self.assertGreater(boosted, plain)

    # ---------- без шины ----------

    def test_no_bus_is_silent(self):
        self.mind.world = None
        self.tick()
        self.assertEqual(self.d.boost("social"), 1.0)
        self.assertEqual(self.d.risk_factor(), 1.0)
        self.assertEqual(self.d.current(), None)


class DirectorSwitchTest(unittest.TestCase):
    def make(self, env=None, world=WORLD):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mem = Memory(Path(tmp.name) / "m.sqlite")
        self.addCleanup(mem.close)

        async def send(a):
            return 1
        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        return Mind(Settings.from_env(env or {}), persona, mem, send, Path(tmp.name) / "d.jsonl", RuleGate(),
                    peers={"Arkady", "Vera"}, world=world)

    def test_switches(self):
        self.assertIsInstance(self.make().director, Director)
        self.assertIsNone(self.make(env={"BRAIN_DISABLE": "director"}).director)
        w = json.loads(json.dumps(WORLD))
        w["director"]["enabled"] = False
        self.assertIsNone(self.make(world=w).director)
        self.assertIsNone(self.make(world=None).director)


if __name__ == "__main__":
    unittest.main()
