"""Травник у старого фармацевта (herbal.py, ORG-076, ТЗ Т-32): данные crafts.json и правила мозга.

Данные (меню, рецепты, клетка у NPC, путь) сверяются со скриптами rAthena и полями/таблицами OpenKore, если рядом
есть сабмодули или заданы LIVE_RO_RATHENA / LIVE_RO_OPENKORE; иначе эта часть пропускается. Это проверка данных
и правил мозга, а не поездки в игре.

Запуск: cd brain && python3 -m unittest -v tests.test_herbal
"""
import asyncio
import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import herbal as HB
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

from tests.test_newborn import HAVE_BOTH, OPENKORE, RATHENA, ROOT, ok_walkable, rathena_walkable, script_line

BRAIN_DIR = Path(__file__).resolve().parents[1]
CRAFTS = json.loads((BRAIN_DIR / "world" / "crafts.json").read_text(encoding="utf-8"))
ROUTE = CRAFTS["routes"]["prontera"]["pharmacist"]


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


def herbs(**kw):
    """Рюкзак ремесла: травы/бутылки по ID."""
    items = {str(i): 0 for i in (507, 508, 509, 510, 511, 713)}
    items.update({k.lstrip("i"): v for k, v in kw.items()})
    return items


class HerbalCase(unittest.TestCase):
    def make(self, enabled=True, herbalist=True, world_patch=None):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(a)
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        if herbalist:
            persona["herbalist"] = True
        world = load_world(BRAIN_DIR / "world" / "goals.json")
        world["herbal"] = dict(world.get("herbal") or {}, enabled=enabled)
        world.update(world_patch or {})
        self.mem = Memory(root / "m.sqlite")
        self.alerts = []
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=world)
        self.mind.alert = lambda kind, text, every=3600: self.alerts.append(kind)
        self.dec = root / "d.jsonl"
        self.clock = Clock()
        self.h = self.mind.herbal
        if self.h:
            self.h.clock = self.clock
        town = self.mind.routine.town
        self.state(map=town["map"], x=town["x"], y=town["y"], lock_map=town["map"], lock_x=town["x"],
                   lock_y=town["y"])
        r = self.mind.routine
        r.new_day(self.clock.t)
        r.st.update(mode="town", arrived=True, rest_until=self.clock.t + 3600, mode_since=self.clock.t)
        r.sleep_window = lambda now: None
        if self.mind.social:
            self.mind.social.is_night = lambda now: False

    def tearDown(self):
        if hasattr(self, "mem"):
            self.mem.close()
            self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 50,
             "job": "Swordsman", "dead": False, "weight_pct": 20, "zeny": 60000,
             "items": {"501": 30, "504": 0, "505": 0}, "players": [],
             "craft": {"items": herbs(), "kept": [507, 508, 509, 510, 511, 713], "weight_free": 1500,
                       "skills": {"AC_MAKINGARROW": 0}}}
        s.update(getattr(self, "_last", {}))
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def craft(self, **kw):
        c = dict(self._last["craft"])
        c["items"] = herbs(**kw)
        self.state(craft=c)

    def tick(self, minutes=1):
        self.clock.t += minutes * 60
        asyncio.run(self.h.tick())

    def actions(self, kind):
        return [a for a in self.sent if a.get("action") == kind]

    def trips(self):
        return [a for a in self.actions("job_change") if a.get("path") == "herbal"]

    def event(self, **kw):
        asyncio.run(self.mind.on_event({"type": "event", "kind": "job_change_result", **kw}))

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ?", (kind,))]


class HerbalTest(HerbalCase):
    def test_disabled_by_default(self):
        world = load_world(BRAIN_DIR / "world" / "goals.json")
        self.assertFalse(world["herbal"]["enabled"])
        self.make(enabled=False)
        self.assertIsNone(self.mind.herbal)

    def test_not_herbalist_silent(self):
        self.make(herbalist=False)
        self.craft(i509=40, i713=40)
        self.tick()
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.h.summary())

    def test_residents_list(self):
        self.make(herbalist=False, world_patch={"herbal": {"enabled": True, "residents": ["Arkady"]}})
        self.assertTrue(self.h.is_herbalist())

    def test_plan_profitable_only(self):
        self.make()
        self.craft(i507=10, i508=10, i509=12, i510=5, i511=10)
        plan = {p["name"]: p["n"] for p in self.h.plan()}
        self.assertEqual(plan, {"Blue Potion": 2, "White Potion": 6}, "Red/Orange/Yellow/Green дешевле купить")
        white = next(r for r in CRAFTS["pharmacist"]["recipes"] if r["potion"] == 504)
        self.assertEqual(self.h.gain(white), 1200 - 20 - 400 - 2 * 60)

    def test_keep_herbs_sent(self):
        self.make()
        c = dict(self._last["craft"], kept=[])
        self.state(craft=c)
        self.tick()
        keep = self.actions("craft_setup")
        self.assertEqual(keep[0]["keep"], [507, 508, 509, 510, 511, 713])
        self.tick()
        self.assertEqual(len(self.actions("craft_setup")), 1, "не чаще keep_every_minutes")

    def test_bottles_bought_first(self):
        self.make()
        self.craft(i509=12, i713=1)
        self.tick()
        setup = [a for a in self.actions("craft_setup") if "bottles" in a]
        self.assertEqual(setup[0]["bottles"], 6)
        self.assertEqual(len(self.actions("service")), 1)
        self.assertEqual(self.trips(), [])
        self.tick()
        self.assertEqual(len(self.actions("service")), 1, "не чаще раза в час")

    def test_trip_steps_and_success_by_fact(self):
        self.make()
        self.craft(i509=12, i713=6)
        self.tick()
        trips = self.trips()
        self.assertEqual(len(trips), 1)
        a = trips[0]
        self.assertEqual(a["stage"], "pharmacist")
        moves = [s for s in a["steps"] if s["do"] == "move"]
        self.assertEqual([[s["map"], s["x"], s["y"]] for s in moves[:-1]], ROUTE["legs"])
        self.assertEqual(moves[-1], {"do": "move", "map": "alberta_in", "x": ROUTE["stand"][0], "y": ROUTE["stand"][1]})
        talk = [s for s in a["steps"] if s["do"] == "talk"]
        self.assertEqual(len(talk), 1)
        self.assertEqual(talk[0]["answers"], [{"text": "Make Potion"}, {"text": "White Potion."},
                                              {"text": "Make as many as I can."}])
        self.assertTrue(talk[0]["ordered"])
        self.assertEqual(a["success"], {"text": "Here you go", "map": "alberta_in"})
        self.state(job_change={"running": True, "stage": "pharmacist"})
        self.tick()
        self.assertEqual(len(self.trips()), 1, "пока идёт этап — без повтора")
        if self.mind.career:
            self.mind.career.on_result = lambda e: self.fail("итог травника ушёл в career")
        self.event(path="herbal", stage="pharmacist", ok=True, reason="ok")
        self.tick()
        self.assertEqual(self.events("herbal_brewed"), [], "зелий ещё нет в рюкзаке — ждём факт")
        self.state(job_change={"running": False}, map="alberta_in", items={"501": 30, "504": 6, "505": 0})
        self.tick()
        brewed = self.events("herbal_brewed")
        self.assertEqual(brewed[0]["potions"], {"White Potion": 6})
        self.assertEqual(brewed[0]["saved"], 6 * (1200 - 20 - 400 - 120))
        self.assertEqual(self.h.st["saved"], brewed[0]["saved"])
        self.state(map="prontera")
        self.tick()
        self.assertEqual(len(self.events("herbal_returned")), 1)
        self.tick(60)
        self.assertEqual(len(self.trips()), 1, "не чаще gap_hours")

    def test_ok_without_potions_is_failure(self):
        self.make()
        self.craft(i509=12, i713=6)
        self.tick()
        self.event(path="herbal", stage="pharmacist", ok=True, reason="ok")
        self.tick(6)
        self.assertIn("зелий в рюкзаке нет", self.events("herbal_failed")[0]["reason"])

    def test_failures_pause_and_alert(self):
        self.make()
        self.craft(i509=12, i713=6)
        for i in range(3):
            self.h.st["last"] = 0
            self.tick()
            self.assertEqual(len(self.trips()), i + 1)
            self.event(path="herbal", stage="pharmacist", ok=False, reason="шаг 3 (move): таймаут")
            self.state(map="prontera")
            self.tick()
            self.clock.t += 7 * 3600
        self.h.st["last"] = 0
        self.tick()
        self.assertEqual(len(self.trips()), 3, "после трёх неудач — сутки паузы")
        self.assertIn("herbal", self.alerts)

    def test_career_still_gets_its_results(self):
        self.make()
        got = []
        if not self.mind.career:
            self.skipTest("career выключен")
        self.mind.career.on_result = lambda e: got.append(e.get("path"))
        self.event(path="knight", stage="s1", ok=False, reason="x")
        self.assertEqual(got, ["knight"])

    def test_conditions(self):
        self.make()
        self.craft(i509=12, i713=6)
        now = self.clock.t
        st = self.mind.state
        self.assertIsNone(self.h.why_not_now(now, st))
        self.state(hp_pct=40)
        self.assertIn("HP", self.h.why_not_now(now, self.mind.state))
        self.state(hp_pct=100, zeny=100)
        self.assertIn("зени", self.h.why_not_now(now, self.mind.state))
        self.state(zeny=60000, craft=dict(self._last["craft"], weight_free=300))
        self.assertIn("вес", self.h.why_not_now(now, self.mind.state))
        self.state(craft=dict(self._last["craft"], weight_free=1500), map="prt_fild08")
        self.assertEqual(self.h.why_not_now(now, self.mind.state), "не в городе отдыха")
        self.state(map="prontera")
        self.mind.routine.sleep_window = lambda n: (n + 3600, n + 8 * 3600)
        self.assertEqual(self.h.why_not_now(now, self.mind.state), "скоро сон")
        self.state(lv=41)                       # moc_fild03: Argos ур. 47 — путь опасен до ~46 уровня
        self.assertIn("опасно по пути: moc_fild03", self.h.why_not_now(now, self.mind.state))

    def test_no_route_waits(self):
        self.make()
        self.h.crafts = dict(CRAFTS, routes={"prontera": {"pharmacist": {"route": None, "why": "нет"}}})
        self.craft(i509=12, i713=6)
        self.assertIn("нет пути", self.h.why_not_now(self.clock.t, self.mind.state))

    def test_topic_and_chronicle(self):
        self.make()
        self.craft(i509=12, i713=6)
        self.tick()
        self.event(path="herbal", stage="pharmacist", ok=True, reason="ok")
        self.state(items={"501": 30, "504": 6, "505": 0})
        self.tick()
        f = self.h.facts("Vera", self.clock.t)
        self.assertEqual((f["n"], f["potion"]), (6, "White Potion"))
        for line in HB.PHRASES["herbal"]:
            self.assertLessEqual(len(line.format(n=60, potion="White Potion", saved=99999)), 60)
        self.h.said("Vera", f, self.clock.t)
        self.assertIsNone(self.h.facts("Vera", self.clock.t))
        from live_brain.chronicle import LINES
        self.assertIn("White Potion", LINES["herbal_brewed"](self.events("herbal_brewed")[0]))


class SafetyTest(unittest.TestCase):
    def test_craft_setup(self):
        from live_brain.safety import SafetyPolicy
        s = SafetyPolicy(["prt_fild08"])
        ok, why = s.check({"action": "craft_setup", "keep": [507, 713]}, {}, protocol=True)
        self.assertEqual(ok, {"action": "craft_setup", "keep": [507, 713]})
        self.assertIsNone(s.check({"action": "craft_setup", "keep": [507]}, {})[0], "модель не получает")
        self.assertIsNone(s.check({"action": "craft_setup", "keep": ["x"]}, {}, protocol=True)[0])
        self.assertIsNone(s.check({"action": "craft_setup", "bottles": 1000}, {}, protocol=True)[0])
        self.assertIsNone(s.check({"action": "craft_setup"}, {}, protocol=True)[0])
        self.assertEqual(s.check({"action": "craft_setup", "bottles": 0}, {}, protocol=True)[0]["bottles"], 0)


@unittest.skipUnless(HAVE_BOTH, "нет upstream/rathena и upstream/openkore (LIVE_RO_RATHENA / LIVE_RO_OPENKORE)")
class CraftsDataTest(unittest.TestCase):
    """crafts.json против скриптов rAthena и полей OpenKore."""

    def test_generator_matches_file(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import gen_crafts
        doc = gen_crafts.generate(RATHENA, OPENKORE, ROOT / "brain" / "world" / "atlas.json", ["prontera:156:185"],
                                  CRAFTS["max_hops"])
        self.assertEqual(json.loads(json.dumps(doc, sort_keys=True)), CRAFTS,
                         "перегенерировать: scripts/gen_crafts.py > brain/world/crafts.json")

    def test_pharmacist_script(self):
        ph = CRAFTS["pharmacist"]
        self.assertTrue(script_line(ph["src"]).startswith(f"alberta_in,{ph['x']},{ph['y']},"))
        self.assertIn('"Make Potion:', script_line(ph["menu"]["src"]))
        self.assertIn("White Potion.", script_line(ph["potion_menu_src"]))
        for ref in ph["amount"]["src"]:
            self.assertIn(ph["amount"]["text"], script_line(ref))
        for ref in ph["proof"]["src"]:
            self.assertIn(ph["proof"]["text"], script_line(ref))
        self.assertIn("MaxWeight - Weight < 5000", script_line(ph["free_weight_src"]))
        red = next(r for r in ph["recipes"] if r["potion"] == 501)
        self.assertEqual((red["herbs"], red["fee"]), ({"507": 2}, 3), "в коде 3z (в тексте 2z)")
        self.assertIn("callsub L_Making,507,3,501", script_line(red["src"]))
        orange = next(r for r in ph["recipes"] if r["potion"] == 502)
        self.assertEqual((orange["herbs"], orange["fee"]), ({"507": 1, "508": 1}, 5))
        self.assertEqual(len(ph["recipes"]), 6)

    def test_stand_and_route(self):
        ph, rt = CRAFTS["pharmacist"], ROUTE
        sx, sy = rt["stand"]
        self.assertLessEqual(max(abs(sx - ph["x"]), abs(sy - ph["y"])), 2)
        self.assertTrue(rathena_walkable("alberta_in", sx, sy) and ok_walkable("alberta_in", sx, sy))
        atlas = json.loads((ROOT / "brain" / "world" / "atlas.json").read_text(encoding="utf-8"))["maps"]
        for (a, b) in zip(rt["path"], rt["path"][1:]):
            if a != b:
                self.assertTrue(any(e["to"] == b for e in atlas[a]["exits"]), f"{a} -> {b}: нет варпа в атласе")
        for m, x, y in rt["legs"]:
            self.assertTrue(rathena_walkable(m, x, y) and ok_walkable(m, x, y), (m, x, y))
        self.assertEqual(rt["path"][-1], "alberta_in")
        self.assertLessEqual(rt["back"], rt["hops"] + 1)
        self.assertIsNone(CRAFTS["routes"]["prontera"]["roberto"]["route"], "Морокк закрыт до патча порталов")

    def test_bottle_shop_and_profile(self):
        atlas = json.loads((ROOT / "brain" / "world" / "atlas.json").read_text(encoding="utf-8"))
        self.assertIn(["prt_in", 126, 76, 400, "Tool Dealer"], atlas["item_shops"]["713"])
        for bot in ("bot01", "bot02"):
            cfg = (ROOT / "bots" / bot / "control" / "config.txt").read_text(encoding="utf-8")
            block = re.search(r"buyAuto Empty Bottle \{(.*?)\}", cfg, re.S)
            self.assertIsNotNone(block, bot)
            self.assertIn("npc prt_in 126 76", block.group(1))
            self.assertIn("disabled 1", block.group(1))


if __name__ == "__main__":
    unittest.main()
