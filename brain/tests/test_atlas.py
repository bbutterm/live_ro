"""Атлас мира: данные rAthena и функции без LLM (AUT-031..036, 039, 043, 045, 073, 074, 077).

Проверки идут на реальном brain/world/atlas.json (scripts/gen_atlas.py). Если рядом есть сабмодуль
upstream/rathena, атлас перегенерируется и сравнивается с закоммиченным (не устарел ли).
Запуск: cd brain && python3 -m unittest -v tests.test_atlas
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from live_brain import atlas
from live_brain.atlas import Atlas

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT = BRAIN_DIR.parent
RATHENA = ROOT / "upstream" / "rathena"
HAS_RATHENA = (RATHENA / "npc" / "re" / "scripts_main.conf").is_file()
A = atlas.default()


def exit_to(src, dst):
    return [e for e in A.maps[src]["exits"] if e["to"] == dst]


class WorldDataTest(unittest.TestCase):
    def test_prontera_and_fild08_linked_both_ways(self):
        self.assertIn("prt_fild08", A.neighbors("prontera"))
        self.assertIn("prontera", A.neighbors("prt_fild08"))
        south = exit_to("prontera", "prt_fild08")
        self.assertIn((156, 22, 170, 375), [(e["x"], e["y"], e["tx"], e["ty"]) for e in south],
                      "варп prt001 из npc/warps/cities/prontera.txt")
        self.assertIn("izlude", A.neighbors("prt_fild08"))

    def test_fild08_monsters_with_mob_db_levels(self):
        names = {A.monsters[i]["name"]: A.monsters[i] for i in A.maps["prt_fild08"]["monsters"]}
        for name, level in (("Poring", 1), ("Lunatic", 3), ("Fabre", 6)):
            self.assertIn(name, names)
            self.assertEqual(names[name]["level"], level)
            self.assertFalse(atlas.aggressive(names[name]), f"{name} не агрессивен")
        self.assertEqual(A.maps["prt_fild08"]["monsters"]["1002"], 87, "Poring: 20+2+5+... по спавнам")
        self.assertEqual(A.maps["prt_fild08"]["kind"], "field")
        self.assertEqual(A.maps["prontera"]["kind"], "town")

    @unittest.skipUnless(HAS_RATHENA, "нет upstream/rathena — сверка с mob_db пропущена")
    def test_levels_match_mob_db(self):
        text = (RATHENA / "db" / "re" / "mob_db.yml").read_text(encoding="utf-8")
        for mid in ("1002", "1063", "1007", "1068"):
            block = re.search(rf"\n  - Id: {mid}\n(.*?)\n  - Id:", text, re.S).group(1)
            level = int(re.search(r"\n    Level: (\d+)", "\n" + block).group(1))
            self.assertEqual(A.monsters[mid]["level"], level, mid)

    def test_monster_info_for_risk_catalog(self):
        p = atlas.monster_info("poring")
        self.assertEqual((p["id"], p["level"], p["aggressive"], p["element"]), (1002, 1, False, "Water 1"))
        self.assertIn("prt_fild08", p["maps"])
        self.assertTrue(atlas.monster_info("Elder Willow")["aggressive"])
        self.assertEqual(atlas.monster_info("PORING")["id"], 1002, "по AegisName тоже")
        self.assertIsNone(atlas.monster_info("Нет такого"))

    def test_shops_and_kafra(self):
        shop = atlas.nearest_shop(501, "prontera")
        self.assertEqual((shop["map"], shop["x"], shop["y"], shop["npc"], shop["price"]),
                         ("prt_in", 126, 76, "Tool Dealer", 10), "Red Potion: -1 в скрипте = Buy 10 из item_db")
        self.assertEqual(shop["route"]["maps"], ["prontera", "prt_in"])
        self.assertEqual(shop["route"]["steps"][0]["to"], "prt_in")
        kafra = atlas.nearest_kafra("prontera")
        self.assertEqual(kafra["map"], "prontera")
        self.assertEqual(kafra["hops"], 0)
        self.assertIn((kafra["x"], kafra["y"]), {(k["x"], k["y"]) for k in A.maps["prontera"]["kafra"]})
        self.assertEqual(len(A.maps["prontera"]["kafra"]), 5)
        self.assertEqual(atlas.nearest_kafra("prt_fild08")["hops"], 1)
        self.assertIsNone(atlas.nearest_shop(999999, "prontera"))

    def test_hunt_maps_of_personas_are_known(self):
        for f in sorted((BRAIN_DIR / "personas").glob("*.json")):
            maps = json.loads(f.read_text(encoding="utf-8")).get("hunt_maps", [])
            self.assertEqual(A.check_hunt_maps(maps), [], f.name)
        self.assertEqual(len(A.check_hunt_maps(["nowhere", "prtg_cas01"])), 2)


class DecisionsTest(unittest.TestCase):
    def test_suitable_maps_for_level_35_melee(self):
        got = atlas.suitable_maps(35, "melee")
        self.assertTrue(got)
        for g in got:
            m = A.maps[g["map"]]
            self.assertEqual(m["kind"], "field", g)
            self.assertTrue(25 <= g["avg_level"] <= 38, g)
            self.assertLess(g["risk"], 0.5)
            self.assertLessEqual(g["hops"], 4)
            self.assertTrue(g["why"])
            self.assertTrue(g["map"].startswith(("prt_", "gef_", "moc_", "pay_", "mjolnir_")), g)
        far = {g["map"] for g in atlas.suitable_maps(35, "melee", hops=20, top=100, kinds=("field", "dungeon"))}
        self.assertFalse(far & {"gl_chyard", "gl_prison", "mag_dun02", "gef_dun02", "orcsdun02", "prt_maze03"})
        self.assertEqual([g["map"] for g in got], [g["map"] for g in atlas.suitable_maps(35, "melee")],
                         "детерминированно")

    def test_suitable_maps_low_level_near_prontera(self):
        got = [g["map"] for g in atlas.suitable_maps(5, "melee", top=10)]
        self.assertIn("prt_fild08", got)
        self.assertNotIn("prt_fild07", got, "Rocker 15 ур. — для 5 ур. вне коридора [−5, 8]")

    def test_route_prontera_to_fild05(self):
        r = atlas.route("prontera", "prt_fild05")
        self.assertEqual(r["maps"], ["prontera", "prt_fild05"])
        self.assertEqual(r["steps"][0], {"from": "prontera", "x": 22, "y": 203, "to": "prt_fild05",
                                         "tx": 367, "ty": 205, "via": "warp", "zeny": 0})
        r = atlas.route("prt_fild08", "prt_fild05", level=30)
        self.assertEqual(r["maps"], ["prt_fild08", "prontera", "prt_fild05"])
        self.assertEqual(r["steps"][0]["to"], "prontera")
        self.assertEqual(r["steps"][1]["from"], "prontera")
        self.assertEqual(atlas.route("prontera", "prontera")["hops"], 0)
        self.assertIsNone(atlas.route("prontera", "nowhere"))

    def test_paid_kafra_only_when_allowed(self):
        free = atlas.route("prontera", "payon")
        self.assertTrue(all(s["via"] == "warp" for s in free["steps"]))
        self.assertEqual(free["zeny"], 0)
        paid = atlas.route("prontera", "payon", paid=True)
        self.assertEqual((paid["hops"], paid["zeny"], paid["steps"][0]["via"]), (1, 1200, "kafra"))

    def test_danger_for(self):
        risk, why = atlas.danger_for("gl_chyard", 30)
        self.assertGreaterEqual(risk, 0.9)
        self.assertTrue(any("агрессивный" in w for w in why), why)
        self.assertEqual(atlas.danger_for("prt_fild08", 30)[0], 0.0)
        self.assertEqual(atlas.danger_for("prtg_cas01", 99)[0], 1.0)
        self.assertEqual(atlas.danger_for("nowhere", 30)[0], 0.5)
        self.assertLess(atlas.danger_for("orcsdun01", 60)[0], atlas.danger_for("orcsdun01", 30)[0])

    def test_filter_hunt_maps(self):
        ok, rejected = A.filter_hunt_maps(["prt_fild08", "gl_chyard", "prt_fild07"], 30)
        self.assertEqual(ok, ["prt_fild08", "prt_fild07"])
        self.assertIn("gl_chyard", rejected)


def toy(**extra):
    mob = lambda lvl, modes: {"name": f"M{lvl}", "aegis": f"M{lvl}", "level": lvl, "hp": 100, "atk": 10, "atk2": 20,
                              "def": 0, "mdef": 0, "base_exp": 1, "job_exp": 1, "range": 1, "element": "Neutral",
                              "element_level": 1, "race": "Brute", "size": "Small", "class": "Normal",
                              "modes": modes}
    ex = lambda to, x=1: {"x": x, "y": 1, "to": to, "tx": 5, "ty": 5}
    m = lambda exits, mons=None, kind="field": {"kind": kind, "flags": [], "exits": exits, "monsters": mons or {},
                                                 "shops": [], "kafra": [], "level": None, "danger": None}
    data = {"maps": {"a": m([ex("b"), ex("c", 9)], kind="town"), "b": m([ex("d")], {"2": 50}),
                     "c": m([ex("e")]), "e": m([ex("d")]), "d": m([])},
            "monsters": {"1": mob(5, ["CanAttack"]), "2": mob(80, ["Aggressive", "CanAttack"])},
            "items": {}, "item_shops": {}}
    data.update(extra)
    raw = json.dumps(data, sort_keys=True).encode()
    return Atlas(json.loads(raw), raw)


class LogicTest(unittest.TestCase):
    def test_route_avoids_dangerous_middle_map_if_possible(self):
        t = toy()
        self.assertEqual(t.route("a", "d")["maps"], ["a", "b", "d"], "без уровня — кратчайший")
        r = t.route("a", "d", level=30)
        self.assertEqual(r["maps"], ["a", "c", "e", "d"], "в обход карты с агрессивными 80 ур.")
        self.assertEqual(r["risky"], [])
        t.maps["c"]["exits"] = []
        r = t.route("a", "d", level=30)
        self.assertEqual(r["maps"], ["a", "b", "d"], "альтернативы нет — идём, но отмечаем риск")
        self.assertEqual(r["risky"], ["b"])

    def test_version_follows_content(self):
        self.assertEqual(toy().version, toy().version)
        self.assertNotEqual(toy().version, toy(meta={"x": 1}).version)
        self.assertRegex(atlas.atlas_version(), r"^[0-9a-f]{16}$")

    def test_archetype(self):
        self.assertEqual(atlas.archetype("Swordman"), "melee")
        self.assertEqual(atlas.archetype("Acolyte"), "support")
        self.assertEqual(atlas.archetype("Mage"), "magic")
        self.assertEqual(atlas.archetype(None), "melee")


@unittest.skipUnless(HAS_RATHENA, "нет upstream/rathena — проверка свежести атласа пропущена")
class FreshnessTest(unittest.TestCase):
    def test_committed_atlas_matches_generator(self):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_atlas.py"), str(RATHENA)],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, (BRAIN_DIR / "world" / "atlas.json").read_text(encoding="utf-8"),
                         "атлас устарел: перегенерируйте scripts/gen_atlas.py upstream/rathena")

    def test_atlas_built_from_pinned_rathena(self):
        pin = re.search(r'"upstream/rathena":\s*"([0-9a-f]{40})"', (ROOT / "scripts" / "check.py").read_text())
        self.assertEqual(A.data["meta"]["rathena_commit"], pin.group(1))


if __name__ == "__main__":
    unittest.main()
