"""Заточка у кузнеца (refine.py, ORG-072, ТЗ Т-28): выбор шагов до безопасного предела, ритуал, подтверждение.

Настоящий Mind (мир goals.json с refine.enabled=true, два жителя), поддельное тело записывает действия; часы и
случай модуля подменные. Исполнитель в теле (плагин refine) проверяется отдельно: bots/tests/refine.t.
Запуск: cd brain && python3 -m unittest -v tests.test_refine
"""
import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.lifecycle import quest_busy
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.refine import load
from live_brain.routine import load_world
from live_brain.safety import SafetyPolicy

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT = BRAIN_DIR.parent
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
RATHENA = Path(os.environ.get("LIVE_RO_RATHENA") or ROOT / "upstream" / "rathena")
T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Lucky:
    def __init__(self, x=0.0):
        self.x = x

    def random(self):
        return self.x

    def choice(self, seq):
        return seq[0]


def world(enabled=True):
    w = copy.deepcopy(WORLD)
    w["refine"] = dict(w.get("refine") or {}, enabled=enabled)
    return w


class Lab:
    def __init__(self, w=None, env=None):
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
                         peers={"Arkady", "Vera"}, world=w or world())
        self.r = self.mind.refine
        if self.r:
            self.r.clock = self.clock
            self.r.rng = Lucky()

    def state(self, up=4, zeny=60000, ores=1, running=False, weapon=1201, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 30,
             "dead": False, "weight_pct": 10, "zeny": zeny, "items": {"501": 30}, "players": [],
             "refine": {"running": running, "phase": None, "ores": {"1010": ores, "1011": 0},
                        "weapon": {"id": weapon, "inv": 3, "name": "Knife", "upgrade": up, "equipped": True}}}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def town(self):
        self.mind.routine.st.update(mode="town", arrived=True)

    def tick(self):
        self.r.next_check = 0
        asyncio.run(self.r.tick())

    def event(self, **ev):
        asyncio.run(self.mind.on_event(dict(ev, type="event", kind="refine_result")))

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ? ORDER BY id",
                                                              (kind,))]

    def close(self):
        self.mem.close()
        self.tmp.cleanup()


class DataTest(unittest.TestCase):
    def test_data(self):
        d = load()
        self.assertIn(1201, d["weapons"]["1"], "Knife — оружие ур. 1")
        self.assertEqual({k: v["safe"] for k, v in d["levels"].items() if k in "1234"},
                         {"1": 7, "2": 6, "3": 5, "4": 4}, "безопасный предел renewal (refine.yml Rate 10000)")
        self.assertEqual((d["levels"]["1"]["ore"], d["levels"]["1"]["fee"]), (1010, 50))
        self.assertEqual((d["levels"]["2"]["ore"], d["levels"]["2"]["fee"]), (1011, 200))
        self.assertEqual((d["smith"]["npc"], d["smith"]["x"], d["smith"]["y"]), ("Hollgrehenn", 63, 60))
        self.assertEqual(d["shop"]["items"]["1010"], {"menu": "Phracon", "price": 200})

    @unittest.skipUnless((RATHENA / "db/re/refine.yml").exists(), "нет upstream/rathena")
    def test_generator_matches_file(self):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_refine.py"), str(RATHENA)],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, (BRAIN_DIR / "world" / "refine.json").read_text(encoding="utf-8"))

    @unittest.skipUnless((RATHENA / "npc/merchants/refine.txt").exists(), "нет upstream/rathena")
    def test_npc_lines(self):
        lines = (RATHENA / "npc/merchants/refine.txt").read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[525].startswith("prt_in,63,60,0\tscript\tHollgrehenn"))
        self.assertIn("refineui()", "\n".join(lines[526:533]), "Hollgrehenn открывает Refine UI")
        self.assertTrue(lines[969].startswith("prt_in,56,68,5\tscript\tVurewell"))
        self.assertIn('select("Phracon - 200 Zeny:Emveretarcon - 1000 Zeny', "\n".join(lines[1000:1020]))
        vestri = (RATHENA / "npc/re/merchants/refine.txt").read_text(encoding="utf-8")
        self.assertIn("I only refine items that are Level 10 or higher", vestri, "Vestri — только +10 и выше")


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.lab = Lab()

    def tearDown(self):
        self.lab.close()

    def test_steps_and_cost(self):
        self.lab.state(up=4, ores=1)
        p, why = self.lab.r.plan(self.lab.mind.state)
        self.assertIsNone(why)
        self.assertEqual((p["from"], p["to"], p["safe"], p["ore"], p["buy"]), (4, 7, 7, 1010, 2))
        self.assertEqual(p["cost"], 3 * 50 + 2 * 200)

    def test_refusals(self):
        lab = self.lab
        lab.state(up=7)
        self.assertIn("пределе +7", lab.r.plan(lab.mind.state)[1])
        lab.state(up=0, zeny=6000)
        self.assertIn("лишних зени мало", lab.r.plan(lab.mind.state)[1])
        lab.state(weapon=1162)                   # Broad Sword — ур. 3: руду (Oridecon) не продают
        self.assertIn("не того уровня", lab.r.plan(lab.mind.state)[1])
        lab.state(weapon=999999)
        self.assertIn("не точится", lab.r.plan(lab.mind.state)[1])

    def test_savings_reserve(self):
        lab = self.lab
        lab.state(up=0, zeny=12000)
        self.assertIsNotNone(lab.r.plan(lab.mind.state)[0])
        lab.mind.economy.reserve = lambda: 6000
        self.assertIsNone(lab.r.plan(lab.mind.state)[0], "копилка мечты — не лишние зени")

    def test_why_not(self):
        lab = self.lab
        lab.state()
        self.assertEqual(lab.r.why_not(T0, lab.mind.state), "не отдых в городе")
        lab.town()
        self.assertIsNone(lab.r.why_not(T0, lab.mind.state))
        lab.state(map="prt_fild08")
        self.assertEqual(lab.r.why_not(T0, lab.mind.state), "не в городе кузнеца")
        lab.state(refine=None)
        self.assertIn("плагин refine", lab.r.why_not(T0, lab.mind.state))
        lab.state()
        lab.r.st["last"] = T0 - 3600
        self.assertEqual(lab.r.why_not(T0, lab.mind.state), "недавно точил(а)")


class RitualTest(unittest.TestCase):
    def setUp(self):
        self.lab = Lab()
        self.lab.town()

    def tearDown(self):
        self.lab.close()

    def start(self):
        lab = self.lab
        lab.state(up=4, ores=1)
        lab.tick()
        return [a for a in lab.out if a["action"] == "refine"]

    def test_done_confirmed_by_state(self):
        lab = self.lab
        sent = self.start()
        self.assertEqual(len(sent), 1)
        a = sent[0]
        self.assertEqual((a["item"], a["inv"], a["target"], a["ore"], a["buy"]), (1201, 3, 7, 1010, 2))
        self.assertEqual((a["smith"]["x"], a["smith"]["y"], a["shop"]["menu"]), (63, 60, "Phracon"))
        says = [x["text"] for x in lab.out if x["action"] == "say"]
        self.assertTrue(says and "Knife" in says[0] and "+7" in says[0], "волнение перед походом")
        self.assertTrue([x for x in lab.out if x["action"] == "emote"])
        self.assertTrue(quest_busy(lab.mind, lab.mind.state, None), "арбитр: тело занято до state.refine.running")
        lab.out.clear()
        lab.tick()
        self.assertFalse([a for a in lab.out if a["action"] == "refine"], "второй раз не шлёт, пока ждёт итога")
        lab.mind.job_change_sent = 0
        lab.state(running=True)
        self.assertTrue(quest_busy(lab.mind, lab.mind.state, None), "арбитр: плагин точит — тело занято")
        lab.event(ok=True, item=1201, inv=3, to=7, done=3, reason="+7 — цель")
        lab.state(up=6)
        lab.tick()
        self.assertFalse(lab.events("refine_done"), "state ещё +6 — не засчитано")
        lab.state(up=7)
        lab.tick()
        rows = lab.events("refine_done")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["from"], rows[0]["to"]), (4, 7))
        self.assertIn("Knife теперь +7", [x["text"] for x in lab.out if x["action"] == "say"][-1])
        self.assertEqual(LINES["refine_done"](rows[0]), "заточил(а) Knife у Hollgrehenn: +4 → +7 (без риска)")
        lab.out.clear()
        lab.tick()
        self.assertFalse([a for a in lab.out if a["action"] == "refine"], "после заточки — пауза gap_days")

    def test_unverified(self):
        lab = self.lab
        self.start()
        lab.event(ok=True, item=1201, inv=3, to=5, done=1)
        lab.clock.t += 61
        lab.tick()
        self.assertEqual(len(lab.events("refine_unverified")), 1, "тело сказало, state не подтвердил")
        self.assertFalse(lab.events("refine_done"))

    def test_failed(self):
        lab = self.lab
        self.start()
        lab.event(ok=False, item=1201, inv=3, to=4, done=0, reason="шаг smith_talk: тайм-аут 45 с")
        rows = lab.events("refine_failed")
        self.assertEqual(len(rows), 1)
        self.assertIn("тайм-аут", rows[0]["reason"])
        self.assertIsNone(lab.r.st.get("pending"))

    def test_no_result(self):
        lab = self.lab
        self.start()
        lab.clock.t += 31 * 60
        lab.tick()
        self.assertEqual(lab.events("refine_failed")[0]["reason"], "нет итога от тела")

    def test_chance(self):
        lab = self.lab
        lab.r.rng = Lucky(0.99)
        lab.state()
        lab.tick()
        self.assertFalse([a for a in lab.out if a["action"] == "refine"], "не выпал шанс — не идёт")


class SafetyTest(unittest.TestCase):
    def setUp(self):
        self.s = SafetyPolicy(["prt_fild08"], extra_point_maps=["prontera"])
        d = load()
        self.a = {"action": "refine", "item": 1201, "inv": 3, "target": 7, "ore": 1010, "buy": 2,
                  "smith": {k: d["smith"][k] for k in ("map", "x", "y", "stand")},
                  "shop": {k: d["shop"][k] for k in ("map", "x", "y", "stand")} | {"menu": "Phracon"}}
        self.st = {"map": "prontera", "hp_pct": 100}

    def test_ok(self):
        a, why = self.s.check(self.a, self.st, protocol=True)
        self.assertIsNone(why)
        self.assertEqual(a["target"], 7)

    def test_rejects(self):
        self.assertEqual(self.s.check(self.a, self.st)[1], "неизвестное действие", "модели — нельзя")
        for bad in ({"ore": 984}, {"target": 11}, {"buy": 21}, {"item": "1201"}, {"shop": None},
                    {"shop": dict(self.a["shop"], menu="r~/x/")}):
            self.assertIsNotNone(self.s.check(dict(self.a, **bad), self.st, protocol=True)[1], bad)
        self.assertIn("города", self.s.check(self.a, dict(self.st, map="prt_fild08"), protocol=True)[1])
        self.assertIsNone(self.s.check(self.a, dict(self.st, map="prt_in"), protocol=True)[1], "у кузнеца — можно")
        self.assertIsNotNone(self.s.check(self.a, dict(self.st, dead=True), protocol=True)[1])


class SwitchTest(unittest.TestCase):
    def test_off_by_default(self):
        lab = Lab(w=copy.deepcopy(WORLD))
        try:
            self.assertIsNone(lab.mind.refine, "goals.json: refine выключен по умолчанию")
        finally:
            lab.close()
        lab = Lab(env={"BRAIN_DISABLE": "refine"})
        try:
            self.assertIsNone(lab.mind.refine)
        finally:
            lab.close()

    def test_result_event_owned(self):
        lab = Lab(w=copy.deepcopy(WORLD))
        try:
            lab.state()
            consumed = asyncio.run(lab.mind.registry.event(lab.mind, "refine_result", {"ok": True}))
            self.assertTrue(consumed, "refine_result не уходит в gate/LLM даже при выключенном модуле")
        finally:
            lab.close()


if __name__ == "__main__":
    unittest.main()
