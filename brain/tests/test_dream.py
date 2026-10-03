"""Жизненный путь (dream.py, ORG-081): выбор мечты, этапы по фактам, смена, цели недели и мотивы.

Настоящий Mind (Arkady) с поддельным телом и настоящей шиной мира во временном каталоге; время модуля — подменные
часы. Запуск: cd brain && python3 -m unittest -v tests.test_dream
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import world_bus
from live_brain.aims import Aims
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.dream import DEATHS_TURN, JOB2_LV, Dream
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
LOW = dict.fromkeys(("bravery", "sociability", "greed", "curiosity", "diligence", "generosity", "patience",
                     "whimsy"), 0.1)
REACH = {"towns": {"prontera": {"maps": {"prt_fild05": {"hops": 1}, "prt_fild06": {"hops": 1},
                                         "prt_fild08": {"hops": 1}, "prt_fild07": {"hops": 2},
                                         "prt_in": {"hops": 1}, "gef_fild01": {"hops": 3}}}}}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self, env=None, traits=None, persona_dream=None):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)

        async def send(a):
            return 1

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        if persona_dream:
            persona["dream"] = persona_dream
        self.mem = Memory(root / "m.sqlite")
        self.bus = world_bus.WorldBus(root / "world.sqlite", "Arkady")
        self.mind = Mind(Settings.from_env(env or {}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=WORLD, world_bus_db=self.bus)
        self.clock = Clock(time.time())
        self.mind.needs.t = dict(LOW, **(traits if traits is not None else {"diligence": 0.9}))
        if self.mind.dream:
            self.d = Dream(self.mind, WORLD, clock=self.clock, rng=random.Random(1), reach=REACH)
            self.mind.dream = self.d
            self.mind.world.pump()                     # курсор шины на конец истории
        self.state(job="Swordman", job_lv=30)

    def tearDown(self):
        self.bus.close()
        self.mem.close()
        self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 40,
             "job": "Swordman", "job_lv": 30, "dead": False, "weight_pct": 20, "zeny": 60000, "players": []}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def tick(self, advance=0):
        self.clock.t += advance
        self.d.next_tick = 0
        self.d.tick()

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ? ORDER BY id",
                                                              (kind,))]


class ChoiceTest(Base):
    # ---------- выбор ----------

    def test_choice_by_traits(self):
        self.tick()
        st = self.d.st
        self.assertEqual((st["kind"], st["text"]), ("job2", "стать Knight"))
        self.assertEqual([s["metric"] for s in st["stages"]], ["job_lv", "job_is"])   # уже Swordman
        self.assertEqual(self.events("dream_new")[0]["dream"], "стать Knight")
        self.assertTrue(any("Моя мечта: стать Knight" in m["text"] for m in self.mem.top_memories(20)))
        self.assertEqual(self.d.summary()["этап"], f"1/2: уровень профессии {JOB2_LV} (30/{JOB2_LV})")

    def test_unavailable_templates_skipped(self):
        self.state(job="Knight", job_lv=10)
        self.assertIsNone(self.d.make("job2", self.mind.state))                # уже вторая профессия
        self.assertIsNone(self.d.make("guild", self.mind.state))               # guild выключен по умолчанию
        self.mind.pets = None
        self.assertIsNone(self.d.make("pet", self.mind.state))
        self.mem.set("places", {m: {"first": 1} for m in ("prt_fild05", "prt_fild06", "prt_fild07", "prt_fild08")})
        self.assertIsNone(self.d.make("explorer", self.mind.state))           # окрестности обойдены
        self.tick()
        self.assertNotEqual(self.d.st["kind"], "job2")

    def test_novice_first_job_stage(self):
        self.mind.persona["job"] = "Acolyte"
        if self.mind.career:
            self.mind.career.target = "Acolyte"
            self.mind.career.data = {"stub": True}
        self.state(job="Novice", job_lv=5)
        d = self.d.make("job2", self.mind.state)
        self.assertEqual(d["text"], "стать Priest")
        self.assertEqual(d["stages"][0], {"text": "стать Acolyte", "metric": "job_first", "target": 1})

    def test_explorer_region(self):
        self.assertEqual(self.d.region(), ["prt_fild05", "prt_fild06", "prt_fild07", "prt_fild08"])
        d = self.d.make("explorer", self.mind.state)
        self.assertEqual([s["target"] for s in d["stages"]], [2, 3, 4])


class PersonaDreamTest(Base):
    def setUp(self):
        super().setUp(traits={}, persona_dream="rich")

    def test_persona_favorite(self):
        self.tick()
        self.assertEqual(self.d.st["kind"], "rich")
        self.assertEqual(self.d.st["params"]["target"], 200000)               # 60 000 × 3, до 50 000 вверх
        self.assertEqual(self.d.save_target(), (200000, "скопить 200000 зени"))



class DreamLifeTest(Base):
    def test_stage_done_and_finish(self):
        self.tick()
        self.state(job_lv=JOB2_LV)
        self.tick()
        ev = self.events("dream_stage")
        self.assertEqual(ev, [{"dream": "стать Knight", "stage": f"уровень профессии {JOB2_LV}", "n": 1, "of": 2}])
        self.state(job="Knight", job_lv=1)
        self.tick()
        self.assertEqual(self.events("dream_done")[0]["dream"], "стать Knight")
        self.assertTrue(any(m["importance"] == 5 and "Мечта сбылась" in m["text"] for m in self.mem.top_memories(5)))
        self.assertEqual(self.d.st["history"][-1]["result"], "done")
        self.tick()                                                            # новая мечта — не того же вида
        self.assertNotIn(self.d.st.get("kind"), (None, "job2"))
        self.mind.world.pump()
        rows = {r["kind"]: r["importance"] for r in self.bus.read()}
        self.assertEqual(rows["dream_done"], 5)
        self.assertIn("мечта сбылась", LINES["dream_done"](self.events("dream_done")[0]))
        kinds = [e["kind"] for e in self.mem.recent_events(50)]                # данные события не затирают kind
        self.assertIn("dream_done", kinds)

    def test_impossible_module_off(self):
        self.mind.needs.t = dict(LOW, generosity=0.9, sociability=0.9)
        self.mind.interests = None                     # interests: ORG-103 — Arkady не увлечён питомцами
        self.tick()
        self.assertEqual(self.d.st["kind"], "pet")
        self.mind.pets = None
        self.tick()
        ch = self.events("dream_changed")
        self.assertEqual(ch[0]["why"], "модуль pets выключен")
        self.assertTrue(any(m["kind"] == "note" and "Решил(а) оставить мечту" in m["text"]
                            for m in self.mem.top_memories(20)))

    def test_turning_point_deaths(self):
        self.mind.needs.t = dict(LOW, curiosity=0.9)
        self.tick()
        self.assertEqual(self.d.st["kind"], "explorer")
        self.clock.t += 60
        for _ in range(DEATHS_TURN):
            self.mem.add_event("died", {"map": "prt_fild07"})
        self.tick(advance=60)
        self.assertIn("смертей", self.events("dream_changed")[0]["why"])
        self.tick()
        self.assertNotEqual(self.d.st["kind"], "explorer")                    # не сразу та же мечта

    def test_explorer_progress_by_places(self):
        self.mind.needs.t = dict(LOW, curiosity=0.9)
        self.tick()
        self.mem.set("places", {"prt_fild05": {"first": 1}, "prt_fild08": {"first": 2}, "prt_in": {"first": 3}})
        self.tick()
        self.assertEqual(self.events("dream_stage")[0]["n"], 1)
        self.assertEqual(self.d.st["stages"][1]["value"], 2)

    def test_stale(self):
        self.tick()
        self.tick(advance=46 * 86400)
        self.assertIn("без нового этапа", self.events("dream_changed")[0]["why"])

    # ---------- цели недели и мотивы ----------

    def test_weekly_aim_and_bonus(self):
        self.tick()
        aims = Aims(self.mind, clock=self.clock, rng=random.Random(2))
        aim = self.d.weekly_aim(self.mind.state, self.clock())
        self.assertEqual((aim["kind"], aim["metric"], aim["base"], aim["target"], aim["need"]),
                         ("dream", "job_lv", 30, 3, "progress"))
        self.assertIn("мечта: стать Knight", aim["text"])
        self.assertEqual(self.d.aim_bonus("job"), 0.3)
        self.assertEqual(self.d.aim_bonus("friend"), 0.0)
        kinds = [a["kind"] for a in aims.choose(self.mind.state, self.clock())]
        self.assertIn("dream", kinds)                                          # шаг этапа — среди шаблонов
        self.assertLess(kinds.index("level"), kinds.index("friend"))          # бонус этапа (+0.3)
        self.assertLess(kinds.index("dream"), kinds.index("friend"))
        self.state(job_lv=32)
        self.assertEqual(aims.progress(aim, self.mind.state, 0), 2)
        st = {"start": self.clock(), "until": self.clock() + 7 * 86400, "items": [dict(aim, progress=0)]}
        aims.save(st)
        self.assertGreater(aims.boost("progress", self.clock() + 3600), 1.0)    # need берётся из цели
        self.assertGreater(self.d.boost("progress"), 1.0)
        self.assertEqual(self.d.boost("social"), 1.0)
        w = self.mind.needs.weighted()
        self.assertIn("progress", w)

    def test_topic(self):
        self.tick()
        f = self.d.facts("Vera", self.clock())
        self.assertEqual(f, {"dream": "стать Knight", "stage": 1, "stages": 2})
        self.mind.social.phrases.setdefault("dream", [])
        for p in self.mind.social.phrases["dream"]:
            self.assertLessEqual(len(p.format(**f)), 60)
        self.d.said("Vera", f, self.clock())
        self.assertIsNone(self.d.facts("Vera", self.clock()))
        self.assertIsNotNone(self.d.facts("Arkady2", self.clock()))
        self.assertIn("dream", self.mind.social.topics)


class DreamOffTest(unittest.TestCase):
    def test_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            async def send(a):
                return 1
            persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
            mem = Memory(Path(tmp) / "m.sqlite")
            mind = Mind(Settings.from_env({"BRAIN_DISABLE": "dream"}), persona, mem, send, Path(tmp) / "d.jsonl",
                        RuleGate(), peers={"Arkady", "Vera"}, world=WORLD)
            self.assertIsNone(mind.dream)
            aims = Aims(mind, rng=random.Random(1))
            kinds = [a["kind"] for a in aims.choose({"lv": 10, "job": "Swordman", "job_lv": 5, "zeny": 100}, 0)]
            self.assertNotIn("dream", kinds)
            mem.close()


if __name__ == "__main__":
    unittest.main()
