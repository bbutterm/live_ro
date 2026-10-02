"""Память, бюджет, оповещения, флаги, журналы (AUT-097/100/102/107/109/113/118/120).

Запуск: cd brain && python3 -m unittest -v tests.test_reliability
"""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from live_brain.budget import SharedBudget
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import MAX_MEMORIES, Memory
from live_brain.mind import Mind, rotate
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
PERSONA = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")


async def send(action):
    return 1


class ReliabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_migration_from_old_schema_keeps_memories_and_backs_up(self):
        path = self.root / "memory.sqlite"
        old = sqlite3.connect(str(path))
        old.executescript("CREATE TABLE memories (id INTEGER PRIMARY KEY, ts REAL NOT NULL, text TEXT NOT NULL, "
                          "importance INTEGER NOT NULL); INSERT INTO memories (ts, text, importance) "
                          "VALUES (1, 'Я Arkady из Пронтеры', 5);")
        old.commit()
        old.close()
        mem = Memory(path)
        self.assertTrue((self.root / "memory.sqlite.bak-v1").exists(), "копия до миграции")
        self.assertEqual(mem.top_memories(1)[0]["text"], "Я Arkady из Пронтеры")
        self.assertEqual(mem.top_memories(1)[0]["kind"], "note")
        self.assertEqual(mem.get("schema_version"), 2)
        mem.close()
        Memory(path).close()                                # повторный запуск — миграция не повторяется
        self.assertEqual(len(list(self.root.glob("*.bak-*"))), 1)

    def test_memory_kinds_and_cap(self):
        mem = Memory(self.root / "m.sqlite")
        mem.remember("Vera вылечила меня — по пакету сервера", 3)
        mem.remember("Наверное, Vera обиделась", 2, kind="thought")
        kinds = {m["text"][:5]: m["kind"] for m in mem.top_memories(5)}
        self.assertEqual(kinds, {"Vera ": "fact", "Навер": "thought"})
        for i in range(MAX_MEMORIES + 10):
            mem.db.execute("INSERT INTO memories (ts, text, importance, kind) VALUES (?, ?, 1, 'note')", (i, f"m{i}"))
        mem.prune()
        self.assertEqual(mem.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], MAX_MEMORIES)
        self.assertEqual(len([m for m in mem.top_memories(5) if m["importance"] >= 2]), 2, "важное осталось")
        mem.close()

    def mind(self, env=None, shared=None):
        mem = Memory(self.root / "m.sqlite")
        self.addCleanup(mem.close)
        return Mind(Settings.from_env(env or {}), PERSONA, mem, send, self.root / "decisions.jsonl", RuleGate(),
                    peers={"Arkady", "Vera"}, world=WORLD, shared_budget=shared, alerts_path=self.root / "alerts.log")

    def test_llm_relation_capped_per_day(self):
        m = self.mind()
        self.assertEqual([m.llm_relation_delta("Vera", 2), m.llm_relation_delta("Vera", 2),
                          m.llm_relation_delta("Vera", -1)], [2, 0, 0], "за сутки не больше 2 в любую сторону")
        self.assertEqual(m.llm_relation_delta("Tester", 5), 2)

    def test_feature_flags(self):
        m = self.mind({"BRAIN_DISABLE": "party, economy"})
        self.assertIsNone(m.party)
        self.assertIsNone(m.economy)
        self.assertIsNotNone(m.routine)

    def test_alert_dedup_and_file(self):
        m = self.mind()
        self.assertTrue(m.alert("stuck", "prt_fild08: 3 застревания"))
        self.assertFalse(m.alert("stuck", "ещё раз"))
        self.assertTrue(m.alert("deaths", "3 смерти"))
        lines = [json.loads(l) for l in (self.root / "alerts.log").read_text().splitlines()]
        self.assertEqual([l["kind"] for l in lines], ["stuck", "deaths"])

    def test_shared_budget_across_bots_and_threads(self):
        path = self.root / "shared" / "budget.sqlite"
        a, v = SharedBudget(path, "bot01"), SharedBudget(path, "bot02")
        self.assertIsNone(a.reserve("openrouter", 3)[1])
        self.assertIsNone(v.reserve("openrouter", 3)[1])
        got = []
        def grab():
            b = SharedBudget(path, "bot03")
            got.append(b.reserve("openrouter", 3)[0])
            b.close()
        threads = [threading.Thread(target=grab) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len([g for g in got if g]), 1, "параллельные резервы не проходят лимит вместе")
        self.assertIn("общий лимит", a.reserve("openrouter", 3)[1])
        rid, _ = a.reserve("jev", 10, max_usd=0.01)
        a.settle(rid, 0.02)
        self.assertIn("денежный", a.reserve("jev", 10, max_usd=0.01)[1])
        a.close()
        v.close()

    def test_residents_are_running_bots(self):
        """ORG-004: жители — только запущенные (LAB_BOTS), иначе лидер зовёт офлайн-жителя."""
        from live_brain.__main__ import env_bots, peer_names
        persona = BRAIN_DIR / "personas" / "bot01.json"
        self.assertEqual(peer_names(persona, ["bot01"]), {"Arkady"})
        self.assertEqual(peer_names(persona, ["bot01", "bot02"]), {"Arkady", "Vera"})
        env = self.root / "live_ro.env"
        env.write_text('LAB_BOTS="bot01 bot02"\n')
        self.assertEqual(env_bots(env).split(), ["bot01", "bot02"])

    def test_rotate(self):
        p = self.root / "decisions.jsonl"
        p.write_text("x" * 100)
        rotate(str(p), limit=50)
        self.assertFalse(p.exists())
        self.assertTrue((self.root / "decisions.jsonl.1").exists())


if __name__ == "__main__":
    unittest.main()
