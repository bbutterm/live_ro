"""Дом и точка сохранения (home.py, ORG-014): дом жителя, сохранение у Kafra, возрождение дома.

Данные Kafra (brain/world/homes.json) сверяются со скриптами rAthena и полями OpenKore, если рядом есть сабмодули
или заданы LIVE_RO_RATHENA / LIVE_RO_OPENKORE; иначе эта часть пропускается. Это проверка данных и правил мозга,
а не диалога в игре.

Запуск: cd brain && python3 -m unittest -v tests.test_home
"""
import asyncio
import json
from tests.persona_fixture import prontera_persona
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import home as H
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

from tests.test_newborn import HAVE_BOTH, RATHENA, ROOT, ok_walkable, rathena_walkable, reach, script_line

BRAIN_DIR = Path(__file__).resolve().parents[1]
HOMES = json.loads((BRAIN_DIR / "world" / "homes.json").read_text(encoding="utf-8"))


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class HomeTownTest(unittest.TestCase):
    def test_resolution(self):
        self.assertEqual(H.home_town({"name": "X", "home": "geffen"}), "geffen")
        self.assertEqual(H.home_town({"name": "Arkady"}), "prontera")          # roster.json home_town
        self.assertIsNone(H.home_town({"name": "Nobody"}))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "roster.json"
            p.write_text(json.dumps({"residents": {"bot09": {"name": "Odette", "home_town": "payon"}}}))
            self.assertEqual(H.home_town({"name": "Odette"}, p), "payon")


class HomeTest(unittest.TestCase):
    def make(self, home=None):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(a)
            return len(self.sent)

        persona = prontera_persona()
        persona.pop("sleep", None)
        if home:
            persona["home"] = home
        self.mem = Memory(root / "m.sqlite")
        self.alerts = []
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=load_world(BRAIN_DIR / "world" / "goals.json"))
        self.mind.alert = lambda kind, text, every=3600: self.alerts.append(kind)
        self.clock = Clock()
        self.h = self.mind.home
        self.h.clock = self.clock
        self.dec = root / "d.jsonl"
        town = self.mind.routine.town
        self.state(map=town["map"], x=town["x"], y=town["y"], lock_map=town["map"], lock_x=town["x"],
                   lock_y=town["y"])
        r = self.mind.routine
        r.new_day(self.clock.t)
        r.st.update(mode="town", arrived=True, rest_until=self.clock.t + 3600, mode_since=self.clock.t)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 41,
             "job": "Swordsman", "dead": False, "weight_pct": 20, "zeny": 60000, "items": {"501": 30},
             "players": []}
        s.update(getattr(self, "_last", {}))
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def tick(self):
        asyncio.run(self.h.tick())

    def saves(self):
        return [a for a in self.sent if a.get("action") == "job_change" and a.get("path") == "home"]

    def records(self, event):
        return [json.loads(l) for l in self.dec.read_text().splitlines()
                if '"home"' in l and json.loads(l).get("event") == event] if self.dec.exists() else []

    def test_default_home_prontera_rest(self):
        """R2 (IDEAS2): отдых в Пронтере — rest из homes.json (западный квартал), не фонтан вечернего круга."""
        self.make()
        self.assertEqual(self.h.town, "prontera")
        rest = HOMES["towns"]["prontera"]["rest"]
        self.assertEqual(self.mind.routine.town, {"map": "prontera", "x": rest["x"], "y": rest["y"], "radius": 3})
        self.assertNotEqual((rest["x"], rest["y"]), (156, 185), "не у фонтана")

    def test_save_at_kafra_then_confirmed(self):
        self.make()
        self.tick()
        saves = self.saves()
        self.assertEqual(len(saves), 1)
        a = saves[0]
        self.assertEqual(a["stage"], "kafra_save")
        self.assertEqual(a["steps"][0], {"do": "move", "map": "prontera", "x": 144, "y": 87})
        self.assertEqual(a["steps"][1], {"do": "talk", "x": 146, "y": 89, "answers": [{"text": "Save"}]})
        self.assertEqual(a["success"], {"text": "Respawn Point", "map": "prontera"})
        self.state(job_change={"running": True, "stage": "kafra_save"})
        self.tick()
        self.assertEqual(len(self.saves()), 1, "пока идёт этап — без повтора")
        career = self.mind.career
        if career:
            career.on_result = lambda e: self.fail("итог этапа «дом» ушёл в career")
        asyncio.run(self.mind.on_event({"type": "event", "kind": "job_change_result", "path": "home",
                                        "stage": "kafra_save", "ok": True, "reason": "ok"}))
        self.state(job_change={"running": False})
        self.assertTrue(self.h.at_home_saved())
        self.assertEqual(self.h.st["saved"]["how"], "kafra")
        self.assertEqual((self.h.st["saved"]["x"], self.h.st["saved"]["y"]), (116, 73))
        self.assertEqual(self.mem.count_events("home_saved", 0), 1)
        self.assertTrue(any("Kafra" in m for m in [r[0] for r in self.mem.db.execute("SELECT text FROM memories")]))
        self.tick()
        self.assertEqual(len(self.saves()), 1, "уже сохранён дома — больше не ходит")

    def test_not_while_hunting_or_elsewhere(self):
        self.make()
        self.mind.routine.st.update(arrived=False)
        self.tick()
        self.state(map="prt_fild08")
        self.mind.routine.st.update(arrived=True)
        self.tick()
        self.assertEqual(self.saves(), [])

    def test_failures_retry_then_day_pause(self):
        self.make()
        for i in range(3):
            self.tick()
            self.assertEqual(len(self.saves()), i + 1)
            asyncio.run(self.mind.on_event({"type": "event", "kind": "job_change_result", "path": "home",
                                            "stage": "kafra_save", "ok": False, "reason": "меню не из сценария"}))
            self.tick()
            self.assertEqual(len(self.saves()), i + 1, "сразу не повторяет")
            self.clock.t += 31 * 60
        self.tick()
        self.assertEqual(len(self.saves()), 3, "после трёх неудач — пауза на сутки")
        self.assertIn("home", self.alerts)
        self.clock.t += 86400
        self.tick()
        self.assertEqual(len(self.saves()), 4)

    def test_no_result_times_out(self):
        self.make()
        self.tick()
        self.clock.t += 11 * 60
        self.tick()
        self.assertEqual(self.records("home_save_failed")[0]["text"].split(":")[1].strip(),
                         "нет итога этапа от тела.")

    def test_respawn_home_and_elsewhere(self):
        self.make()
        self.h.st["saved"] = {"map": "prontera", "how": "kafra"}
        self.state(map="prt_fild08", x=100, y=100)
        asyncio.run(self.mind.on_event({"type": "event", "kind": "died", "map": "prt_fild08"}))
        self.assertEqual(self.records("death")[0]["expect"], "prontera")
        self.state(dead=True)
        self.tick()
        self.state(dead=False, map="prontera", x=116, y=73, hp_pct=1)
        self.tick()
        rec = self.records("home_respawn")[-1]
        self.assertTrue(rec["home"])
        self.assertTrue(self.h.at_home_saved())
        # точка сохранения оказалась не дома (например, старая): запись меняется, житель пойдёт сохраняться
        asyncio.run(self.mind.on_event({"type": "event", "kind": "died", "map": "prt_fild08"}))
        self.state(dead=True)
        self.tick()
        self.state(dead=False, map="izlude", x=129, y=97)
        self.tick()
        self.assertFalse(self.records("home_respawn")[-1]["home"])
        self.assertEqual(self.h.saved_map(), "izlude")
        self.assertEqual(self.h.why_not_now(self.clock.t, self.mind.state), "не в домашнем городе")
        self.state(map="prontera", x=156, y=185)
        self.tick()
        self.assertEqual(len(self.saves()), 1)

    def test_revived_in_place_is_not_respawn(self):
        self.make()
        self.h.st["saved"] = {"map": "prontera", "how": "kafra"}
        self.state(map="prt_fild08", x=100, y=100)
        asyncio.run(self.mind.on_event({"type": "event", "kind": "died", "map": "prt_fild08"}))
        self.state(dead=True)
        self.tick()
        self.state(dead=False)                                   # воскресил житель-Acolyte
        self.tick()
        self.assertEqual(self.h.saved_map(), "prontera")
        self.assertEqual(self.records("revived_in_place")[0]["map"], "prt_fild08")

    def test_other_home_town(self):
        self.make(home="geffen")
        self.assertEqual(self.h.town, "geffen")
        self.assertEqual(self.mind.routine.town, {"map": "geffen", "x": 119, "y": 40, "radius": 3})
        clean, why = self.mind.safety.check({"action": "meet_point", "map": "geffen", "x": 119, "y": 40},
                                            self.mind.state, protocol=True)
        self.assertIsNone(why)
        self.assertEqual(self.mind.social.pick_point(self.mind.state), (None, None),
                         "точки прогулок social знают только Пронтеру")
        self.tick()
        self.assertEqual(self.saves()[0]["steps"][1], {"do": "talk", "x": 120, "y": 62, "answers": [{"text": "Save"}]})

    def test_unknown_home_falls_back(self):
        self.make(home="atlantis")
        self.assertEqual(self.h.town, "prontera")
        self.assertEqual(self.h.unknown, "atlantis")


@unittest.skipUnless(HAVE_BOTH, "нет upstream/rathena и upstream/openkore (LIVE_RO_RATHENA / LIVE_RO_OPENKORE)")
class HomesDataTest(unittest.TestCase):
    """homes.json против скриптов rAthena (renewal) и полей OpenKore."""

    def test_kafra_scripts_and_menu(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from gen_atlas import loaded_files
        loaded = loaded_files(RATHENA)
        self.assertIn("npc/kafras/kafras.txt", loaded)
        self.assertIn("npc/kafras/functions_kafras.txt", loaded)
        menu = script_line("npc/kafras/functions_kafras.txt:142")
        self.assertIn("default:", menu)
        self.assertEqual(re.findall(r'"([^"]+)"', menu)[0], HOMES["save"]["answer"])
        self.assertIn('== "Save")', script_line("npc/kafras/functions_kafras.txt:148"))
        self.assertIn(HOMES["save"]["proof_text"], script_line("npc/kafras/functions_kafras.txt:452"))
        for town, rec in HOMES["towns"].items():
            npc, sp = rec["npc"], rec["savepoint"]
            head = script_line(npc["src"])
            self.assertTrue(head.startswith(f"{town},{npc['x']},{npc['y']},"), (town, head))
            self.assertIn(f"::{npc['name']}\t", head)
            path, line = npc["src"].split(":")
            body = (RATHENA / path).read_text(encoding="utf-8", errors="replace").splitlines()[int(line):int(line) + 15]
            call = next(l for l in body if 'callfunc "F_Kafra"' in l)
            self.assertEqual(int(re.search(r'"F_Kafra",\s*\d+,\s*(\d+)', call).group(1)), 0,
                             f"{town}: не default-меню (Save — не первым пунктом)")
            self.assertIn(f'savepoint "{town}",{sp["x"]},{sp["y"]},', script_line(sp["src"]))

    def test_cells(self):
        for town, rec in HOMES["towns"].items():
            npc, st, sp = rec["npc"], rec["stand"], rec["savepoint"]
            self.assertLessEqual(max(abs(npc["x"] - st["x"]), abs(npc["y"] - st["y"])), 2, town)
            for x, y in ((st["x"], st["y"]), (sp["x"], sp["y"])):
                self.assertTrue(rathena_walkable(town, x, y) and ok_walkable(town, x, y), (town, x, y))
            self.assertTrue(reach(town, (sp["x"], sp["y"]), (st["x"], st["y"]), 1), f"{town}: от возрождения к Kafra")
            rest = rec.get("rest")
            if rest:
                self.assertTrue(rathena_walkable(town, rest["x"], rest["y"]) and ok_walkable(town, rest["x"], rest["y"]))

    def test_resident_town_is_storage_kafra(self):
        for bot in ("bot01", "bot02"):
            with self.subTest(bot=bot):
                persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
                town = persona.get("routine", {}).get("town", {}).get("map") or H.home_town(persona)
                npc = HOMES["towns"][town]["npc"]
                cfg = (ROOT / "bots" / bot / "control" / "config.txt").read_text(encoding="utf-8")
                self.assertRegex(cfg, rf"(?m)^storageAuto_npc {town} {npc['x']} {npc['y']}\s*$")


if __name__ == "__main__":
    unittest.main()
