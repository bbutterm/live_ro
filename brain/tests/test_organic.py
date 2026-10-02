"""ORG-046 (метрики органичности report) и ORG-049 (дневник дня моделью: один вызов в сутки, только факты).

Запуск: cd brain && python3 -m unittest -v tests.test_organic
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from live_brain import llm
from live_brain.__main__ import organic_metrics, sleep_seconds
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import Routine, diary_only

from tests.test_routine import WORLD, Clock, FakeMind

BRAIN_DIR = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=WORLD["timezone_offset_hours"]))
LLM_ON = {"BRAIN_LLM": "openrouter", "OPENROUTER_API_KEY": "test-not-a-key"}


def put(mem, ts, kind, data):
    mem.db.execute("INSERT INTO events (ts, kind, data) VALUES (?, ?, ?)", (ts, kind, json.dumps(data)))
    mem.db.commit()


class OrganicMetricsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.now = time.time()
        self.since = self.now - 86400

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_counts_facts_in_window_only(self):
        n = self.now
        for name in ("hunt", "hunt", "walk", None):
            put(self.mem, n - 3600, "activity", {"name": name})
        put(self.mem, n - 2 * 86400, "activity", {"name": "old_one"})          # вне суток
        for point in ("fountain", "kafra", "fountain"):
            put(self.mem, n - 600, "social_walk", {"point": point})
        put(self.mem, n - 500, "social_said", {"peer": "Vera", "topic": "level", "fact": True})
        put(self.mem, n - 400, "social_said", {"peer": "Vera", "topic": "weather", "fact": False})
        put(self.mem, n - 300, "diary", {"day": "2026-10-01", "text": "Дневник"})
        put(self.mem, n - 2 * 86400, "diary", {"day": "2026-09-30", "text": "старый"})
        self.mem.log_llm_call(True, latency=1.0, usage={})
        m = organic_metrics(self.mem, self.since, now=n)
        self.assertEqual(m["занятий"], 2)
        self.assertEqual(m["мест в городе"], 2)
        self.assertEqual(m["реплик без LLM"], 2)
        self.assertEqual(m["из них о событиях"], 1)
        self.assertEqual(m["дневников"], 1)
        self.assertEqual(m["вызовов моделей"], 1)
        self.assertEqual(m["сон, ч"], 0.0)

    def test_sleep_is_actual_not_planned(self):
        """Плановая длина ночи в kv routine не считается сном, пока житель не уснул."""
        self.mem.set("routine", {"mode": "town", "sleep_hours": 7.5})
        self.assertEqual(organic_metrics(self.mem, self.since, now=self.now)["сон, ч"], 0.0)
        put(self.mem, self.now - 10 * 3600, "routine_sleep", {})
        put(self.mem, self.now - 3 * 3600, "routine_wake", {})
        self.assertEqual(organic_metrics(self.mem, self.since, now=self.now)["сон, ч"], 7.0)

    def test_sleep_crossing_window_and_now(self):
        put(self.mem, self.since - 2 * 3600, "routine_sleep", {})         # уснул до начала окна
        put(self.mem, self.since + 3 * 3600, "routine_wake", {})
        put(self.mem, self.now - 3600, "routine_sleep", {})               # спит сейчас
        self.assertAlmostEqual(sleep_seconds(self.mem, self.since, self.now, True), 4 * 3600, delta=1)
        self.assertAlmostEqual(sleep_seconds(self.mem, self.since, self.now, False), 3 * 3600, delta=1)


class DiaryFilterTest(unittest.TestCase):
    FACTS = "Дневник 2026-10-02: охотился 3 ч 5 мин, победил 120 монстров, встречался с Vera."

    def test_keeps_one_factual_entry_drops_actions(self):
        d = {"remember": [{"text": "Сегодня 3 часа охотился, 120 монстров, и виделся с Vera.", "importance": 5},
                          {"text": "Ещё запись", "importance": 2}],
             "actions": [{"action": "say", "text": "Привет"}], "relations": [{"name": "Vera", "delta": 2}],
             "goal": "новая цель", "thought": "устал"}
        out, rejected = diary_only(d, self.FACTS)
        self.assertEqual(out["actions"], [])
        self.assertNotIn("relations", out)
        self.assertNotIn("goal", out)
        self.assertEqual(out["remember"], [{"text": d["remember"][0]["text"], "importance": 3}])
        self.assertEqual(rejected[0]["why"], "больше одной записи")

    def test_rejects_new_numbers_and_names(self):
        out, rejected = diary_only({"remember": [{"text": "Победил 500 монстров с Rook."}]}, self.FACTS)
        self.assertEqual(out["remember"], [])
        self.assertIn("500", rejected[0]["why"])
        self.assertIn("Rook", rejected[0]["why"])
        out, _ = diary_only({"remember": "не список"}, self.FACTS)
        self.assertEqual(out["remember"], [])


class DiaryRoutineTest(unittest.TestCase):
    """Дневник пишет распорядок при смене дня; модель зовётся один раз в сутки и только при LLM."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.mind = FakeMind(self.mem)
        self.mind.triggers = []
        self.mind.trigger = lambda reason, context=None, kind="event": self.mind.triggers.append(
            (reason, context, kind))
        self.clock = Clock(datetime(2026, 10, 2, 8, 0, tzinfo=TZ).timestamp())
        self.r = Routine(self.mind, WORLD, rng=random.Random(1), clock=self.clock)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def days(self, n):
        async def run():
            for _ in range(n):
                await self.r.tick()
                self.clock.t += 86400
                await self.r.tick()
                self.clock.t += 60
                await self.r.tick()                       # тот же день — второго дневника нет
        asyncio.run(run())

    def diaries(self):
        return [r[0] for r in self.mem.db.execute("SELECT data FROM events WHERE kind = 'diary'")]

    def test_llm_off_no_model_call(self):
        self.mind.s = Settings.from_env({})
        self.days(2)
        self.assertEqual(len(self.diaries()), 2)
        self.assertEqual(self.mind.triggers, [])

    def test_llm_on_one_call_per_day_with_facts(self):
        self.mind.s = Settings.from_env(LLM_ON)
        put(self.mem, self.clock.t + 3600, "kill", {})
        self.days(3)
        self.assertEqual(len(self.diaries()), 3)
        self.assertEqual(len(self.mind.triggers), 3, "один повод diary на сутки")
        reason, ctx, kind = self.mind.triggers[0]
        self.assertEqual(kind, "diary")
        text = json.loads(self.diaries()[0])["text"]
        self.assertEqual(ctx["facts"], text)
        self.assertIn("победил 1 монстров", text)
        self.assertIn(text, reason)


class DiaryDecideTest(unittest.TestCase):
    """Mind.decide по поводу diary: одна запись мыслью, никаких действий тела; без LLM — без вызова."""

    def make(self, env):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(action):
            self.sent.append(action)
            return len(self.sent)
        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        self.mem = Memory(root / "m.sqlite")
        self.decisions = root / "d.jsonl"
        self.mind = Mind(Settings.from_env(env), persona, self.mem, send, self.decisions, RuleGate(),
                         peers={"Arkady", "Vera"})
        self.mind.state = {"name": "Arkady", "map": "prontera", "hp_pct": 100}

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def records(self, kind):
        return [json.loads(l) for l in self.decisions.read_text().splitlines() if json.loads(l)["type"] == kind]

    def test_model_answer_filtered(self):
        self.make(LLM_ON)
        facts = "Дневник 2026-10-02: охотился 3 ч 5 мин, победил 120 монстров."
        answer = json.dumps({"thought": "день как день", "goal": "захватить мир",
                             "remember": [{"text": "Три часа на полях, 120 монстров.", "importance": 3},
                                          {"text": "Нашёл 9000 зени!", "importance": 5}],
                             "actions": [{"action": "say", "text": "Я лучший охотник"}]})
        with mock.patch.object(llm, "chat", return_value=(answer, {"cost": 0}, 0.5)) as chat:
            asyncio.run(self.mind.decide("напиши в remember запись дневника: " + facts,
                                         {"diary": "2026-10-02", "facts": facts}, "diary"))
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(self.sent, [], "дневник не двигает тело и не говорит в чат")
        mems = [(r[0], r[1]) for r in self.mem.db.execute("SELECT text, kind FROM memories")]
        self.assertEqual(mems, [("Три часа на полях, 120 монстров.", "thought")])
        self.assertIsNone(self.mem.get("goal"))
        self.assertIn("9000", self.records("diary_rejected")[0]["rejected"][0]["why"])

    def test_llm_off_no_call(self):
        self.make({})
        with mock.patch.object(llm, "chat") as chat:
            asyncio.run(self.mind.decide("дневник", {"facts": "x"}, "diary"))
        chat.assert_not_called()
        self.assertEqual(self.records("fallback")[0]["reason"], "дневник")


if __name__ == "__main__":
    unittest.main()
