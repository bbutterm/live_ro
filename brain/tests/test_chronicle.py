"""Хроника мира из памяти жителей (только факты событий).

Запуск: cd brain && python3 -m unittest -v tests.test_chronicle
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from live_brain.chronicle import chronicle
from live_brain.memory import Memory


class ChronicleTest(unittest.TestCase):
    def test_world_day_from_two_residents(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for bot, name, events in (
                ("bot01", "Arkady", [("kill", {}), ("kill", {}), ("level_up", {"level": 42}),
                                     ("heal_confirmed", {"from": "Vera", "to": "Arkady", "amount": 120})]),
                ("bot02", "Vera", [("death_report", {"map": "prt_fild08", "cause": "Lunatic", "unknown": ["вес"]}),
                                   ("gift_given", {"peer": "Arkady", "item": "501", "amount": 5}),
                                   ("trade_sold", {"peer": "Arkady", "item": "Jellopy", "amount": 10, "price": 30,
                                                   "paid": 30})])):
            (root / "state" / bot).mkdir(parents=True)
            mem = Memory(root / "state" / bot / "memory.sqlite")
            mem.set("last_state", {"name": name, "job": "Swordman", "lv": 42, "job_lv": 20, "map": "prt_fild08"})
            for kind, data in events:
                mem.add_event(kind, data)
            mem.close()
        (root / "run").mkdir()
        day = time.strftime("%Y-%m-%d", time.gmtime())
        (root / "run" / "alerts.log").write_text(json.dumps({"ts": f"{day}T10:00:00Z", "bot": "Vera",
                                                             "kind": "stuck", "text": "тупик"}, ensure_ascii=False) + "\n")
        text = chronicle(root, ["bot01", "bot02", "bot03"], day=day, tz_hours=0)
        self.assertIn("Arkady: достиг 42 уровня", text)
        self.assertIn("Heal: Vera → Arkady +120 HP", text)
        self.assertIn("Vera: погиб на prt_fild08 (бил Lunatic); неизвестно: вес", text)
        self.assertIn("побед 2", text)
        self.assertIn("bot03: памяти нет", text)
        self.assertIn("Vera: продал Arkady 10 × Jellopy за 30z", text)
        self.assertIn("продал жителям, z 30", text)
        self.assertIn("stuck — тупик", text)
        self.assertNotIn("Хроника мира за 2000", text)


if __name__ == "__main__":
    unittest.main()
