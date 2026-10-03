"""Сквозной тест live_brain без сети и без OpenKore.

Фейковый OpenRouter (http.server) + настоящий процесс `python3 -m live_brain` +
фейковый плагин brainBridge, подключающийся к Unix-сокету.

Запуск: cd brain && python3 -m unittest -v tests.test_brain
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from tests.worldtime import brain_argv, shift_time

BRAIN_DIR = Path(__file__).resolve().parents[1]
FAKE_KEY = "sk-or-v1-" + "0123456789abcdef" * 4

DECISION = {
    "thought": "Кто-то зовёт меня. Отвечу и сменю поле.",
    "goal": "качаться на prt_fild07",
    "mood": "бодрый",
    "actions": [
        {"action": "say", "text": "Привет, Tester. Некогда болтать, охочусь."},
        {"action": "set_hunt_map", "map": "prt_fild07"},
        {"action": "set_hunt_map", "map": "gef_dun02"},
    ],
    "remember": [{"text": "Tester поздоровался со мной у prt_fild08", "importance": 4}],
    "relations": [{"name": "Tester", "delta": 1, "note": "вежливый незнакомец"}],
}


JEV_REPLY = {"importance": 2, "call_llm": False,
             "quick": {"action": "whisper", "text": "Привет. Занят, охочусь."}, "why": "простое приветствие"}


class FakeOpenRouter(BaseHTTPRequestHandler):
    """Один фейковый сервер для обоих провайдеров: модель test/jev отвечает как JEV."""
    requests = []
    jev_reply = dict(JEV_REPLY)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenRouter.requests.append({"auth": self.headers.get("Authorization"), "body": body})
        system = body["messages"][0]["content"]
        if body["model"] == "test/jev":
            content = json.dumps(FakeOpenRouter.jev_reply, ensure_ascii=False)
        elif "Проверка связи" in body["messages"][1]["content"]:
            content = json.dumps({"greeting": "Здорово."}, ensure_ascii=False)
        else:
            content = json.dumps(DECISION, ensure_ascii=False)
        assert "Arkady" in system
        payload = {"choices": [{"message": {"content": content}}],
                   "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.01}}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class FakePlugin:
    def __init__(self, path, timeout=15):
        deadline = time.time() + timeout
        while not os.path.exists(path):
            if time.time() > deadline:
                raise TimeoutError("мозг не создал сокет")
            time.sleep(0.1)
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.f = self.s.makefile("rwb")

    def send(self, msg):
        msg.setdefault("ts", time.time())
        self.f.write((json.dumps(msg, ensure_ascii=False) + "\n").encode())
        self.f.flush()

    # Сборку группы жителей (party.py) мозг ведёт сам в фоне — эти тесты о другом.
    BACKGROUND = ("party_create", "party_invite", "party_leave")

    def recv(self, timeout=15):
        self.s.settimeout(timeout)
        while True:
            line = self.f.readline()
            msg = json.loads(line) if line else None
            if not msg or msg.get("action") not in self.BACKGROUND:
                return msg

    def close(self):
        self.s.close()


class BrainHarness(unittest.TestCase):
    """Фейковый OpenRouter, процесс мозга и фейковый плагин. Тестов не содержит."""

    @classmethod
    def setUpClass(cls):
        cls.http = HTTPServer(("127.0.0.1", 0), FakeOpenRouter)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        FakeOpenRouter.requests.clear()
        FakeOpenRouter.jev_reply = dict(JEV_REPLY)    # тесты меняют ответ JEV — не тащить его в следующий тест
        self.shift = shift_time(self)        # timefix: полдень мира фиксированного дня — тесты не о распорядке

    def tearDown(self):
        self.tmp.cleanup()

    def env_file(self, key=FAKE_KEY, **extra):
        values = {
            "BRAIN_LLM": "openrouter",
            "OPENROUTER_API_KEY": key,
            "OPENROUTER_MODEL": "test/model",
            "BRAIN_API_BASE": f"http://127.0.0.1:{self.http.server_port}/api/v1",
            "BRAIN_DECIDE_INTERVAL": "3600",
            "BRAIN_CHAT_MIN_GAP": "1",
            "BRAIN_PEER_SMALLTALK": "0",
            **extra,
        }
        path = self.root / "live_ro.env"
        path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        return path

    def start_brain(self, env):
        with open(self.root / "brain.log", "ab") as log:
            proc = subprocess.Popen(
                brain_argv(self.shift, "--env", str(env), "--bot", "bot01", "--lab-root", str(self.root)),
                cwd=BRAIN_DIR, stdout=log, stderr=subprocess.STDOUT)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def stop(self, proc):
        proc.terminate()
        self.assertEqual(proc.wait(timeout=10), 0)

    def decisions(self, background=False):
        """Журнал решений; фоновая сборка группы (source/type party) — только по запросу."""
        path = self.root / "state" / "bot01" / "decisions.jsonl"
        recs = [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
        return recs if background else [r for r in recs if "party" not in (r.get("source"), r.get("type"))]

    def connect(self):
        return FakePlugin(str(self.root / "run" / "brain" / "bot01.sock"))

    def wait_for(self, cond, what, timeout=20):
        """flaky: ждать, пока мозг дойдёт до нужного состояния, вместо фиксированного sleep — под нагрузкой
        (параллельные прогоны) процесс мозга отвечает медленнее, и пауза в 1–3 с иногда не хватала."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                if cond():
                    return
            except (ValueError, OSError, sqlite3.Error):     # журнал/БД дописываются прямо сейчас
                pass
            if time.monotonic() > deadline:
                self.fail(f"за {timeout} с не дождались: {what}")
            time.sleep(0.05)

    def count(self, rtype, background=False):
        return sum(1 for r in self.decisions(background) if r["type"] == rtype)



class BrainTest(BrainHarness):
    def test_chat_decision_execution_and_memory(self):
        env = self.env_file()
        proc = self.start_brain(env)
        plugin = self.connect()
        plugin.send({"type": "hello", "char": "Arkady"})
        plugin.send({"type": "state", "name": "Arkady", "lv": 17, "hp_pct": 80, "map": "prt_fild08",
                     "lock_map": "prt_fild08", "ai": "auto"})
        plugin.send({"type": "event", "kind": "chat_private", "from": "Tester", "text": "Привет, Arkady!",
                     "map": "prt_fild08"})

        actions = [plugin.recv(), plugin.recv()]
        # Смена карты идёт через распорядок: модель выбирает карту, тело получает команду hunt.
        self.assertEqual([a["action"] for a in actions], ["say", "hunt"])
        self.assertEqual(actions[1]["map"], "prt_fild07")
        for a in actions:
            plugin.send({"type": "ack", "id": a["id"], "ok": True, "command": f"cmd-{a['action']}"})
        self.wait_for(lambda: self.count("ack") >= 2, "оба ack в журнале")
        plugin.close()
        self.stop(proc)

        req = FakeOpenRouter.requests[0]
        self.assertEqual(req["auth"], f"Bearer {FAKE_KEY}")
        self.assertIn("Tester", req["body"]["messages"][1]["content"])

        recs = self.decisions()
        decision = [r for r in recs if r["type"] == "decision" and r["source"] == "llm"][0]
        self.assertEqual([a["action"] for a in decision["actions"]], ["say"])
        self.assertEqual(decision["source"], "llm")
        routine = [r for r in recs if r["type"] == "routine_decision"]
        self.assertEqual([(r["action"]["map"], r["result"]) for r in routine],
                         [("prt_fild07", "ok"), ("gef_dun02", "карта не из списка hunt_maps")])
        acks = [r for r in recs if r["type"] == "ack"]
        self.assertEqual(sorted(a["command"] for a in acks), ["cmd-hunt", "cmd-say"])

        db = sqlite3.connect(self.root / "state" / "bot01" / "memory.sqlite")
        self.assertIn("Tester поздоровался", " ".join(r[0] for r in db.execute("SELECT text FROM memories")))
        self.assertEqual(db.execute("SELECT affinity FROM relations WHERE name='Tester'").fetchone()[0], 1)
        db.close()

        # Память переживает перезапуск, ключ не попадает в лог.
        proc = self.start_brain(env)
        self.wait_for(lambda: (self.root / "brain.log").read_text().count("; память: ") >= 2,
                      "второй запуск загрузил память")
        self.stop(proc)
        log = (self.root / "brain.log").read_text()
        self.assertNotIn(FAKE_KEY, log)
        self.assertRegex(log, r"память: [1-9]\d* воспоминаний")

    def test_fallback_without_key(self):
        env = self.env_file(key="", BRAIN_DECIDE_INTERVAL="1")
        proc = self.start_brain(env)
        plugin = self.connect()
        plugin.send({"type": "state", "name": "Arkady", "lv": 17, "map": "prt_fild08"})
        plugin.send({"type": "event", "kind": "died", "map": "prt_fild08"})
        self.wait_for(lambda: self.count("fallback") >= 1, "решение fallback без ключа")
        plugin.close()
        self.stop(proc)
        self.assertEqual(FakeOpenRouter.requests, [])
        self.assertTrue(any(r["type"] == "fallback" for r in self.decisions()))
        db = sqlite3.connect(self.root / "state" / "bot01" / "memory.sqlite")
        self.assertIn("погиб", " ".join(r[0] for r in db.execute("SELECT text FROM memories")))
        db.close()

    def test_llm_off_by_default_even_with_key(self):
        env = self.root / "live_ro.env"
        env.write_text(f"OPENROUTER_API_KEY={FAKE_KEY}\n"
                       f"BRAIN_API_BASE=http://127.0.0.1:{self.http.server_port}/api/v1\n"
                       "BRAIN_DECIDE_INTERVAL=1\n")
        out = subprocess.run(
            [sys.executable, "-m", "live_brain", "--env", str(env), "--lab-root", str(self.root), "--check"],
            cwd=BRAIN_DIR, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 2)
        self.assertIn("CHECK SKIP", out.stdout)
        proc = self.start_brain(env)
        plugin = self.connect()
        plugin.send({"type": "state", "name": "Arkady", "lv": 17, "map": "prt_fild08"})
        time.sleep(2.5)
        plugin.close()
        self.stop(proc)
        self.assertEqual(FakeOpenRouter.requests, [])

    def test_check_command(self):
        env = self.env_file()
        out = subprocess.run(
            [sys.executable, "-m", "live_brain", "--env", str(env), "--lab-root", str(self.root), "--check"],
            cwd=BRAIN_DIR, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("CHECK OK", out.stdout)
        self.assertNotIn(FAKE_KEY, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
