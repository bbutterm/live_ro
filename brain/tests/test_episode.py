"""Сериал-хроника: серия недели (episode.py, ORG-091) — только чтение памяти и шины, правила без LLM.

Две памяти жителей и шина мира во временном каталоге; время событий подменяется (ts в таблице events).
Запуск: cd brain && python3 -m unittest -v tests.test_episode
"""
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain import episode, world_bus
from live_brain.config import Settings
from live_brain.dashboard import collect, render
from live_brain.memory import Memory

TZ = 3
WEEK_START = datetime(2026, 9, 28, tzinfo=timezone(timedelta(hours=TZ))).timestamp()   # понедельник, 2026-W40
H = 3600


class FakeBudget:
    def __init__(self, why=None):
        self.why = why
        self.reserved = self.settled = 0

    def reserve(self, provider, max_calls, max_usd=None, now=None):
        self.reserved += 1
        return (None, self.why) if self.why else (1, None)

    def settle(self, call_id, cost):
        self.settled += 1

    def close(self):
        pass


class EpisodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mems = {}
        for bot, name in (("bot01", "Arkady"), ("bot02", "Vera")):
            (self.root / "state" / bot).mkdir(parents=True)
            mem = Memory(self.root / "state" / bot / "memory.sqlite")
            mem.set("last_state", {"name": name, "job": "Swordman", "lv": 30})
            self.mems[name] = mem

    def tearDown(self):
        for m in self.mems.values():
            m.close()
        self.tmp.cleanup()

    def ev(self, who, kind, hours, **data):
        """Событие памяти жителя в момент «понедельник 00:00 + hours»."""
        mem = self.mems[who]
        mem.add_event(kind, data)
        mem.db.execute("UPDATE events SET ts = ? WHERE id = (SELECT MAX(id) FROM events)", (WEEK_START + hours * H,))
        mem.db.commit()

    def story(self):
        self.ev("Arkady", "society_quarrel", 10, peer="Vera", cause="не помогла в бою")
        self.ev("Vera", "society_quarrel", 10.1, peer="Arkady", cause="не помог в бою")
        self.ev("Arkady", "society_reconciled", 50, peer="Vera", occasion="подарок")
        self.ev("Vera", "death_report", 30, map="pay_fild04", cause="Wolf")
        self.ev("Vera", "kill", 33, monster="Poring")
        self.ev("Arkady", "explore_start", 60, map="moc_fild01", hops=2)
        self.ev("Arkady", "explore_found", 61, map="moc_fild01")
        self.ev("Vera", "pet_tamed", 70, name="Poring", mob=1002)
        self.ev("Arkady", "aim_new", 1, aim="level", text="взять 32 уровень")
        self.ev("Arkady", "aim_done", 80, aim="level", text="взять 32 уровень")
        self.ev("Vera", "tradition_stage", 90, stage="habit", label="привычка",
                text="Вечерний круг у фонтана стал привычкой")
        self.ev("Arkady", "kill", 24 * 9, monster="Poring")            # следующая неделя — не в серии

    def test_week_bounds(self):
        s, e, label, _ = episode.week_bounds("2026-10-02", TZ)
        self.assertEqual((s, e - s, label), (WEEK_START, 7 * 86400, "2026-W40"))
        self.assertEqual(episode.week_bounds("2026-W40", TZ)[0], WEEK_START)
        self.assertEqual(episode.week_bounds(None, TZ, now=WEEK_START + 3 * 86400)[2], "2026-W40")
        with self.assertRaises(ValueError):
            episode.week_bounds("неделя", TZ)

    def test_episode_scenes_from_facts(self):
        self.story()
        ep = episode.build(self.root, ["bot01", "bot02", "bot03"], "2026-W40", TZ)
        kinds = [s["kind"] for s in ep["scenes"]]
        self.assertEqual(kinds[0], "quarrel")                          # самая сильная линия — заголовок
        self.assertEqual(ep["title"], "Ссора и примирение: Arkady и Vera")
        self.assertTrue(3 <= len(ep["scenes"]) <= 5, kinds)
        self.assertEqual(kinds.count("quarrel"), 1)                    # ссора пары с двух сторон — одна линия
        self.assertIn("death", kinds)
        self.assertEqual(ep["missing"], ["bot03"])
        for s in ep["scenes"]:                                         # каждая сцена — ссылки на факты с датой
            self.assertTrue(s["refs"])
            for r in s["refs"]:
                self.assertRegex(r["when"], r"^2026-(09-2[89]|09-30|10-0[1-4]) \d\d:\d\d$")
        text = episode.render_text(ep)
        self.assertIn("# Серия 1. Ссора и примирение: Arkady и Vera", text)
        self.assertIn("Arkady и Vera поссорились: не помогла в бою", text)
        self.assertIn("помирились (подарок)", text)
        self.assertIn("Vera вернулся(ась) в строй — снова на охоте", text)
        self.assertIn("Факты: 2026-09-28 10:00 · Arkady · society_quarrel", text)
        self.assertIn("## В следующей серии", text)
        self.assertIn("вылупится ли питомец у Vera?", text)            # открытая линия

    def test_open_quarrel_and_number(self):
        self.ev("Arkady", "level_up", -7 * 24 + 5, level=29)             # прошлая неделя — серия №2
        self.ev("Arkady", "society_quarrel", 5, peer="Vera", cause="увёл добычу")
        ep = episode.build(self.root, ["bot01", "bot02"], "2026-W40", TZ)
        self.assertEqual(ep["number"], 2)
        self.assertEqual(ep["title"], "Ссора Arkady и Vera")
        self.assertIn("помирятся ли Arkady и Vera?", ep["next"])

    def test_quiet_week_and_singles(self):
        ep = episode.build(self.root, ["bot01", "bot02"], "2026-W40", TZ)
        self.assertEqual((ep["title"], ep["scenes"]), ("Тихая неделя", []))
        self.assertIn("Сюжетных линий за неделю нет", episode.render_text(ep))
        self.ev("Arkady", "level_up", 5, level=31)
        self.ev("Arkady", "level_up", 7, level=32)
        ep = episode.build(self.root, ["bot01", "bot02"], "2026-W40", TZ)
        self.assertEqual(len(ep["scenes"]), 1)
        self.assertIn("Arkady достиг(ла) 32 уровня", ep["scenes"][0]["text"])

    def test_bus_only_resident(self):
        bus = world_bus.WorldBus(self.root / "state" / "shared" / "world.sqlite", "Lena")
        bus.publish("pet_hatched", {"name": "Lunatic"}, 4, now=WEEK_START + 5 * H)
        bus.publish("rival_score", {"lv": 3}, 1, now=WEEK_START + 6 * H)    # тихий снимок — не факт
        bus.close()
        ep = episode.build(self.root, ["bot01", "bot02"], "2026-W40", TZ)
        self.assertIn("Lena", ep["residents"])
        self.assertEqual(ep["scenes"][0]["title"], "Новый друг Lena")

    def test_read_only(self):
        self.story()
        db = self.root / "state" / "bot01" / "memory.sqlite"
        before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM events").fetchone()[0]
        episode.build(self.root, ["bot01", "bot02"], "2026-W40", TZ)
        self.assertEqual(sqlite3.connect(db).execute("SELECT COUNT(*) FROM events").fetchone()[0], before)

    def test_cli_without_llm(self):
        self.story()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = episode.main(["2026-W40", "--lab-root", str(self.root), "--bots", "bot01", "bot02"])
        self.assertEqual(rc, 0)
        self.assertIn("# Серия 1. Ссора и примирение", out.getvalue())
        self.assertIn("Неделя 2026-W40", out.getvalue())
        self.assertNotIn("Пересказ", out.getvalue())

    # ---------- LLM: по умолчанию выкл., один вызов, только факты ----------

    def llm_settings(self):
        return Settings.from_env({"BRAIN_LLM": "openrouter", "OPENROUTER_API_KEY": "k" * 20})

    def test_llm_off_no_call(self):
        self.story()
        ep = episode.build(self.root, ["bot01", "bot02"], "2026-W40", TZ)
        calls = []
        text, why = episode.colorize(ep, Settings.from_env({}), FakeBudget(), call=lambda *a, **k: calls.append(1))
        self.assertIsNone(text)
        self.assertIn("BRAIN_LLM=off", why)
        self.assertEqual(calls, [])

    def test_llm_budget_exhausted(self):
        self.story()
        ep = episode.build(self.root, ["bot01", "bot02"], "2026-W40", TZ)
        calls = []
        text, why = episode.colorize(ep, self.llm_settings(), FakeBudget("общий лимит"),
                                     call=lambda *a, **k: calls.append(1))
        self.assertEqual((text, why, calls), (None, "общий лимит", []))

    def test_llm_only_facts_and_cache(self):
        self.story()
        ep = episode.build(self.root, ["bot01", "bot02"], "2026-W40", TZ)
        calls = []

        def good(settings, messages, json_mode=True, max_tokens=None):
            calls.append(messages)
            return json.dumps({"text": "Arkady и Vera поссорились, но помирились."}), {"cost": 0.001}, 0.1

        def bad(settings, messages, json_mode=True, max_tokens=None):
            return json.dumps({"text": "Arkady убил 999 драконов вместе с Baphomet."}), {}, 0.1

        budget = FakeBudget()
        text, why = episode.colorize(ep, self.llm_settings(), budget, call=bad)
        self.assertIsNone(text)
        self.assertIn("999", why)
        self.assertEqual(budget.settled, 1)
        orig = episode.colorize
        episode.colorize = lambda e, s, b: orig(e, s, b, call=good)
        try:
            for _ in range(2):                                         # второй запуск — из кэша, без вызова
                text, note = episode.colored_cached(self.root, ep, self.llm_settings, FakeBudget)
                self.assertEqual(text, "Arkady и Vera поссорились, но помирились.")
        finally:
            episode.colorize = orig
        self.assertEqual(len(calls), 1)
        self.assertIn("Пересказ", episode.render_text(ep, text))

    # ---------- дашборд ----------

    def test_dashboard_section(self):
        self.story()
        data = collect(self.root, ["bot01", "bot02"], day="2026-10-01", tz_hours=TZ, now=WEEK_START + 80 * H)
        self.assertEqual(data["episode"]["title"], "Ссора и примирение: Arkady и Vera")
        page = render(data)
        self.assertIn("Серия недели", page)
        self.assertIn("Серия 1. Ссора и примирение: Arkady и Vera", page)


if __name__ == "__main__":
    unittest.main()
