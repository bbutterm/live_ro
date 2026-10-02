"""Взгляд на собеседника (gaze.py, ORG-067, ТЗ Т-29): поворот к собеседнику при разговоре и встрече, лимиты,
подтверждение по направлению соседа (dir в state.players), safety look_at.

Настоящий Mind (мир goals.json, два жителя), поддельное тело записывает действия; часы модуля подменные.
Мост (lookp по имени, dir в players) проверяется в bots/tests/brain_bridge.t.
Запуск: cd brain && python3 -m unittest -v tests.test_gaze
"""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path

from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.gaze import direction, faces
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.safety import SafetyPolicy

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Lab:
    def __init__(self, world=None, env=None):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out = []

        async def send(a):
            self.out.append(dict(a))
            return len(self.out)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.clock = Clock(T0)
        self.mind = Mind(Settings.from_env(env or {}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=world or copy.deepcopy(WORLD))
        self.g = self.mind.gaze
        if self.g:
            self.g.clock = self.clock

    def state(self, vera=(158, 185), **kw):
        players = [] if vera is None else [{"name": "Vera", "x": vera[0], "y": vera[1], "lv": 30,
                                            "dir": kw.pop("vera_dir", None)}]
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 30,
             "dead": False, "weight_pct": 10, "zeny": 1000, "items": {"501": 30}, "players": players,
             "activity": "idle"}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def tick(self, dt=3):
        self.clock.t += dt
        asyncio.run(self.g.tick())

    def looks(self):
        return [a for a in self.out if a["action"] == "look_at"]

    def close(self):
        self.mem.close()
        self.tmp.cleanup()


class DirectionTest(unittest.TestCase):
    def test_eight_winds(self):
        # OpenKore Misc::lookAtPosition: round((360 - vectorToDegree) / 45) % 8; 0 — север (y+), 2 — запад, 4 — юг, 6 — восток
        me = {"x": 100, "y": 100}
        cases = {(0, 1): 0, (-1, 1): 1, (-1, 0): 2, (-1, -1): 3, (0, -1): 4, (1, -1): 5, (1, 0): 6, (1, 1): 7}
        for (dx, dy), want in cases.items():
            self.assertEqual(direction(me, {"x": 100 + dx * 5, "y": 100 + dy * 5}), want, (dx, dy))
        self.assertIsNone(direction(me, me))

    def test_faces(self):
        self.assertTrue(faces({"x": 158, "y": 185, "dir": 2}, {"x": 156, "y": 185}), "Vera к западу — смотрит на меня")
        self.assertFalse(faces({"x": 158, "y": 185, "dir": 6}, {"x": 156, "y": 185}), "спиной")
        self.assertFalse(faces({"x": 158, "y": 185}, {"x": 156, "y": 185}), "направление неизвестно")


class LookTest(unittest.TestCase):
    def setUp(self):
        self.lab = Lab()
        self.lab.mind.routine.st.update(mode="town", arrived=True)

    def tearDown(self):
        self.lab.close()

    def say(self, peer="Vera"):
        self.lab.mem.add_event("social_said", {"peer": peer, "topic": "hello", "fact": False, "text": "Привет!"})

    def test_after_social_said(self):
        lab = self.lab
        lab.state()
        lab.tick()                                    # первый такт — курсор, история не переносится
        self.assertFalse(lab.looks())
        self.say()
        lab.tick()
        self.assertEqual([a["name"] for a in lab.looks()], ["Vera"])
        self.say()
        lab.tick()
        self.assertEqual(len(lab.looks()), 1, "не чаще gap/peer_gap")
        lab.clock.t += 61
        lab.mind.safety.looked = 0                    # safety считает по настоящим часам — «прошла минута»
        self.say()
        lab.tick()
        self.assertEqual(len(lab.looks()), 2, "через минуту — снова")
        self.assertEqual(lab.g.st["sent"], 2)

    def test_meeting(self):
        lab = self.lab
        lab.state()
        lab.tick()
        lab.mem.add_event("meeting_confirmed", {"partner": "Vera", "map": "prontera"})
        lab.tick()
        self.assertEqual([a["name"] for a in lab.looks()], ["Vera"])

    def test_incoming_chat_echo(self):
        lab = self.lab
        lab.state()
        asyncio.run(lab.mind.on_event({"type": "event", "kind": "chat_private", "from": "Vera",
                                       "text": "Привет! [chat:hello:1]"}))
        self.assertEqual([a["name"] for a in lab.looks()], ["Vera"], "житель заговорил — повернулся к нему")

    def test_not_now(self):
        lab = self.lab
        lab.state()
        lab.tick()
        for kw in ({"map": "prt_fild08"}, {"activity": "route"}, {"vera": None}, {"vera": (156, 199)},
                   {"dead": True}, {"dir": 6}):
            lab.out.clear()
            lab.clock.t += 120
            lab.state(**kw)
            self.say()
            lab.tick()
            self.assertFalse(lab.looks(), kw)
        self.say("Stranger")
        lab.state()
        lab.clock.t += 120
        lab.tick()
        self.assertFalse(lab.looks(), "не житель — нет")

    def test_seen_counter(self):
        lab = self.lab
        lab.state(vera_dir=2)
        lab.tick()
        self.assertEqual(lab.g.st["seen"], 1, "Vera смотрит на меня — подтверждение взгляда")
        lab.tick()
        self.assertEqual(lab.g.st["seen"], 1, "не чаще раза в минуту")
        lab.state(vera_dir=6)
        lab.clock.t += 120
        lab.tick()
        self.assertEqual(lab.g.st["seen"], 1, "спиной — не считается")


class SafetyTest(unittest.TestCase):
    def test_look_at(self):
        s = SafetyPolicy(["prt_fild08"], extra_point_maps=["prontera"])
        st = {"map": "prontera"}
        self.assertEqual(s.check({"action": "look_at", "name": "Vera"}, st)[1], "неизвестное действие", "модели — нет")
        a, why = s.check({"action": "look_at", "name": "Vera"}, st, now=100, protocol=True)
        self.assertEqual((a, why), ({"action": "look_at", "name": "Vera"}, None))
        self.assertIn("20 с", s.check({"action": "look_at", "name": "Vera"}, st, now=110, protocol=True)[1])
        self.assertIsNone(s.check({"action": "look_at", "name": "Vera"}, st, now=121, protocol=True)[1])
        self.assertIn("городе", s.check({"action": "look_at", "name": "Vera"}, {"map": "prt_fild08"}, now=200,
                                        protocol=True)[1])
        self.assertIn("имя", s.check({"action": "look_at", "name": 'Ve"ra'}, st, now=300, protocol=True)[1])
        self.assertIsNotNone(s.check({"action": "look_at", "name": "Vera"}, dict(st, dead=True), now=400,
                                     protocol=True)[1])


class SwitchTest(unittest.TestCase):
    def test_disabled(self):
        w = copy.deepcopy(WORLD)
        w["gaze"] = dict(w.get("gaze") or {}, enabled=False)
        for kw in ({"world": w}, {"env": {"BRAIN_DISABLE": "gaze"}}):
            lab = Lab(**kw)
            try:
                self.assertIsNone(lab.mind.gaze)
            finally:
                lab.close()


if __name__ == "__main__":
    unittest.main()
