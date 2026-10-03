"""Стоки зени в метриках (ORG-100 часть 2, ТЗ Т-46): npc_bought (автозакупка NPC), service_paid (сбор почты RODEX),
treat_given (угощение); M18 organic.zeny_flow видит сток. Запуск: cd brain && python3 -m unittest -v tests.test_sinks
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from live_brain import organic
from live_brain.config import Settings
from live_brain.economy import economy_metrics, metrics_from_rows
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
ORGANIC = json.loads((BRAIN_DIR / "world" / "organic.json").read_text())


class MetricsTest(unittest.TestCase):
    def test_rows(self):
        m = metrics_from_rows([("npc_bought", {"zeny": 600}), ("npc_bought", '{"zeny": 400}'),
                               ("service_paid", {"zeny": 2520, "service": "mail"}),
                               ("treat_given", {"peer": "Vera", "zeny": 150})])
        self.assertEqual((m["покупки NPC, z"], m["услуги, z"], m["угощений"]), (1000, 2520, 1))

    def test_zeny_flow_sees_sinks(self):
        rows = [(0, "npc_sold", {"zeny": 3000}), (0, "npc_bought", {"zeny": 600}),
                (0, "service_paid", {"zeny": 2520})]
        self.assertEqual(organic.zeny_flow(rows, ORGANIC), (3000, 3120))
        self.assertEqual(organic.zeny_flow(rows[:1], ORGANIC), (3000, None))      # без трат — «—», как раньше


class MindTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=WORLD)
        asyncio.run(self.mind.on_message({"type": "state", "name": "Arkady", "map": "prontera", "x": 150, "y": 150,
                                          "hp_pct": 100, "zeny": 30000, "items": {"501": 40}, "dead": False}))

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ?", (kind,))]

    def test_npc_bought_event(self):
        asyncio.run(self.mind.on_message({"type": "event", "kind": "npc_bought", "zeny": 600}))
        self.assertEqual(economy_metrics(self.mem, 0)["покупки NPC, z"], 600)
        self.assertEqual(self.sent, [])                                 # только память, не в gate/LLM

    def test_mail_fee(self):
        econ = self.mind.economy
        econ.mailing = {"to": "Vera", "kind": "gift", "item": 501, "amount": 2, "zeny": 0, "since": 0}
        econ.on_mail_result({"ok": True, "to": "Vera", "title": "Подарок", "zeny": 0, "item": 501, "amount": 2})
        econ.mailing = {"to": "Vera", "kind": "week", "item": None, "amount": None, "zeny": 1000, "since": 0}
        econ.on_mail_result({"ok": True, "to": "Vera", "title": "Итог", "zeny": 1000})
        econ.on_mail_result({"ok": True, "to": "Vera", "title": "Письмо", "zeny": 0})           # без вложения — даром
        econ.on_mail_result({"ok": False, "to": "Vera", "reason": "нет зени", "zeny": 500})      # не ушло — не платил
        self.assertEqual([e["zeny"] for e in self.events("service_paid")], [2500, 20])


if __name__ == "__main__":
    unittest.main()
