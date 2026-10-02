"""Прогрессия жителей (progression.py, AUT-049..054, 079..084): сценарии, цели, снаряжение.

Сверка сценариев со скриптами rAthena и полями OpenKore идёт, если рядом есть сабмодули
(upstream/rathena, upstream/openkore) или пути в LIVE_RO_RATHENA / LIVE_RO_OPENKORE; иначе пропускается.
Это проверка разбора, а не прохождения квеста в игре.

Запуск: cd brain && python3 -m unittest -v tests.test_progression
"""
import copy
import gzip
import os
import re
import struct
import unittest
from pathlib import Path

from live_brain import progression as P

ROOT = Path(__file__).resolve().parents[2]
RATHENA = Path(os.environ.get("LIVE_RO_RATHENA") or ROOT / "upstream" / "rathena")
OPENKORE = Path(os.environ.get("LIVE_RO_OPENKORE") or ROOT / "upstream" / "openkore")
DATA = P.load()

ARKADY = {"name": "Arkady", "job": "Swordsman", "lv": 41, "job_lv": 20, "zeny": 30000, "sex": "Male",
          "items": {"501": 20}}
VERA = {"name": "Vera", "job": "Acolyte", "lv": 30, "job_lv": 18, "zeny": 8000, "sex": "Female",
        "items": {"501": 10}}


def talk_steps():
    for path, p in DATA["paths"].items():
        for stage in p["stages"]:
            for step in stage["steps"]:
                if step["do"] == "talk":
                    yield path, stage, step
                for at in step.get("autotalk", []):
                    yield path, stage, dict(at, do="talk")


def script_line(ref):
    m = re.match(r"(npc/[^:]+):(\d+)", ref)
    lines = (RATHENA / m.group(1)).read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[int(m.group(2)) - 1]


def menu_options(line):
    m = re.search(r'select\("([^"]*)"\)', line) or re.search(r'set \.@mes\$,"([^"]*)"', line)
    return m.group(1).split(":") if m else None


class ScenarioDataTest(unittest.TestCase):
    def test_paths_loaded_with_refs(self):
        # newborn: первые профессии (Novice -> …) проверяет tests/test_newborn.py
        second = {k: p for k, p in DATA["paths"].items() if p["from"] != "Novice"}
        self.assertEqual(set(second), {"knight", "priest"})
        for path, p in second.items():
            self.assertTrue(p["script"].startswith("npc/jobs/2-1/"), path)
            self.assertEqual(p["requirements"]["job_lv"], 40)
            self.assertTrue(all(re.match(r"npc/.+\.txt:\d+", r) for r in p["requirements"]["refs"]))
            self.assertGreaterEqual(len(p["stages"]), 4)
            for stage in p["stages"]:
                self.assertTrue(stage["steps"], f"{path}/{stage['id']}: нет шагов")
                self.assertIn("ref", stage["success"], f"{path}/{stage['id']}: нет ссылки на успех")
                for step in stage["steps"]:
                    self.assertIn(step["do"], ("move", "talk", "chat_join", "fight", "wait", "walk"))

    def test_every_answer_has_script_line(self):
        n = 0
        for path, stage, step in talk_steps():
            for a in step["answers"]:
                n += 1
                self.assertRegex(a["ref"], r"^npc/[^:]+\.txt:\d+$", f"{path}/{stage['id']}: {a['text']}")
                self.assertGreaterEqual(a["select"], 1)
        self.assertGreater(n, 40)

    def test_talk_npcs_described(self):
        for path, stage, step in talk_steps():
            if "npc" in step and step["npc"] in DATA["paths"][path]["npcs"]:
                npc = DATA["paths"][path]["npcs"][step["npc"]]
                self.assertEqual((npc["x"], npc["y"]), (step["x"], step["y"]), f"{path}/{stage['id']}")

    def test_item_sets(self):
        sets = DATA["paths"]["knight"]["item_sets"]
        self.assertEqual(len(sets["A"]["items"]), 6)
        self.assertEqual(len(sets["B"]["items"]), 6)
        self.assertTrue(all(n == 5 for s in ("A", "B") for n in sets[s]["items"].values()))


@unittest.skipUnless((RATHENA / "npc/jobs/2-1/knight.txt").exists(), "нет upstream/rathena")
class ScriptRefsTest(unittest.TestCase):
    """Каждый ответ сверяется с меню в строке скрипта, на которую ссылается."""

    def test_answers_match_script_menus(self):
        for path, stage, step in talk_steps():
            for i, a in enumerate(step["answers"]):
                opts = menu_options(script_line(a["ref"]))
                where = f"{path}/{stage['id']} {a['ref']}"
                self.assertIsNotNone(opts, f"{where}: в строке нет меню")
                self.assertEqual(opts[a["select"] - 1], a["text"], where)
                pos = i if step.get("ordered") else None
                self.assertEqual(P.choose_answer(step["answers"], opts, pos), a["select"],
                                 f"{where}: правило выбора ответа даёт другой пункт")

    def test_npc_refs(self):
        for path, p in DATA["paths"].items():
            for key, npc in p["npcs"].items():
                line = script_line(npc["ref"])
                self.assertIn(f"{npc['map']},{npc['x']},{npc['y']}", line, f"{path}/{key}")
                self.assertIn(npc["name"], line, f"{path}/{key}")

    def test_success_quests_in_script(self):
        for path, p in DATA["paths"].items():
            text = (RATHENA / p["script"]).read_text(encoding="utf-8", errors="replace")
            for stage in p["stages"]:
                for q in [stage["success"].get("quest")] + stage["success"].get("quests_any", []):
                    if q:
                        found = re.search(rf"(setquest |changequest \d+,){q}\b", text)
                        self.assertTrue(found, f"{path}/{stage['id']}: квест {q} не найден в скрипте")

    def test_scripts_loaded_in_renewal(self):
        conf = (RATHENA / "npc/scripts_jobs.conf").read_text()
        self.assertIn("npc: npc/jobs/2-1/knight.txt", conf)
        self.assertIn("npc: npc/jobs/2-1/priest.txt", conf)
        self.assertIn("import: npc/scripts_jobs.conf", (RATHENA / "npc/re/scripts_main.conf").read_text())
        self.assertFalse((RATHENA / "npc/re/jobs/2-1").exists(), "появилась renewal-версия — пересверить")

    def test_pilgrimage_text(self):
        rx = re.compile([s for s in DATA["paths"]["priest"]["stages"] if s["id"] == "pilgrimage"][0]["success"]["text"])
        self.assertTrue(rx.search(script_line("npc/jobs/2-1/priest.txt:1845")))
        self.assertTrue(rx.search(script_line("npc/jobs/2-1/priest.txt:1854")))


@unittest.skipUnless((OPENKORE / "fields" / "prt_in.fld2.gz").exists(), "нет полей OpenKore")
class FieldsTest(unittest.TestCase):
    """Точки move/walk проходимы на картах OpenKore (fields/*.fld2.gz: байт клетки & 1)."""

    def test_move_targets_walkable(self):
        cache = {}
        for p in DATA["paths"].values():
            for stage in p["stages"]:
                for step in stage["steps"]:
                    if step["do"] not in ("move", "walk"):
                        continue
                    m = step["map"]
                    if m not in cache:
                        raw = gzip.open(OPENKORE / "fields" / f"{m}.fld2.gz").read()
                        w, h = struct.unpack("<HH", raw[:4])
                        cache[m] = (w, raw[4:])
                    w, cells = cache[m]
                    self.assertTrue(cells[step["y"] * w + step["x"]] & 1, f"{stage['id']}: {m} {step['x']},{step['y']}")


class PlanTest(unittest.TestCase):
    def test_arkady_job20_needs_job40(self):
        g = P.plan(ARKADY, DATA, now=1000)
        self.assertEqual(g["path"], "knight")
        self.assertEqual(g["next"]["kind"], "job_lv")
        self.assertEqual(g["next"]["target"], 40)
        self.assertEqual(g["next"]["have"], 20)
        self.assertGreater(g["next"]["expires"], 1000)
        self.assertIsNone(P.stage_action(ARKADY, DATA), "без job 40 к капитану не идём")

    def test_arkady_job40_missing_list(self):
        st = dict(ARKADY, job_lv=40)
        r = P.readiness(st, DATA)
        self.assertFalse(r["ready"])
        self.assertEqual([m["kind"] for m in r["missing"]], [])
        self.assertEqual({u["kind"] for u in r["unknown"]}, {"skill_points", "item_set"})
        st["skill_points"] = 4
        r = P.readiness(st, DATA)
        self.assertEqual(r["missing"][0], {"kind": "skill_points", "need": 0, "have": 4,
                                           "ref": "npc/jobs/2-1/knight.txt:132 SkillPoint -> отказ"})

    def test_arkady_items_after_andrew(self):
        st = dict(ARKADY, job_lv=42, skill_points=0, quests=[9002], items={"946": 5, "1042": 2})
        r = P.readiness(st, DATA)
        need = {m["id"]: (m["have"], m["need"]) for m in r["missing"] if m["kind"] == "item"}
        self.assertEqual(set(need), {1042, 950, 1032, 966, 7031})
        self.assertEqual(need[1042], (2, 5))
        g = P.plan(st, DATA)
        self.assertEqual(g["next"]["kind"], "collect_items")
        self.assertFalse(g["prefer_job_skip"], "набор уже выбран — job 50 не поможет")
        self.assertIsNone(P.stage_action(st, DATA), "предметов нет — к Sir Andrew не идём")
        st["items"] = {k: 5 for k in DATA["paths"]["knight"]["item_sets"]["B"]["items"]}
        self.assertEqual(P.stage_action(st, DATA)["stage"], "andrew_deliver")

    def test_knight_stage_by_quest(self):
        st = dict(ARKADY, job_lv=45, skill_points=0)
        for quests, stage in (([], "apply"), ([9000], "andrew_items"), ([9003], "siracuse_quiz"),
                              ([9005], "windsor_arena"), ([9008], "amy_etiquette"), ([9010], "edmond_patience"),
                              ([9011], "gray_interview"), ([9012], "captain_final")):
            self.assertEqual(P.current_stage(dict(st, quests=quests), DATA)["id"], stage, quests)

    def test_hard_sets_prefer_job50(self):
        g = P.plan(dict(ARKADY, job_lv=40), DATA)
        self.assertTrue(all(s["hard"] for s in g["item_sets"].values()))
        self.assertTrue(g["prefer_job_skip"])
        self.assertEqual(g["next"]["target"], 50)

    def test_vera_job_then_quest(self):
        g = P.plan(VERA, DATA)
        self.assertEqual((g["path"], g["next"]["kind"], g["next"]["target"]), ("priest", "job_lv", 40))
        st = dict(VERA, job_lv=40, skill_points=0, quests=[])
        self.assertEqual(P.readiness(st, DATA)["missing"], [])
        act = P.stage_action(st, DATA)
        self.assertEqual((act["action"], act["path"], act["stage"]), ("job_change", "priest", "apply"))
        self.assertEqual(act["steps"][1]["answers"][0]["text"], "I want to be a Priest.")
        self.assertEqual(P.current_stage(dict(st, quests=[8010]), DATA)["id"], "pilgrimage")
        self.assertEqual(P.current_stage(dict(st, quests=[8010]), DATA, done={"pilgrimage"})["id"], "spiritual_test")
        self.assertEqual(P.current_stage(dict(st, quests=[8014]), DATA)["id"], "oath")

    def test_unknown_job_and_after_change(self):
        self.assertIsNone(P.plan(dict(ARKADY, job="Merchant"), DATA)["path"])
        g = P.plan(dict(ARKADY, job="Knight", job_lv=12), DATA)
        self.assertEqual((g["path"], g["next"]["kind"], g["next"]["target"]), (None, "job_lv", 50))

    def test_goal_expires(self):
        g = P.plan(ARKADY, DATA, now=0)
        self.assertFalse(P.expired(g["next"], now=1))
        self.assertTrue(P.expired(g["next"], now=g["next"]["expires"]))
        self.assertTrue(P.expired(None))

    def test_unreachable_item_marked(self):
        data = copy.deepcopy(DATA)
        data["catalog"]["items"]["950"] = {"name": "Heart of Mermaid", "shops": [], "drops": [
            {"mob": "Obeune", "level": 31, "rate_pct": 45.0, "maps": {}}]}      # монстр нигде не появляется
        st = dict(ARKADY, job_lv=42, skill_points=0, quests=[9002])
        self.assertEqual(P.item_sources(950, data)["status"], "unreachable")
        g = P.plan(st, data)
        self.assertTrue(g["unreachable"])
        self.assertEqual(g["next"]["unreachable"], [950])
        self.assertIn(950, g["item_sets"]["B"]["unreachable"])
        self.assertIn("недостижимо", P.summary(st, data))

    def test_item_sources_real(self):
        snail = P.item_sources(946, DATA, lv=41)
        self.assertEqual(snail["status"], "drop")
        self.assertEqual(snail["best"]["mob"], "Elusive Ambernite")
        self.assertEqual(P.item_sources(1057, DATA, lv=41)["status"], "hard")      # Moth Dust: Dustiness 62
        self.assertEqual(P.item_sources(1, DATA)["status"], "unknown")


class FarmMapsTest(unittest.TestCase):
    def test_snail_shell_low_risk(self):
        from live_brain import atlas
        maps = P.farm_maps(946, DATA, 41, atlas.default())
        self.assertTrue(maps)
        self.assertIn(maps[0]["map"], ("prt_fild04", "prt_fild00"))
        self.assertLess(maps[0]["risk"], 0.5)
        self.assertEqual(P.farm_maps(946, DATA, 41)[0]["mob"], "Ambernite")


class ChooseAnswerTest(unittest.TestCase):
    def test_set_and_ordered(self):
        ans = [{"text": "Sir Andrew sent me to take your test."}, {"text": "I wish to take the test again."}]
        self.assertEqual(P.choose_answer(ans, ["I wish to take the test again.", "Oh, nothing."]), 1)
        self.assertIsNone(P.choose_answer(ans, ["Katana", "Slayer"]), "чужое меню — не отвечать")
        yes_no = [{"text": "Yes."}, {"text": "No."}]
        self.assertEqual(P.choose_answer(yes_no, ["Yes.", "No."], pos=1), 2)


class EquipmentTest(unittest.TestCase):
    def test_swordsman_lv40_budget_20000(self):
        e = P.next_equipment({"job": "Swordsman", "lv": 40, "sex": "Male"}, 20000, DATA)
        item = {x["id"]: x for x in DATA["catalog"]["equipment"]}[e["id"]]
        self.assertIn("Swordman", item["jobs"])
        self.assertLessEqual(e["price"], 20000)
        self.assertLessEqual(item["equip_lv"], 40)
        self.assertEqual((e["slot"], item["subtype"]), ("weapon", "1hSword"))
        self.assertIn(e["shop"]["map"], DATA["catalog"]["shop_maps"])
        self.assertTrue(e["equip_unknown"])

    def test_next_slot_when_weapon_is_best(self):
        e = P.next_equipment({"job": "Swordsman", "lv": 40, "equip": {"weapon": 1113}}, 20000, DATA)
        self.assertEqual(e["slot"], "Armor")
        self.assertIn("Swordman", {x["id"]: x for x in DATA["catalog"]["equipment"]}[e["id"]]["jobs"])

    def test_knight_two_hand_no_shield(self):
        eq = {"weapon": 1154, "Armor": 2316, "Shoes": 2405, "Garment": 2505, "Head_Top": 2228}
        e = P.next_equipment({"job": "Knight", "lv": 60, "equip": eq}, 10 ** 6, DATA)
        self.assertNotEqual((e or {}).get("slot"), "Left_Hand", "двуручный меч — щит не предлагать")

    def test_acolyte_mace_and_budget(self):
        e = P.next_equipment({"job": "Acolyte", "lv": 30, "sex": "Female"}, 20000, DATA)
        item = {x["id"]: x for x in DATA["catalog"]["equipment"]}[e["id"]]
        self.assertEqual(item["subtype"], "Mace")
        self.assertIn("Acolyte", item["jobs"])
        self.assertIsNone(P.next_equipment({"job": "Acolyte", "lv": 30}, 0, DATA))

    def test_summary(self):
        text = P.summary(ARKADY, DATA)
        self.assertIn("Knight", text)
        self.assertIn("40", text)
        self.assertIn("Priest", P.summary(VERA, DATA))


if __name__ == "__main__":
    unittest.main()
