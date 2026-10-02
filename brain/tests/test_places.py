"""Имена мест (ORG-084, ТЗ Т-25): словарь по атласу, свои имена по фактам, консенсус через шину, речь и летопись.

Настоящий Mind (social с грамматикой, places) с поддельным телом и общей шиной мира во временном каталоге.
Запуск: cd brain && python3 -m unittest -v tests.test_places
"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import grammar as g
from live_brain import places as pl
from live_brain import world_bus
from live_brain.chronicle import chronicle
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.safety import MAX_TEXT

from tests.test_topics import WORLD

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT = BRAIN_DIR.parent
NAMES = json.loads((BRAIN_DIR / "world" / "place_names.json").read_text(encoding="utf-8"))


def make(root, bot, name, bus, env=None):
    async def send(a):
        return 1

    persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text(encoding="utf-8"))
    persona.pop("sleep", None)
    mem = Memory(root / f"{bot}.sqlite")
    mind = Mind(Settings.from_env(env or {}), persona, mem, send, root / f"{bot}.jsonl", RuleGate(),
                peers={"Arkady", "Vera", "Ilsa"}, world=WORLD, world_bus_db=bus)
    mind.state = {"name": name, "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 40, "dead": False,
                  "players": []}
    return mind


class DictionaryTest(unittest.TestCase):
    def test_generator_matches_file(self):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_place_names.py")], capture_output=True,
                             text=True, check=True).stdout
        self.assertEqual(out, (BRAIN_DIR / "world" / "place_names.json").read_text(encoding="utf-8"),
                         "словарь устарел: scripts/gen_place_names.py > brain/world/place_names.json")

    def test_names_and_cases(self):
        m = NAMES["maps"]["prt_fild08"]
        self.assertEqual((m["town"], m["hops"], m["dir"], m["land"]), ("prontera", 1, "s", "холм"))
        self.assertEqual(m["full"]["nom"], "Южное поле Пронтеры")
        self.assertEqual(m["full"]["loc"], "на Южном поле Пронтеры")
        self.assertEqual(m["short"]["gen"], "Южного поля")
        self.assertEqual(NAMES["maps"]["prt_fild05"]["full"]["nom"], "Западное поле Пронтеры")
        self.assertEqual(NAMES["maps"]["pay_fild01"]["full"]["loc"], "в Южном лесу Пайона")
        self.assertEqual(NAMES["maps"]["pay_dun01"]["full"]["nom"], "Пещера Пайона, ярус 2")
        self.assertEqual(NAMES["maps"]["pay_dun01"]["short"]["loc"], "в Пещере Пайона, ярус 2")   # с городом
        self.assertEqual(NAMES["towns"]["prontera"]["loc"], "в Пронтере")
        noms = [m["full"]["nom"] for m in NAMES["maps"].values()]
        self.assertEqual(len(noms), len(set(noms)), "имена карт совпадают")
        self.assertNotIn("prt_in", NAMES["maps"])                        # интерьер — без имени

    def test_personal_forms(self):
        f = pl.personal_forms(NAMES, "prt_fild08", "death")
        self.assertEqual((f["nom"], f["loc"]), ("Гиблый холм", "на Гиблом холме"))
        self.assertEqual(pl.personal_forms(NAMES, "prt_fild07", "death")["nom"], "Гиблая роща")
        self.assertEqual(pl.personal_forms(NAMES, "prt_fild05", "rescue")["nom"], "Луг спасения")
        self.assertEqual(pl.personal_forms(NAMES, "prt_fild08", "mob", mob="Порингов")["loc"], "на Холме Порингов")
        self.assertEqual(pl.personal_forms(NAMES, "pay_fild01", "found", who="Vera")["nom"], "Тропа Vera")
        self.assertIsNone(pl.personal_forms(NAMES, "prt_in", "death"))

    def test_consensus(self):
        f = {"nom": "X"}
        rows = [{"bot": "Arkady", "ts": 1, "data": {"map": "m1", "name": "A", "forms": f}},
                {"bot": "Vera", "ts": 2, "data": {"map": "m1", "name": "B", "forms": f}},
                {"bot": "Ilsa", "ts": 3, "data": {"map": "m1", "name": "B", "forms": f, "adopt": True}},
                {"bot": "Arkady", "ts": 4, "data": {"map": "m1", "name": "A", "forms": f}},     # повтор автора
                {"bot": "Arkady", "ts": 5, "data": {"map": "m2", "name": "C", "forms": f}}]
        out = pl.consensus(rows)
        self.assertEqual(set(out), {"m1"})
        self.assertEqual((out["m1"]["name"], out["m1"]["users"]), ("B", ["Vera", "Ilsa"]))
        rows.append({"bot": "Bors", "ts": 6, "data": {"map": "m1", "name": "A", "forms": f}})
        self.assertEqual(pl.consensus(rows)["m1"]["name"], "A")           # 2 на 2 — раньше


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "state" / "shared" / "world.sqlite"
        self.bus_a = world_bus.WorldBus(self.path, "Arkady")
        self.bus_v = world_bus.WorldBus(self.path, "Vera")
        self.a = make(self.root, "bot01", "Arkady", self.bus_a)
        self.v = make(self.root, "bot02", "Vera", self.bus_v)

    def tearDown(self):
        for m in (self.a, self.v):
            m.mem.close()
        self.bus_a.close()
        self.bus_v.close()
        self.tmp.cleanup()

    def tick(self, mind):
        mind.places.next_check = 0
        mind.places.tick()

    def own(self, mind, code):
        return (mind.places.st["own"].get(code) or {}).get("name")

    def test_module_wired(self):
        self.assertIsNotNone(self.a.places)
        self.assertEqual(self.a.social.grammar.resolver, self.a.places.forms)
        self.assertIn("place", self.a.social.topics)

    def test_death_name_published(self):
        for _ in range(2):
            self.a.mem.add_event("died", {"map": "prt_fild08"})
        self.tick(self.a)
        self.assertIsNone(self.own(self.a, "prt_fild08"))                # 2 < death_min
        self.a.mem.add_event("died", {"map": "prt_fild08"})
        self.tick(self.a)
        self.assertEqual(self.own(self.a, "prt_fild08"), "Гиблый холм")
        rows = self.bus_v.recent("place_name")
        self.assertEqual([(r["bot"], r["data"]["name"], r["data"]["kind"]) for r in rows],
                         [("Arkady", "Гиблый холм", "death")])
        self.assertEqual(rows[0]["data"]["why"], "там я погиб")
        mem = " ".join(m["text"] for m in self.a.mem.top_memories(10))
        self.assertIn("Я зову Южное поле «Гиблый холм»: там я погиб.", mem)
        self.assertEqual(self.a.mem.recent_events(1)[-1]["rule"], "death")      # событие памяти place_named
        self.assertEqual(world_bus.describe("place_name", rows[0]["data"]),
                         "назвал(а) Южное поле Пронтеры «Гиблый холм»: там я погиб")

    def test_kv_of_explorer_untouched(self):
        """kv places — открытые места explorer (needs читает first): имена мест живут в kv place_names."""
        self.a.mem.set("places", {"prt_fild08": {"first": 1.0}})
        for _ in range(3):
            self.a.mem.add_event("died", {"map": "prt_fild08"})
        self.tick(self.a)
        self.assertEqual(self.a.mem.get("places"), {"prt_fild08": {"first": 1.0}})
        self.assertEqual(self.a.mem.get("place_names")["own"]["prt_fild08"]["name"], "Гиблый холм")

    def test_others_deaths_count(self):
        self.bus_v.publish("death_report", {"map": "prt_fild07"}, 4)
        self.bus_v.publish("death_report", {"map": "prt_fild07"}, 4)
        self.a.mem.add_event("died", {"map": "prt_fild07"})
        self.tick(self.a)
        self.assertEqual(self.own(self.a, "prt_fild07"), "Гиблая роща")
        self.assertEqual(self.a.places.st["own"]["prt_fild07"]["why"], "там я погиб")

    def test_rescue_with_map_from_support_and_priority(self):
        self.a.mem.set("known_players", {"Vera": {"sex": "Female"}})
        for _ in range(3):
            self.a.mem.add_event("died", {"map": "prt_fild05"})
        self.a.mem.add_event("support", {"map": "prt_fild05", "from": "Vera", "to": "Arkady", "skill": "AL_HEAL"})
        self.a.state["map"] = "prontera"                                 # карта — из события, а не из state
        self.a.mem.add_event("heal_confirmed", {"from": "Vera", "to": "Arkady", "amount": 300, "hp_before": 20,
                                                "hp_max": 400})
        self.tick(self.a)
        own = self.a.places.st["own"]["prt_fild05"]
        self.assertEqual((own["name"], own["kind"], own["why"]), ("Луг спасения", "rescue", "там Vera спасла меня"))
        for _ in range(5):
            self.a.mem.add_event("died", {"map": "prt_fild05"})
        self.tick(self.a)
        self.assertEqual(self.own(self.a, "prt_fild05"), "Луг спасения")   # имя не меняется

    def test_light_heal_is_not_rescue(self):
        self.a.mem.add_event("support", {"map": "prt_fild05", "from": "Vera", "to": "Arkady", "skill": "AL_HEAL"})
        self.a.mem.add_event("heal_confirmed", {"from": "Vera", "to": "Arkady", "amount": 50, "hp_before": 300,
                                                "hp_max": 400})
        self.tick(self.a)
        self.assertEqual(self.a.places.st["own"], {})

    def test_found_meet_mob(self):
        self.a.mem.add_event("explore_found", {"map": "prt_fild06", "hops": 1})
        self.bus_v.publish("place_found", {"map": "prt_fild10"}, 3, now=time.time() - 3600)   # Vera — раньше
        self.a.mem.add_event("explore_found", {"map": "prt_fild10", "hops": 4})
        for _ in range(2):
            self.a.mem.add_event("meeting_confirmed", {"partner": "Vera", "map": "prt_fild01"})
        for _ in range(150):
            self.a.mem.add_event("kill", {"monster": "Poring", "map": "prt_fild02"})
        for _ in range(200):
            self.a.mem.add_event("kill", {"monster": "Unknownmob", "map": "prt_fild04"})
        self.tick(self.a)
        self.assertEqual(self.own(self.a, "prt_fild06"), "Луг Arkady")
        self.assertIsNone(self.own(self.a, "prt_fild10"))                # не первооткрыватель
        self.assertEqual(self.own(self.a, "prt_fild01"), "Холм встреч")
        self.assertEqual(self.own(self.a, "prt_fild02"), "Долина Порингов")
        self.assertIsNone(self.own(self.a, "prt_fild04"))                # монстра нет в словаре
        self.assertEqual(self.a.places.st["own"]["prt_fild06"]["why"], "я тут был первым")

    def test_taken_name_skipped(self):
        self.bus_v.publish("place_name", {"map": "prt_fild01", "name": "Холм встреч", "forms": {"nom": "x"}}, 2)
        for _ in range(2):
            self.a.mem.add_event("meeting_confirmed", {"partner": "Vera", "map": "prt_fild04"})
        self.tick(self.a)
        self.assertIsNone(self.own(self.a, "prt_fild04"))                # «Холм встреч» уже у другой карты

    # ---------- консенсус ----------

    def name_by_arkady(self):
        for _ in range(3):
            self.a.mem.add_event("died", {"map": "prt_fild08"})
        self.tick(self.a)

    def test_adopt_when_knows_place_and_fixed(self):
        self.name_by_arkady()
        self.assertEqual(self.a.places.fixed, {})                        # один житель — не закреплено
        self.v.mem.add_event("kill", {"monster": "Lunatic", "map": "prt_fild08"})   # Vera знает место
        self.tick(self.v)
        self.assertEqual(self.own(self.v, "prt_fild08"), "Гиблый холм")
        self.assertTrue(self.v.places.st["own"]["prt_fild08"]["adopted"])
        self.assertEqual(self.v.places.fixed["prt_fild08"]["users"], ["Arkady", "Vera"])
        self.tick(self.a)
        self.assertIn("prt_fild08", self.a.places.fixed)
        self.tick(self.v)
        self.assertEqual(len(self.bus_a.recent("place_name")), 2)        # поддержка — один раз

    def test_no_adopt_without_knowledge_unless_friend(self):
        self.name_by_arkady()
        self.tick(self.v)
        self.assertIsNone(self.own(self.v, "prt_fild08"))
        for _ in range(3):
            self.v.mem.update_relation("Arkady", 1, "друг")
        self.tick(self.v)
        self.assertEqual(self.own(self.v, "prt_fild08"), "Гиблый холм")

    # ---------- речь ----------

    def test_speech_uses_names(self):
        s = self.a.social
        s.grammar.cfg.update(mix=0, tic_chance=0, tail_chance=0)
        s.phrases = dict(s.phrases, death=["Слёг на {death_map}. Обидно."])
        f = {"name": "Vera", "death_map": "prt_fild08"}
        self.assertEqual(s.phrase("death", f), "Слёг на Южном поле. Обидно.")             # свой город — коротко
        f["death_map"] = "pay_fild01"
        self.assertEqual(s.phrase("death", f), "Слёг в Южном лесу Пайона. Обидно.")
        self.name_by_arkady()
        f["death_map"] = "prt_fild08"
        self.assertEqual(s.phrase("death", f), "Слёг на Гиблом холме. Обидно.")           # своё имя
        f["death_map"] = "xyz_unknown01"
        self.assertEqual(s.phrase("death", f), "Слёг на карте xyz_unknown01. Обидно.")

    def test_long_name_falls_back_to_short(self):
        s = self.a.social
        s.grammar.cfg.update(mix=0, tic_chance=0, tail_chance=0)
        s.phrases = dict(s.phrases, hunt=["{name}, на {map} побед: {kills}."])
        text = s.phrase("hunt", {"name": "Abcdefgh", "map": "gef_fild06", "kills": 999})
        self.assertEqual(text, "Abcdefgh, на Дальнем северо-западном поле II побед: 999.")   # полное не влезло

    def test_place_topic(self):
        self.name_by_arkady()
        facts = self.a.places.facts("Vera", time.time())
        self.assertEqual((facts["place"], facts["base"], facts["why"]), ("Гиблый холм", "Южное поле", "там я погиб"))
        text, extra = self.a.social.compose("Vera", "place", None, time.time())
        self.assertIn("Гиблый холм", text)
        self.a.places.said("Vera", extra, time.time())
        self.assertIsNone(self.a.places.facts("Vera", time.time()))       # уже говорил
        self.assertIsNotNone(self.a.places.facts("Ilsa", time.time()))

    def test_phrases_fit(self):
        subs = {"place": "x" * pl.NAME_MAX, "why": "там Abcdefgh спасла меня", "base": "x" * pl.BASE_MAX}
        for key, pool in pl.PHRASES.items():
            for p in pool:
                text = p.format(**{k: v for k, v in subs.items() if k in g.fields(p)})
                self.assertLessEqual(len(text), 60, text)
                self.assertLessEqual(len(f"{text} [chat:place:4]"), MAX_TEXT)

    # ---------- летопись ----------

    def test_chronicle_uses_fixed_names(self):
        self.name_by_arkady()
        self.v.mem.add_event("kill", {"monster": "Lunatic", "map": "prt_fild08"})
        self.tick(self.v)
        for bot, name in (("bot01", "Arkady"), ("bot02", "Vera")):
            (self.root / "state" / bot).mkdir(parents=True, exist_ok=True)
            mem = Memory(self.root / "state" / bot / "memory.sqlite")
            mem.set("last_state", {"name": name, "map": "pay_fild01"})
            mem.add_event("death_report", {"map": "prt_fild08", "cause": "Lunatic"})
            mem.close()
        day = time.strftime("%Y-%m-%d", time.gmtime())
        text = chronicle(self.root, ["bot01", "bot02"], day=day, tz_hours=0)
        self.assertIn("Vera: погиб на Гиблом холме (бил Lunatic)", text)
        self.assertIn("Южный лес Пайона", text)
        self.assertIn("назвал(а) Южное поле Пронтеры «Гиблый холм»", text)   # строка шины — словарным именем
        self.assertNotIn("prt_fild08", text)


class SwitchTest(unittest.TestCase):
    def test_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            m = make(Path(d), "bot01", "Arkady", None, env={"BRAIN_DISABLE": "places"})
            self.assertIsNone(m.places)
            self.assertIsNone(m.social.grammar.resolver)
            m.social.grammar.cfg.update(mix=0, tic_chance=0, tail_chance=0)
            m.social.phrases = dict(m.social.phrases, death=["Слёг на {death_map}."])
            self.assertEqual(m.social.phrase("death", {"name": "Vera", "death_map": "prt_fild08"}),
                             "Слёг на карте prt_fild08.")
            m.mem.close()

    def test_no_bus_own_names_only(self):
        with tempfile.TemporaryDirectory() as d:
            m = make(Path(d), "bot01", "Arkady", None)
            for _ in range(3):
                m.mem.add_event("died", {"map": "prt_fild08"})
            m.places.next_check = 0
            m.places.tick()
            self.assertEqual(m.places.name("prt_fild08"), "Гиблый холм")
            self.assertEqual(m.places.fixed, {})
            m.mem.close()


if __name__ == "__main__":
    unittest.main()
