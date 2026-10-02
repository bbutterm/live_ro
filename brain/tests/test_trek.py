"""Дальний поход группой (trek.py, ORG-078, ТЗ Т-34): сбор, плечи с привалами, Kafra на привале, возвращение.

Настоящие Mind + Memory двух жителей во временном каталоге, тела — поддельные состояния; плечи — экспедиции
explorer (их приход — смена state.map). Плечи trek_routes.json сверяются с генератором и атласом при наличии
upstream (LIVE_RO_OPENKORE). Это проверка правил мозга, а не похода в игре.

Запуск: cd brain && python3 -m unittest -v tests.test_trek
"""
import asyncio
import json
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import trek as TR
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

from tests.test_newborn import HAVE_BOTH, OPENKORE, ROOT

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROUTES = json.loads((BRAIN_DIR / "world" / "trek_routes.json").read_text(encoding="utf-8"))


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


def world(**trek):
    w = load_world(BRAIN_DIR / "world" / "goals.json")
    w["trek"] = dict(w.get("trek") or {}, enabled=True, min_curiosity=0, **trek)
    w["explore"] = dict(w.get("explore") or {}, enabled=True)
    return w


class Body:
    """Житель с поддельным телом: Mind, часы на всех модулях, распорядок «отдых в городе»."""

    def __init__(self, root, bot, name, clock, peers, w):
        self.sent, self.name, self.clock = [], name, clock

        async def send(a):
            self.sent.append(a)
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(Path(root) / f"{bot}.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, Path(root) / f"{bot}.jsonl", RuleGate(),
                         peers=set(peers), world=w)
        m = self.mind
        if m.social:
            m.social.is_night = lambda now: False
        m.routine.sleep_window = lambda now: None
        for mod in (m.explorer, m.trek):
            mod.clock = clock
        m.trek.rng = random.Random(1)
        m.party.st["confirmed"] = True
        self._last = {}
        self.state(map="prontera", x=156, y=185, lock_map="prontera", lock_x=156, lock_y=185)
        m.routine.new_day(clock.t)
        m.routine.st.update(mode="town", arrived=True, rest_until=clock.t + 3600, mode_since=clock.t)

    def state(self, **kw):
        s = {"type": "state", "name": self.name, "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 99,
             "job": "Swordsman", "dead": False, "weight_pct": 20, "zeny": 60000, "items": {"501": 30},
             "players": [], "party": "LR_Arkady"}
        s.update(self._last)
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def whisper_from(self, sender, text):
        asyncio.run(self.mind.on_message({"type": "event", "kind": "chat_private", "from": sender, "text": text}))

    def tick(self):
        asyncio.run(self.mind.explorer.tick())
        asyncio.run(self.mind.trek.tick())

    def whispers(self):
        return [(a["to"], a["text"]) for a in self.sent if a.get("action") == "whisper"]

    def actions(self, kind):
        return [a for a in self.sent if a.get("action") == kind]

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ?", (kind,))]

    def close(self):
        self.mem.close()


class TrekCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()

    def tearDown(self):
        for b in getattr(self, "bodies", []):
            b.close()
        self.tmp.cleanup()

    def body(self, bot, name, **trek):
        b = Body(self.tmp.name, bot, name, self.clock, {"Arkady", "Vera"}, world(**trek))
        self.bodies = getattr(self, "bodies", []) + [b]
        return b

    def leader(self, **trek):
        a = self.body("bot01", "Arkady", **trek)
        a.state(party_members=[{"name": "Vera", "online": True, "map": "prontera"}])
        return a

    def walk(self, b, town, minutes=None):
        """Плечо: тело дошло до города, отстояло стоянку — экспедиция плеча закрыта."""
        b.state(map=town, lock_map=town)
        b.tick()
        ex = b.mind.explorer
        self.assertIsNotNone(ex.trip, f"плечо до {town} идёт")
        self.assertEqual(ex.trip["map"], town)
        self.clock.t = ex.trip["stay_until"] + 1
        b.tick()


class ChainTest(unittest.TestCase):
    def test_chain_with_halt(self):
        legs = TR.chain(ROUTES, "prontera", "alberta", 3)
        self.assertEqual([(t, h) for t, h, _p in legs], [("payon", 6), ("alberta", 4)])
        self.assertEqual([t for t, _h, _p in TR.chain(ROUTES, "alberta", "prontera", 3)], ["payon", "prontera"])
        self.assertIsNone(TR.chain(ROUTES, "prontera", "morocc", 3), "Морокк без плеч до патча порталов")
        self.assertIsNone(TR.chain(ROUTES, "prontera", "alberta", 1), "лимит плеч")


class TrekTest(TrekCase):
    def test_disabled_by_default(self):
        self.assertFalse(load_world(BRAIN_DIR / "world" / "goals.json")["trek"]["enabled"])

    def test_full_trek_leader_with_halts_and_kafra(self):
        a = self.leader(targets=["alberta"])
        a.tick()
        self.assertEqual(a.whispers(), [("Vera", "[trek:go:alberta]")])
        self.assertEqual(a.mind.trek.trip["phase"], "gather")
        self.assertTrue(a.mind.trek.busy())
        a.whisper_from("Vera", "[trek:ok:alberta]")
        a.tick()
        self.assertIn(("Vera", "[trek:ok:alberta]"), a.whispers(), "лидер подтвердил участнику")
        start = a.events("trek_start")[0]
        self.assertEqual(start["legs"], ["payon", "alberta", "payon", "prontera"])
        self.assertEqual(start["members"], ["Vera"])
        ex = a.mind.explorer
        self.assertEqual(ex.trip["map"], "payon")
        self.assertEqual(ex.trip["trek"], "alberta")
        self.assertEqual(round(ex.trip["deadline"] - self.clock.t), 6 * 6 * 60)
        self.assertTrue(ex.cfg["group"], "приглашение экспедиции вернулось как было")
        self.assertNotIn("[explore:trip:payon]", [t for _to, t in a.whispers()], "своё [explore:trip:] не шлёт")
        self.assertEqual([x["map"] for x in a.actions("explore")], ["payon"])
        # привал в Пайоне: сохранение у Kafra
        self.walk(a, "payon")
        self.assertEqual(a.events("trek_halt")[0]["town"], "payon")
        save = [x for x in a.actions("job_change") if x["path"] == "trek"]
        self.assertEqual(save[0]["steps"][0]["map"], "payon")
        self.assertEqual(save[0]["success"]["map"], "payon")
        if a.mind.career:
            a.mind.career.on_result = lambda e: self.fail("итог Kafra похода ушёл в career")
        asyncio.run(a.mind.on_event({"type": "event", "kind": "job_change_result", "path": "trek",
                                     "stage": "kafra_save", "ok": True}))
        self.assertEqual(a.mind.home.saved_map(), "payon", "дом знает: точка возрождения — Пайон")
        a.tick()
        self.assertEqual(ex.trip["map"], "alberta")
        self.walk(a, "alberta")
        self.assertEqual(a.events("trek_arrived")[0]["target"], "alberta")
        asyncio.run(a.mind.on_event({"type": "event", "kind": "job_change_result", "path": "trek",
                                     "stage": "kafra_save", "ok": False, "reason": "меню"}))
        a.tick()
        self.assertEqual(ex.trip["map"], "payon", "Kafra не вышло — поход идёт дальше")
        self.walk(a, "payon")
        asyncio.run(a.mind.on_event({"type": "event", "kind": "job_change_result", "path": "trek",
                                     "stage": "kafra_save", "ok": True}))
        a.tick()
        self.assertEqual(ex.trip["map"], "prontera")
        self.walk(a, "prontera")
        done = a.events("trek_done")
        self.assertTrue(done[0]["ok"])
        self.assertIsNone(a.mind.trek.trip)
        self.assertFalse(a.mind.home.at_home_saved(), "дома home.py пересохранится сам")
        # тема разговора и летопись
        f = a.mind.trek.facts("Bob", self.clock.t)
        self.assertEqual((f["town"], f["mate"], f["halt"], f["_key"]), ("alberta", "Vera", "payon", "trek_halt"))
        self.assertIsNone(a.mind.trek.facts("Vera", self.clock.t), "спутнику не рассказывают")
        for key in ("trek", "trek_halt"):
            for line in TR.PHRASES[key]:
                self.assertLessEqual(len(line.format(town="aldebaran", mate="Arkadyyyyyy", halt="aldebaran")), 60)
        from live_brain.chronicle import LINES
        self.assertIn("alberta", LINES["trek_done"](done[0]))
        # раз в неделю
        a.tick()
        self.assertEqual(len(a.events("trek_start")), 1)

    def test_member_joins_and_follows_own_legs(self):
        v = self.body("bot02", "Vera", targets=["geffen"])
        v.state(party_members=[{"name": "Arkady", "online": True, "map": "prontera"}])
        v.whisper_from("Bob", "[trek:go:geffen]")
        self.assertIsNone(v.mind.trek.trip, "не житель — не слушаю")
        v.whisper_from("Arkady", "[trek:go:geffen]")
        self.assertEqual(v.whispers()[-1], ("Arkady", "[trek:ok:geffen]"))
        v.tick()
        self.assertIsNone(v.mind.explorer.trip, "ждёт подтверждения лидера")
        v.whisper_from("Arkady", "[trek:ok:geffen]")
        ex = v.mind.explorer
        self.assertEqual((ex.trip["map"], ex.trip["led_by"]), ("geffen", "Arkady"))
        v.state(party_members=[{"name": "Arkady", "online": False, "map": "prontera"}])
        v.tick()
        v.tick()
        self.assertFalse(v.events("trek_done")[0]["ok"], "лидер вышел — поход окончен")

    def test_member_declines_without_supplies(self):
        v = self.body("bot02", "Vera", targets=["geffen"])
        v.state(party_members=[{"name": "Arkady", "online": True, "map": "prontera"}], items={"501": 2})
        v.whisper_from("Arkady", "[trek:go:geffen]")
        self.assertEqual(v.whispers()[-1], ("Arkady", "[trek:no:geffen]"))
        self.assertIsNone(v.mind.trek.trip)

    def test_member_declines_risky_route(self):
        v = self.body("bot02", "Vera", targets=["aldebaran"])
        v.state(party_members=[{"name": "Arkady", "online": True, "map": "prontera"}], lv=41)
        v.whisper_from("Arkady", "[trek:go:aldebaran]")
        self.assertEqual(v.whispers()[-1], ("Arkady", "[trek:no:aldebaran]"), "mjolnir_12 опасен для 41 ур.")

    def test_gather_cancelled_when_alone(self):
        a = self.leader(targets=["geffen"])
        a.tick()
        a.whisper_from("Vera", "[trek:no:geffen]")
        self.clock.t += 121
        a.tick()
        self.assertIsNone(a.mind.trek.trip)
        self.assertEqual(a.events("trek_start"), [])
        a.tick()
        self.assertEqual(len([w for w in a.whispers() if w[1].startswith("[trek:go")]), 1, "пауза после отмены")

    def test_limits(self):
        a = self.leader(targets=["geffen"])
        tr = a.mind.trek
        now = self.clock.t
        a.state(items={"501": 3})
        self.assertIn("зелий", tr.can_lead(now))
        a.state(items={"501": 30}, party_members=[])
        self.assertEqual(tr.can_lead(now), "участников рядом мало")
        a.state(party_members=[{"name": "Vera", "online": True, "map": "prontera"}])
        self.assertIsNone(tr.can_lead(now))
        a.mind.routine.sleep_window = lambda n: (n + 3600, n + 8 * 3600)
        self.assertEqual(tr.candidates(now), [], "не успеет до сна")
        a.mind.routine.sleep_window = lambda n: None
        self.assertEqual([t for t, _l in tr.candidates(now)], ["geffen"])
        tr.st["visited"]["geffen"] = now
        self.assertEqual(tr.candidates(now), [], "недавно был")
        a.state(map="prt_fild08")
        self.assertIsNotNone(tr.can_lead(now), "не в городе отдыха")

    def test_leg_abort_ends_trek(self):
        a = self.leader(targets=["geffen"])
        a.tick()
        a.whisper_from("Vera", "[trek:ok:geffen]")
        a.tick()
        a.state(hp_pct=30)
        a.tick()
        done = a.events("trek_done")
        self.assertFalse(done[0]["ok"])
        self.assertIn("geffen", done[0]["why"])


@unittest.skipUnless(HAVE_BOTH, "нет upstream/rathena и upstream/openkore (LIVE_RO_RATHENA / LIVE_RO_OPENKORE)")
class TrekRoutesDataTest(unittest.TestCase):
    def test_generator_matches_file(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import gen_trek_routes
        doc = gen_trek_routes.generate(OPENKORE, ROOT / "brain" / "world" / "atlas.json",
                                       ROOT / "brain" / "world" / "homes.json", ROUTES["max_hops"])
        self.assertEqual(json.loads(json.dumps(doc, sort_keys=True)), ROUTES,
                         "перегенерировать: scripts/gen_trek_routes.py > brain/world/trek_routes.json")

    def test_legs_are_server_warps(self):
        atlas = json.loads((ROOT / "brain" / "world" / "atlas.json").read_text(encoding="utf-8"))["maps"]
        for town, rec in ROUTES["towns"].items():
            for to, leg in rec["legs"].items():
                self.assertEqual((leg["path"][0], leg["path"][-1]), (town, to))
                for a, b in zip(leg["path"], leg["path"][1:]):
                    self.assertTrue(any(e["to"] == b for e in atlas[a]["exits"]), f"{a} -> {b}")
                self.assertLessEqual(leg["back"], leg["hops"] + 1)
        self.assertEqual(ROUTES["towns"]["morocc"]["legs"], {})


if __name__ == "__main__":
    unittest.main()
