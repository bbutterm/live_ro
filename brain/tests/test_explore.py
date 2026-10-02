"""Исследование мира (ORG-054, explore.py): выбор цели, достижимость, safety, путь, прибытие, возврат, слухи, группа.

Без сети и без игры: настоящий Mind с поддельным телом (send пишет действия в список, состояние — сообщения state).
Это проверка правил, а не прохождение экспедиции в игре.

Запуск: cd brain && python3 -m unittest -v tests.test_explore
"""
import asyncio
import importlib.util
import json
import os
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import atlas, chronicle, world_bus
from live_brain.__main__ import organic_metrics
from live_brain.activity import Activities
from live_brain.config import Settings
from live_brain.explore import DEFAULTS, REACH_PATH, TAG, Explorer, denied, load_reach
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.safety import SafetyPolicy

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT = BRAIN_DIR.parent
OPENKORE = Path(os.environ.get("LIVE_RO_OPENKORE") or ROOT / "upstream" / "openkore")
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
CFG = dict(WORLD["explore"], min_curiosity=0.0)
ROADS = {"prt_fild05", "prt_fild06", "prt_fild08"}         # поля у ворот Пронтеры (соседи по атласу, достижимы)


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


def make_mind(root, bot, sent, peers=("Arkady", "Vera")):
    async def send(a):
        sent.append(a)
        return len(sent)

    persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
    persona.pop("sleep", None)
    mem = Memory(Path(root) / f"{bot}.sqlite")
    mind = Mind(Settings.from_env({}), persona, mem, send, Path(root) / f"{bot}.jsonl", RuleGate(),
                peers=set(peers), world=WORLD)
    if mind.social:
        mind.social.is_night = lambda now: False          # тест не зависит от времени суток на машине
    return mind


class Base(unittest.TestCase):
    bot, name = "bot01", "Arkady"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sent = []
        self.mind = make_mind(self.tmp.name, self.bot, self.sent)
        self.mem = self.mind.mem
        self.clock = Clock()
        self.ex = Explorer(self.mind, CFG, clock=self.clock, rng=random.Random(1))
        self.mind.explorer = self.ex
        self.state(map="prontera", x=156, y=185, lock_map="prontera", lock_x=156, lock_y=185)
        r = self.mind.routine
        r.new_day(self.clock.t)
        r.st.update(mode="town", arrived=True, rest_until=self.clock.t + 3600, mode_since=self.clock.t)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": self.name, "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 41,
             "job": "Swordsman", "dead": False, "weight_pct": 20, "zeny": 60000, "items": {"501": 30},
             "players": []}
        s.update(getattr(self, "_last", {}))
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def tick(self, dt=1):
        self.clock.t += dt
        asyncio.run(self.ex.tick())

    def events(self, kind):
        return [json.loads(r[0]) for r in self.mem.db.execute("SELECT data FROM events WHERE kind = ?", (kind,))]

    def actions(self, kind):
        return [a for a in self.sent if a.get("action") == kind]

    def seen(self, *maps):
        places = self.mem.get("places") or {}
        for m in maps:
            places[m] = {"source": "seen", "first": self.clock.t - 86400, "last": self.clock.t - 86400}
        self.mem.set("places", places)


class TargetTest(Base):
    def test_knows_only_roads_from_visited_maps(self):
        """Житель знает посещённые карты и выходы с них: из одной Пронтеры видны только поля у ворот."""
        got = {c["map"] for c in self.ex.candidates()}
        self.assertEqual(got, ROADS)
        self.seen("prt_fild05")
        got = {c["map"] for c in self.ex.candidates()}
        self.assertNotIn("prt_fild05", got, "уже был — не цель")
        self.assertTrue({"mjolnir_09", "prt_fild04", "prt_fild07"} <= got, "с prt_fild05 видны новые дороги")

    def test_rumor_makes_far_map_known_and_bonus(self):
        self.assertNotIn("prt_fild04", {c["map"] for c in self.ex.candidates()})
        self.mind.rumors.hear("new", "prt_fild04", "Vera")
        rec = next(c for c in self.ex.candidates() if c["map"] == "prt_fild04")
        self.assertGreater(rec["rumor"], 0, "слух жителя — повод проверить")
        self.assertEqual(rec["hops"], 2)
        self.mind.rumors.hear("rich", "pay_fild04", "Vera")
        self.assertNotIn("pay_fild04", {c["map"] for c in self.ex.candidates()})
        self.assertIn("босс", self.ex.check_target("pay_fild04", 41)[1], "слух не отменяет риск по уровню")

    def test_danger_rumor_excludes(self):
        self.mind.rumors.hear("danger", "prt_fild06", "Vera")
        self.assertNotIn("prt_fild06", {c["map"] for c in self.ex.candidates()})

    def test_ban_excludes(self):
        self.mem.set("map_bans", {"prt_fild08": self.clock.t + 3600})
        self.assertNotIn("prt_fild08", {c["map"] for c in self.ex.candidates()})

    def test_level_risk_for_target_and_path(self):
        rec, why = self.ex.check_target("prt_fild09", 41, need_unknown=False)
        self.assertIsNone(rec)
        self.assertIn("риск", why)
        rec, why = self.ex.check_target("moc_fild01", 5, need_unknown=False)
        self.assertIsNone(rec, "ур. 5 на moc_fild01 (средний 23) — нет")
        rec, why = self.ex.check_target("moc_fild01", 30, need_unknown=False)
        self.assertIsNotNone(rec, why)
        self.assertEqual(rec["path"], ["prontera", "prt_fild08", "moc_fild01"])

    def test_weakest_in_group_sets_level(self):
        self.mind.party.st["confirmed"] = True
        self.state(party="LR_Arkady", party_members=[{"name": "Vera", "online": True, "map": "prontera"}])
        self.mind.crew.st["prefs"] = {"Vera": {"map": "prt_fild08", "lv": 3, "ts": time.time()}}
        self.assertEqual(self.ex.weakest(), 3)
        self.seen("prt_fild08")
        self.assertNotIn("moc_fild01", {c["map"] for c in self.ex.candidates()}, "не по силам слабейшему")
        self.assertIn("moc_fild01", {c["map"] for c in self.ex.candidates(level=41)})

    def test_unreachable_and_denied(self):
        rec, why = self.ex.check_target("izlude", 41, need_unknown=False)
        self.assertIsNone(rec)
        self.assertIn("OpenKore", why, "перестроенный izlude: portals.txt не совпадает с сервером")
        self.assertTrue(denied("iz_int01", DEFAULTS) and denied("int_land02", DEFAULTS) and denied("izlude", DEFAULTS))
        self.assertFalse(denied("prt_fild06", DEFAULTS))
        rec, why = self.ex.check_target("geffen", 41, need_unknown=False)
        self.assertIsNone(rec)
        self.assertIn("переходов", why, "geffen в 5 переходах — дальше max_hops")
        rec, why = self.ex.check_target("prt_in", 41, need_unknown=False)
        self.assertIn("вид карты", why)


class BlockTest(Base):
    def test_conditions(self):
        self.assertIsNone(self.ex.blocked(self.mind.state))
        self.state(hp_pct=70)
        self.assertIn("HP", self.ex.blocked(self.mind.state))
        self.state(hp_pct=100, map="prt_fild08")
        self.assertIn("не в городе", self.ex.blocked(self.mind.state))
        self.state(map="prontera")
        self.mind.social.is_night = lambda now: True
        self.assertEqual(self.ex.blocked(self.mind.state), "ночь")
        self.mind.social.is_night = lambda now: False
        self.mind.plans.store.active = lambda: {"id": "x"}
        self.assertEqual(self.ex.blocked(self.mind.state), "план встречи")
        self.mind.plans.store.active = lambda: None
        self.ex.st["last"] = self.clock.t - 3600
        self.assertIn("не чаще", self.ex.blocked(self.mind.state))
        self.assertIsNone(self.ex.blocked(self.mind.state, joining=True), "за лидером — без своей паузы")

    def test_curiosity_and_member(self):
        ex = Explorer(self.mind, dict(CFG, min_curiosity=0.9), clock=self.clock)
        self.assertIn("любопытство", ex.blocked(self.mind.state))
        self.mind.party.leader_wants = lambda now=None: ("town", "")
        self.assertEqual(self.ex.blocked(self.mind.state), "в группе ведёт лидер")


class TripTest(Base):
    def start(self):
        target = next(c for c in self.ex.candidates() if c["map"] == "prt_fild06")
        self.assertTrue(asyncio.run(self.ex.start(target)))
        return target

    def test_go_arrive_stay_return_rumor(self):
        self.start()
        self.assertEqual(self.actions("explore")[-1], {"action": "explore", "map": "prt_fild06", "id": 1},
                         "поле — без точки, через safety в тело")
        self.assertTrue(self.ex.busy())
        n = len(self.sent)
        self.state(lock_map="prt_fild06", lock_x=None, lock_y=None)
        self.tick(70)
        self.assertEqual(len(self.sent), n, "тело уже идёт туда — повторов нет")
        self.state(lock_map="prontera")                      # кто-то сбил настройку — повтор не чаще RESEND
        self.tick(61)
        self.assertEqual(len(self.actions("explore")), 2)
        self.state(map="prt_fild06", x=23, y=193, lock_map="prt_fild06")
        self.tick()
        self.assertEqual(self.ex.trip["phase"], "stay", "прибытие — по факту state.map")
        place = self.mem.get("places")["prt_fild06"]
        self.assertEqual((place["source"], place["visits"]), ("seen", 1))
        self.assertEqual(self.events("explore_found")[-1]["map"], "prt_fild06")
        self.mem.db.execute("INSERT INTO events (ts, kind, data) VALUES (?, 'kill', ?)",
                            (self.clock.t + 1, json.dumps({"monster": "Poring", "map": "prt_fild06"})))
        self.tick(self.ex.trip["stay"] + 1)
        self.assertFalse(self.ex.busy())
        done = self.events("explore_done")[-1]
        self.assertEqual((done["ok"], done["rumor"], done["kills"]), (True, "new", 1))
        self.assertEqual(self.mem.get("places")["prt_fild06"]["monsters"], ["Poring"])
        self.assertIn({"action": "whisper", "to": "Vera", "text": "[info:new:prt_fild06]"},
                      [{k: a[k] for k in ("action", "to", "text")} for a in self.actions("whisper")])
        self.assertFalse(self.mind.routine.st["arrived"], "распорядок снова ведёт в город")
        self.state(map="prontera", x=150, y=60)
        self.tick()
        self.assertEqual(self.events("explore_returned")[-1]["map"], "prt_fild06")
        self.assertEqual(self.ex.last_trip()["map"], "prt_fild06")
        self.assertNotIn("prt_fild06", {c["map"] for c in self.ex.candidates()}, "теперь знаю — не цель")

    def test_alarm_on_target_returns_with_danger_rumor(self):
        self.start()
        self.state(map="prt_fild06", lock_map="prt_fild06", lock_x=None, lock_y=None)
        self.tick()
        asyncio.run(self.mind.on_message({"type": "event", "kind": "danger", "map": "prt_fild06"}))
        self.tick()
        done = self.events("explore_done")[-1]
        self.assertFalse(done["ok"])
        self.assertEqual(done["rumor"], "danger")
        self.assertIn("[info:danger:prt_fild06]", [a.get("text") for a in self.actions("whisper")])

    def test_low_hp_and_deadline_abort(self):
        self.start()
        self.state(hp_pct=40)
        self.tick()
        self.assertFalse(self.ex.busy())
        self.assertIn("HP", self.events("explore_done")[-1]["why"])
        self.ex.st["last"] = 0
        self.state(hp_pct=100)
        self.start()
        self.tick(CFG["travel_minutes"] * 60 + 1)
        done = self.events("explore_done")[-1]
        self.assertEqual((done["ok"], done["arrived"], done["rumor"]), (False, False, None))

    def test_routine_and_social_yield(self):
        self.start()
        r = self.mind.routine
        r.st["rest_until"] = time.time() - 1
        r.last_sent = 0
        n = len(self.sent)
        asyncio.run(r.tick())
        self.assertEqual(self.sent[n:], [], "распорядок не шлёт hunt/meet_point во время экспедиции")
        self.assertEqual(r.st["mode"], "town")
        self.assertFalse(self.mind.social.may_walk(r))

    def test_activity_requires_and_proof(self):
        a = Activities(self.mind, clock=self.clock, rng=random.Random(1))
        self.assertTrue(a.requires_ok({"explore_target": True}, self.mind.state, {}))
        asyncio.run(a.start("explore", self.clock.t, self.mind.state))
        self.assertTrue(self.ex.busy())
        self.assertEqual(a.blocked(self.mind.state), "экспедиция")
        self.state(map=self.ex.trip["map"], lock_map=self.ex.trip["map"])
        self.tick()
        asyncio.run(a.check_proof(self.clock.t, self.mind.state))
        self.assertTrue(a.st["proved"])


class GroupTest(Base):
    def test_leader_calls_member_joins_and_party_chat(self):
        self.mind.party.st["confirmed"] = True
        self.state(party="LR_Arkady", party_members=[{"name": "Vera", "online": True, "map": "prontera"}])
        target = next(c for c in self.ex.candidates() if c["map"] == "prt_fild06")
        asyncio.run(self.ex.start(target))
        self.assertIn("[explore:trip:prt_fild06]", [a.get("text") for a in self.actions("whisper")])
        self.state(map="prt_fild06", lock_map="prt_fild06")
        self.tick()
        self.assertTrue(any("prt_fild06" in a["text"] for a in self.actions("party_say")), "фраза в чат группы")

        vsent = []
        vera = make_mind(self.tmp.name, "bot02", vsent)
        try:
            vex = Explorer(vera, CFG, clock=self.clock)
            vera.explorer = vex
            vera.routine.new_day(self.clock.t)
            vera.routine.st.update(mode="town", arrived=True)
            st = {"type": "state", "name": "Vera", "map": "prontera", "x": 150, "y": 180, "hp_pct": 100, "lv": 30,
                  "dead": False, "party": "LR_Arkady", "players": [],
                  "party_members": [{"name": "Arkady", "online": True, "map": "prontera"}]}
            asyncio.run(vera.on_message(st))
            asyncio.run(vera.on_message({"type": "event", "kind": "chat_private", "from": "Bob",
                                         "text": "[explore:trip:prt_fild06]"}))
            self.assertFalse(vex.busy(), "не житель — не ведёт")
            asyncio.run(vera.on_message({"type": "event", "kind": "chat_private", "from": "Arkady",
                                         "text": "[explore:trip:prt_fild06]"}))
            self.assertTrue(vex.busy(), "лидер позвал — участник идёт сам")
            self.assertEqual(vex.trip["led_by"], "Arkady")
            self.assertEqual([a["map"] for a in vsent if a["action"] == "explore"], ["prt_fild06"])
            asyncio.run(vera.on_message(dict(st, party_members=[{"name": "Arkady", "online": False}])))
            self.clock.t += 1
            asyncio.run(vex.tick())
            self.assertFalse(vex.busy(), "лидер вышел из игры — возвращаюсь")
        finally:
            vera.mem.close()


class SafetyTest(unittest.TestCase):
    def setUp(self):
        self.s = SafetyPolicy(["prt_fild08"])
        self.st = {"lv": 41, "hp_pct": 100, "dead": False}

    def check(self, **a):
        return self.s.check(dict(action="explore", **a), self.st, protocol=True)

    def test_rules(self):
        self.assertEqual(self.check(map="prt_fild06")[0], {"action": "explore", "map": "prt_fild06"})
        self.assertEqual(self.check(map="geffen", x=119, y=63)[0]["x"], 119)
        self.assertIn("запрещена", self.check(map="izlude")[1])
        self.assertIsNone(self.check(map="iz_int01")[0], "полигон новичков")
        self.assertIn("pvp", self.check(map="prtg_cas01")[1])
        self.assertIn("атласе", self.check(map="nosuch_map")[1])
        self.assertIn("неверная", self.check(map="prt; quit")[1])
        self.assertIn("координаты", self.check(map="prt_fild06", x=5)[1])
        self.st["lv"] = 5
        self.assertIn("риск", self.check(map="prt_fild09")[1])
        self.st.update(lv=41, hp_pct=10)
        self.assertIn("HP", self.check(map="prt_fild06")[1])
        self.assertEqual(self.s.check({"action": "explore", "map": "prt_fild06"}, self.st)[1], "неизвестное действие",
                         "модель explore не получает")


class ReachTest(unittest.TestCase):
    def test_reach_consistent_with_atlas(self):
        reach = load_reach()
        a = atlas.default()
        maps = reach["towns"]["prontera"]["maps"]
        self.assertTrue(ROADS <= set(maps))
        self.assertNotIn("izlude", maps, "portals.txt устарел для izlude (renewal)")
        for m, r in maps.items():
            self.assertIn(m, a.maps)
            self.assertEqual(r["path"][0], "prontera")
            self.assertEqual(r["path"][-1], m)
            self.assertEqual(len(r["path"]) - 1, r["hops"])
            for src, dst in zip(r["path"], r["path"][1:]):
                self.assertIn(dst, a.neighbors(src), f"{src}->{dst}: такого варпа у сервера нет")

    @unittest.skipUnless((OPENKORE / "fields" / "prontera.fld2.gz").exists(), "нет полей OpenKore")
    def test_reach_file_matches_generator(self):
        spec = importlib.util.spec_from_file_location("gen_explore_reach", ROOT / "scripts" / "gen_explore_reach.py")
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        doc = gen.generate(str(OPENKORE), str(BRAIN_DIR / "world" / "atlas.json"), gen.DEFAULT_TOWNS,
                           load_reach()["max_hops"])
        self.assertEqual(json.loads(json.dumps(doc)), json.loads(REACH_PATH.read_text(encoding="utf-8")),
                         "перегенерируйте: scripts/gen_explore_reach.py > brain/world/explore_reach.json")


class StoriesTest(Base):
    def test_social_trip_topic(self):
        self.ex.st["found"] = [{"map": "prt_fild06", "ts": self.clock.t - 600, "new": True}]
        f = self.mind.social.facts("Vera", self.clock.t)
        self.assertEqual(f["trip_map"], "prt_fild06")
        self.assertIn("trip", self.mind.social.available(f))
        self.assertIn("prt_fild06", self.mind.social.phrase("trip", f))

    def test_bus_chronicle_metrics(self):
        self.assertEqual(world_bus.describe("place_found", {"map": "prt_fild06"}), "открыл(а) prt_fild06")
        self.assertEqual(world_bus.PUBLISH["explore_found"][0], "place_found")
        self.assertEqual(chronicle.LINES["explore_found"]({"map": "prt_fild06"}), "открыл(а) prt_fild06")
        self.mem.add_event("explore_found", {"map": "prt_fild06"})
        self.assertEqual(organic_metrics(self.mem, time.time() - 3600)["открытых мест"], 1)

    def test_tag(self):
        self.assertEqual(TAG.search("[explore:trip:prt_fild06]").group(2), "prt_fild06")
        self.assertIsNone(TAG.search("[explore:trip:PRT;x]"))


if __name__ == "__main__":
    unittest.main()
