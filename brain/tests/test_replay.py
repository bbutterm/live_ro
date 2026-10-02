"""Реплей (ORG-050): синтетический день тела через настоящий мозг на ускоренных часах + инварианты.

Сценарий: охота 1 ч (бой, рост веса, траты зелий) → смерть → возрождение в городе с 5% HP →
медленное восстановление → отдых. Тело в реплее не реагирует на команды (запись), проверяется
только поведение мозга: мёртвому не двигаться, больной не идёт охотиться, без LLM — ноль вызовов,
нет спама действиями. Реальные записи с VPS (BRAIN_RECORD=1) прогоняются тем же run().

Запуск: cd brain && python3 -m unittest -v tests.test_replay
"""
import asyncio
import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from live_brain import replay
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def synthetic_day(t0):
    rng = random.Random(7)
    msgs = [{"type": "hello", "char": "Arkady", "ts": t0}]
    base = {"type": "state", "name": "Arkady", "job": "Swordsman", "lv": 41, "job_lv": 20, "sp_pct": 80,
            "zeny": 20000, "ai": "auto", "players": [], "party": None, "party_members": [], "friends": []}

    def state(t, **kw):
        s = dict(base, ts=t)
        s.update(kw)
        msgs.append(s)

    hp, x, y, weight, pots = 100, 100, 100, 20, 30
    for t in range(0, 3600, 15):                                    # охота
        hp = max(35, min(100, hp + rng.randint(-15, 12)))
        x, y = x + rng.randint(-3, 3), y + rng.randint(-3, 3)
        weight = min(55, weight + 0.15)
        pots = max(3, pots - (1 if rng.random() < 0.1 else 0))
        state(t0 + t, map="prt_fild08", x=x, y=y, hp_pct=hp, weight_pct=int(weight), lock_map="prt_fild08",
              lock_x=None, lock_y=None, items={"501": pots}, activity="attack" if t % 60 else "route", dead=False)
        if t % 30 == 0:
            msgs.append({"type": "event", "kind": "attack", "monster": "Lunatic", "ts": t0 + t + 1})
            msgs.append({"type": "event", "kind": "kill", "monster": "Lunatic", "map": "prt_fild08", "ts": t0 + t + 5})
    msgs.append({"type": "event", "kind": "died", "map": "prt_fild08", "ts": t0 + 3600})
    for t in range(3600, 3620, 5):                                  # лежит мёртвым
        state(t0 + t, map="prt_fild08", x=x, y=y, hp_pct=0, dead=True, weight_pct=55, items={"501": 0},
              lock_map="prt_fild08", activity="dead")
    hp = 5
    for t in range(3620, 7200, 15):                                 # возрождение в городе, медленное восстановление
        hp = min(100, hp + (1 if t < 5000 else 3))
        state(t0 + t, map="prontera", x=156, y=185, hp_pct=hp, dead=False, weight_pct=55, items={"501": 0},
              lock_map="prontera", lock_x=156, lock_y=185, sitting=True, activity="idle")
    return msgs


class ReplayTest(unittest.TestCase):
    def test_synthetic_day_invariants(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        clock = Clock()
        tz = timezone(timedelta(hours=WORLD["timezone_offset_hours"]))
        t0 = datetime(2026, 10, 2, 12, 0, tzinfo=tz).timestamp()   # вне окна сна Arkady (сова, до ~10:30)
        clock.t = t0
        sent = []
        with mock.patch("time.time", clock):
            mem = Memory(root / "m.sqlite")
            persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
            mind = None

            async def send(a):
                sent.append((clock.t, dict(a), dict(mind.state)))
                return len(sent)

            mind = Mind(Settings.from_env({}), persona, mem, send, root / "d.jsonl", RuleGate(),
                        peers={"Arkady", "Vera"}, world=WORLD)
            asyncio.run(replay.run(mind, synthetic_day(t0), clock))
            calls = mem.db.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
            kinds = [r[0] for r in mem.db.execute("SELECT kind FROM events")]
            mem.close()
        bad = replay.invariants(sent, llm_calls=calls)
        self.assertEqual(bad, [])
        self.assertIn("death_report", kinds, "смерть разобрана")
        self.assertIn("routine_recover", kinds, "после смерти — восстановление")
        decisions = [json.loads(l) for l in (root / "d.jsonl").read_text().splitlines()]
        states = [d["to"] for d in decisions if d.get("type") == "status"]
        self.assertIn("DEAD", states)
        self.assertIn("RECOVERING", states)
        hunt_after = [s.get("hp_pct") for t, a, s in sent if a["action"] == "hunt" and t > t0 + 3600]
        self.assertTrue(all(hp >= 80 for hp in hunt_after), f"на охоту только с HP >= 80: {hunt_after}")

    def test_recorder_hides_strangers(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rec = replay.Recorder(Path(tmp.name) / "replay.jsonl", peers={"Vera"})
        rec({"type": "event", "kind": "chat_private", "from": "Stranger", "text": "мой пароль 123"})
        rec({"type": "event", "kind": "chat_private", "from": "Vera", "text": "Привет [chat:hello:1]"})
        lines = replay.load(Path(tmp.name) / "replay.jsonl")
        self.assertEqual(lines[0]["text"], "<скрыто>")
        self.assertIn("[chat:hello:1]", lines[1]["text"])


if __name__ == "__main__":
    unittest.main()
