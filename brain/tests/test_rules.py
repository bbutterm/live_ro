"""Правила без LLM: decision gate и SafetyPolicy, плюс сквозной «первый результат».

Запуск: cd brain && python3 -m unittest -v tests.test_rules
"""
import json
import time
import unittest

from live_brain.config import Settings
from live_brain.gate import GateContext, RuleGate, make_fast_gate, status_line
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

    def test_jev_without_settings_means_rules_only(self):
        self.assertIsNone(make_fast_gate(Settings.from_env({"BRAIN_GATE": "jev"})))
        self.assertIsNone(make_fast_gate(Settings.from_env({})))
        full = {"BRAIN_GATE": "jev", "JEV_API_BASE": "http://x", "JEV_API_KEY": "k", "JEV_MODEL": "m"}
        self.assertEqual(make_fast_gate(Settings.from_env(full)).name, "jev")

    def test_peer_conversation_limited(self):
        ctx = GateContext(name="Arkady", hunt_maps=[], peers={"Vera"}, peer_replies_per_hour=2)
        ev = {"kind": "chat_private", "from": "Vera", "text": "как дела?"}
        llm_asks = [self.gate.evaluate(ev, STATE, ctx).llm is not None for _ in range(3)]
        self.assertEqual(llm_asks, [True, True, False])
        human = {"kind": "chat_private", "from": "Tester", "text": "как дела?"}
        self.assertTrue(all(self.gate.evaluate(human, STATE, ctx).llm for _ in range(5)))

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
        ok = [self.p.check({"action": "say", "text": f"a{i}"}, STATE, now)[1] is None for i in range(3)]
        self.assertEqual(ok, [True, True, False])
        self.assertIsNone(self.p.check({"action": "whisper", "to": "A", "text": "x"}, STATE, now)[1])
        self.assertIsNotNone(self.p.check({"action": "whisper", "to": "A", "text": "y"}, STATE, now + 1)[1])
        self.assertIsNone(self.p.check({"action": "whisper", "to": "A", "text": "z"}, STATE, now + 11)[1])

    def test_text_fits_one_message_and_keeps_tag(self):
        """OpenKore режет > 80 символов на несколько шёпотов: реплика ≤ 78, метка в конце целиком."""
        from live_brain.safety import MAX_TEXT, fit_text
        long = "Очень длинная реплика жителя про охоту и погоду в Пронтере, " * 3
        tagged = fit_text(long + "[meet:abc123:prt_fild08:150:160]")
        self.assertLessEqual(len(tagged), MAX_TEXT)
        self.assertTrue(tagged.endswith("[meet:abc123:prt_fild08:150:160]"))
        self.assertLessEqual(len(fit_text(long)), MAX_TEXT)
        self.assertEqual(fit_text("  коротко  "), "коротко")
        clean, _ = self.p.check({"action": "whisper", "to": "A", "text": long + "[chat:greet:1]"}, STATE, 5000.0,
                                protocol=True)
        self.assertTrue(clean["text"].endswith("[chat:greet:1]"))

    def test_no_repeat_same_text(self):
        """AUT-095: та же реплика тому же адресату — не раньше чем через час; протокол не ограничен."""
        now = 1000.0
        self.assertIsNone(self.p.check({"action": "whisper", "to": "A", "text": "Привет"}, STATE, now)[1])
        self.assertIn("та же реплика", self.p.check({"action": "whisper", "to": "A", "text": "привет"}, STATE, now + 60)[1])
        self.assertIsNone(self.p.check({"action": "whisper", "to": "B", "text": "Привет"}, STATE, now + 60)[1])
        self.assertIsNone(self.p.check({"action": "whisper", "to": "A", "text": "Привет"}, STATE, now + 3700)[1])
        for i in range(3):
            self.assertIsNone(self.p.check({"action": "whisper", "to": "A", "text": "[party:hunt:x]"}, STATE,
                                           now + i, protocol=True)[1])

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
        self.wait_for(lambda: self.count("ack") >= 2, "оба ack в журнале")
        plugin.close()
        self.stop(proc)
        self.assertEqual(greet["action"], "say")
        self.assertEqual((reply["action"], reply["to"]), ("whisper", "Tester"))
        self.assertIn("prt_fild08", reply["text"])
        self.assertEqual(FakeOpenRouter.requests, [])
        recs = self.decisions()
        self.assertEqual([r["source"] for r in recs if r["type"] == "decision"], ["rule", "rule"])
        self.assertEqual(sum(1 for r in recs if r["type"] == "ack" and r["ok"]), 2)



class JevTest(BrainHarness):
    def jev_env(self, llm="off", jev_base=None):
        env = self.env_file()
        base = jev_base or f"http://127.0.0.1:{self.http.server_port}/api/v1"
        text = env.read_text().replace("BRAIN_LLM=openrouter", f"BRAIN_LLM={llm}")
        env.write_text(text + f"BRAIN_GATE=jev\nJEV_API_BASE={base}\nJEV_API_KEY=jev-test-key\n"
                       "JEV_MODEL=test/jev\nJEV_TIMEOUT=3\n")
        return env

    def private_message(self, env, text="привет, как ты?"):
        proc = self.start_brain(env)
        plugin = self.connect()
        plugin.send(dict(STATE, type="state"))
        plugin.send({"type": "event", "kind": "chat_private", "from": "Tester", "text": text})
        return proc, plugin

    def models(self):
        return [r["body"]["model"] for r in FakeOpenRouter.requests]

    def test_quick_reply_without_main_llm(self):
        FakeOpenRouter.jev_reply = {"importance": 2, "call_llm": False,
                                    "quick": {"action": "whisper", "text": "Привет. Занят, охочусь."},
                                    "why": "простое приветствие"}
        proc, plugin = self.private_message(self.jev_env(llm="openrouter"))
        action = plugin.recv()
        plugin.send({"type": "ack", "id": action["id"], "ok": True, "command": "pm"})
        self.wait_for(lambda: self.count("ack") >= 1, "ack быстрого ответа в журнале")
        plugin.close()
        self.stop(proc)
        self.assertEqual((action["action"], action["to"]), ("whisper", "Tester"))
        self.assertEqual(self.models(), ["test/jev"])        # DeepSeek не вызывался
        types = [r["type"] for r in self.decisions()]
        self.assertIn("jev", types)
        self.assertEqual([r["source"] for r in self.decisions() if r["type"] == "decision"], ["jev"])

    def test_jev_escalates_to_main_llm(self):
        FakeOpenRouter.jev_reply = {"importance": 4, "call_llm": True, "quick": None, "why": "важно"}
        proc, plugin = self.private_message(self.jev_env(llm="openrouter"))
        first = plugin.recv()
        self.wait_for(lambda: self.count("decision") >= 1, "решение основной модели в журнале")
        plugin.close()
        self.stop(proc)
        self.assertEqual(self.models(), ["test/jev", "test/model"])
        self.assertEqual(first["action"], "say")

    def test_jev_failure_falls_back_to_rules(self):
        proc, plugin = self.private_message(self.jev_env(llm="openrouter", jev_base="http://127.0.0.1:9/v1"))
        first = plugin.recv()
        self.wait_for(lambda: self.count("decision") >= 1, "решение основной модели в журнале")
        plugin.close()
        self.stop(proc)
        self.assertEqual(self.models(), ["test/model"])
        self.assertIn("jev_error", [r["type"] for r in self.decisions()])
        self.assertEqual(first["action"], "say")

    def test_peer_smalltalk_whispers_other_resident(self):
        env = self.env_file()
        env.write_text(env.read_text().replace("BRAIN_PEER_SMALLTALK=0", "BRAIN_PEER_SMALLTALK=3600"))
        decision = {"thought": "Давно не видел Vera.", "actions": [
            {"action": "whisper", "to": "Vera", "text": "Vera, ты где? На поле тихо."}]}
        import tests.test_brain as tb
        old, tb.DECISION = tb.DECISION, decision
        try:
            proc = self.start_brain(env)
            plugin = self.connect()
            plugin.send(dict(STATE, type="state"))
            action = plugin.recv()
            plugin.close()
            self.stop(proc)
        finally:
            tb.DECISION = old
        self.assertEqual((action["action"], action["to"]), ("whisper", "Vera"))
        reason = [r for r in self.decisions() if r["type"] == "decision"][0]["reason"]
        self.assertIn("давно не общался с Vera", reason)

    def test_check_jev(self):
        import subprocess, sys
        from tests.test_brain import BRAIN_DIR
        FakeOpenRouter.jev_reply = {"importance": 1, "call_llm": False, "quick": None, "why": "тест"}
        out = subprocess.run([sys.executable, "-m", "live_brain", "--env", str(self.jev_env()),
                              "--lab-root", str(self.root), "--check-jev"],
                             cwd=BRAIN_DIR, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("CHECK OK (JEV test/jev)", out.stdout)
        self.assertNotIn("jev-test-key", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
