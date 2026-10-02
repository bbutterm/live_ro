"""Сквозной тест встречи: два настоящих процесса мозга (Arkady, Vera) + фейковый мир.

Мир вместо OpenKore и сервера: позиции, движение к lockMap-точке (2 клетки за шаг),
видимость игроков в радиусе 14 клеток, доставка шёпота между ботами, ack и delivery.
LLM выключен: предложение даёт оператор (inbox), принятие — правило.

Запуск: cd brain && python3 -m unittest -v tests.test_plans
"""
import json
import os
import select
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.worldtime import brain_argv, shift_time

BRAIN_DIR = Path(__file__).resolve().parents[1]
NAMES = {"bot01": "Arkady", "bot02": "Vera"}
IDENTITY = {"Arkady": ("Swordman", "Male", 40), "Vera": ("Acolyte", "Female", 30)}


class Body:
    def __init__(self, bot, x, y):
        self.bot, self.name, self.map, self.x, self.y = bot, NAMES[bot], "prt_fild08", x, y
        self.lock = None
        self.sock, self.buf = None, b""


class World:
    def __init__(self, root):
        self.root = root
        self.bodies = {"bot01": Body("bot01", 130, 140), "bot02": Body("bot02", 100, 100)}
        self.last_state = 0
        self.log = []

    def connect(self):
        for b in self.bodies.values():
            if b.sock is None:
                path = self.root / "run" / "brain" / f"{b.bot}.sock"
                if path.exists():
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    try:
                        s.connect(str(path))
                    except OSError:
                        s.close()
                        continue
                    s.setblocking(False)
                    b.sock, b.buf = s, b""
                    self.send(b, {"type": "hello", "char": b.name})

    def send(self, b, msg):
        if b.sock is None:
            return
        try:
            b.sock.sendall((json.dumps(dict(msg, ts=time.time()), ensure_ascii=False) + "\n").encode())
        except OSError:
            b.sock = None

    def by_name(self, name):
        return next((b for b in self.bodies.values() if b.name == name), None)

    def handle(self, b, msg):
        if msg.get("type") != "action":
            return
        kind, cmd = msg["action"], msg["action"]
        if kind == "meet_point":
            b.lock = (msg["map"], msg["x"], msg["y"])
            cmd = f"conf lockMap_x {msg['x']}"
        elif kind == "clear_point":
            b.lock = None
        self.log.append((b.name, kind, msg.get("to"), msg.get("text")))
        self.send(b, {"type": "ack", "id": msg["id"], "ok": True, "command": cmd})
        if kind == "whisper":
            target = self.by_name(msg["to"])
            self.send(b, {"type": "delivery", "id": msg["id"], "action": "whisper", "to": msg["to"],
                          "ok": target is not None, "code": 0 if target else 1, "reason": "test"})
            if target:
                self.send(target, {"type": "event", "kind": "chat_private", "from": b.name,
                                   "text": msg["text"], "map": target.map})

    def step(self):
        self.connect()
        for b in self.bodies.values():
            if b.sock is None:
                continue
            r, _, _ = select.select([b.sock], [], [], 0)
            if r:
                try:
                    data = b.sock.recv(65536)
                except OSError:
                    data = b""
                if not data:
                    b.sock = None
                    continue
                b.buf += data
                while b"\n" in b.buf:
                    line, b.buf = b.buf.split(b"\n", 1)
                    self.handle(b, json.loads(line))
        if time.time() - self.last_state >= 0.3:
            self.last_state = time.time()
            for b in self.bodies.values():
                if b.lock and b.lock[0] == b.map:
                    b.x += max(-2, min(2, b.lock[1] - b.x))
                    b.y += max(-2, min(2, b.lock[2] - b.y))
            for b in self.bodies.values():
                others = [o for o in self.bodies.values() if o is not b and o.map == b.map
                          and max(abs(o.x - b.x), abs(o.y - b.y)) <= 14]
                job, sex, lv = IDENTITY[b.name]
                self.send(b, {"type": "state", "name": b.name, "job": job, "sex": sex, "lv": lv,
                              "hp_pct": 90, "map": b.map, "x": b.x, "y": b.y, "lock_map": "prt_fild08",
                              "lock_x": b.lock[1] if b.lock else None, "lock_y": b.lock[2] if b.lock else None,
                              "ai": "auto", "activity": "route" if b.lock else "attack", "dead": False,
                              "players": [{"name": o.name, "job": IDENTITY[o.name][0], "sex": IDENTITY[o.name][1],
                                           "lv": IDENTITY[o.name][2], "x": o.x, "y": o.y} for o in others]})

    def run_until(self, cond, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.step()
            if cond():
                return True
            time.sleep(0.05)
        return False


class MeetingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = self.root / "live_ro.env"
        self.env.write_text("BRAIN_LLM=off\nBRAIN_PLAN_AUTO_ACCEPT=1\nBRAIN_PLAN_ANSWER_TIMEOUT=8\n"
                            "BRAIN_PLAN_TRAVEL_TIMEOUT=60\nBRAIN_PLAN_WAIT_TIMEOUT=60\n")
        self.procs = {}
        self.world = World(self.root)
        self.shift = shift_time(self)        # timefix: полдень мира фиксированного дня — встреча не упирается в сон

    def tearDown(self):
        for p in self.procs.values():
            if p.poll() is None:
                p.kill()
        self.tmp.cleanup()

    def start(self, bot):
        with open(self.root / f"{bot}.log", "ab") as log:
            self.procs[bot] = subprocess.Popen(
                brain_argv(self.shift, "--env", str(self.env), "--bot", bot, "--lab-root", str(self.root)), cwd=BRAIN_DIR, stdout=log, stderr=subprocess.STDOUT)

    def stop(self, bot):
        self.procs[bot].terminate()
        self.assertEqual(self.procs[bot].wait(timeout=10), 0)
        body = self.world.bodies.get(bot)
        if body and body.sock:
            body.sock.close()
            body.sock = None

    def operator(self, bot, cmd, who=""):
        (self.root / "run" / "brain").mkdir(parents=True, exist_ok=True)
        with open(self.root / "run" / "brain" / f"{bot}.inbox", "a") as f:
            f.write(json.dumps({"cmd": cmd, "with": who}) + "\n")

    def db(self, bot):
        return sqlite3.connect(self.root / "state" / bot / "memory.sqlite")

    def plan(self, bot):
        if not (self.root / "state" / bot / "memory.sqlite").exists():
            return None
        con = self.db(bot)
        try:
            row = con.execute("SELECT status, phase, result, history FROM plans ORDER BY created DESC LIMIT 1").fetchone()
        except sqlite3.OperationalError:
            row = None
        con.close()
        return row

    def memories(self, bot):
        con = self.db(bot)
        rows = [r[0] for r in con.execute("SELECT text FROM memories ORDER BY id")]
        con.close()
        return rows

    def decisions(self, bot):
        path = self.root / "state" / bot / "decisions.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def connected(self):
        return all(b.sock is not None for b in self.world.bodies.values())

    def test_meeting_with_brain_restart_midway(self):
        self.start("bot01")
        self.start("bot02")
        self.assertTrue(self.world.run_until(self.connected, 10))
        self.world.run_until(lambda: False, 1)                  # оба получили состояние
        self.operator("bot02", "meet", "Arkady")
        # Arkady принял (правило), и мир (тело) уже получил точку встречи
        self.assertTrue(self.world.run_until(lambda: self.world.bodies["bot01"].lock is not None, 20))
        self.world.run_until(lambda: False, 0.7)            # тело успело сделать пару шагов
        self.stop("bot01")                                      # мозг упал посреди пути, тело идёт дальше
        self.world.run_until(lambda: False, 1)
        self.start("bot01")
        done = lambda: (self.plan("bot01") or ("",))[0] == "completed" and (self.plan("bot02") or ("",))[0] == "completed"
        self.assertTrue(self.world.run_until(done, 40), (self.plan("bot01"), self.plan("bot02")))
        self.world.run_until(lambda: False, 1.5)
        for bot in ("bot01", "bot02"):
            self.stop(bot)

        arkady, vera = self.memories("bot01"), self.memories("bot02")
        self.assertTrue(any("Я встретился с Vera" in m and "Vera была" in m for m in arkady), arkady)
        self.assertTrue(any("Я встретился с Arkady" in m for m in vera), vera)
        self.assertTrue(any("Я предложил Arkady встретиться" in m for m in vera))      # отдельное событие
        self.assertTrue(any("Vera предложил встретиться" in m for m in arkady))
        rec = [d for d in self.decisions("bot01") if d.get("event") == "reconciled"]
        self.assertEqual(len(rec), 1)
        self.assertTrue(rec[0]["point_set"])            # точка в игре была — команда не повторялась
        arkady_body = self.world.bodies["bot01"]
        self.assertIsNone(arkady_body.lock)             # после встречи вернулся к охоте
        points = [e for e in self.world.log if e[0] == "Arkady" and e[1] == "meet_point"]
        self.assertEqual(len(points), 1, points)

    def test_restart_with_point_lost_resends_point(self):
        self.start("bot01")
        self.start("bot02")
        self.assertTrue(self.world.run_until(self.connected, 10))
        self.world.run_until(lambda: False, 1)
        self.operator("bot02", "meet", "Arkady")
        # Arkady принял (правило), и мир (тело) уже получил точку встречи
        self.assertTrue(self.world.run_until(lambda: self.world.bodies["bot01"].lock is not None, 20))
        self.world.run_until(lambda: False, 0.7)            # тело успело сделать пару шагов
        self.stop("bot01")
        self.world.bodies["bot01"].lock = None           # как после перезапуска OpenKore: точка сброшена
        self.start("bot01")
        done = lambda: (self.plan("bot01") or ("",))[0] == "completed"
        self.assertTrue(self.world.run_until(done, 40), self.plan("bot01"))
        for bot in ("bot01", "bot02"):
            self.stop(bot)
        rec = [d for d in self.decisions("bot01") if d.get("event") == "reconciled"]
        self.assertFalse(rec[0]["point_set"])
        points = [e for e in self.world.log if e[0] == "Arkady" and e[1] == "meet_point"]
        self.assertEqual(len(points), 2)                 # вторая — после сверки, а не вслепую

    def test_no_answer_fails_and_is_remembered(self):
        self.start("bot02")                             # Arkady не запущен: ответа не будет
        self.world.bodies.pop("bot01")
        self.assertTrue(self.world.run_until(self.connected, 10))
        self.world.run_until(lambda: False, 1)
        self.operator("bot02", "meet", "Arkady")
        self.assertTrue(self.world.run_until(lambda: (self.plan("bot02") or ("",))[0] == "failed", 20))
        self.stop("bot02")
        vera = self.memories("bot02")
        self.assertTrue(any("Я предложил Arkady" in m for m in vera))
        self.assertTrue(any("Встреча с Arkady не состоялась" in m for m in vera))
        self.assertFalse(any("Я встретился" in m for m in vera))


if __name__ == "__main__":
    unittest.main()


from tests.test_brain import BrainHarness, FakeOpenRouter  # noqa: E402


class LlmPlanActionTest(BrainHarness):
    def test_llm_proposes_meeting_and_sees_plan(self):
        import tests.test_brain as tb
        old, tb.DECISION = tb.DECISION, {"thought": "Хочу увидеть Vera.", "goal": "встретиться с Vera",
                                          "actions": [{"action": "propose_meeting", "to": "Vera"}]}
        try:
            proc = self.start_brain(self.env_file())
            plugin = self.connect()
            plugin.send({"type": "state", "name": "Arkady", "hp_pct": 90, "map": "prt_fild08", "x": 150, "y": 160,
                         "lock_map": "prt_fild08", "dead": False, "players": []})
            plugin.send({"type": "event", "kind": "chat_private", "from": "Tester", "text": "Ты давно видел Vera?"})
            whisper = plugin.recv()
            time.sleep(1)
            plugin.close()
            self.stop(proc)
        finally:
            tb.DECISION = old
        self.assertEqual((whisper["action"], whisper["to"]), ("whisper", "Vera"))
        self.assertRegex(whisper["text"], r"\[meet:[a-z0-9]{6}:prt_fild08:150:160\]")
        prompt = json.loads(FakeOpenRouter.requests[0]["body"]["messages"][1]["content"])
        self.assertIn("план", prompt)
        self.assertIn("propose_meeting", FakeOpenRouter.requests[0]["body"]["messages"][0]["content"])
        plans = [d for d in self.decisions() if d["type"] == "plan_decision"]
        self.assertEqual(plans[0]["result"], "ok")
