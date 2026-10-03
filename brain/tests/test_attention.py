"""Бюджет внимания (attention.py, ORG-109, Т-38): база по характеру, жетоны дел и охоты, цены, пара, новый день,
отказ в журнале, протокол без ограничений, выключатель.

Запуск: cd brain && python3 -m unittest -v tests.test_attention
"""
import asyncio
import io
import json
import random
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from live_brain import __main__ as main_mod
from live_brain.attention import Attention
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from tests.worldtime import shift_time

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
SRC = BRAIN_DIR / "live_brain"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class AttentionTest(unittest.TestCase):
    def setUp(self):
        shift_time(self)                          # полдень обычного дня мира: не ночь, не круг
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock(time.time())
        self.minds = []

    def tearDown(self):
        for m in self.minds:
            m.mem.close()
        self.tmp.cleanup()

    def make(self, bot="bot01", env=None, world=WORLD):
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        persona.pop("sleep", None)
        mem = Memory(self.root / f"{bot}-{len(self.minds)}.sqlite")
        mind = Mind(Settings.from_env(env or {}), persona, mem, send, self.root / f"{bot}-{len(self.minds)}.jsonl",
                    RuleGate(), peers={"Arkady", "Vera"}, world=world)
        self.minds.append(mind)
        mind.state = {"name": persona["name"], "map": "prontera", "x": 156, "y": 185, "dead": False, "lv": 30,
                      "players": []}
        mind.fresh_state = True
        if mind.attention:
            mind.attention.clock = self.clock
        if mind.social:
            mind.social.clock = self.clock
            mind.social.rng = random.Random(3)
            mind.social.grammar = None
        if mind.mood:
            mind.mood.clock = self.clock
        return mind

    def whispers(self):
        return [a for a in self.sent if a.get("action") == "whisper"]

    def decisions(self, mind):
        p = Path(mind.decisions_path)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    def test_base_by_sociability(self):
        a, v = self.make("bot01").attention, self.make("bot02").attention
        self.assertAlmostEqual(a.budget()["base"], 3 + 9 * 0.4, places=2)
        self.assertGreater(v.budget()["base"], a.budget()["base"])
        self.assertEqual(a.budget()["deeds"], 0)
        self.assertEqual(a.budget()["spent"], 0)

    def test_deeds_and_cap(self):
        m = self.make()
        att = m.attention
        m.mem.add_event("level_up", {"level": 31})
        att.cache = (None, None, None)
        self.assertEqual(att.budget()["deeds"], 1)
        for i in range(10):
            m.mem.add_event("card_found", {"id": i})
        m.mem.add_event("heal_confirmed", {"from": "Vera", "to": "Someone"})   # лечили не меня — не дело
        att.cache = (None, None, None)
        self.assertEqual(att.budget()["deeds"], att.cfg["deed_cap"])
        m.mem.add_event("kill", {"mob": "Poring"})                              # не дело
        att.cache = (None, None, None)
        self.assertEqual(att.budget()["deeds"], 6)

    def test_hunt_tokens(self):
        m = self.make()
        r = m.routine
        r.new_day(self.clock.t)
        r.st["hunted"] = 3 * 3600
        self.assertEqual(m.attention.budget()["deeds"], 2)
        r.st["day"] = "1999-01-01"                                    # вчерашняя охота — не сегодня
        self.assertEqual(m.attention.hunt_tokens(self.clock.t), 0)

    def test_spend_costs_and_new_day(self):
        att = self.make().attention
        left = att.budget()["left"]
        att.spend("rumor", "Vera")
        att.spend("wed", "Vera")
        att.spend("director")
        self.assertAlmostEqual(att.budget()["left"], left - 0.5)
        att.spend("chat", "Vera")
        self.assertAlmostEqual(att.budget()["left"], left - 1.5)
        self.assertEqual(att.st["pairs"], {"Vera": 1})
        self.clock.t += 86400
        b = att.budget()
        self.assertEqual(b["spent"], 0)
        self.assertEqual(att.st["pairs"], {})

    def test_pair_max(self):
        att = self.make("bot02").attention                            # Vera: бюджет большой, упирается в пару
        for _ in range(att.cfg["pair_max"]):
            self.assertTrue(att.may("chat", "Arkady"))
            att.spend("chat", "Arkady")
        self.assertFalse(att.may("chat", "Arkady"))
        self.assertTrue(att.may("chat", "Rook"))
        self.assertTrue(att.may("gossip", "Arkady"))                  # пара ограничивает только разговоры

    def test_exhausted_chat_does_not_open_but_replies(self):
        m = self.make("bot01")
        att = m.attention
        att.spend("gossip", None)
        att.st["spent"] = att.budget()["base"] + 0.5                 # бюджет исчерпан
        m.state["players"] = [{"name": "Vera", "x": 157, "y": 185, "lv": 30}]
        asyncio.run(m.social.chat(self.clock.t, m.state))
        self.assertEqual(self.whispers(), [])
        self.assertNotIn("Vera", m.social.st["pairs"])                # пауза пары не сброшена
        # шаг 2 ответа — без бюджета
        asyncio.run(m.social.on_tag("Vera", "Привет! [chat:hello:1]"))
        self.clock.t += 30
        asyncio.run(m.social.flush(self.clock.t))
        texts = [w["text"] for w in self.whispers()]
        self.assertTrue(any("[chat:hello:2]" in t for t in texts), texts)

    def test_chat_spends(self):
        m = self.make("bot01")
        m.state["players"] = [{"name": "Vera", "x": 157, "y": 185, "lv": 30}]
        m.social.near_since = {"Vera": self.clock.t - 120}
        asyncio.run(m.social.chat(self.clock.t, m.state))
        self.assertTrue(any("[chat:" in w["text"] for w in self.whispers()))
        self.assertEqual(m.attention.budget()["spent"], 1)
        self.assertEqual(m.mem.get("attention")["pairs"], {"Vera": 1})

    def test_deny_logged_hourly(self):
        m = self.make()
        att = m.attention
        att.st = {"day": att.day(self.clock.t), "spent": 100.0, "pairs": {}}
        for _ in range(5):
            self.assertFalse(att.may("chat", "Vera"))
        self.assertFalse(att.may("gossip", "Vera"))
        deny = [d for d in self.decisions(m) if d.get("type") == "attention"]
        self.assertEqual([d["kind"] for d in deny], ["chat", "gossip"])
        self.clock.t += 3601
        att.may("chat", "Vera")
        self.assertEqual(len([d for d in self.decisions(m) if d.get("type") == "attention"]), 3)
        self.assertTrue(att.may("director"))                          # цена 0 — всегда можно
        self.assertTrue(att.may("wed", "Vera"))

    def test_rivalry_tease_blocked(self):
        m = self.make()
        if not m.rivalry:
            self.skipTest("rivalry выключен")
        m.attention.st = {"day": m.attention.day(self.clock.t), "spent": 100.0, "pairs": {}}
        sent = asyncio.run(m.rivalry.tease(self.clock.t, "Vera", "rival_lead", mine=5, theirs=1))
        self.assertFalse(sent)
        self.assertEqual(self.whispers(), [])
        m.attention.st["spent"] = 0.0
        self.assertTrue(asyncio.run(m.rivalry.tease(self.clock.t, "Vera", "rival_lead", mine=5, theirs=1)))
        self.assertEqual(m.attention.budget()["spent"], 1)

    def test_protocol_modules_do_not_ask(self):
        """Протокол (сделки, заказы, группа, лечение, спарринг) бюджет не спрашивает."""
        for name in ("economy.py", "orders.py", "party.py", "crew.py", "healer.py", "spar.py", "plans.py"):
            self.assertNotIn("attention", (SRC / name).read_text(), name)

    def test_disabled(self):
        m = self.make(env={"BRAIN_DISABLE": "attention"})
        self.assertIsNone(m.attention)
        m.state["players"] = [{"name": "Vera", "x": 157, "y": 185, "lv": 30}]
        asyncio.run(m.social.chat(self.clock.t, m.state))
        self.assertTrue(any("[chat:" in w["text"] for w in self.whispers()))
        off = dict(WORLD, attention=dict(WORLD["attention"], enabled=False))
        self.assertIsNone(self.make(world=off).attention)

    def test_report_line(self):
        m = self.make()
        m.attention.spend("chat", "Vera")
        out = io.StringIO()
        with redirect_stdout(out):
            main_mod.report(SimpleNamespace(bot="bot01"), m.mem, self.root)
        self.assertIn("внимание", out.getvalue())
        self.assertIn("потрачено 1 из", out.getvalue())


if __name__ == "__main__":
    unittest.main()
