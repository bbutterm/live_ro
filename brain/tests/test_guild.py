"""Гильдия жителей (guild.py, ORG-052): основатель, согласие, создание, приглашение, состав по state, чат.

На настоящем Mind (safety, память, шина) с поддельным телом: действия, ушедшие в мост, — в self.sent.
Сервер не участвует: «гильдия создана» здесь — это state.guild, который мост строит из пакетов OpenKore.
Запуск: cd brain && python3 -m unittest -v tests.test_guild
"""
import asyncio
import json
import random
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain import world_bus
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.guild import (ASK_GAP, CHAT_DAY, CHAT_GAP, NAME_TEMPLATES, Guild, clean_name,
                              resident_traits)
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.safety import PLAN_ACTIONS, SafetyPolicy

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZ = timezone(timedelta(hours=WORLD["timezone_offset_hours"]))
RATHENA_NAME = re.compile(r"^[A-Za-z0-9 ]{1,23}$")   # NAME_LENGTH 24, char_name_letters upstream


def at_hour(hour):
    return datetime.now(TZ).replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def run(coro):
    return asyncio.run(coro)


class GuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.world = json.loads(json.dumps(WORLD))
        self.world["guild"] = dict(self.world.get("guild") or {}, enabled=True)
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera", "Boris"}, world=self.world)
        self.clock = Clock(at_hour(14))
        self.g = self.make()
        self.state(players=[{"name": "Vera", "x": 150, "y": 180}, {"name": "Boris", "x": 152, "y": 182}])
        for p in ("Vera", "Boris"):
            self.mem.update_relation(p, 2, "знакомы")

    def make(self, **cfg):
        world = dict(self.world, guild=dict(self.world["guild"], **cfg))
        g = Guild(self.mind, world, clock=self.clock, rng=random.Random(3),
                  traits={"Arkady": 0.4, "Vera": 0.3, "Boris": 0.2})
        g.st.clear()
        g.st.update(yes={}, ready={}, chat={})
        self.mind.guild = self.g = g
        self.mind.needs.t["sociability"] = 0.9
        return g

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 150, "y": 180, "hp_pct": 100, "lv": 30,
             "dead": False, "zeny": 1000, "emperium": 1, "players": []}
        s.update(getattr(self, "_last", {}))
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        run(self.mind.on_message(s))

    def actions(self, kind=None):
        return [a for a in self.sent if kind is None or a["action"] == kind]

    def whispers(self, tag):
        return [a for a in self.actions("whisper") if f"[guild:{tag}:" in a["text"]]

    def tick(self, dt=5):
        self.clock.t += dt
        run(self.g.tick())

    def hear(self, sender, text):
        run(self.mind.on_message({"type": "event", "kind": "chat_private", "from": sender, "text": text}))

    def found_guild(self, master="Arkady", members=("Arkady",)):
        self.state(guild={"name": "Hearth of Prontera", "master": master, "online": len(members),
                          "members": [{"name": m, "online": True, "lv": 30} for m in members]})

    # ---------- основатель и условия ----------

    def test_founder_most_sociable_then_level(self):
        self.assertEqual(self.g.founder(), "Arkady")              # 0.9 у себя против 0.3/0.2
        self.mind.needs.t["sociability"] = 0.3
        self.state(players=[{"name": "Vera", "x": 1, "y": 1, "lv": 50}, {"name": "Boris", "x": 2, "y": 2}])
        self.assertEqual(self.g.founder(), "Vera", "общительность равна — выше уровень")
        self.assertEqual(self.make(founder="Boris").founder(), "Boris", "правило goals.json важнее")

    def test_founder_only_among_online(self):
        self.mind.needs.t["sociability"] = 0.1
        self.state(players=[])                                     # Vera и Boris не онлайн по данным тела
        self.assertEqual(self.g.founder(), "Arkady")

    def test_traits_from_roster(self):
        t = resident_traits()
        self.assertGreater(t["Vera"], t["Arkady"], "Vera общительнее (personas/bot02.json)")

    def test_asks_friends_then_creates(self):
        self.tick()
        asked = sorted(a["to"] for a in self.whispers("ask"))
        self.assertEqual(asked, ["Boris", "Vera"])
        self.tick()
        self.assertEqual(len(self.whispers("ask")), 2, f"повтор не раньше {ASK_GAP} с")
        self.hear("Vera", "[guild:yes:]")
        self.tick()
        self.assertFalse(self.actions("guild_create"), "нужно min_members=3 вместе со мной")
        self.hear("Boris", "[guild:yes:]")
        self.tick()
        create = self.actions("guild_create")
        self.assertEqual(len(create), 1)
        self.assertRegex(create[0]["name"], RATHENA_NAME)
        self.tick()
        self.assertEqual(len(self.actions("guild_create")), 1, "ждём ответа сервера, не повторяем")

    def test_conditions_block(self):
        self.state(emperium=0)
        self.tick()
        self.assertFalse(self.whispers("ask"), "нет Emperium, а проверка на сервере включена")
        self.make(emperium_check=False)
        self.tick()
        self.assertTrue(self.whispers("ask"), "проверка выключена владельцем — Emperium не нужен")
        self.sent.clear()
        self.state(emperium=1)
        self.mem.update_relation("Boris", -1, "обидел")
        self.make()
        self.tick()
        self.assertFalse(self.whispers("ask"), "к Boris отношение 1 < 2 — вдвоём гильдию не основать")

    def test_quarrel_excludes(self):
        self.mind.society.st["quarrel"]["Vera"] = {"cause": "тест", "since": 0}
        self.tick()
        self.assertFalse(self.whispers("ask"), "в ссоре с Vera — кандидатов меньше двух")

    def test_not_founder_does_not_ask(self):
        self.mind.needs.t["sociability"] = 0.1
        self.tick()
        self.assertFalse(self.whispers("ask"))

    def test_known_guild_from_world_news(self):
        self.mem.set("world_news", [{"ts": 1, "bot": "bot02", "kind": "guild_founded", "data": {"name": "Vera Hearth"}}])
        self.tick()
        self.assertFalse(self.whispers("ask"), "гильдия жителей уже есть — второй не основываем")

    def test_yield_to_other_founder(self):
        self.mind.persona["name"] = "Vera"                       # я — Vera, спрашивает Arkady (имя раньше)
        self.mind.state["name"] = "Vera"
        self.mind.ctx.peers = {"Arkady", "Boris"}
        self.mem.update_relation("Arkady", 2, "знакомы")
        self.state(name="Vera", players=[{"name": "Arkady", "x": 1, "y": 1}, {"name": "Boris", "x": 2, "y": 2}])
        self.hear("Arkady", "[guild:ask:]")
        self.assertEqual([a["text"] for a in self.whispers("yes")], ["[guild:yes:]"])
        self.tick()
        self.assertFalse(self.whispers("ask"), "уступаю основание Arkady")

    # ---------- ответы жителя ----------

    def test_member_answers_by_affinity(self):
        self.hear("Vera", "[guild:ask:]")
        self.assertEqual(self.whispers("yes")[0]["to"], "Vera")
        self.mem.update_relation("Boris", -2, "ссора")
        self.hear("Boris", "[guild:ask:]")
        self.assertEqual(self.whispers("no")[0]["to"], "Boris")

    def test_invite_handshake(self):
        self.hear("Vera", "[guild:invite:Vera Hearth]")
        expect = self.actions("guild_expect")
        self.assertEqual(expect, [dict(expect[0], name="Vera Hearth")])
        self.assertEqual(self.whispers("ready")[0]["to"], "Vera")
        self.sent.clear()
        self.hear("Boris", "[guild:invite:Bad_Name]")          # «_» — не попадает в метку (только латиница/пробел)
        self.assertFalse(self.actions("guild_expect"))

    def test_invite_refused_when_low_affinity(self):
        self.mem.update_relation("Boris", -2, "ссора")
        self.hear("Boris", "[guild:invite:Boris Club]")
        self.assertFalse(self.actions("guild_expect"))
        self.assertTrue(self.whispers("no"))

    def test_stranger_tag_ignored(self):
        run(self.mind.on_message({"type": "event", "kind": "chat_private", "from": "Stranger", "text": "[guild:invite:X]"}))
        self.assertFalse(self.actions("guild_expect"))

    # ---------- создана / вступил — только по state ----------

    def test_founded_by_state_and_published(self):
        self.mem.set("world_pub_cursor", 0)
        self.found_guild()
        self.tick()
        rows = [k for (k,) in self.mem.db.execute("SELECT kind FROM events WHERE kind LIKE 'guild_%'")]
        self.assertIn("guild_founded", rows)
        self.assertIn("guild_founded", world_bus.PUBLISH)
        self.assertIn("guild_joined", world_bus.PUBLISH)
        self.assertIn("guild_founded", LINES)
        self.tick()
        rows = [k for (k,) in self.mem.db.execute("SELECT kind FROM events WHERE kind = 'guild_founded'")]
        self.assertEqual(len(rows), 1, "один раз")

    def test_master_invites_visible_friends(self):
        self.found_guild()
        self.tick()
        invites = sorted(a["to"] for a in self.whispers("invite"))
        self.assertEqual(invites, ["Boris", "Vera"])
        self.assertTrue(all("Hearth of Prontera" in a["text"] for a in self.whispers("invite")))
        self.hear("Vera", "[guild:ready:]")
        self.assertEqual([a["to"] for a in self.actions("guild_invite")], ["Vera"])
        self.found_guild(members=("Arkady", "Vera"))
        self.tick(CHAT_GAP)
        self.assertTrue(any("Vera" in a["text"] for a in self.actions("guild_say")), "мастер приветствует новичка")

    def test_joined_by_state(self):
        self.found_guild(master="Vera", members=("Vera", "Arkady"))
        self.tick()
        row = self.mem.db.execute("SELECT data FROM events WHERE kind = 'guild_joined'").fetchone()
        self.assertEqual(json.loads(row[0])["master"], "Vera")
        self.assertFalse(self.actions("guild_invite"), "звать может только мастер")

    def test_left(self):
        self.found_guild()
        self.tick()
        self.state(guild=None)
        self.tick()
        self.assertTrue(self.mem.db.execute("SELECT 1 FROM events WHERE kind = 'guild_left'").fetchone())

    def test_create_results(self):
        self.g.st["creating"] = {"name": "x", "ts": self.clock.t}
        first = self.g.guild_name()
        run(self.mind.on_message({"type": "event", "kind": "guild_create_result", "code": 2}))
        self.assertNotEqual(self.g.guild_name(), first, "имя занято — другое имя")
        run(self.mind.on_message({"type": "event", "kind": "guild_create_result", "code": 3}))
        self.assertGreater(self.g.st["no_emperium_until"], self.clock.t)
        self.tick()
        self.assertFalse(self.whispers("ask"), "нет Emperium по ответу сервера — пауза на сутки")

    # ---------- имя ----------

    def test_names_fit_rathena(self):
        self.assertEqual(clean_name("  Дом  Hearth_of  Prontera!! "), "Hearth of Prontera")
        self.mind.persona["name"] = "A" * 23
        self.mind.state["name"] = "A" * 23
        for n in range(len(NAME_TEMPLATES) * 2 + 1):
            self.g.st["name_try"] = n
            self.assertRegex(self.g.guild_name(), RATHENA_NAME)
        self.g.st["name_try"] = 0
        self.mind.persona["guild_name"] = "Кружок"              # кириллица — шаблон
        self.assertRegex(self.g.guild_name(), RATHENA_NAME)
        self.mind.persona["guild_name"] = "Old Friends"
        self.assertEqual(self.g.guild_name(), "Old Friends")

    # ---------- чат гильдии ----------

    def test_chat_rare(self):
        self.found_guild(members=("Arkady", "Vera"))
        self.tick()
        self.g.pending_welcome = []
        says = self.actions("guild_say")
        self.assertEqual(len(says), 1, "приветствие раз в сутки")
        for _ in range(20):
            self.tick(60)
        self.assertEqual(len(self.actions("guild_say")), 1, f"не чаще {CHAT_GAP} с")
        self.assertLessEqual(len(self.g.chat_times), CHAT_DAY)

    def test_who_in_town_reply(self):
        self.found_guild(members=("Arkady", "Vera"))
        self.g.st["chat"]["hello"] = self.clock.t
        run(self.mind.on_message({"type": "event", "kind": "chat_guild", "from": "Vera", "text": "Кто в городе?"}))
        self.assertTrue(any("Prontera" in a["text"] for a in self.actions("guild_say")))

    def test_day_summary_evening_and_night_silence(self):
        self.found_guild(members=("Arkady", "Vera"))
        self.clock.t = at_hour(3)
        self.tick()
        self.assertFalse(self.actions("guild_say"), "ночью молчим")
        self.clock.t = at_hour(22)
        self.g.st["chat"]["hello"] = self.clock.t
        self.state(map="prt_fild08")
        self.tick()
        self.assertTrue(any("Итог" in a["text"] or "побед" in a["text"] for a in self.actions("guild_say")))

    def test_disabled_by_default(self):
        self.assertFalse(WORLD["guild"]["enabled"])
        mind = Mind(Settings.from_env({}), self.mind.persona, self.mem, lambda a: None, Path(self.tmp.name) / "x.jsonl",
                    RuleGate(), peers={"Arkady", "Vera"}, world=WORLD)
        self.assertIsNone(mind.guild)

    # ---------- safety ----------

    def test_safety(self):
        for a in ("guild_create", "guild_invite", "guild_say", "guild_expect"):
            self.assertIn(a, PLAN_ACTIONS)
        p = SafetyPolicy(["prt_fild08"], peers={"Vera"})
        out = {"name": "Arkady", "hp_pct": 100}
        inside = dict(out, guild={"name": "G"})
        self.assertIsNone(p.check({"action": "guild_create", "name": "Hearth"}, out)[0], "модели нельзя")
        self.assertEqual(p.check({"action": "guild_create", "name": " Hearth  of X "}, out, protocol=True)[0],
                         {"action": "guild_create", "name": "Hearth of X"})
        self.assertIsNone(p.check({"action": "guild_create", "name": "LR_G"}, out, protocol=True)[0])
        self.assertIsNone(p.check({"action": "guild_create", "name": "A" * 24}, out, protocol=True)[0])
        self.assertIsNone(p.check({"action": "guild_create", "name": "Hearth"}, inside, protocol=True)[0])
        self.assertIsNone(p.check({"action": "guild_invite", "to": "Vera"}, out, protocol=True)[0], "не в гильдии")
        self.assertIsNone(p.check({"action": "guild_invite", "to": "Stranger"}, inside, protocol=True)[0])
        self.assertIsNotNone(p.check({"action": "guild_invite", "to": "Vera"}, inside, protocol=True)[0])
        self.assertIsNone(p.check({"action": "guild_say", "text": "hi"}, out, protocol=True)[0])
        self.assertEqual(p.check({"action": "guild_say", "text": "x" * 100}, inside, protocol=True)[0]["text"], "x" * 78)
        self.assertIsNone(p.check({"action": "guild_say", "text": "hi"}, dict(inside, dead=True), protocol=True)[0])


if __name__ == "__main__":
    unittest.main()
