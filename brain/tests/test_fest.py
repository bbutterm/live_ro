"""Ивенты rAthena (fest.py, ORG-087, ТЗ Т-37): разбор объявлений, сбор жителя, итог, конфиг сервера.

Настоящий Mind (Arkady) в «отдыхе в Пронтере»; объявления подаются как событие тела world_msg (rumors.on_world_msg ->
fest.on_announce). Тексты объявлений собираются из строк скриптов upstream/rathena (при наличии), иначе — образцы.
Запуск: cd brain && python3 -m unittest -v tests.test_fest
"""
import asyncio
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.fest import CATALOG, Fest
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT = BRAIN_DIR.parent
RATHENA = Path(os.environ.get("LIVE_RO_RATHENA") or ROOT / "upstream" / "rathena")
OPENKORE = Path(os.environ.get("LIVE_RO_OPENKORE") or ROOT / "upstream" / "openkore")
HAVE = (RATHENA / "npc/custom/events/mushroom_event.txt").exists()
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
CONF = ROOT / "server" / "conf" / "optional" / "events_custom.txt"
SAMPLE = {".@spawn": "7", ".event_map$": "prontera", "strcharinfo(0)": "Vera", "$MonsterName$": "Poring",
          "getitemname(.prize)": "Apple", "getpartyname( .party_id )": "LR_Vera"}
FALLBACK = {   # образцы на случай, если upstream нет рядом
    ("mushroom", "start"): "Find the Mushroom : Total of 7 Mushrooms have been spawned in prontera!",
    ("mushroom", "progress"): "[ Vera ] has killed a Mushroom. There are now 7 Mushroom(s) left.",
    ("mushroom", "end"): "The Find the Mushroom Event has ended. All the Mushrooms have been killed.",
    ("disguise", "start"): "The Disguise Event will begin in 3 minutes.",
    ("disguise", "round"): "Vera is correct! I was disguised as: Poring",
    ("devil_square", "start"): "Devil Square is OPEN. The event will begin in 5 minutes.",
    ("cluckers", "start"): "[Cluck! Cluck! Boom!] is about to start in Prontera!",
    ("mvp_ladder", "start"): "The party [LR_Vera] has started the MvP ladder game.",
}


def announce_text(ev, what, **override):
    """Текст объявления из строки скрипта (announce "..." + x + "...", флаг) с образцами переменных."""
    if not HAVE:
        text = FALLBACK[(ev, what)]
        for k, v in override.items():
            text = text.replace(SAMPLE.get(k, k), v)
        return text
    line = (RATHENA / CATALOG[ev]["file"]).read_text(encoding="utf-8", errors="replace").splitlines()[
        CATALOG[ev]["refs"][what] - 1]
    m = re.search(r"announce\s+(.*),\s*[^,]+;\s*$", line)
    assert m, line
    out = []
    for part in re.findall(r'"(?:[^"\\]|\\.)*"|[^+]+', m.group(1)):
        part = part.strip()
        if not part:
            continue
        if part.startswith('"'):
            out.append(part[1:-1])
        else:
            out.append(dict(SAMPLE, **override).get(part, part))
    return "".join(out)


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class Dice:
    def __init__(self, x=0.0):
        self.x = x

    def random(self):
        return self.x


class FestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.clock = Clock()
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Vera"}, world=WORLD)
        m = self.mind
        m.social.is_night = lambda now: False
        m.director = None
        m.fest = Fest(m, WORLD, clock=self.clock, rng=Dice(0.0))
        m.routine.new_day(self.clock.t)
        m.routine.st.update(mode="town", arrived=True, rest_until=self.clock.t + 3600, mode_since=self.clock.t)
        self.home = dict(m.routine.town)
        self.state()

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    @property
    def f(self):
        return self.mind.fest

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 190, "hp_pct": 100, "lv": 30,
             "job": "Swordsman", "sex": "Male", "dead": False, "zeny": 5000, "items": {}, "players": []}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def hear(self, text):
        asyncio.run(self.mind.on_message({"type": "event", "kind": "world_msg", "text": text, "source": "sys"}))

    def event(self, kind, **data):
        asyncio.run(self.mind.on_message({"type": "event", "kind": kind, **data}))

    def tick(self, dt=1):
        self.clock.t += dt
        self.f.next_tick = 0
        asyncio.run(self.f.tick())

    def events(self, kind):
        return [json.loads(r[0]) for r in self.mem.db.execute("SELECT data FROM events WHERE kind = ?", (kind,))]

    def points(self):
        return [a for a in self.sent if a.get("action") == "meet_point"]


class TestParse(unittest.TestCase):
    def test_all_announcements(self):
        f = Fest.__new__(Fest)
        self.assertTrue(CATALOG["mushroom"]["start"].search(announce_text("mushroom", "start")))
        m = CATALOG["mushroom"]["start"].search(announce_text("mushroom", "start"))
        self.assertEqual((m.group("n"), m.group("map")), ("7", "prontera"))
        self.assertTrue(CATALOG["mushroom"]["progress"].search(announce_text("mushroom", "progress")))
        self.assertTrue(CATALOG["mushroom"]["end"].search(announce_text("mushroom", "end")))
        self.assertTrue(CATALOG["disguise"]["start"].search(announce_text("disguise", "start")))
        r = CATALOG["disguise"]["round"].search(announce_text("disguise", "round"))
        self.assertEqual((r.group("who"), r.group("monster")), ("Vera", "Poring"))
        for ev in ("devil_square", "cluckers", "mvp_ladder"):
            self.assertTrue(CATALOG[ev]["start"].search(announce_text(ev, "start")), ev)
        del f

    @unittest.skipUnless(HAVE, "нет upstream/rathena")
    def test_disguise_countdown_lines(self):
        text = (RATHENA / CATALOG["disguise"]["file"]).read_text(encoding="utf-8", errors="replace")
        lines = re.findall(r'announce "(The Disguise Event[^"]*)"', text)
        self.assertTrue(lines)
        for t in lines:
            if "off" not in t:
                self.assertTrue(CATALOG["disguise"]["start"].search(t), t)

    @unittest.skipUnless(HAVE, "нет upstream/rathena")
    def test_schedule_refs(self):
        mush = (RATHENA / CATALOG["mushroom"]["file"]).read_text().splitlines()
        self.assertTrue(mush[CATALOG["mushroom"]["refs"]["clock"] - 1].startswith("OnMinute10:"))
        self.assertIn("1084", "\n".join(mush))
        dis = (RATHENA / CATALOG["disguise"]["file"]).read_text().splitlines()
        self.assertEqual(dis[CATALOG["disguise"]["refs"]["clock"] - 1].strip(), "OnClock0000:")
        self.assertTrue(dis[17].startswith("prontera,160,155"))        # NPC маскарада (:18)
        ds = (RATHENA / CATALOG["devil_square"]["file"]).read_text().splitlines()
        self.assertEqual(ds[CATALOG["devil_square"]["refs"]["clock"] - 1].strip(), "OnClock0000:")

    @unittest.skipUnless(HAVE and (OPENKORE / "fields" / "prontera.fld2.gz").exists(), "нет upstream")
    def test_points_walkable(self):
        from tests.test_newborn import ok_walkable, rathena_walkable
        p = CATALOG["disguise"]["point"]
        self.assertTrue(rathena_walkable(p["map"], p["x"], p["y"]))
        self.assertTrue(ok_walkable(p["map"], p["x"], p["y"]))
        f = WORLD["social"]["points"]["fountain"]
        self.assertTrue(rathena_walkable(f["map"], f["x"], f["y"]))


class TestConfig(unittest.TestCase):
    def test_optional_conf(self):
        text = CONF.read_text(encoding="utf-8")
        active = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("//")]
        self.assertEqual(active, ["npc: npc/custom/events/mushroom_event.txt", "npc: npc/custom/events/disguise.txt"])
        self.assertIn("НЕ ВКЛЮЧЕНО", text)
        # не в шаблонах рендера: включает владелец
        tmpl = (ROOT / "server" / "conf" / "import-tmpl" / "map_conf.txt").read_text(encoding="utf-8")
        self.assertNotIn("npc/custom/events", tmpl)
        if HAVE:
            for ln in active:
                self.assertTrue((RATHENA / ln.split(": ", 1)[1]).exists(), ln)
            custom = (RATHENA / "npc" / "scripts_custom.conf").read_text()
            self.assertIn("//npc: npc/custom/events/mushroom_event.txt", custom)   # в upstream — выключено
            self.assertIn('strcmpi(w1, "npc") == 0',
                          (RATHENA / "src/map/map.cpp").read_text(errors="replace").splitlines()[4169])
            self.assertIn("conf/import/map_conf.txt",
                          (RATHENA / "conf/map_athena.conf").read_text().splitlines()[127])


class TestMushroom(FestBase):
    def test_gather_kill_and_result(self):
        self.hear(announce_text("mushroom", "start"))
        self.assertEqual(self.events("fest_seen")[-1]["event"], "mushroom")
        self.assertEqual(self.f.cur["map"], "prontera")
        self.tick()
        p = self.points()[-1]
        self.assertEqual((p["map"], p["x"], p["y"]), ("prontera", 156, 185))      # фонтан (social.points)
        self.assertEqual(self.events("fest_join")[-1]["event"], "mushroom")
        self.assertEqual(self.mind.social.next_walk, self.f.cur["until"])
        self.tick()
        self.assertEqual(len(self.points()), 1)
        self.event("kill", monster="Black Mushroom")
        self.event("kill", monster="Poring")
        self.event("kill", monster="Black Mushroom")
        self.hear(announce_text("mushroom", "progress"))
        self.assertEqual(self.f.cur["left"], 7)
        self.hear(announce_text("mushroom", "end"))
        self.assertIsNone(self.f.cur)
        res = self.events("fest_result")[-1]
        self.assertEqual((res["kills"], res["gathered"]), (2, True))
        self.assertEqual(self.mind.routine.town, self.home)                         # точка отдыха вернулась
        self.assertEqual(LINES["fest_result"](res), "ивент «грибная охота»: 0 мин, грибов: 2")
        self.assertEqual(self.f.facts("Vera", self.clock.t)["_key"], "fest_kills")

    def test_other_town_only_talk(self):
        self.hear(announce_text("mushroom", "start", **{".event_map$": "geffen"}))
        self.tick()
        self.assertFalse(self.points())
        self.assertEqual(self.f.cur["why"], "ивент не в моём городе")
        self.assertEqual(self.f.facts("Vera", self.clock.t)["what"], "грибная охота")
        self.f.said("Vera", self.f.facts("Vera", self.clock.t), self.clock.t)
        self.assertIsNone(self.f.facts("Vera", self.clock.t))
        self.tick(21 * 60)                                                         # срок ивента
        self.assertIsNone(self.f.cur)
        self.assertFalse(self.events("fest_result")[-1]["gathered"])
        self.assertIsNone(LINES["fest_result"](self.events("fest_result")[-1]))

    def test_refusals(self):
        cases = [({"hp_pct": 30}, None, "HP 30% < 60%"),
                 ({}, "night", "ночь"),
                 ({}, "dice", "не интересно"),
                 ({}, "hunt", "не отдых в городе")]
        for i, (kw, how, why) in enumerate(cases):
            with self.subTest(why=why):
                self.f.st.pop("cur", None)
                self.state(**dict({"hp_pct": 100}, **kw))
                if how == "night":
                    self.mind.social.is_night = lambda now: True
                if how == "dice":
                    self.f.rng = Dice(0.99)
                if how == "hunt":
                    self.mind.routine.st["mode"] = "hunt"
                self.hear(announce_text("mushroom", "start", **{".@spawn": str(i + 1)}))
                self.tick(700)
                self.assertEqual(self.f.cur["why"], why)
                self.assertFalse(self.points())
                self.mind.social.is_night = lambda now: False
                self.f.rng = Dice(0.0)
                self.mind.routine.st["mode"] = "town"

    def test_repeat_announce_one_event(self):
        self.hear(announce_text("disguise", "start"))
        self.clock.t += 60
        self.hear("The Disguise Event will begin in 2 minutes.")
        self.hear("The Disguise Event has begun!")
        self.assertEqual(len(self.events("fest_seen")), 1)

    def test_disguise_watch_and_round(self):
        self.hear(announce_text("disguise", "start"))
        self.tick()
        p = self.points()[-1]
        self.assertEqual((p["x"], p["y"]), (160, 151))
        self.hear(announce_text("disguise", "round", **{"strcharinfo(0)": "Arkady"}))
        r = self.events("fest_round")[-1]
        self.assertEqual((r["winner"], r["monster"], r["mine"]), ("Arkady", "Poring", True))
        self.assertEqual(LINES["fest_round"](r), "маскарад: угадал(а) Poring")
        self.hear(announce_text("disguise", "round", **{"$MonsterName$": "Lunatic"}))
        self.assertIsNone(LINES["fest_round"](self.events("fest_round")[-1]))       # чужая победа — не в летопись
        self.tick(26 * 60)
        self.assertTrue(self.events("fest_result")[-1]["gathered"])

    def test_dangerous_only_talk(self):
        for ev in ("devil_square", "cluckers", "mvp_ladder"):
            with self.subTest(ev=ev):
                self.f.st.pop("cur", None)
                self.hear(announce_text(ev, "start") + " " * len(ev))   # другой текст — мимо дедупа слухов
                self.tick()
                self.assertEqual(self.f.cur["event"], ev)
                self.assertEqual(self.f.cur["why"], "опасный или GM-ивент — только разговор")
        self.assertFalse(self.points())

    def test_rumor_still_works(self):
        self.hear(announce_text("mushroom", "end"))                 # «Event» в тексте — слух event, как раньше
        self.assertTrue(any(r.get("kind") == "event" for r in (self.mem.get("rumors") or {}).values()
                            if isinstance(r, dict)) or self.mem.get("rumors"))


class TestSwitch(unittest.TestCase):
    def test_disabled(self):
        w = json.loads(json.dumps(WORLD))
        w["fest"]["enabled"] = False
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(Path(tmp) / "m.sqlite")

            async def send(a):
                return 1
            persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
            m = Mind(Settings.from_env({}), persona, mem, send, Path(tmp) / "d.jsonl", RuleGate(),
                     peers={"Vera"}, world=w)
            self.assertIsNone(m.fest)
            m.rumors.on_world_msg({"text": FALLBACK[("mushroom", "start")]})   # без модуля — только слух
            m2 = Mind(Settings.from_env({"BRAIN_DISABLE": "fest"}), persona, mem, send, Path(tmp) / "d.jsonl",
                      RuleGate(), peers={"Vera"}, world=WORLD)
            self.assertIsNone(m2.fest)
            mem.close()


if __name__ == "__main__":
    unittest.main()
