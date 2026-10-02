"""Мемуары жителя (memoir.py, ORG-082): главы по неделям от первого лица только по фактам памяти.

Память во временной раскладке лаборатории (state/bot01/memory.sqlite), события с заданным временем; LLM —
подменный вызов. Запуск: cd brain && python3 -m unittest -v tests.test_memoir
"""
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain import memoir
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZ = 3
WEEK1 = datetime(2026, 9, 14, tzinfo=timezone(timedelta(hours=TZ))).timestamp()   # понедельник, 2026-W38
H, D = 3600, 86400


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeBudget:
    def __init__(self, why=None):
        self.why = why
        self.settled = 0

    def reserve(self, provider, max_calls, max_usd=None, now=None):
        return (None, self.why) if self.why else (1, None)

    def settle(self, call_id, cost):
        self.settled += 1

    def close(self):
        pass


class MemoirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "state" / "bot01").mkdir(parents=True)
        self.mem = Memory(self.root / "state" / "bot01" / "memory.sqlite")

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def add(self, hours, kind, **data):
        self.mem.add_event(kind, data)
        self.mem.db.execute("UPDATE events SET ts = ? WHERE id = (SELECT MAX(id) FROM events)", (WEEK1 + hours * H,))
        self.mem.db.commit()

    def life(self, sex="Male"):
        self.mem.set("last_state", {"name": "Arkady", "sex": sex})
        self.add(10, "in_game")                                              # рождение: первое событие памяти
        for i, lv in enumerate((13, 14, 15)):
            self.add(20 + i, "level_up", level=lv)
        self.add(30, "died", map="prt_fild07")
        self.add(30.01, "death_report", map="prt_fild07", cause="Poring")
        self.add(40, "meeting_confirmed", partner="Vera", map="prontera")
        self.add(41, "gift_received", peer="Vera", item="501", amount=5, got=5)
        self.add(50, "dream_new", dream="стать Knight", type="job2", stages=2)
        self.add(60, "card_found", id="4002", name="Fabre Card", first=True, n=1)
        self.mem.set("places", {"prt_fild05": {"first": WEEK1 + 70 * H}, "prontera": {"first": WEEK1 - D}})
        # неделя 2 — тихая; неделя 3 — смена профессии и мечта сбылась
        self.add(14 * 24 + 5, "job_changed", **{"from": "Swordman", "to": "Knight"})
        self.add(14 * 24 + 6, "dream_done", dream="стать Knight", type="job2", days=14)
        self.add(14 * 24 + 7, "society_quarrel", peer="Vera", cause="отказ в помощи")

    def book(self, **kw):
        db = sqlite3.connect(f"file:{self.mem.path}?mode=ro", uri=True)
        try:
            return memoir.build(db, "Arkady", kw.pop("sex", "Male"), tz_hours=TZ, **kw)
        finally:
            db.close()

    # ---------- главы ----------

    def test_first_chapter_facts(self):
        self.life()
        b = self.book(now=WEEK1 + 3 * 7 * D - H)
        self.assertEqual([c["week"] for c in b["chapters"]], ["2026-W38", "2026-W39", "2026-W40"])
        ch = b["chapters"][0]
        text = " ".join(ch["lines"])
        self.assertEqual(ch["title"], "Рождение")
        self.assertIn("Я появился в мире 2026-09-14", text)
        self.assertIn("Дорос с 12 до 15 уровня.", text)
        self.assertIn("Погиб 1 раз: prt_fild07 (бил Poring).", text)
        self.assertIn("Встречался с Vera.", text)
        self.assertIn("Получил от Vera 5 × Red Potion.", text)
        self.assertIn("Решил, что моя мечта — стать Knight.", text)
        self.assertIn("Нашёл карту Fabre Card — первую в жизни!", text)
        self.assertIn("Впервые побывал: prt_fild05.", text)                 # prontera — раньше этой недели
        self.assertEqual(ch["kinds"]["level_up"], 3)

    def test_quiet_week_and_turns(self):
        self.life()
        b = self.book(now=WEEK1 + 3 * 7 * D - H)
        self.assertEqual(b["chapters"][1]["lines"], ["Тихая неделя: в памяти нет событий."])
        self.assertEqual(b["chapters"][1]["title"], "Неделя 2")
        ch3 = b["chapters"][2]
        self.assertEqual(ch3["title"], "Мечта сбылась")
        self.assertIn("Стал Knight — раньше был Swordman.", ch3["lines"])
        self.assertIn("Поссорился с Vera: отказ в помощи.", ch3["lines"])

    def test_sex_forms(self):
        self.life(sex="Female")
        text = " ".join(self.book(sex="Female", week="2026-W38")["chapters"][0]["lines"])
        self.assertIn("Я появилась в мире", text)
        self.assertIn("Доросла с 12 до 15", text)
        text = " ".join(self.book(sex=None, week="2026-W38")["chapters"][0]["lines"])
        self.assertIn("Я появился(ась) в мире", text)
        self.assertIn("Нашёл/Нашла карту", text)

    def test_only_finished_weeks(self):
        self.life()
        b = self.book(now=WEEK1 + 15 * D, until_week_end=True)
        self.assertEqual([c["week"] for c in b["chapters"]], ["2026-W38", "2026-W39"])

    def test_render_and_cli(self):
        self.life()
        text = memoir.render(self.book(now=WEEK1 + 20 * D))
        self.assertTrue(text.startswith("# Мемуары Arkady"))
        self.assertIn("## Глава 3. Мечта сбылась", text)
        self.assertIn("_Факты: ", text)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = memoir.main(["2026-W40", "--lab-root", str(self.root), "--bot", "bot01", "--write"])
        self.assertEqual(rc, 0)
        self.assertIn("## Глава 3. Мечта сбылась", out.getvalue())
        self.assertNotIn("Глава 1", out.getvalue())
        self.assertTrue((self.root / "state" / "bot01" / "memoir.md").exists())

    def test_empty_memory(self):
        self.assertTrue(memoir.render(self.book()).endswith("В памяти ещё нет событий.\n"))

    # ---------- модуль мозга: раз в неделю ----------

    def test_module_weekly_file(self):
        self.life()

        async def send(a):
            return 1
        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        mind = Mind(Settings.from_env({}), persona, self.mem, send, self.root / "state" / "bot01" / "decisions.jsonl",
                    RuleGate(), peers={"Arkady", "Vera"}, world=WORLD)
        self.assertIsNotNone(mind.memoir)
        clock = Clock(WEEK1 + 15 * D)
        m = memoir.Memoir(mind, dict(WORLD, timezone_offset_hours=TZ), clock=clock)
        m.tick()
        path = self.root / "state" / "bot01" / "memoir.md"
        self.assertIn("## Глава 2. Неделя 2", path.read_text(encoding="utf-8"))
        ev = [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = 'memoir_chapter'")]
        self.assertEqual(ev, [{"week": "2026-W39", "title": "Неделя 2", "chapters": 2}])
        m.next_tick = 0
        clock.t += D
        m.tick()                                                             # та же неделя — не пишет
        self.assertEqual(self.mem.count_events("memoir_chapter", 0), 1)
        clock.t += 7 * D
        m.next_tick = 0
        m.tick()
        self.assertEqual(self.mem.count_events("memoir_chapter", 0), 2)
        self.assertIn("Мечта сбылась", path.read_text(encoding="utf-8"))

    # ---------- LLM: выкл. по умолчанию, фильтр дневника, кэш ----------

    def llm_settings(self):
        return Settings.from_env({"BRAIN_LLM": "openrouter", "OPENROUTER_API_KEY": "k" * 20})

    def test_llm(self):
        self.life()
        ch = dict(self.book(week="2026-W38")["chapters"][0], week="2026-W38")
        calls = []
        text, why = memoir.colorize(ch, "Arkady", Settings.from_env({}), FakeBudget(),
                                    call=lambda *a, **k: calls.append(1))
        self.assertEqual((text, calls), (None, []))
        self.assertIn("BRAIN_LLM=off", why)

        def bad(settings, messages, json_mode=True, max_tokens=None):
            return json.dumps({"text": "Я убил 999 драконов вместе с Baphomet."}), {}, 0.1

        def good(settings, messages, json_mode=True, max_tokens=None):
            calls.append(messages)
            return json.dumps({"text": "Я родился, встретил Vera и нашёл Fabre Card."}), {"cost": 0.001}, 0.1

        budget = FakeBudget()
        text, why = memoir.colorize(ch, "Arkady", self.llm_settings(), budget, call=bad)
        self.assertIsNone(text)
        self.assertIn("999", why)
        self.assertEqual(budget.settled, 1)
        book = {"name": "Arkady", "chapters": [ch]}
        orig = memoir.colorize
        memoir.colorize = lambda c, n, s, b: orig(c, n, s, b, call=good)
        try:
            for _ in range(2):                                               # второй раз — из кэша
                colored, notes = memoir.colored_cached(self.root, "bot01", book, self.llm_settings, FakeBudget)
                self.assertEqual(colored, {"2026-W38": "Я родился, встретил Vera и нашёл Fabre Card."})
        finally:
            memoir.colorize = orig
        self.assertEqual(len(calls), 1)
        self.assertIn("Строго по фактам:", memoir.render(book, colored))


if __name__ == "__main__":
    unittest.main()
