"""Лекарь у собора (healer.py, ORG-069): роль, пост-занятие, просьбы жителей и людей, касты через safety, вывеска,
пациент, факты по пакету support.

Настоящие Mind (Vera — лекарь, Arkady — пациент) с общей шиной мира во временном каталоге и поддельным телом;
часы модуля подменные. Запуск: cd brain && python3 -m unittest -v tests.test_healer
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import world_bus
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.healer import Healer
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.safety import CAST_LIMIT, SafetyPolicy

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
POST = WORLD["social"]["points"]["church"]
PX, PY = POST["x"], POST["y"]


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Resident:
    def __init__(self, root, bot, name, bus_path, clock, world=WORLD):
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        persona.pop("sleep", None)
        self.name = name
        self.mem = Memory(root / f"{bot}.sqlite")
        self.bus = world_bus.WorldBus(bus_path, name, clock=clock)
        self.dec = root / f"{bot}.jsonl"
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, self.dec, RuleGate(),
                         peers={"Arkady", "Vera"}, world=world, world_bus_db=self.bus)
        self.clock = clock
        if self.mind.world:
            self.mind.world.pump()                                   # курсор шины: история не переносится
        if self.mind.healer:
            self.h = Healer(self.mind, world, clock=clock, rng=random.Random(1))
            self.mind.healer = self.h
        r = self.mind.routine
        r.st.update(mode="town", arrived=True, mode_since=clock(), rest_until=clock() + 3600)

    def state(self, x=PX, y=PY, others=(), **kw):
        s = {"type": "state", "name": self.name, "map": "prontera", "x": x, "y": y, "hp_pct": 100, "sp_pct": 90,
             "lv": 30, "dead": False, "weight_pct": 10, "zeny": 5000, "items": {"501": 20},
             "players": [{"name": n, "x": ox, "y": oy, "lv": 30} for n, ox, oy in others]}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def event(self, **ev):
        asyncio.run(self.mind.on_message(dict(ev, type="event")))

    def tick(self):
        asyncio.run(self.h.tick())

    def casts(self):
        return [a for a in self.sent if a.get("action") == "skill_on_player"]

    def whispers(self):
        return [a for a in self.sent if a.get("action") == "whisper"]

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ? ORDER BY id",
                                                              (kind,))]

    def close(self):
        self.mem.close()
        self.bus.close()


class HealerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock(time.time())
        self.bus_path = self.root / "shared" / "world.sqlite"
        self.v = Resident(self.root, "bot02", "Vera", self.bus_path, self.clock)
        self.a = Resident(self.root, "bot01", "Arkady", self.bus_path, self.clock)
        self.v.state(job="Acolyte", support_skills={"AL_HEAL": 10, "AL_BLESSING": 5, "AL_INCAGI": 5})

    def tearDown(self):
        self.v.close()
        self.a.close()
        self.tmp.cleanup()

    def on_post(self, others=(("Arkady", PX + 2, PY + 1),), **kw):
        """Vera на посту: занятие healer_post начато, она у собора."""
        act = self.v.mind.activities
        act.st.update(name="healer_post", mode="town", since=self.clock.t)
        asyncio.run(self.v.h.start_post())
        self.v.state(PX, PY, others, job="Acolyte", support_skills={"AL_HEAL": 10, "AL_BLESSING": 5,
                                                                    "AL_INCAGI": 5}, **kw)
        self.v.tick()

    # ---------- роль и занятие ----------

    def test_role(self):
        self.assertEqual(self.v.h.role(), "healer")                       # Acolyte, generosity 0.8
        self.a.state(job="Swordman")
        self.assertEqual(self.a.h.role(), "patient")
        self.v.state(job="Acolyte", support_skills={"AL_INCAGI": 1})       # Heal не выучен
        self.assertEqual(self.v.h.role(), "patient")
        self.v.state(job="Acolyte")                                        # старый мост без поля — по профессии
        self.assertEqual(self.v.h.role(), "healer")
        self.v.mind.persona["traits"]["generosity"] = 0.4
        self.assertEqual(self.v.h.role(), "patient")

    def test_activity_only_for_healer(self):
        act = self.v.mind.activities
        needs = self.v.mind.needs.weighted()
        self.assertTrue(act.requires_ok({"healer_role": True}, self.v.mind.state, needs))
        self.assertFalse(self.a.mind.activities.requires_ok({"healer_role": True}, self.a.mind.state, needs))
        self.v.state(job="Acolyte", sp_pct=20)                             # SP мало для смены
        self.assertFalse(act.requires_ok({"healer_role": True}, self.v.mind.state, needs))

    def test_post_walk_and_proof(self):
        self.v.state(150, 180, job="Acolyte")
        act = self.v.mind.activities
        asyncio.run(act.start("healer_post", self.clock.t, self.v.mind.state))
        moves = [a for a in self.v.sent if a.get("action") == "meet_point"]
        self.assertEqual((moves[-1]["x"], moves[-1]["y"]), (PX, PY))       # идёт к собору
        self.assertGreater(self.v.mind.social.next_walk, self.clock.t + 15 * 60)   # стоит смену, не гуляет
        self.assertFalse(self.v.h.at_post())
        self.v.state(PX + 1, PY, job="Acolyte")
        asyncio.run(act.check_proof(self.clock.t, self.v.mind.state))
        self.assertTrue(act.st["proved"])
        self.v.tick()
        self.assertEqual(len(self.v.events("healer_post_start")), 1)
        snap = self.a.bus.latest("healer_post")
        self.assertTrue(snap["Vera"]["data"]["open"])
        self.assertNotIn("healer_post", {e["kind"] for e in world_bus.read_period(self.bus_path, 0, 1e12)})  # тихий

    # ---------- просьбы и касты ----------

    def test_resident_request_heal(self):
        self.on_post()
        asyncio.run(self.v.mind.on_event({"kind": "chat_private", "from": "Arkady",
                                          "text": "Vera, подлечишь? HP 40% [heal:ask:40]"}))
        self.v.tick()
        self.assertEqual([(c["skill"], c["to"]) for c in self.v.casts()], [("AL_HEAL", "Arkady")])
        self.assertEqual(self.v.whispers(), [])                             # лечит молча: каст — ответ

    def test_not_on_post_says_no(self):
        asyncio.run(self.v.mind.on_event({"kind": "chat_private", "from": "Arkady", "text": "Vera? [heal:ask:40]"}))
        self.v.tick()
        self.assertEqual(self.v.casts(), [])
        self.assertIn("[heal:no]", self.v.whispers()[-1]["text"])

    def test_high_hp_and_far(self):
        self.on_post(others=(("Arkady", PX + 2, PY), ("Rook", PX + 20, PY)))
        asyncio.run(self.v.h.on_tag("Arkady", "[heal:ask:95]"))
        self.assertIn("[heal:no]", self.v.whispers()[-1]["text"])
        asyncio.run(self.v.h.on_tag("Rook", "[heal:ask:30]"))
        self.assertIn("ближе", self.v.whispers()[-1]["text"])
        self.v.tick()
        self.assertEqual(self.v.casts(), [])

    def test_limits_sp_gap_person(self):
        self.on_post(sp_pct=20)                                            # SP < min_sp
        asyncio.run(self.v.h.on_tag("Arkady", "[heal:ask:40]"))
        self.v.tick()
        self.assertEqual(self.v.casts(), [])
        self.v.state(PX, PY, [("Arkady", PX + 2, PY)], job="Acolyte", sp_pct=90)
        asyncio.run(self.v.h.on_tag("Arkady", "[heal:ask:40]"))
        self.v.tick()
        self.assertEqual(len(self.v.casts()), 1)
        self.clock.t += 10                                                 # тому же раньше person_gap — нет
        asyncio.run(self.v.h.on_tag("Arkady", "[heal:ask:40]"))
        self.v.tick()
        self.assertEqual(len(self.v.casts()), 1)
        self.clock.t += 60
        asyncio.run(self.v.h.on_tag("Arkady", "[heal:ask:40]"))
        self.v.tick()
        self.assertEqual(len(self.v.casts()), 2)

    def test_stranger_word(self):
        self.on_post(others=(("Human Guy", PX + 3, PY),))
        asyncio.run(self.v.mind.on_event({"kind": "chat_public", "from": "Human Guy", "text": "привет всем"}))
        self.v.tick()
        self.assertEqual(self.v.casts(), [])                               # обычная фраза — ничего
        asyncio.run(self.v.mind.on_event({"kind": "chat_public", "from": "Human Guy", "text": "heal pls"}))
        self.v.tick()
        self.assertEqual([(c["skill"], c["to"]) for c in self.v.casts()], [("AL_HEAL", "Human Guy")])
        self.clock.t += 30                                                 # человеку — не чаще 2 мин
        asyncio.run(self.v.mind.on_event({"kind": "chat_private", "from": "Human Guy", "text": "лечи ещё"}))
        self.v.tick()
        self.assertEqual(len(self.v.casts()), 1)
        texts = [d for (d,) in self.v.mem.db.execute("SELECT data FROM events WHERE kind = 'healer_heal'")]
        self.assertEqual(texts, [])                                        # каст ≠ лечение: ждём пакет support

    def test_support_counts_and_blesses(self):
        self.on_post()
        asyncio.run(self.v.h.on_tag("Arkady", "[heal:ask:40]"))
        self.v.tick()
        self.v.event(kind="support", skill="AL_HEAL", **{"from": "Vera"}, to="Arkady", amount=320)
        self.assertEqual(self.v.events("healer_heal"), [{"to": "Arkady", "amount": 320, "who": "resident"}])
        self.clock.t += 6
        self.v.tick()
        self.clock.t += 6
        self.v.tick()
        self.assertEqual([c["skill"] for c in self.v.casts()], ["AL_HEAL", "AL_BLESSING", "AL_INCAGI"])
        self.v.event(kind="support", skill="AL_BLESSING", **{"from": "Vera"}, to="Arkady", amount=0)
        self.assertEqual(self.v.h.st["post"]["blesses"], 1)
        # смена окончена — итог в память, шину и летопись
        self.clock.t = self.v.h.st["post"]["until"] + 1
        self.v.tick()
        shift = self.v.events("healer_shift")
        self.assertEqual((shift[0]["heals"], shift[0]["blesses"], shift[0]["patients"]), (1, 1, 1))
        self.assertIn("Heal 1", LINES["healer_shift"](shift[0]))
        self.v.mind.world.pump()
        kinds = [r["kind"] for r in self.v.bus.read()]
        self.assertIn("healer_shift", kinds)
        self.assertFalse(self.a.bus.latest("healer_post")["Vera"]["data"]["open"])

    def test_no_bless_low_sp_or_unknown(self):
        self.on_post(sp_pct=50)
        asyncio.run(self.v.h.on_tag("Arkady", "[heal:ask:40]"))
        self.v.tick()
        self.v.event(kind="support", skill="AL_HEAL", **{"from": "Vera"}, to="Arkady", amount=100)
        self.clock.t += 6
        self.v.tick()
        self.assertEqual([c["skill"] for c in self.v.casts()], ["AL_HEAL"])   # SP 50 < bless_sp 60

    def test_battle_heal_not_counted(self):
        self.v.event(kind="support", skill="AL_HEAL", **{"from": "Vera"}, to="Arkady", amount=100)
        self.assertEqual(self.v.events("healer_heal"), [])                 # не на посту — это partySkill в бою

    # ---------- вывеска ----------

    def test_sign_in_society(self):
        self.on_post(others=())
        self.assertEqual(self.v.h.sign(self.v.mind.state, self.clock.t), "Лечу у собора")
        self.v.mind.society.next_room = self.clock.t + 3600                  # обычная вывеска не скоро
        asyncio.run(self.v.mind.society.rooms(self.clock.t, self.v.mind.state))
        rooms = [a for a in self.v.sent if a.get("action") == "chat_room"]
        self.assertEqual(rooms[-1]["title"], "Лечу у собора")
        self.v.h.last_cast = self.clock.t                                  # только что кастовала — не сразу
        self.assertIsNone(self.v.h.sign(self.v.mind.state, self.clock.t))

    # ---------- пациент ----------

    def test_patient_asks_visible_healer(self):
        self.on_post()
        self.a.state(PX + 2, PY + 1, [("Vera", PX, PY)], hp_pct=45)
        self.a.tick()
        w = self.a.whispers()
        self.assertEqual(w[-1]["to"], "Vera")
        self.assertTrue(w[-1]["text"].endswith("[heal:ask:45]"))
        self.clock.t += 10
        self.a.tick()
        self.assertEqual(len(self.a.whispers()), 1)                         # не чаще ask_gap
        asyncio.run(self.v.mind.on_event({"kind": "chat_private", "from": "Arkady", "text": w[-1]["text"]}))
        self.v.tick()
        self.assertEqual(self.v.casts()[-1]["to"], "Arkady")                # просьба дошла до лекаря

    def test_patient_visits_post(self):
        self.on_post(others=())
        self.a.state(156, 185, [], hp_pct=40)
        self.a.mind.social.next_walk = self.clock.t + 3600
        self.a.tick()
        moves = [a for a in self.a.sent if a.get("action") == "meet_point"]
        self.assertEqual((moves[-1]["x"], moves[-1]["y"]), (PX, PY - 2))
        self.assertEqual(len(self.a.events("healer_visit")), 1)

    def test_patient_quiet(self):
        self.a.state(PX + 2, PY + 1, [("Vera", PX, PY)], hp_pct=45)
        self.a.tick()
        self.assertEqual(self.a.whispers(), [])                             # поста нет
        self.on_post()
        self.a.state(PX + 2, PY + 1, [("Vera", PX, PY)], hp_pct=90)
        self.clock.t += 6
        self.a.tick()
        self.assertEqual(self.a.whispers(), [])                             # HP в порядке
        asyncio.run(self.a.h.on_tag("Vera", "Нет. [heal:no]"))
        self.a.state(PX + 2, PY + 1, [("Vera", PX, PY)], hp_pct=40)
        self.clock.t += 6
        self.a.tick()
        self.assertEqual(self.a.whispers(), [])                             # после [heal:no] — пауза

    def test_patient_remembers_healer(self):
        self.on_post()
        before = (self.a.mem.relation("Vera") or {}).get("affinity", 0)
        self.a.event(kind="support", skill="AL_HEAL", **{"from": "Vera"}, to="Arkady", amount=200,
                     hp_before=100, hp_max=500)
        self.a.event(kind="support", skill="AL_HEAL", **{"from": "Vera"}, to="Arkady", amount=200,
                     hp_before=300, hp_max=500)
        self.assertEqual(len(self.a.events("healer_healed_me")), 1)          # раз в сутки
        self.assertGreaterEqual(self.a.mem.relation("Vera")["affinity"], before + 1)

    # ---------- safety и выключатель ----------

    def test_safety_cast(self):
        s = SafetyPolicy(["prt_fild08"])
        st = {"name": "Vera", "sp_pct": 80}
        ok, why = s.check({"action": "skill_on_player", "skill": "AL_HEAL", "to": "Arkady"}, st, protocol=True)
        self.assertEqual(ok, {"action": "skill_on_player", "skill": "AL_HEAL", "to": "Arkady"})
        self.assertIsNone(s.check({"action": "skill_on_player", "skill": "AL_HEAL", "to": "Arkady"}, st)[0])  # модель — нет
        for bad in ({"skill": "MG_FIREBOLT", "to": "Arkady"}, {"skill": "AL_HEAL", "to": "Vera"},
                    {"skill": "AL_HEAL", "to": 'a"b'}, {"skill": "AL_HEAL", "to": ""}):
            self.assertIsNone(s.check(dict(bad, action="skill_on_player"), st, protocol=True)[0], bad)
        self.assertIsNone(s.check({"action": "skill_on_player", "skill": "AL_HEAL", "to": "A"}, {"sp_pct": 5},
                                  protocol=True)[0])
        self.assertIsNone(s.check({"action": "skill_on_player", "skill": "AL_HEAL", "to": "A"}, {"dead": True},
                                  protocol=True)[0])
        s2 = SafetyPolicy([])
        for i in range(CAST_LIMIT):
            self.assertTrue(s2.check({"action": "skill_on_player", "skill": "AL_HEAL", "to": "A"}, st, now=1000 + i,
                                     protocol=True)[0])
        self.assertIsNone(s2.check({"action": "skill_on_player", "skill": "AL_HEAL", "to": "A"}, st, now=1100,
                                   protocol=True)[0])

    def test_disabled(self):
        world = dict(WORLD, healer={"enabled": False})
        r = Resident(self.root, "bot02", "Vera2", self.root / "w2.sqlite", self.clock, world=world)
        try:
            self.assertIsNone(r.mind.healer)
        finally:
            r.close()
        mem = Memory(self.root / "x.sqlite")
        try:
            m = Mind(Settings.from_env({"BRAIN_DISABLE": "healer"}),
                     json.loads((BRAIN_DIR / "personas" / "bot02.json").read_text()), mem, None,
                     self.root / "x.jsonl", RuleGate(), peers={"Arkady", "Vera"}, world=WORLD)
            self.assertIsNone(m.healer)
        finally:
            mem.close()


if __name__ == "__main__":
    unittest.main()
