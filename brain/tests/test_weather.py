"""Погода мира (ORG-085): общий детерминированный факт, тема weather через реестр тем, множитель прогулки.

Запуск: cd brain && python3 -m unittest -v tests.test_weather
"""
import asyncio
import json
import random
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from live_brain import weather
from live_brain.activity import Activities
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.social import TAG

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZH = WORLD["timezone_offset_hours"]
TZ = timezone(timedelta(hours=TZH))


def local(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=TZ).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class WeatherFunctionTest(unittest.TestCase):
    def test_deterministic_and_shared(self):
        t = local(2026, 10, 2, 14) + 1234
        self.assertEqual(weather.weather(t, TZH), weather.weather(t, TZH))
        w = weather.weather(t, TZH)
        self.assertIn(w["kind"], weather.KINDS)
        self.assertEqual(w["label"], weather.LABEL[w["kind"]])
        self.assertLessEqual(w["since_hour"], 14)
        self.assertNotEqual([weather.weather(t + d * 86400, TZH)["kind"] for d in range(30)],
                            [w["kind"]] * 30, "погода не одна и та же весь месяц")

    def test_changes_at_most_every_3_hours(self):
        start = local(2026, 1, 1)
        kinds = [weather.weather(start + h * 3600 + 60, TZH)["kind"] for h in range(24 * 60)]
        for h in range(1, len(kinds)):
            if kinds[h] != kinds[h - 1]:
                self.assertEqual(h % weather.BLOCK_H, 0, f"смена не на границе блока: час {h}")
        for day in range(59):
            window = kinds[day * 24:(day + 1) * 24 + 1]
            self.assertLessEqual(sum(a != b for a, b in zip(window, window[1:])), 8)

    def test_season_distribution(self):
        def year_counts(month):
            c = Counter()
            for y in range(2026, 2030):
                for d in range(1, 29):
                    for h in range(0, 24, 3):
                        c[weather.weather(local(y, month, d, h) + 60, TZH)["kind"]] += 1
            return c
        jan, jul = year_counts(1), year_counts(7)
        self.assertEqual(jan["hot"], 0, "в январе нет жары")
        self.assertEqual(jul["cold"], 0, "в июле нет холода")
        self.assertGreater(jan["cold"], jul["cold"])
        self.assertGreater(jul["hot"], 50)
        self.assertGreaterEqual(len(jul), 4, "летом погода разная")

    def test_stroll_factor(self):
        self.assertEqual(weather.stroll_factor("rain"), 0.8)
        self.assertEqual(weather.stroll_factor("clear"), 1.1)
        self.assertEqual(weather.stroll_factor("cloudy"), 1.0)
        self.assertEqual(weather.stroll_factor(None), 1.0)


class WeatherTalkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock(local(2026, 7, 15, 13) + 600)
        self.minds, self.sent = {}, {}
        for bot, name in (("bot01", "Arkady"), ("bot02", "Vera")):
            self.minds[name] = self.make(bot, name)

    def make(self, bot, name, env=None, persona=None):
        sent = self.sent.setdefault(name, [])

        async def send(a):
            sent.append(dict(a))
            return len(sent)

        persona = persona or json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        persona.pop("sleep", None)
        mem = Memory(self.root / f"{bot}-{len(self.sent[name])}-{id(persona)}.sqlite")
        mind = Mind(Settings.from_env(env or {}), persona, mem, send, self.root / f"{bot}.jsonl", RuleGate(),
                    peers={"Arkady", "Vera"}, world=WORLD)
        mind.social.clock = self.clock
        mind.social.rng = random.Random(1)
        mind.social.grammar = None   # grammar: здесь — точные фразы персоны (ORG-065 — test_grammar)
        mind.safety.whisper_gap = 0
        mind.state = {"name": name, "map": "prontera", "x": 156, "y": 185, "dead": False, "lv": 30}
        self.addCleanup(mem.close)
        return mind

    def tearDown(self):
        self.tmp.cleanup()

    def said(self, name):
        w = [a for a in self.sent[name] if a["action"] == "whisper"][-1]["text"]
        return TAG.sub("", w).strip(), TAG.search(w)

    def test_two_residents_same_weather(self):
        kind = weather.weather(self.clock.t, TZH)["kind"]
        for name, peer in (("Arkady", "Vera"), ("Vera", "Arkady")):
            m = self.minds[name]
            self.assertTrue(asyncio.run(m.social.say(peer, "weather", 3)))
            text, tag = self.said(name)
            self.assertEqual(tag.group(1), "weather")
            self.assertIn(text, [p.format(name=peer) for p in m.persona["phrases"][f"weather_{kind}"]],
                          f"{name}: фраза не про {kind}")

    def test_reply_agrees(self):
        v = self.minds["Vera"]
        asyncio.run(v.social.on_tag("Arkady", "Дождь. [chat:weather:3]"))
        self.clock.t += 10
        asyncio.run(v.social.flush(self.clock.t))
        text, tag = self.said("Vera")
        self.assertEqual((tag.group(1), tag.group(2)), ("weather", "4"))
        label = weather.weather(self.clock.t, TZH)["label"]
        self.assertIn(text, [p.format(name="Arkady", weather=label) for p in v.persona["phrases"]["weather_re"]])

    def test_old_persona_and_disabled(self):
        old = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        old["phrases"] = {k: v for k, v in old["phrases"].items() if not k.startswith("weather_")}
        m = self.make("bot01", "Arkady", persona=old)
        asyncio.run(m.social.say("Vera", "weather", 3))
        self.assertIn(self.said("Arkady")[0], [p.format(name="Vera") for p in old["phrases"]["weather"]])
        off = self.make("bot01", "Arkady", env={"BRAIN_DISABLE": "weather"})
        self.assertIsNone(weather.kind_now(off, self.clock.t))
        self.sent["Arkady"].clear()
        self.clock.t += 20
        asyncio.run(off.social.say("Vera", "weather", 3))
        self.assertIn(self.said("Arkady")[0], [p.format(name="Vera") for p in old["phrases"]["weather"]])

    def test_stroll_gets_factor(self):
        m = self.minds["Arkady"]
        a = Activities(m, clock=self.clock, rng=random.Random(3))
        r = m.routine
        r.new_day(self.clock.t)
        r.st.update(mode="town", arrived=True, rest_until=self.clock.t + 3600, mode_since=self.clock.t)
        m.fresh_state = True
        m.state.update(hp_pct=100, weight_pct=10, zeny=1000, items={})
        got = {}
        for kind in ("rain", "clear", "cloudy"):
            a.rng = random.Random(3)
            with mock.patch.object(weather, "kind_now", return_value=kind), \
                    mock.patch.object(m.needs, "noise", return_value=0.0):
                scores, _ = a.scores(m.state)
            if "stroll" not in scores:
                self.skipTest("прогулка недоступна в этом состоянии")
            got[kind] = scores["stroll"]
        self.assertAlmostEqual(got["rain"], round(got["cloudy"] * 0.8, 3), places=2)
        self.assertAlmostEqual(got["clear"], round(got["cloudy"] * 1.1, 3), places=2)


if __name__ == "__main__":
    unittest.main()
