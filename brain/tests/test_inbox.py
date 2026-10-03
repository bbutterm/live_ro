"""AUT-006: команды оператора до первого состояния тела ждут, исполняются один раз, устаревшие — отказ.

Запуск: cd brain && python3 -m unittest -v tests.test_inbox
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


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.inbox = root / "bot01.inbox"
        self.sent = []

        async def send(action):
            self.sent.append(action)
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "decisions.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, inbox_path=str(self.inbox),
                         world=load_world(BRAIN_DIR / "world" / "goals.json"))
        self.decisions = root / "decisions.jsonl"

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def put(self, cmd, ts=None):
        with open(self.inbox, "a") as f:
            f.write(json.dumps({"cmd": cmd, "with": "", "ts": ts or time.time()}) + "\n")

    def operator_results(self):
        if not self.decisions.exists():
            return []
        return [json.loads(l)["result"] for l in self.decisions.read_text().splitlines()
                if json.loads(l).get("type") == "operator"]

    def state(self):
        asyncio.run(self.mind.on_message({"type": "state", "name": "Arkady", "map": "prt_fild08", "x": 100,
                                          "y": 100, "hp_pct": 100, "lock_map": "prt_fild08", "dead": False}))

    def test_waits_for_fresh_state_then_once(self):
        self.put("rest")
        asyncio.run(self.mind.read_inbox())
        self.assertTrue(self.inbox.exists(), "тело ещё не прислало состояние — команда ждёт")
        self.assertEqual(self.operator_results(), [])
        self.state()
        asyncio.run(self.mind.read_inbox())              # забрал (rename), исполнит на следующем тике
        asyncio.run(self.mind.read_inbox())
        asyncio.run(self.mind.read_inbox())
        self.assertFalse(self.inbox.exists())
        self.assertEqual(self.operator_results(), ["ok"])
        self.assertEqual(self.mind.routine.st["mode"], "town")

    def test_command_written_after_open_not_lost(self):
        # flaky: писатель открыл/создал inbox (`>>` в bash, open("a")), мозг прочитал его в этот момент,
        # и лишь потом писатель записал строку. Раньше пустой файл удалялся и команда терялась.
        self.state()
        with open(self.inbox, "a") as f:
            asyncio.run(self.mind.read_inbox())
            f.write(json.dumps({"cmd": "rest", "with": "", "ts": time.time()}) + "\n")
        asyncio.run(self.mind.read_inbox())
        asyncio.run(self.mind.read_inbox())
        self.assertEqual(self.operator_results(), ["ok"])
        self.assertEqual(self.mind.routine.st["mode"], "town")
        self.assertFalse(self.inbox.exists())
        self.assertFalse(Path(str(self.inbox) + ".taken").exists())

    def test_stale_command_rejected(self):
        self.state()
        self.put("rest", ts=time.time() - 3600)
        asyncio.run(self.mind.read_inbox())
        asyncio.run(self.mind.read_inbox())
        self.assertIn("устарела", self.operator_results()[0])
        self.assertNotEqual((self.mind.routine.st or {}).get("mode"), "town")


    def test_pause_from_body_expires_after_brain_restart(self):
        self.state()
        self.mind.state["paused"] = True                 # пауза в config.txt тела, мозг перезапущен
        asyncio.run(self.mind.safety_tick())
        self.assertFalse(self.sent)
        self.mind.safety.paused_at -= self.mind.safety.max_pause + 1
        asyncio.run(self.mind.safety_tick())
        self.assertEqual([a["action"] for a in self.sent], ["resume"])
        asyncio.run(self.mind.safety_tick())             # тело ещё не прислало новое состояние
        self.assertIsNone(self.mind.safety.paused_at, "пауза не взводится заново по старому снимку")


if __name__ == "__main__":
    unittest.main()
