"""Питомец жителя (pets.py, ORG-051): любимцы по картам охоты, приручение, вылупление, корм, факты от тела.

Запуск: cd brain && python3 -m unittest -v tests.test_pets
"""
import asyncio
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_brain import pets as pets_mod
from live_brain.memory import Memory
from live_brain.pets import INCUBATOR, PET_FOOD, TAME_GAP, Pets
from live_brain.safety import SafetyPolicy

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT = BRAIN_DIR.parent
RATHENA = Path(os.environ.get("LIVE_RO_RATHENA") or ROOT / "upstream" / "rathena")   # клон без сабмодулей — путь из окружения
DATA = {"1002": {"mob": "PORING", "name": "Poring", "level": 1, "tame": 619, "tame_name": "Unripe Apple",
                 "egg": 9001, "egg_name": "Poring Egg", "food": 531, "food_name": "Apple Juice", "capture": 2000},
        "1011": {"mob": "CHONCHON", "name": "Chonchon", "level": 5, "tame": 624, "tame_name": "Rotten Fish",
                 "egg": 9008, "egg_name": "Chonchon Egg", "food": PET_FOOD, "food_name": "Pet Food", "capture": 1500},
        "1063": {"mob": "LUNATIC", "name": "Lunatic", "level": 3, "tame": 622, "tame_name": "Rainbow Carrot",
                 "egg": 9004, "egg_name": "Lunatic Egg", "food": 534, "food_name": "Carrot Juice", "capture": 1500},
        "1150": {"mob": "MOONLIGHT", "name": "Moonlight Flower", "level": 79, "tame": 636, "tame_name": "x",
                 "egg": 9025, "egg_name": "y", "food": PET_FOOD, "food_name": "Pet Food", "capture": 300}}
MAPS = {"prt_fild08": {"1002": 80, "1063": 60, "1150": 1}, "prt_fild01": {"1011": 10}, "pay_fild01": {"1031": 5}}


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class PetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.sent, self.dec = [], []
        policy = SafetyPolicy(["prt_fild08"], peers=set())

        async def execute(actions, source, reason, protocol=False):
            for a in actions:
                ok, why = policy.check(a, self.mind.state, protocol=protocol)
                self.assertIsNotNone(ok, f"safety отклонил {a}: {why}")
                self.sent.append(ok)

        persona = json.loads((BRAIN_DIR / "personas" / "bot02.json").read_text())
        persona["hunt_maps"] = ["prt_fild08", "prt_fild01"]
        self.mind = SimpleNamespace(
            mem=self.mem, fresh_state=True, execute=execute, write_decision=self.dec.append, epoch=1,
            persona=persona, needs=SimpleNamespace(t={"generosity": 0.7}),
            routine=SimpleNamespace(st={"mode": "hunt"}),
            state={"name": "Vera", "lv": 10, "hp_pct": 100, "dead": False,
                   "pet": {"has": False, "eggs": [], "items": {}, "near": {}}})
        self.clock = Clock()
        self.p = Pets(self.mind, clock=self.clock, data=DATA, atlas_maps=MAPS)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def run_tick(self):
        self.clock.t += 20
        asyncio.run(self.p.tick())

    def kinds(self):
        return [d.get("event") for d in self.dec]

    def test_favorites_from_hunt_maps(self):
        fav = [p["name"] for p in self.p.favorites()]
        self.assertEqual(fav[0], "Chonchon", "Pet Food впереди: его можно докупить")
        self.assertIn("Poring", fav)
        self.assertNotIn("Moonlight Flower", fav, "сильнее жителя — не любимец")
        self.assertEqual(set(self.p.wants()), {"624", "619", "622"})
        self.mind.needs.t["generosity"] = 0.1
        self.assertEqual(self.p.wants(), {}, "равнодушный к заботе питомца не хочет")

    def test_setup_once_per_connection(self):
        self.run_tick()
        self.assertEqual(self.sent[0]["action"], "pet_setup")
        self.assertEqual(self.sent[0]["mobs"], [1011, 1002, 1063])
        self.assertFalse(self.sent[0]["food_on"])
        self.run_tick()
        self.assertEqual(len(self.sent), 1, "повторно не шлю в том же подключении")
        self.mind.epoch = 2
        self.run_tick()
        self.assertEqual(len(self.sent), 2, "новое подключение тела — настройка заново")

    def test_tame_only_when_item_and_mob_near(self):
        self.run_tick()
        self.sent.clear()
        pet = self.mind.state["pet"]
        pet["items"] = {"619": 1}
        pet["near"] = {"1002": 12}
        self.run_tick()
        self.assertEqual(self.sent, [], "монстр далеко")
        pet["near"] = {"1002": 5}
        self.mind.state["hp_pct"] = 40
        self.run_tick()
        self.assertEqual(self.sent, [], "HP мало — не до питомца")
        self.mind.state["hp_pct"] = 90
        self.mind.routine.st["mode"] = "town"
        self.run_tick()
        self.assertEqual(self.sent, [], "приручают на охоте")
        self.mind.routine.st["mode"] = "hunt"
        self.run_tick()
        self.assertEqual(self.sent, [{"action": "pet_tame", "item": 619, "mob": 1002}])
        self.run_tick()
        self.assertEqual(len(self.sent), 1, f"не чаще раза в {TAME_GAP} с")

    def test_hatch_egg_with_incubator(self):
        self.run_tick()
        self.sent.clear()
        pet = self.mind.state["pet"]
        pet["eggs"] = [9001]
        self.run_tick()
        self.assertEqual(self.sent, [])
        self.assertIn(str(INCUBATOR), self.p.wants(), "без инкубатора — хочу его купить у жителей")
        pet["items"] = {str(INCUBATOR): 1}
        self.run_tick()
        self.assertEqual(self.sent, [{"action": "pet_hatch", "egg": 9001}])

    def test_facts_from_body(self):
        self.p.on_event({"kind": "pet_tame_result", "ok": True, "mob": 1002, "item": 619})
        self.p.on_event({"kind": "pet_hatched", "ok": True, "egg": 9001, "mob": 1011, "name": "Chonchon"})
        self.assertEqual(self.kinds(), ["pet_tamed", "pet_hatched"])
        self.assertEqual(self.p.topic(), "мой питомец Chonchon")
        self.assertEqual(self.mem.count_events("pet_hatched", 0), 1)
        # после вылупления — настройка корма: Chonchon ест Pet Food
        self.mind.state["pet"] = {"has": True, "type": 1011, "name": "Chonchon", "hungry": 60, "items": {}, "near": {}}
        self.run_tick()
        self.assertTrue(self.sent[-1]["food_on"], "Pet Food — докупать")
        self.p.on_event({"kind": "pet_fed", "ok": True, "food": PET_FOOD})
        self.assertEqual(self.mem.count_events("pet_fed", 0), 1)

    def test_hungry_without_food_noted_once(self):
        self.mind.state["pet"] = {"has": True, "type": 1002, "name": "Poring", "hungry": 10, "items": {"531": 0}}
        self.run_tick()
        self.run_tick()
        self.assertEqual(self.kinds().count("pet_hungry"), 1)

    def test_pet_gone(self):
        self.mind.state["pet"] = {"has": True, "type": 1002, "name": "Poring", "hungry": 60, "items": {}}
        self.run_tick()
        self.mind.state["pet"] = {"has": False, "eggs": [9001], "items": {}, "near": {}}
        self.run_tick()
        self.assertIn("pet_gone", self.kinds())

    def test_no_plugin_no_actions(self):
        self.mind.state.pop("pet")
        self.run_tick()
        self.assertEqual(self.sent, [])

    def test_safety_rejects_bad_pet_actions(self):
        policy = SafetyPolicy(["prt_fild08"], peers=set())
        st = {"dead": False}
        for bad in ({"action": "pet_tame", "item": "619", "mob": 1002}, {"action": "pet_hatch", "egg": 1},
                    {"action": "pet_setup", "items": list(range(100, 130)), "mobs": []}):
            self.assertIsNone(policy.check(bad, st, protocol=True)[0], bad)
        self.assertIsNone(policy.check({"action": "pet_tame", "item": 619, "mob": 1002}, st)[0], "модели нельзя")
        self.assertIsNone(policy.check({"action": "pet_tame", "item": 619, "mob": 1002}, {"dead": True},
                                       protocol=True)[0], "мёртвому нельзя")


class PetsDataTest(unittest.TestCase):
    def test_generated_data(self):
        data = pets_mod.load()
        self.assertGreater(len(data), 30)
        poring = data["1002"]
        self.assertEqual((poring["tame"], poring["egg"], poring["food"]), (619, 9001, 531))
        for p in data.values():
            self.assertTrue(9000 <= p["egg"] <= 9999 and p["tame"] < 20000 and p["food"] < 20000, p)

    @unittest.skipUnless((RATHENA / "db" / "re" / "pet_db.yml").exists(), "нет upstream/rathena")
    def test_matches_generator(self):
        spec = importlib.util.spec_from_file_location("gen_pets", ROOT / "scripts" / "gen_pets.py")
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        pets = gen.build(RATHENA)
        self.assertEqual(gen.dump(pets), pets_mod.PETS_PATH.read_text(encoding="utf-8"),
                         "pets.json устарел: scripts/gen_pets.py > brain/world/pets.json")
        keep = gen.items_control(pets)
        for bot in ("bot01", "bot02"):
            text = (ROOT / "bots" / bot / "control" / "items_control.txt").read_text(encoding="utf-8")
            self.assertIn(keep.strip(), text, f"{bot}: раздел питомцев в items_control устарел")


if __name__ == "__main__":
    unittest.main()
