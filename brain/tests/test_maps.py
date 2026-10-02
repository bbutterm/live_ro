"""Опыт по картам, места и слухи, слова и действия (AUT-030/045/073/075/076/091).

Запуск: cd brain && python3 -m unittest -v tests.test_maps
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.maps import EXPLORE_MIN, MapStats
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
MAPS = ["prt_fild08", "prt_fild07", "prt_fild05"]


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


class MapStatsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.clock = Clock()
        self.ms = MapStats(SimpleNamespace(mem=self.mem), clock=self.clock)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def hunt(self, hmap, minutes, kills=0, deaths=0, exp_gain=0.0):
        exp = 10.0
        for i in range(int(minutes * 12) + 1):                  # тик каждые 5 с (первый — точка отсчёта)
            self.clock.t += 5
            self.ms.tick({"map": hmap, "exp_pct": exp, "zeny": 100}, hunting=True)
            exp += exp_gain / (minutes * 12)
        for _ in range(kills):
            self.ms.on_kill(hmap)
        for _ in range(deaths):
            self.ms.on_death(hmap)

    def test_time_counted_only_while_hunting_without_gaps(self):
        self.hunt("prt_fild08", 10, exp_gain=3)
        self.ms.tick({"map": "prt_fild08", "exp_pct": 50}, hunting=False)
        self.clock.t += 3600                                    # мозг не работал час
        self.ms.tick({"map": "prt_fild08", "exp_pct": 60}, hunting=True)
        m = self.ms.stats()["prt_fild08"]                       # в SQLite пишется не чаще раза в 30 с
        self.assertAlmostEqual(m["minutes"], 10, delta=0.2)
        self.assertAlmostEqual(m["exp"], 3, delta=0.3)

    def test_explore_then_best_and_bans(self):
        self.assertEqual(self.ms.choose(MAPS, {})[0], "prt_fild08", "мало опыта — пробует по порядку")
        self.hunt("prt_fild08", EXPLORE_MIN, kills=20)
        self.assertEqual(self.ms.choose(MAPS, {})[0], "prt_fild07")
        self.hunt("prt_fild07", EXPLORE_MIN, kills=60)
        self.hunt("prt_fild05", EXPLORE_MIN, kills=90, deaths=2)
        choice, why = self.ms.choose(MAPS, {})
        self.assertEqual(choice, "prt_fild07", "смерти перевешивают победы")
        self.assertIn("лучшая", why)
        self.assertEqual(self.ms.choose(MAPS, {"prt_fild07": 9e9})[0], "prt_fild08", "исключённую не выбирает")

    def test_places_seen_and_rumor_not_fact(self):
        self.ms.seen("prontera")
        self.ms.told("gef_fild10", "Vera", "danger")
        places = self.mem.get("places")
        self.assertEqual(places["prontera"]["source"], "seen")
        self.assertEqual(places["gef_fild10"]["source"], "told")
        self.assertEqual(places["gef_fild10"]["rumors"][0]["from"], "Vera")


class PromiseTest(unittest.TestCase):
    def test_promise_without_plan_rejected(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sent = []

        async def send(a):
            sent.append(a)
            return len(sent)

        mem = Memory(Path(tmp.name) / "m.sqlite")
        self.addCleanup(mem.close)
        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        mind = Mind(Settings.from_env({}), persona, mem, send, Path(tmp.name) / "d.jsonl", RuleGate(),
                    peers={"Arkady", "Vera"}, world=load_world(BRAIN_DIR / "world" / "goals.json"))
        asyncio.run(mind.apply({"actions": [{"action": "whisper", "to": "Vera", "text": "Уже иду к тебе!"},
                                            {"action": "whisper", "to": "Vera", "text": "Как охота?"}]}, "тест", 0.1, {}))
        self.assertEqual([a["text"] for a in sent], ["Как охота?"])
        recs = [json.loads(l) for l in (Path(tmp.name) / "d.jsonl").read_text().splitlines()]
        self.assertIn("rejected_promise", [r["type"] for r in recs])


if __name__ == "__main__":
    unittest.main()
