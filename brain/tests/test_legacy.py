"""Наследие и уход на покой (legacy.py, ORG-083, ТЗ Т-36): условия, наследник, посылки, прощание, перепись.

Настоящий Mind (Arkady, соседи Vera и Rook) с включённым модулем; почта — через настоящий economy (действия
mail_send в журнале отправленного, ответ сервера mail_result подаётся вручную). Часы модуля подменные.
Запуск: cd brain && python3 -m unittest -v tests.test_legacy
"""
import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from live_brain import census
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.legacy import Legacy
from live_brain.memoir import build, render
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
ON = json.loads(json.dumps(WORLD))
ON["legacy"]["enabled"] = True
DAY = 86400


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class LegacyBase(unittest.TestCase):
    born_days = 100

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.clock = Clock()
        born = datetime.fromtimestamp(self.clock.t - self.born_days * DAY, timezone.utc).strftime("%Y-%m-%d")
        (root / "world").mkdir()
        (root / "world" / "roster.json").write_text(json.dumps({"residents": {
            "bot01": {"name": "Arkady", "active": True, "born": born}}}))
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        (root / "state" / "bot01").mkdir(parents=True)
        self.mem = Memory(root / "state" / "bot01" / "memory.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Vera", "Rook"}, world=ON)
        self.mind.director = None
        self.mind.legacy = Legacy(self.mind, ON, clock=self.clock, roster_dir=root / "world")
        self.lg = self.mind.legacy
        self.state(lv=50)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 50,
             "job": "Knight", "sex": "Male", "dead": False, "zeny": 80000, "items": {"4001": 2, "501": 10},
             "players": []}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def tick(self, dt=1):
        self.clock.t += dt
        self.lg.next_tick = 0
        asyncio.run(self.lg.tick())

    def events(self, kind):
        return [json.loads(r[0]) for r in self.mem.db.execute("SELECT data FROM events WHERE kind = ?", (kind,))]

    def mails(self):
        return [a for a in self.sent if a.get("action") == "mail_send"]

    def server_ok(self):
        """Ответ сервера на последнее письмо — как событие тела mail_result."""
        a = self.mails()[-1]
        asyncio.run(self.mind.on_message({"type": "event", "kind": "mail_result", "ok": True, "to": a["to"],
                                          "title": a["title"], "zeny": a.get("zeny", 0), "item": a.get("item"),
                                          "amount": a.get("amount")}))


class TestConditions(LegacyBase):
    def test_age_from_roster(self):
        self.assertEqual(self.lg.reasons(self.mind.state, self.clock.t), ["прожито 100 дн."])

    def test_level_and_long_dream(self):
        self.mem.add_event("dream_done", {"dream": "стать Knight", "days": 12})
        self.mem.add_event("dream_done", {"dream": "собрать 5 карт", "days": 40})
        self.state(lv=92)
        r = self.lg.reasons(self.mind.state, self.clock.t)
        self.assertEqual(r[:2], ["мечта сбылась: собрать 5 карт (40 дн.)", "уровень 92"])

    def test_young_stays(self):
        self.lg.roster_dir = Path(self.tmp.name) / "nowhere"           # нет реестра — возраст по памяти (свежей)
        self.tick()
        self.assertIsNone(self.lg.st.get("phase"))
        self.assertFalse(self.events("legacy_ready"))

    def test_blocked_by_engagement_and_mentee(self):
        self.mem.update_relation("Vera", 2)
        if self.mind.wed is not None:
            self.mind.wed.st["engaged"] = {"peer": "Vera", "since": self.clock.t}
            self.assertEqual(self.lg.blocker(self.mind.state, self.clock.t), "помолвка: сначала свадьба")
            self.mind.wed.st["engaged"] = None
        self.mind.mentor.st["role"] = "mentor"
        self.mind.mentor.st["peer"] = "Rook"
        self.assertEqual(self.lg.blocker(self.mind.state, self.clock.t), "ученик под опекой")
        self.tick()
        self.assertIsNone(self.lg.st.get("phase"))
        self.mind.mentor.st["role"] = None
        self.tick(3600)
        self.assertIsNone(self.lg.st.get("phase"))                     # не чаще check_hours
        self.tick(DAY)
        self.assertEqual(self.lg.st.get("phase"), "farewell")          # наследника нет: affinity Vera 2 < 4


class TestHeir(LegacyBase):
    def test_order(self):
        for _ in range(3):
            self.mem.update_relation("Vera", 2)                        # 6
            self.mem.update_relation("Rook", 2)
        self.mem.update_relation("Rook", -1)                           # 5
        self.assertEqual(self.lg.heir(), ("Vera", "друг"))
        self.mind.society.st["quarrel"]["Vera"] = {"since": self.clock.t, "cause": "тест"}
        self.assertEqual(self.lg.heir(), ("Rook", "друг"))
        if self.mind.wed is not None:
            self.mind.wed.st["married"] = {"peer": "Rook", "since": 0}
            self.assertEqual(self.lg.heir(), ("Rook", "спутник"))
        self.mind.mentor.st["history"] = [{"peer": "Rook", "role": "mentor"}]
        self.assertEqual(self.lg.heir(), ("Rook", "ученик"))

    def test_plan(self):
        p = self.lg.plan(self.mind.state)
        self.assertEqual(p[0], {"item": 4001, "amount": 2, "name": "Poring Card"})
        self.assertEqual(p[1], {"zeny": int((80000 - 20000 - 2500) / 1.02)})
        self.assertEqual(len(self.lg.plan(dict(self.mind.state, zeny=500000))), 2)
        self.assertEqual(self.lg.plan(dict(self.mind.state, zeny=500000))[1]["zeny"], 100000)   # max_zeny
        self.assertEqual(self.lg.plan(dict(self.mind.state, zeny=21000, items={})), [])


class TestFlow(LegacyBase):
    def test_bequest_farewell_retire(self):
        for _ in range(3):
            self.mem.update_relation("Vera", 2)
        self.mem.add_event("dream_done", {"dream": "стать Knight", "days": 45})
        self.tick()
        self.assertEqual(self.lg.st["phase"], "bequest")
        self.assertEqual(self.events("legacy_ready")[-1]["heir"], "Vera")
        self.tick()
        a = self.mails()[-1]
        self.assertEqual((a["to"], a["item"], a["amount"]), ("Vera", 4001, 2))
        self.tick()
        self.assertEqual(len(self.mails()), 1)                         # почта занята — ждём ответа сервера
        self.server_ok()
        self.tick()
        self.assertEqual(self.mails()[-1]["zeny"], int((80000 - 20000 - 2500) / 1.02))
        self.server_ok()
        self.tick()
        self.assertEqual(self.lg.st["phase"], "retired")
        ev = self.events("legacy_retired")[-1]
        self.assertEqual((ev["heir"], ev["gifts"]), ("Vera", 2))
        fw = self.mem.get("legacy_farewell")
        self.assertEqual(fw["days"], 100)
        self.assertEqual(fw["dreams"], ["стать Knight"])
        # мемуары: глава «Прощание» (Memoir перезаписывает файл на следующем такте)
        self.assertIsNone(self.mem.get("memoir_week"))
        self.mind.memoir.tick()
        text = (Path(self.tmp.name) / "state" / "bot01" / "memoir.md").read_text(encoding="utf-8")
        self.assertIn("## Прощание", text)
        self.assertIn("Наследник — Vera (друг): передал посылок: 2 — сервер подтвердил.", text)
        self.assertEqual(len(self.events("memoir_farewell")), 1)
        book = build(self.mem.db, "Arkady", "Male", now=self.clock.t)
        self.assertIn("Ухожу на покой", render(book))
        # летопись, тема разговора, дальше модуль молчит
        self.assertEqual(LINES["legacy_retired"]({"heir": "Vera", "gifts": 2}),
                         "ушёл(ушла) на покой; наследнику Vera передано посылок: 2")
        self.assertEqual(self.lg.facts("Rook", self.clock.t), {"heir": "Vera"})
        self.lg.said("Rook", {"heir": "Vera"}, self.clock.t)
        self.assertIsNone(self.lg.facts("Rook", self.clock.t))
        n = len(self.sent)
        self.tick(DAY * 3)
        self.assertEqual(len(self.sent), n)
        # мозг никаких действий с процессами/реестром не шлёт — только письма
        self.assertEqual({a["action"] for a in self.sent}, {"mail_send"})
        roster = json.loads((Path(self.tmp.name) / "world" / "roster.json").read_text())
        self.assertTrue(roster["residents"]["bot01"]["active"])


class TestCensus(unittest.TestCase):
    def test_retire_advice(self):
        rs = [{"bot": "bot01", "name": "Arkady", "active": True,
               "legacy": {"phase": "retired", "reasons": ["прожито 100 дн."], "heir": "Vera", "gifts": 2}},
              {"bot": "bot02", "name": "Vera", "active": True, "legacy": None},
              {"bot": "bot03", "name": "Rook", "active": False, "legacy": {"phase": "retired", "reasons": []}}]
        adv = census.retire_advice(rs)
        self.assertEqual([a["bot"] for a in adv], ["bot01", "bot03"])
        self.assertIn('"active": false', adv[0]["todo"])
        self.assertIn("scripts/lab stop live bot01", adv[0]["todo"])
        self.assertNotIn("todo", adv[1])                               # уже не активен — делать нечего

    def test_read_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(Path(tmp) / "memory.sqlite")
            mem.set("last_state", {"name": "Arkady"})
            mem.set("legacy", {"phase": "retired", "heir": "Vera"})
            mem.close()
            got = census.read_memory(Path(tmp) / "memory.sqlite", time.time(), {"Vera"})
            self.assertEqual(got["legacy"]["phase"], "retired")


class TestSwitch(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(WORLD["legacy"]["enabled"])
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(Path(tmp) / "m.sqlite")

            async def send(a):
                return 1
            persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
            m = Mind(Settings.from_env({}), persona, mem, send, Path(tmp) / "d.jsonl", RuleGate(),
                     peers={"Vera"}, world=WORLD)
            self.assertIsNone(m.legacy)
            m2 = Mind(Settings.from_env({"BRAIN_DISABLE": "legacy"}), persona, mem, send, Path(tmp) / "d.jsonl",
                      RuleGate(), peers={"Vera"}, world=ON)
            self.assertIsNone(m2.legacy)
            mem.close()


if __name__ == "__main__":
    unittest.main()
