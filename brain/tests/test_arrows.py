"""Arrow Crafting — ремесло лучника (arrows.py, ORG-075, ТЗ Т-33) на синтетическом лучнике.

Настоящий Mind + Memory во временном каталоге, тело — поддельное состояние (state.job Archer, job_lv, state.craft).
Данные Roberto и рецептов — brain/world/crafts.json (сверка со скриптами — tests.test_arrows.ArrowDataTest при наличии
upstream/rathena). Это проверка правил мозга, а не ремесла в игре.

Запуск: cd brain && python3 -m unittest -v tests.test_arrows
"""
import asyncio
import json
import unittest

from live_brain import arrows as AR
from tests.test_herbal import CRAFTS, HerbalCase
from tests.test_newborn import HAVE_RATHENA, RATHENA, script_line

QUEST = {"907": 20, "921": 7, "906": 41, "1019": 13}


def bag(**kw):
    items = {str(i): 0 for i in (507, 508, 509, 510, 511, 713, 902, 906, 907, 909, 921, 1019, 1750, 1770)}
    items.update({k.lstrip("i"): v for k, v in kw.items()})
    return items


class ArrowsTest(HerbalCase):
    def archer(self, job="Archer", job_lv=35, skill=0, **items):
        self.make(herbalist=False)
        self.a = self.mind.arrows
        self.a.clock = self.clock
        self.state(name="Ilsa", job=job, job_lv=job_lv,
                   craft={"items": bag(**items), "kept": [], "weight_free": 1500, "skills": {"AC_MAKINGARROW": skill}})

    def bag(self, **items):
        self.state(craft=dict(self._last["craft"], items=bag(**items)))

    def run_tick(self, minutes=1):
        self.clock.t += minutes * 60
        asyncio.run(self.a.tick())

    def test_sleeps_without_archer(self):
        self.make(herbalist=False)
        self.a = self.mind.arrows
        self.a.clock = self.clock
        self.assertIsNotNone(self.a, "модуль включён, но спит")
        self.run_tick()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.events("arrows_stage"), [])
        self.assertIsNone(self.a.summary())
        self.assertIsNone(self.a.facts("Vera", self.clock.t))

    def test_quest_stages(self):
        self.archer(job_lv=12)
        self.run_tick()
        self.assertEqual(self.a.st["stage"], "exp")
        keep = self.actions("craft_setup")
        self.assertEqual(keep[0]["keep"], [906, 907, 921, 1019], "материалы Roberto не продавать")
        self.state(job_lv=30)
        self.bag(i907=20, i921=3, i906=41, i1019=13)
        self.run_tick()
        self.assertEqual(self.a.st["stage"], "items")
        self.assertEqual(self.a.missing(), {"921": 4})
        self.assertEqual(self.a.summary()["не_хватает"], {"921": 4})
        self.bag(i907=20, i921=7, i906=41, i1019=13)
        self.run_tick()
        self.assertEqual(self.a.st["stage"], "no_route", "Морокк закрыт — пути нет")
        self.assertEqual(self.actions("job_change"), [])
        stages = [e["stage"] for e in self.events("arrows_stage")]
        self.assertEqual(stages, ["exp", "items", "no_route"])
        self.run_tick()
        self.assertEqual(len(self.events("arrows_stage")), 3, "запись о стадии — один раз")

    def test_hunter_needs_no_job_level(self):
        self.archer(job="Hunter", job_lv=5, **{f"i{k}": v for k, v in QUEST.items()})
        self.run_tick()
        self.assertEqual(self.a.st["stage"], "no_route")

    def test_red_potion_counts_from_items(self):
        self.archer(**{f"i{k}": v for k, v in QUEST.items()})
        self.state(items={"501": 0})
        self.assertEqual(self.a.missing(), {"501": 1})

    def test_quest_with_route(self):
        crafts = json.loads(json.dumps(CRAFTS))
        crafts["routes"]["prontera"]["roberto"] = {"hops": 2, "legs": [["moc_fild01", 200, 200]], "stand": [118, 97],
                                                   "path": ["prontera", "moc_fild01", "moc_ruins"], "back": 2}
        self.archer(**{f"i{k}": v for k, v in QUEST.items()})
        self.a.crafts = crafts
        self.a.cfg["quest_auto"] = True
        self.run_tick()
        self.assertEqual(self.a.st["stage"], "ready")
        a = [x for x in self.actions("job_change") if x["path"] == "arrows"][0]
        self.assertEqual(a["steps"][-1], {"do": "talk", "x": 118, "y": 99, "answers": []})
        self.assertEqual(a["success"], {"text": "as I promised", "map": "moc_ruins"})
        if self.mind.career:
            self.mind.career.on_result = lambda e: self.fail("итог квеста лучника ушёл в career")
        self.event(path="arrows", stage="roberto", ok=True, reason="ok")
        self.assertEqual(self.events("arrows_skill"), [], "фраза NPC — не доказательство")
        self.state(craft=dict(self._last["craft"], skills={"AC_MAKINGARROW": 1}))
        self.run_tick()
        sk = self.events("arrows_skill")
        self.assertTrue(sk[0]["learned"] and sk[0]["roberto"])
        from live_brain.chronicle import LINES
        self.assertIn("Roberto", LINES["arrows_skill"](sk[0]))

    def test_crafting_by_fact(self):
        self.archer(skill=1, i1019=3, i909=10)
        self.run_tick()
        sk = self.events("arrows_skill")
        self.assertFalse(sk[0]["learned"], "навык был до наблюдения — не событие недели")
        crafts = self.actions("arrowcraft")
        self.assertEqual([c["item"] for c in crafts], [1019], "первый источник из sources — Trunk")
        asyncio.run(self.mind.on_event({"type": "event", "kind": "arrowcraft_result", "item": 1019, "ok": True}))
        self.bag(i1019=2, i909=10, i1750=40)
        self.run_tick()
        done = self.events("arrows_crafted")
        self.assertEqual((done[0]["n"], done[0]["arrow"], done[0]["source"]), (40, "Arrow", "Trunk"))
        self.run_tick(5)
        self.assertEqual(len(self.actions("arrowcraft")), 1, "не чаще craft_minutes")
        self.run_tick(20)
        self.assertEqual(len(self.actions("arrowcraft")), 2)
        f = self.a.facts("Vera", self.clock.t)
        self.assertEqual((f["n"], f["source"]), (40, "Trunk"))
        for line in AR.PHRASES["arrows"]:
            self.assertLessEqual(len(line.format(n=600, arrow="Immaterial Arrow", source="Old Blue Box")), 60)

    def test_crafting_without_arrows_fails(self):
        self.archer(skill=1, i909=2)
        self.run_tick()
        self.assertEqual(self.actions("arrowcraft")[0]["item"], 909)
        asyncio.run(self.mind.on_event({"type": "event", "kind": "arrowcraft_result", "item": 909, "ok": False,
                                        "reason": "предмета нет в списке сервера"}))
        self.run_tick()
        self.assertNotIn("craft", self.a.st)
        self.assertEqual(self.events("arrows_crafted"), [])

    def test_no_craft_in_fight_or_without_loot(self):
        self.archer(skill=1)
        self.run_tick()
        self.assertEqual(self.actions("arrowcraft"), [], "нет лута")
        self.bag(i1019=1)
        self.state(activity="attack")
        self.run_tick()
        self.assertEqual(self.actions("arrowcraft"), [], "в бою не крафтит")

    def test_safety(self):
        from live_brain.safety import SafetyPolicy
        s = SafetyPolicy(["prt_fild08"])
        self.assertEqual(s.check({"action": "arrowcraft", "item": 1019}, {}, protocol=True)[0],
                         {"action": "arrowcraft", "item": 1019})
        self.assertIsNone(s.check({"action": "arrowcraft", "item": 1019}, {})[0], "модель не получает")
        self.assertIsNone(s.check({"action": "arrowcraft", "item": "Trunk"}, {}, protocol=True)[0])


@unittest.skipUnless(HAVE_RATHENA, "нет upstream/rathena (LIVE_RO_RATHENA)")
class ArrowDataTest(unittest.TestCase):
    def test_roberto_script(self):
        r = CRAFTS["roberto"]
        self.assertTrue(script_line(r["src"]).startswith(f"moc_ruins,{r['x']},{r['y']},"))
        self.assertIn("JobLevel >= 30", script_line(r["job_lv_src"]))
        self.assertEqual(r["items"], {"1019": 13, "501": 1, "906": 41, "907": 20, "921": 7})
        self.assertIn(r["proof"]["text"], script_line(r["proof"]["src"]))
        self.assertIn('skill "AC_MAKINGARROW",1', script_line(r["skill_src"]))

    def test_arrow_recipes(self):
        text = (RATHENA / "db" / "create_arrow_db.yml").read_text(encoding="utf-8")
        self.assertIn("Source: Wooden_Block", text)
        self.assertEqual(CRAFTS["arrows"]["1019"]["make"], [[1750, "Arrow", 40]])
        self.assertEqual(CRAFTS["arrows"]["909"]["make"], [[1750, "Arrow", 4]])
        self.assertEqual(CRAFTS["arrows"]["713"]["make"], [[1770, "Iron Arrow", 2]])
        for src in AR.DEFAULTS["sources"]:
            self.assertIn(str(src), CRAFTS["arrows"])


if __name__ == "__main__":
    unittest.main()
