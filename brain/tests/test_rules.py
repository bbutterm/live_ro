"""Правила без LLM: decision gate и SafetyPolicy, плюс сквозной «первый результат».

Запуск: cd brain && python3 -m unittest -v tests.test_rules
"""
import json
import time
import unittest

from live_brain.gate import GateContext, RuleGate, make_gate, status_line
from live_brain.safety import SafetyPolicy
from tests.test_brain import BrainHarness, FakeOpenRouter

STATE = {"name": "Arkady", "lv": 17, "hp_pct": 80, "sp_pct": 40, "map": "prt_fild08",
         "lock_map": "prt_fild08", "activity": "attack", "dead": False}


class GateTest(unittest.TestCase):
    def setUp(self):
        self.gate = RuleGate()
        self.ctx = GateContext(name="Arkady", hunt_maps=["prt_fild08"], greeting="Привет")

    def test_status_command_whispers_back_without_llm(self):
        r = self.gate.evaluate({"kind": "chat_private", "from": "Tester", "text": " !Status "}, STATE, self.ctx)
        self.assertIsNone(r.llm)
        self.assertEqual(r.actions[0]["action"], "whisper")
        self.assertEqual(r.actions[0]["to"], "Tester")
        self.assertIn("HP 80%", r.actions[0]["text"])

    def test_other_private_message_asks_llm(self):
        r = self.gate.evaluate({"kind": "chat_private", "from": "Tester", "text": "привет"}, STATE, self.ctx)
        self.assertEqual((r.actions, r.llm_kind), ([], "chat"))
        self.assertIn("Tester", r.llm)

    def test_greeting_rate_limited(self):
        first = self.gate.evaluate({"kind": "in_game"}, STATE, self.ctx)
        second = self.gate.evaluate({"kind": "in_game"}, STATE, self.ctx)
        self.assertEqual(first.actions, [{"action": "say", "text": "Привет"}])
        self.assertEqual(second.actions, [])

    def test_death_remembered_and_routine_events_ignored(self):
        r = self.gate.evaluate({"kind": "died", "map": "prt_fild08"}, STATE, self.ctx)
        self.assertTrue(r.memory and r.llm)
        r = self.gate.evaluate({"kind": "kill", "monster": "Poring"}, STATE, self.ctx)
        self.assertEqual((r.actions, r.llm, r.memory), ([], None, []))

    def test_jev_not_installed_falls_back_to_rules(self):
        self.assertEqual(make_gate("jev").name, "rules")

    def test_status_line_fits_chat(self):
        self.assertLessEqual(len(status_line(STATE)), 100)


class SafetyTest(unittest.TestCase):
    def setUp(self):
        self.p = SafetyPolicy(["prt_fild08", "prt_fild07"], safe_hp=30, say_limit=2, max_pause=5)

    def test_dead_blocks_everything(self):
        _, why = self.p.check({"action": "say", "text": "x"}, dict(STATE, dead=True))
        self.assertIn("мёртв", why)

    def test_low_hp_blocks_map_change_and_resume(self):
        low = dict(STATE, hp_pct=20)
        self.assertIsNotNone(self.p.check({"action": "set_hunt_map", "map": "prt_fild07"}, low)[1])
        self.assertIsNotNone(self.p.check({"action": "resume"}, low)[1])
        self.assertIsNone(self.p.check({"action": "set_hunt_map", "map": "prt_fild07"}, STATE)[1])

    def test_unknown_map_and_action_rejected(self):
        self.assertIsNotNone(self.p.check({"action": "set_hunt_map", "map": "gef_dun02"}, STATE)[1])
        self.assertIsNotNone(self.p.check({"action": "shell", "cmd": "rm -rf /"}, STATE)[1])

    def test_chat_limits(self):
        now = 1000.0
        ok = [self.p.check({"action": "say", "text": "a"}, STATE, now)[1] is None for _ in range(3)]
        self.assertEqual(ok, [True, True, False])
        self.assertIsNone(self.p.check({"action": "whisper", "to": "A", "text": "x"}, STATE, now)[1])
        self.assertIsNotNone(self.p.check({"action": "whisper", "to": "A", "text": "x"}, STATE, now + 1)[1])
        self.assertIsNone(self.p.check({"action": "whisper", "to": "A", "text": "x"}, STATE, now + 11)[1])

    def test_pause_expires(self):
        self.p.check({"action": "pause"}, STATE, 100.0)
        self.assertFalse(self.p.pause_expired(103.0))
        self.assertTrue(self.p.pause_expired(106.0))


class FirstResultTest(BrainHarness):
    """Без LLM: реальное событие тела -> правило -> команда в тело -> подтверждение."""

    def test_rules_execute_without_llm(self):
        env = self.env_file(key="")
        env.write_text(env.read_text().replace("BRAIN_LLM=openrouter", "BRAIN_LLM=off"))
        proc = self.start_brain(env)
        plugin = self.connect()
        plugin.send({"type": "hello", "char": "Arkady"})
        plugin.send(dict(STATE, type="state"))
        plugin.send({"type": "event", "kind": "in_game", "map": "prt_fild08"})
        greet = plugin.recv()
        plugin.send({"type": "ack", "id": greet["id"], "ok": True, "command": "c " + greet["text"]})
        plugin.send({"type": "event", "kind": "chat_private", "from": "Tester", "text": "!status"})
        reply = plugin.recv()
        plugin.send({"type": "ack", "id": reply["id"], "ok": True, "command": "pm ..."})
        time.sleep(1)
        plugin.close()
        self.stop(proc)
        self.assertEqual(greet["action"], "say")
        self.assertEqual((reply["action"], reply["to"]), ("whisper", "Tester"))
        self.assertIn("prt_fild08", reply["text"])
        self.assertEqual(FakeOpenRouter.requests, [])
        recs = self.decisions()
        self.assertEqual([r["source"] for r in recs if r["type"] == "decision"], ["rule", "rule"])
        self.assertEqual(sum(1 for r in recs if r["type"] == "ack" and r["ok"]), 2)


if __name__ == "__main__":
    unittest.main()
