"""Лимиты и транспорт JEV: выбор провайдера, обрезка промпта, деньги, гонка лимита JEV.

Запуск: cd brain && python3 -m unittest -v tests.test_limits
"""
import json
import time
import unittest

from live_brain.config import TYPESAFE_ENDPOINT, Settings
from live_brain.mind import fit_json
from tests.test_brain import BrainHarness, FakeOpenRouter
from tests.test_rules import STATE


class ConfigTest(unittest.TestCase):
    def test_jev_provider_kind(self):
        old_env = {"JEV_API_BASE": TYPESAFE_ENDPOINT}            # env на VPS до JEV_PROVIDER
        self.assertEqual(Settings.from_env(old_env).jev.kind, "typesafe")
        self.assertEqual(Settings.from_env({"JEV_API_BASE": "http://x/v1"}).jev.kind, "openai")
        self.assertEqual(Settings.from_env({"JEV_API_BASE": "http://x/v1", "JEV_PROVIDER": "typesafe"}).jev.kind,
                         "typesafe")

    def test_usd_limit_parsed(self):
        self.assertEqual(Settings.from_env({}).daily_usd_limit, 1.0)
        self.assertEqual(Settings.from_env({"BRAIN_DAILY_USD_LIMIT": "0.25"}).daily_usd_limit, 0.25)

    def test_fit_json_drops_old_events_first(self):
        data = {"повод": "x", "последние_события": [{"text": "a" * 300, "n": i} for i in range(20)],
                "воспоминания": [{"text": "важное"}]}
        text = fit_json(data, 2000)
        self.assertLessEqual(len(text), 2000)
        obj = json.loads(text)
        self.assertEqual(obj["воспоминания"], [{"text": "важное"}])
        self.assertEqual(obj["последние_события"][-1]["n"], 19)   # свежие остаются


class LiveLimitsTest(BrainHarness):
    def models(self):
        return [r["body"]["model"] for r in FakeOpenRouter.requests]

    def test_usd_limit_stops_main_llm(self):
        env = self.env_file()
        env.write_text(env.read_text() + "BRAIN_DAILY_USD_LIMIT=0.005\n")
        proc = self.start_brain(env)
        plugin = self.connect()
        plugin.send(dict(STATE, type="state"))
        for who in ("Tester", "Other"):
            plugin.send({"type": "event", "kind": "chat_private", "from": who, "text": "привет"})
            time.sleep(1.6)
        time.sleep(1)
        plugin.close()
        self.stop(proc)
        self.assertEqual(self.models(), ["test/model"])             # второй вызов не сделан
        fallbacks = [r for r in self.decisions() if r["type"] == "fallback"]
        self.assertIn("денежный лимит", fallbacks[-1]["why"])

    def test_jev_limit_holds_under_parallel_events(self):
        FakeOpenRouter.jev_reply = {"importance": 1, "call_llm": False, "quick": None, "why": "тест"}
        env = self.env_file()
        base = f"http://127.0.0.1:{self.http.server_port}/api/v1"
        env.write_text(env.read_text().replace("BRAIN_LLM=openrouter", "BRAIN_LLM=off")
                       + f"BRAIN_GATE=jev\nJEV_PROVIDER=openai\nJEV_API_BASE={base}\nJEV_API_KEY=k\n"
                         "JEV_MODEL=test/jev\nJEV_DAILY_LIMIT=1\n")
        proc = self.start_brain(env)
        plugin = self.connect()
        plugin.send(dict(STATE, type="state"))
        for who in ("A", "B", "C"):
            plugin.send({"type": "event", "kind": "chat_private", "from": who, "text": "привет"})
        time.sleep(2)
        plugin.close()
        self.stop(proc)
        self.assertEqual(self.models().count("test/jev"), 1)
        self.assertEqual(sum(1 for r in self.decisions() if r["type"] == "jev_skip"), 2)


if __name__ == "__main__":
    unittest.main()
