"""Календарь мира (world_calendar.py, ORG-059): дни недели, праздники, дни рождения, множитель мотивов,
темы holiday/birthday в разговоре, заголовок дня в летописи.

Запуск: cd brain && python3 -m unittest -v tests.test_calendar
"""
import asyncio
import json
import random
import string
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from live_brain import world_calendar as wc
from live_brain.chronicle import chronicle
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.safety import MAX_TEXT

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZH = WORLD["timezone_offset_hours"]
TZ = timezone(timedelta(hours=TZH))


def ts(date, hour=14):
    return datetime.strptime(date, "%Y-%m-%d").replace(hour=hour, tzinfo=TZ).timestamp()


SAT = "2026-09-26"          # суббота
HARVEST = "2026-09-23"      # среда, Праздник урожая
FRI = "2026-10-02"          # пятница, без праздника
ROSTER = {"residents": {"bot01": {"name": "Arkady", "born": None},
                        "bot02": {"name": "Vera", "born": "2025-10-02"},
                        "bot03": {"name": "Leap", "born": "2024-02-29"}}}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class CalendarRulesTest(unittest.TestCase):
    def setUp(self):
        self.cal = wc.load()

    def test_file_valid(self):
        self.assertTrue(self.cal, "calendar.json читается")
        self.assertTrue(set(self.cal["weekdays"]) <= {str(i) for i in range(1, 8)})
        months = sorted(m for s in self.cal["seasons"] for m in s["months"])
        self.assertEqual(months, list(range(1, 13)), "каждый месяц — ровно в одном сезоне")
        ids = set()
        for h in self.cal["holidays"]:
            datetime.strptime("2024-" + h["date"], "%Y-%m-%d")
            self.assertNotIn(h["id"], ids)
            ids.add(h["id"])
        for src in [d.get("needs", {}) for d in self.cal["weekdays"].values()] + [h.get("needs", {})
                                                                                  for h in self.cal["holidays"]]:
            for need, f in src.items():
                self.assertIn(need, ("safety", "supply", "progress", "social", "curiosity", "rest", "wealth", "care"))
                self.assertTrue(wc.FACTOR_MIN <= f <= wc.FACTOR_MAX)

    def test_saturday_market_day(self):
        day = wc.today(ts(SAT), TZH, self.cal)
        self.assertEqual((day["weekday"], day["weekday_name"], day["day_name"]), (6, "суббота", "рыночный день"))
        self.assertEqual(wc.need_factor(day, "wealth"), 1.2)
        self.assertEqual(wc.need_factor(day, "safety"), 1.0)
        self.assertEqual(day["season"], "autumn")
        self.assertEqual(wc.need_factor(wc.today(ts(FRI), TZH, self.cal), "wealth"), 1.0)

    def test_limits(self):
        cal = {"weekdays": {"6": {"needs": {"wealth": 1.4, "rest": 0.5}}},
               "holidays": [{"date": "09-26", "id": "x", "name": "X", "needs": {"wealth": 1.4, "rest": 0.9}}]}
        day = wc.today(ts(SAT), TZH, cal)
        self.assertEqual(wc.need_factor(day, "wealth"), wc.FACTOR_MAX)
        self.assertEqual(wc.need_factor(day, "rest"), wc.FACTOR_MIN)
        self.assertEqual(wc.need_factor({}, "rest"), 1.0)

    def test_holiday_by_date_and_tz(self):
        day = wc.today(ts(HARVEST), TZH, self.cal)
        self.assertEqual([h["id"] for h in day["holidays"]], ["harvest"])
        self.assertEqual(wc.need_factor(day, "social"), 1.3)
        late = ts("2026-09-22", 23) + 1800          # 23:30 по миру 22-го — ещё не праздник, хотя в UTC тоже 22-е
        self.assertEqual(wc.today(late, TZH, self.cal)["holidays"], [])
        early = ts(HARVEST, 0) + 600                # 00:10 по миру 23-го (в UTC ещё 22-е) — уже праздник
        self.assertEqual([h["id"] for h in wc.today(early, TZH, self.cal)["holidays"]], ["harvest"])

    def test_birthdays_from_roster(self):
        day = wc.today(ts(FRI), TZH, self.cal, ROSTER)
        self.assertEqual(day["birthdays"], ["Vera"], "born: null — не именинник")
        self.assertEqual(wc.today(ts("2026-02-28"), TZH, self.cal, ROSTER)["birthdays"], ["Leap"],
                         "29 февраля в невисокосный год — 28-го")
        self.assertEqual(wc.today(ts("2028-02-28"), TZH, self.cal, ROSTER)["birthdays"], [])
        self.assertEqual(wc.today(ts("2028-02-29"), TZH, self.cal, ROSTER)["birthdays"], ["Leap"])

    def test_world_anniversary(self):
        day = wc.today(ts(FRI), TZH, self.cal, world_born="2025-10-02")
        self.assertIn("День мира (1-я годовщина)", [h["name"] for h in day["holidays"]])
        self.assertEqual(wc.today(ts("2025-10-02"), TZH, self.cal, world_born="2025-10-02")["holidays"], [],
                         "в сам день рождения мира годовщины нет")

    def test_header(self):
        self.assertEqual(wc.header(wc.today(ts(SAT), TZH, self.cal)), "суббота, рыночный день (осень)")
        text = wc.header(wc.today(ts(HARVEST), TZH, self.cal, ROSTER))
        self.assertIn("среда", text)
        self.assertIn("Праздник урожая", text)

    def test_phrases_fit(self):
        """Фразы по умолчанию: без метки ≤ 60, с меткой [chat:birthday:4] ≤ MAX_TEXT при имени в 23 символа."""
        longest = max([h["name"] for h in self.cal["holidays"]] + ["День мира (10-я годовщина)"], key=len)
        facts = {"holiday": longest, "birthday": "W" * 23, "name": "W" * 23}
        for key, items in wc.PHRASES.items():
            for p in items:
                fields = {f for _, f, _, _ in string.Formatter().parse(p) if f}
                self.assertTrue(fields <= set(facts), p)
                text = p.format(**facts)
                self.assertLessEqual(len(text), 60, text)
                self.assertLessEqual(len(f"{text} [chat:birthday:4]"), MAX_TEXT, text)


class CalendarMindTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.persona = persona
        self.send = send
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=WORLD)
        self.clock = Clock(ts(FRI))
        self.mind.calendar = wc.WorldCalendar(self.mind, WORLD, roster=ROSTER, clock=self.clock)
        self.mind.social.clock = self.clock
        self.mind.social.rng = random.Random(1)
        self.mind.social.grammar = None   # grammar: здесь — точные фразы персоны (ORG-065 — test_grammar)
        self.mind.needs.clock = self.clock
        self.mind.state.update(name="Arkady", zeny=1000, hp_pct=100)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_created_and_switch(self):
        self.assertIsInstance(Mind(Settings.from_env({}), self.persona, self.mem, self.send, self.root / "d.jsonl",
                                   RuleGate(), peers={"Vera"}, world=WORLD).calendar, wc.WorldCalendar)
        off = Mind(Settings.from_env({"BRAIN_DISABLE": "calendar"}), self.persona, self.mem, self.send,
                   self.root / "d.jsonl", RuleGate(), peers={"Vera"}, world=WORLD)
        self.assertIsNone(off.calendar)
        off2 = Mind(Settings.from_env({}), self.persona, self.mem, self.send, self.root / "d.jsonl", RuleGate(),
                    peers={"Vera"}, world=dict(WORLD, calendar={"enabled": False}))
        self.assertIsNone(off2.calendar)

    def test_needs_weighted_only_factor(self):
        """needs.weighted меняется только множителем дня: суббота — wealth ×1.2, safety как было."""
        cal = self.mind.calendar
        self.mind.calendar = None
        plain = self.mind.needs.weighted()
        self.mind.calendar = cal
        self.clock.t = ts(FRI)
        self.assertEqual(self.mind.needs.weighted(), plain, "пятница без праздника — без изменений")
        self.clock.t = ts(SAT)
        self.mind.calendar = None
        plain = self.mind.needs.weighted()
        self.mind.calendar = cal
        sat = self.mind.needs.weighted()
        self.assertGreater(plain["wealth"], 0)
        self.assertAlmostEqual(sat["wealth"], round(plain["wealth"] * 1.2, 2), delta=0.011)
        self.assertAlmostEqual(sat["social"], round(plain["social"] * 1.2, 2), delta=0.011)
        self.assertEqual(sat["safety"], plain["safety"])

    def test_birthday_greeting_once_a_day(self):
        social = self.mind.social
        facts = social.facts("Vera", self.clock.t)
        self.assertEqual(facts.get("birthday"), "Vera")
        self.assertIn("birthday", social.available(facts))
        self.assertNotIn("birthday", social.facts("Arkady", self.clock.t), "у Arkady born: null")
        ok = asyncio.run(social.say("Vera", "birthday", 3, self.clock.t))
        self.assertTrue(ok)
        whisper = [a for a in self.sent if a.get("action") == "whisper"][-1]
        self.assertIn("[chat:birthday:3]", whisper["text"])
        self.assertIn("Vera", whisper["text"])
        self.assertLessEqual(len(whisper["text"]), MAX_TEXT)
        self.clock.t += 3600
        self.assertNotIn("birthday", social.facts("Vera", self.clock.t), "второй раз за день не поздравляет")
        self.assertEqual(self.mem.get("calendar_told")["Vera"]["topics"], ["birthday"])
        self.clock.t = ts("2027-10-02")                       # через год — снова день рождения
        self.assertEqual(social.facts("Vera", self.clock.t).get("birthday"), "Vera")

    def test_holiday_topic_once_per_peer(self):
        social = self.mind.social
        self.clock.t = ts(HARVEST)
        self.assertEqual(social.facts("Vera", self.clock.t).get("holiday"), "Праздник урожая")
        asyncio.run(social.say("Vera", "holiday", 3, self.clock.t))
        whisper = [a for a in self.sent if a.get("action") == "whisper"][-1]
        self.assertIn("[chat:holiday:3]", whisper["text"])
        self.assertIsNone(self.mind.calendar.holiday_facts("Vera", self.clock.t))
        self.assertEqual(self.mind.calendar.holiday_facts("Bram", self.clock.t), {"holiday": "Праздник урожая"},
                         "другому жителю — можно")
        self.clock.t = ts("2026-09-24")
        self.assertIsNone(self.mind.calendar.holiday_facts("Vera", self.clock.t), "праздник прошёл")

    def test_install_into_topic_registry(self):
        got = {}
        fake = SimpleNamespace(register_topic=lambda name, provider, reply=None: got.setdefault(name, provider))
        self.assertTrue(wc.install(fake, self.mind))
        self.assertEqual(sorted(got), ["birthday", "holiday"])
        self.assertEqual(got["birthday"]("Vera", self.clock.t), {"birthday": "Vera"})
        self.assertFalse(wc.install(SimpleNamespace(), self.mind), "реестра тем нет — не подключается")

    def test_prompt_has_day(self):
        self.clock.t = ts(SAT)
        self.assertEqual(self.mind.calendar.summary(), "суббота, рыночный день (осень)")


class CalendarChronicleTest(unittest.TestCase):
    def test_chronicle_day_header(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        text = chronicle(tmp.name, ["bot01"], day=SAT, tz_hours=TZH)
        self.assertIn("День мира: суббота, рыночный день", text)
        self.assertIn("День мира: среда, день странствий; Праздник урожая", chronicle(tmp.name, ["bot01"],
                                                                                    day=HARVEST, tz_hours=TZH))


if __name__ == "__main__":
    unittest.main()
