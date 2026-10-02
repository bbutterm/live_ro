"""Дашборд мира (ORG-090): HTML из памяти жителей и шины мира, только чтение.

Запуск: cd brain && python3 -m unittest -v tests.test_dashboard
"""
import contextlib
import io
import os
import re
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import dashboard, world_bus
from live_brain.memory import Memory


def make_lab(root):
    for bot, name, peer, aff, extra in (
            ("bot01", "Arkady", "Vera", 3, {}),
            ("bot02", "Vera", "Arkady", -3, {"society": {"quarrel": {"Arkady": {"cause": "недоплатил", "since": 1}}}})):
        (root / "state" / bot).mkdir(parents=True)
        mem = Memory(root / "state" / bot / "memory.sqlite")
        mem.set("last_state", {"name": name, "job": "Swordman", "lv": 42, "job_lv": 20, "map": "prt_fild08",
                               "zeny": 1500})
        mem.set("status", {"state": "HUNTING", "why": "на карте охоты", "since": time.time()})
        mem.set("needs", {"progress": 0.9, "social": 0.4, "rest": 0.2, "care": 0.1})
        mem.set("aims", {"start": time.time(), "until": time.time() + 86400,
                         "items": [{"kind": "level", "text": "поднять уровень с 42 до 45", "target": 3},
                                   {"kind": "help", "text": "помочь жителям 3 раза", "target": 3, "done": True,
                                    "progress": 3}]})
        mem.set("activity", {"name": "hunt_early"})
        mem.set("pets", {"pet": {"type": 1002, "name": "Poring"}})
        mem.set("mood", "<script>alert(1)</script>")
        for k, v in extra.items():
            mem.set(k, v)
        mem.update_relation(peer, 0)
        mem.db.execute("UPDATE relations SET affinity = ? WHERE name = ?", (aff, peer))
        mem.db.commit()
        mem.update_relation("Stranger", 1, note="человек")
        mem.add_event("level_up", {"level": 42})
        mem.add_event("diary", {"text": 'Сказал мне: <script>x</script> & "ушёл"'})
        mem.add_event("activity", {"name": "hunt_early"})
        mem.close()
    bus = world_bus.WorldBus(root / "state" / "shared" / "world.sqlite", "Vera")
    bus.publish("event", {"text": "Сервер: праздник у фонтана"}, importance=4)
    bus.publish("level_up", {"level": 42}, importance=3)          # дубль летописи — один раз в ленте
    bus.close()


class DashboardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        make_lab(self.root)
        self.day = time.strftime("%Y-%m-%d", time.gmtime())

    def html(self, bots=("bot01", "bot02", "bot03")):
        return dashboard.render(dashboard.collect(self.root, list(bots), self.day, 0))

    def test_cards_feed_graph(self):
        text = self.html()
        for s in ("Arkady", "Vera", "42/20", "prt_fild08", "охотится", "рост 0.90", "поднять уровень с 42 до 45",
                  "Poring", "Сервер: праздник у фонтана", "достиг 42 уровня", "<svg", "Нет памяти: bot03"):
            self.assertIn(s, text)
        self.assertEqual(text.count("Vera</span> достиг 42 уровня"), 1)        # дубль шины не показан
        self.assertIn('class="neu dash"', text)                                # (3 + −3)/2 = 0, ссора — пунктир
        self.assertNotIn(">Stranger<", text)                                   # не житель — не узел графа
        self.assertIn("занятий", text)                                          # органичность ORG-046
        self.assertIn("зени", text)                                             # экономика

    def test_escaped(self):
        text = self.html()
        self.assertNotIn("<script", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertIn("&lt;script&gt;x&lt;/script&gt; &amp; &quot;ушёл&quot;", text)

    def test_offline_safe_and_small(self):
        text = self.html()
        self.assertLess(len(text.encode("utf-8")), 1024 * 1024)
        self.assertNotIn("http", text)
        self.assertIsNone(re.search(r"<(script|link|iframe|img)\b", text))
        self.assertIn("prefers-color-scheme:dark", text)
        self.assertIn("max-width:600px", text)

    def test_empty_lab(self):
        empty = Path(tempfile.mkdtemp(dir=self.root))
        text = dashboard.render(dashboard.collect(empty, ["bot01"], self.day, 0))
        self.assertIn("Нет памяти: bot01", text)
        self.assertIn("Событий за день нет", text)

    def test_read_only(self):
        """Память не меняется: mode=ro, mtime файла прежний, событий столько же."""
        db = self.root / "state" / "bot01" / "memory.sqlite"
        before = db.stat().st_mtime_ns
        self.html()
        self.assertEqual(before, db.stat().st_mtime_ns)
        conn = sqlite3.connect(db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)
        conn.close()

    def test_main_writes_file(self):
        out = self.root / "run" / "dashboard.html"
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = dashboard.main(["--lab-root", str(self.root), "--bots", "bot01", "bot02", "--day", self.day,
                                 "--world", os.devnull])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), str(out))
        self.assertTrue(out.exists())
        self.assertIn("Arkady", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
