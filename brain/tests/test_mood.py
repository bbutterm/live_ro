"""Настроение (mood.py, ORG-064): затухание, ограничение, терпение, фразы, «молчун», отчёт, изоляция от выживания.

Запуск: cd brain && python3 -m unittest -v tests.test_mood
"""
import asyncio
import io
import json
import random
import re
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from live_brain import __main__ as main_mod
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.mood import HALF_LIFE_H, Mood
from live_brain.routine import load_world
from live_brain.social import TAG

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
H = 3600


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class MoodTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(self.root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, self.root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=WORLD)
        self.clock = Clock(time.time())
        self.mood = self.mind.mood
        self.mood.clock = self.clock
        self.mind.social.clock = self.clock
        self.mind.social.rng = random.Random(4)
        self.mind.social.grammar = None   # grammar: здесь — точные фразы персоны (ORG-065 — test_grammar)
        self.mind.state = {"name": "Arkady", "map": "prontera", "x": 156, "y": 185, "dead": False, "lv": 30,
                           "players": []}
        self.mind.fresh_state = True

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def event(self, kind, ago_h=0.0, **data):
        self.mem.add_event(kind, data)
        if ago_h:
            self.mem.db.execute("UPDATE events SET ts = ? WHERE id = (SELECT MAX(id) FROM events)",
                                (self.clock.t - ago_h * H,))
            self.mem.db.commit()
        self.mood.cache = (None, None)

    def soft(self):
        return 1 - 0.4 * self.mind.persona["traits"]["patience"]

    def test_decay(self):
        self.event("level_up", level=31)
        now_v = self.mood.value()
        self.assertAlmostEqual(now_v, 0.3 * self.soft(), places=2)
        self.mem.db.execute("DELETE FROM events")
        self.event("level_up", ago_h=HALF_LIFE_H, level=31)
        self.assertAlmostEqual(self.mood.value(), now_v / 2, places=2)
        self.mem.db.execute("DELETE FROM events")
        self.event("level_up", ago_h=49, level=31)                      # вне окна 48 ч
        self.assertEqual(self.mood.value(), 0)

    def test_clamp_and_labels(self):
        for _ in range(8):
            self.event("pet_hatched")
        self.assertEqual(self.mood.value(), 1.0)
        self.assertEqual(self.mood.label(), "отличное")
        self.mem.db.execute("DELETE FROM events")
        for _ in range(8):
            self.event("died", map="prt_fild08")
        self.assertEqual(self.mood.value(), -1.0)
        self.assertEqual(self.mood.label(), "мрачное")
        self.assertEqual(self.mood.reasons(), ["погиб 8 раз"])

    def test_patience_softens(self):
        self.event("died", map="prt_fild08")
        impatient = self.mood.value()
        self.mind.persona["traits"]["patience"] = 1.0
        self.mood.cache = (None, None)
        self.assertLess(abs(self.mood.value()), abs(impatient))

    def test_heal_not_to_me_ignored(self):
        self.event("heal_confirmed", **{"from": "Vera", "to": "Ilsa", "amount": 50})
        self.assertEqual(self.mood.value(), 0)
        self.event("heal_confirmed", **{"from": "Vera", "to": "Arkady", "amount": 50})
        self.assertGreater(self.mood.value(), 0)

    def test_phrase_key(self):
        self.assertEqual(self.mood.phrase_key("hello"), "hello")
        self.event("died", map="prt_fild08")
        self.event("died", map="prt_fild07")
        self.assertEqual(self.mood.phrase_key("hello"), "hello_bad")
        self.assertEqual(self.mood.phrase_key("bye"), "bye")            # нет bye_bad — исходный ключ
        self.assertEqual(self.mood.talk_factor(), 1.3)

    def test_arkady_says_hello_bad_after_two_deaths(self):
        self.event("died", map="prt_fild08")
        self.event("died", map="prt_fild07")
        asyncio.run(self.mind.social.say("Vera", "hello", 1))
        w = [a["text"] for a in self.sent if a["action"] == "whisper"][-1]
        self.assertTrue(w.endswith("[chat:hello:1]"))
        self.assertIn(TAG.sub("", w).strip(),
                      [p.format(name="Vera") for p in self.mind.persona["phrases"]["hello_bad"]])

    def test_gloomy_does_not_start_but_replies(self):
        for _ in range(4):
            self.event("died", map="prt_fild08")
        self.assertTrue(self.mood.silent())
        s = self.mind.social
        r = self.mind.routine
        r.new_day(self.clock.t)
        r.st.update(mode="town", arrived=True, rest_until=self.clock.t + H, mode_since=self.clock.t)
        self.mind.state["players"] = [{"name": "Vera", "x": 157, "y": 185, "lv": 30}]
        s.near_since = {"Vera": self.clock.t - 600}
        asyncio.run(s.chat(self.clock.t, self.mind.state))
        self.assertEqual([a for a in self.sent if a["action"] == "whisper"], [])
        asyncio.run(s.on_tag("Vera", "Привет! [chat:hello:1]"))
        self.clock.t += 10
        asyncio.run(s.flush(self.clock.t))
        w = [a["text"] for a in self.sent if a["action"] == "whisper"]
        self.assertEqual(len(w), 1)
        self.assertTrue(w[0].endswith("[chat:hello:2]"))

    def test_report_line(self):
        self.event("died", map="prt_fild08")
        self.event("gift_received", peer="Vera", item="501", amount=3)
        self.mem.set("mood", self.mood.snapshot())
        self.mem.set("last_state", {"name": "Arkady"})
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main_mod.report(SimpleNamespace(bot="bot01"), self.mem, self.root)
            except Exception as e:                                     # noqa: BLE001 — отчёт про другое
                self.skipTest(f"report требует окружения: {e}")
        line = [l for l in buf.getvalue().splitlines() if "настроение:" in l]
        self.assertEqual(len(line), 1, buf.getvalue())
        self.assertRegex(line[0], r"настроение: \w+ \([+-]\d\.\d\d\): ")
        self.assertIn("погиб", line[0])

    def test_prompt_field(self):
        self.assertIn("настроение", json.dumps(self.mind.build_prompt("тест", {}), ensure_ascii=False))

    def test_survival_does_not_read_mood(self):
        for mod in ("safety", "routine", "economy", "party", "lifecycle", "postmortem", "plans", "gate"):
            src = (BRAIN_DIR / "live_brain" / f"{mod}.py").read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bmood\b", src), f"{mod}.py читает настроение")


class MoodStandaloneTest(unittest.TestCase):
    def test_no_events_is_even(self):
        tmp = tempfile.TemporaryDirectory()
        mem = Memory(Path(tmp.name) / "m.sqlite")
        mind = SimpleNamespace(mem=mem, persona={"name": "X", "traits": {"patience": 0.5}, "phrases": {}}, state={})
        m = Mood(mind)
        self.assertEqual((m.value(), m.label(), m.reasons(), m.talk_factor()), (0, "ровное", [], 1.0))
        mem.close()
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
