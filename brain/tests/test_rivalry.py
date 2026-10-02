"""Соперничество (rivalry.py, ORG-060): выбор соперника, обгон по фактам, подначки, мотив, летопись.

Настоящий Mind (Arkady) с поддельным телом и настоящей шиной мира во временном каталоге; Vera — второй
писатель той же шины (снимки rival_score). Время модуля — подменные часы.
Запуск: cd brain && python3 -m unittest -v tests.test_rivalry
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain import world_bus
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.rivalry import Rivalry
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZ = timezone(timedelta(hours=WORLD["timezone_offset_hours"]))


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class RivalryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.bus = world_bus.WorldBus(root / "world.sqlite", "Arkady")
        self.vera = world_bus.WorldBus(root / "world.sqlite", "Vera")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=WORLD, world_bus_db=self.bus)
        self.dec = root / "d.jsonl"
        self.clock = Clock(time.time())
        self.r = Rivalry(self.mind, WORLD, clock=self.clock, rng=random.Random(3))
        self.mind.rivalry = self.r
        self.state(lv=41)

    def tearDown(self):
        self.bus.close()
        self.vera.close()
        self.mem.close()
        self.tmp.cleanup()

    # ---------- помощники ----------

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 41,
             "job": "Swordsman", "dead": False, "weight_pct": 20, "zeny": 60000, "items": {"501": 30}, "players": []}
        s.update(getattr(self, "_last", {}))
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def vera_score(self, lv=41, kills_day=0, kills_week=0, places=0, aims=0, job="Acolyte"):
        now = self.clock.t
        d = datetime.fromtimestamp(now, TZ)
        y, w, _ = d.isocalendar()
        self.vera.replace("rival_score", {"lv": lv, "job": job, "day": d.strftime("%Y-%m-%d"), "week": f"{y}-W{w:02d}",
                                          "kills_day": kills_day, "kills_week": kills_week, "places": places,
                                          "aims": aims}, 1, now=now)

    def tick(self, dt=60):
        self.clock.t += dt
        asyncio.run(self.r.tick())

    def whispers(self):
        return [a for a in self.sent if a["action"] == "whisper"]

    def events(self, kind):
        return [json.loads(r[0]) for r in self.mem.db.execute("SELECT data FROM events WHERE kind = ?", (kind,))]

    # ---------- тесты ----------

    def test_trait_default_from_bravery_and_diligence(self):
        self.assertAlmostEqual(self.r.trait(), (0.7 + 0.8) / 2)
        self.mind.persona.setdefault("traits", {})["rivalry"] = 0.1
        self.assertEqual(self.r.trait(), 0.1)
        self.vera_score(lv=41)
        self.assertIsNone(self.r.pick(), "азарта нет — соперника нет")

    def test_pick_by_level_gap_affinity_and_quarrel(self):
        self.vera_score(lv=45)
        self.assertIsNone(self.r.pick(), "разница уровней больше 3")
        self.vera_score(lv=43)
        self.assertEqual(self.r.pick(), "Vera")
        self.mem.update_relation("Vera", -1, "тест")
        self.assertIsNone(self.r.pick(), "отношение < 0 — не соперник")
        self.mem.update_relation("Vera", 1, "тест")

        class Q:
            def quarrel(self, peer):
                return True
        self.mind.society = Q()
        self.assertIsNone(self.r.pick(), "в ссоре — не соперник")

    def test_overtake_level_chronicle_whisper_no_affinity_change(self):
        aff = (self.mem.relation("Vera") or {}).get("affinity", 0)
        self.vera_score(lv=42)
        self.state(lv=41)
        self.tick()
        self.assertEqual(self.r.st.get("rival"), "Vera")
        self.assertEqual(self.events("rival_overtook"), [], "первое сравнение только запоминается")
        self.assertGreater(self.r.boost("progress"), 1.0, "отстаю по уровню — progress выше")
        self.assertEqual(self.r.boost("social"), 1.0)
        self.state(lv=43)
        self.tick()
        ev = self.events("rival_overtook")
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["metric"], ev[0]["mine"], ev[0]["theirs"]), ("level", 43, 42))
        self.assertEqual(LINES["rival_overtook"](ev[0]), "обогнал(а) Vera по уровню (43 против 42)")
        w = self.whispers()
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["to"], "Vera")
        self.assertTrue(w[0]["text"].endswith("[chat:rival:4]"))
        self.assertLessEqual(len(w[0]["text"]), 78)
        self.assertEqual(self.r.boost("progress"), 1.0, "впереди — без усиления")
        self.tick()
        self.assertEqual(len(self.events("rival_overtook")), 1, "обгон не дублируется")
        self.assertEqual((self.mem.relation("Vera") or {}).get("affinity", 0), aff, "соперничество не меняет отношения")

    def test_needs_progress_boost_for_lagging(self):
        self.vera_score(lv=44)
        self.tick()
        base = dict(self.r.st)
        boosted = self.mind.needs.weighted()["progress"]
        self.r.st = {}
        plain = self.mind.needs.weighted()["progress"]
        self.r.st = base
        if plain:
            self.assertGreater(boosted, plain)
        self.assertAlmostEqual(self.r.boost("progress"), 1 + 0.2 * 0.75)

    def test_daily_limit_and_daily_tease(self):
        self.r.cfg["max_per_day"] = 1
        for _ in range(12):
            self.mem.add_event("kill", {"map": "prt_fild08"})
        self.vera_score(lv=41, kills_day=2)
        self.r.rng = random.Random(1)
        self.r.rng.random = lambda: 0.0                  # всегда в настроении
        self.tick()
        w = self.whispers()
        self.assertEqual(len(w), 1, "подначка по победам дня")
        self.assertIn("12", w[0]["text"])
        self.vera_score(lv=40)
        self.state(lv=40)
        self.tick()
        self.state(lv=42)
        self.tick()
        self.assertEqual(len(self.events("rival_overtook")), 1, "обгон записан")
        self.assertEqual(len(self.whispers()), 1, "но лимит сообщений в сутки исчерпан")

    def test_snapshot_one_per_bot_and_quiet(self):
        self.tick()
        self.tick(dt=self.r.cfg["publish_minutes"] * 60 + 1)
        rows = self.bus.db.execute("SELECT COUNT(*) FROM world_events WHERE kind = 'rival_score' AND bot = 'Arkady'"
                                   ).fetchone()[0]
        self.assertEqual(rows, 1, "затирание: один снимок на жителя")
        events = world_bus.read_period(self.bus.path, 0, self.clock.t + 10)
        self.assertFalse([e for e in events if e["kind"] == "rival_score"], "снимок не попадает в летопись")
        self.assertIn("Arkady", self.vera.latest("rival_score"), "Vera видит снимок Arkady")

    def test_group_tease_goes_to_party_chat(self):
        crew, party = self.mind.crew, self.mind.party
        self.assertIsNotNone(crew)
        party.st["confirmed"] = True
        self.state(party=party.name, party_members=[{"name": "Vera", "online": True, "map": "prontera"}])
        self.vera_score(lv=42)
        self.tick()
        self.state(lv=43)
        self.tick()
        said = [a for a in self.sent if a["action"] == "party_say"]
        self.assertEqual(len(said), 1)
        self.assertEqual(self.whispers(), [])


if __name__ == "__main__":
    unittest.main()
