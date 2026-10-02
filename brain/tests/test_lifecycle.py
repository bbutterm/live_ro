"""Состояния жителя, арбитр, свежесть, переподключение, устаревший ответ модели (AUT-001/002/003/005/106/111).

Запуск: cd brain && python3 -m unittest -v tests.test_lifecycle
"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(action):
            self.sent.append(action)
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "decisions.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=load_world(BRAIN_DIR / "world" / "goals.json"))
        self.dec = root / "decisions.jsonl"

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def msg(self, m):
        asyncio.run(self.mind.on_message(m))

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prt_fild08", "x": 100, "y": 100, "hp_pct": 100,
             "lock_map": "prt_fild08", "dead": False, "activity": "idle"}
        s.update(kw)
        self.msg(s)

    def transitions(self):
        return [(r["from"], r["to"]) for r in map(json.loads, self.dec.read_text().splitlines()) if r["type"] == "status"]

    def test_states_and_transitions_logged(self):
        life = self.mind.life
        self.assertEqual(life.tick(), "OFFLINE")
        self.state()
        self.assertEqual(life.tick(), "HUNTING")
        self.state(activity="attack")
        self.assertEqual(life.tick(), "FIGHTING")
        self.state(activity="sellAuto")
        self.assertEqual(life.tick(), "SERVICING")
        self.state(dead=True)
        self.assertEqual(life.tick(), "DEAD")
        self.state(dead=False, activity="idle")
        self.msg({"type": "event", "kind": "danger", "hp_pct": 10})
        self.assertEqual(life.tick(), "ESCAPING")
        self.assertEqual(self.transitions()[:3], [(None, "OFFLINE"), ("OFFLINE", "HUNTING"), ("HUNTING", "FIGHTING")])

    def test_arbiter_priority(self):
        self.state()
        self.assertEqual(self.mind.may_move("routine"), (True, None))
        self.mind.economy.giving = {"id": "x", "to": "Vera", "item": "501", "amount": 1, "since": time.time()}
        self.assertEqual(self.mind.may_move("routine"), (False, "economy"))
        self.assertEqual(self.mind.may_move("party"), (False, "economy"))
        self.assertEqual(self.mind.may_move("plan")[0], True, "встреча важнее передачи")
        self.mind.economy.giving = None
        self.state(dead=True)
        self.assertEqual(self.mind.may_move("plan"), (False, "survival"))

    def test_reconnect_resets_snapshot_and_pending(self):
        self.state()
        self.mind.economy.giving = {"id": "x", "to": "Vera", "item": "501", "amount": 1, "since": time.time()}
        self.msg({"type": "hello", "char": "Arkady"})
        self.assertFalse(self.mind.fresh_state, "старый снимок после переподключения не текущий")
        self.assertIsNone(self.mind.economy.giving, "незавершённая сделка не продолжается вслепую")
        self.assertEqual(self.mind.epoch, 1)

    def test_stale_llm_answer_dropped(self):
        self.state()
        before = self.mind.snapshot()
        self.assertIsNone(self.mind.stale_since(before))
        self.state(dead=True)
        self.assertEqual(self.mind.stale_since(before), "персонаж мёртв")
        self.state(dead=False, map="prontera")
        self.assertEqual(self.mind.stale_since(before), "сменилась карта")


if __name__ == "__main__":
    unittest.main()
