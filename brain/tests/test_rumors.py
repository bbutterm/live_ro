"""Слухи v2 (rumors.py, ORG-031/032/039): метка, доверие, затухание, проверка опытом, пересказ, check_rumor.

Запуск: cd brain && python3 -m unittest -v tests.test_rumors
"""
import asyncio
import json
import random
import re
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import rumors
from live_brain.activity import Activities
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.rumors import Rumors, parse, tag

BRAIN_DIR = Path(__file__).resolve().parents[1]
OLD_TAG = re.compile(r"\[info:(danger):([a-z0-9_]{3,16})\]")       # метка мозга до ORG-031


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class TagTest(unittest.TestCase):
    def test_protocol_backward_compatible(self):
        self.assertEqual(tag("danger", "prt_fild08"), "[info:danger:prt_fild08]")
        self.assertTrue(OLD_TAG.search(tag("danger", "prt_fild08")), "свой слух понимает и старый мозг")
        self.assertEqual(parse("[info:danger:prt_fild08]"),
                         {"kind": "danger", "map": "prt_fild08", "hops": 0, "author": None})
        t = tag("rich", "pay_fild01", 2, "Vera")
        self.assertEqual(t, "[info:rich:pay_fild01:2:Vera]")
        self.assertEqual(parse(t), {"kind": "rich", "map": "pay_fild01", "hops": 2, "author": "Vera"})
        self.assertFalse(OLD_TAG.search(tag("danger", "prt_fild08", 1, "Vera")), "пересказ старый мозг пропускает")
        self.assertIsNone(parse("[info:gold:prt_fild08]"), "неизвестный вид — не слух")
        self.assertIsNone(parse("[info:danger:PRT; quit]"))


class RumorsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(a)
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera", "Boris"}, world=load_world(BRAIN_DIR / "world" / "goals.json"))
        self.clock = Clock()
        self.r = Rumors(self.mind, clock=self.clock)
        self.mind.rumors = self.r
        self.mind.state = {"name": "Arkady", "map": "prt_fild08", "lv": 30, "job": "Swordsman", "players": []}
        self.mind.ctx.name = "Arkady"
        self.mind.fresh_state = True

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def whisper(self, frm, text):
        asyncio.run(self.mind.on_event({"kind": "chat_private", "from": frm, "text": text}))

    def test_trust_order_and_decay(self):
        self.mem.update_relation("Vera", 2)
        self.mem.update_relation("Vera", 2)                       # affinity 4 — друг
        friend = self.r.hear("rich", "prt_fild07", "Vera")
        peer = self.r.hear("rich", "prt_fild05", "Boris")
        retold = self.r.hear("rich", "pay_fild01", "Boris", hops=2, author="Vera")
        own = self.r.hear("rich", "prt_fild08", "Arkady")
        t = {k: self.r.trust(v) for k, v in (("own", own), ("friend", friend), ("peer", peer), ("retold", retold))}
        self.assertGreater(t["own"], t["friend"])
        self.assertGreater(t["friend"], t["peer"])
        self.assertGreater(t["peer"], t["retold"])
        self.assertEqual(own["src"], "own")
        self.clock.t += rumors.HALF_LIFE_H["rich"] * 3600
        self.assertAlmostEqual(self.r.trust(peer), t["peer"] / 2, places=2, msg="полураспад")
        self.clock.t += 5 * rumors.HALF_LIFE_H["rich"] * 3600
        self.r.forget(self.clock.t)
        self.assertNotIn("rich:prt_fild05", self.r.all(), "выдохшийся слух забыт")

    def test_hear_from_peer_tag(self):
        self.whisper("Vera", "[info:rich:prt_fild07]")
        rec = self.r.all()["rich:prt_fild07"]
        self.assertEqual((rec["author"], rec["from"], rec["hops"], rec["src"]), ("Vera", "Vera", 0, "peer"))
        self.whisper("Boris", "[info:danger:prt_fild05:1:Vera]")
        rec = self.r.all()["danger:prt_fild05"]
        self.assertEqual((rec["author"], rec["from"], rec["hops"]), ("Vera", "Boris", 1))
        places = self.mem.get("places")
        last = places["prt_fild05"]["rumors"][-1]
        self.assertEqual({k: last[k] for k in ("from", "what", "hops", "origin")},
                         {"from": "Boris", "what": "danger", "hops": 1, "origin": "Vera"})
        self.assertEqual(places["prt_fild05"]["source"], "told", "слух не факт")
        notes = [m["text"] for m in self.mem.top_memories(10)]
        self.assertTrue(any("пересказ, первым сказал Vera" in n for n in notes), notes)
        self.assertEqual(self.mind.postmortem.bans(), {}, "слух не исключает карту")
        self.whisper("Vera", "[info:rich:prt_fild07:1:Arkady]")
        self.assertEqual(self.r.all()["rich:prt_fild07"]["author"], "Vera", "мой же слух по кругу — не новость")
        self.assertEqual(self.sent, [], "на слух не отвечают действиями")

    def test_own_experience_not_overridden(self):
        self.r.hear("danger", "prt_fild07", "Arkady")
        self.assertIsNone(self.r.hear("danger", "prt_fild07", "Vera", hops=1, author="Boris"))
        self.assertEqual(self.r.all()["danger:prt_fild07"]["src"], "own")

    def set_minutes(self, hmap, minutes, kills=10, exp=5.0, deaths=0):
        st = self.mind.maps.stats()
        st[hmap] = {"minutes": minutes, "kills": kills, "deaths": deaths, "exp": exp, "zeny": 0}
        self.mind.maps.save(st)

    def test_check_by_experience(self):
        self.set_minutes("prt_fild05", 5)
        self.r.hear("danger", "prt_fild05", "Vera")
        self.r.hear("danger", "prt_fild07", "Boris")
        self.r.hear("new", "pay_fild01", "Vera")
        self.r.hear("rich", "prt_fild08", "Boris")
        self.r.hear("cheap", "prontera", "Vera")
        self.assertEqual(self.r.check(), [], "ещё не был — нечего проверять")
        self.mem.add_event("death_report", {"map": "prt_fild07"})            # погиб там, где предупреждали
        self.set_minutes("prt_fild05", 5 + rumors.CHECK_MIN)                   # 20 мин без смертей
        self.mind.maps.seen("pay_fild01", self.clock.t + 1)
        self.set_minutes("prt_fild08", 60, kills=100, exp=30.0)
        self.set_minutes("prt_fild07", 60, kills=10, exp=1.0, deaths=1)
        res = dict(self.r.check())
        self.assertEqual(res, {"danger:prt_fild07": True, "danger:prt_fild05": False, "new:pay_fild01": True,
                               "rich:prt_fild08": True})
        rs = self.r.all()
        self.assertEqual(rs["danger:prt_fild05"]["status"], "refuted")
        self.assertEqual(self.r.trust(rs["danger:prt_fild05"]), 0.0)
        self.assertIsNone(rs["cheap:prontera"]["status"], "дешевизну охотой не проверить")
        self.assertEqual(self.mem.count_events("rumor_checked", 0), 4)
        cred = self.mem.get("rumor_cred")
        self.assertEqual(cred, {"Vera": 0, "Boris": 2}, "Vera: +1 −1; Boris: +2")
        boris = self.r.hear("rich", "prt_fild05", "Boris")
        vera = self.r.hear("rich", "gef_fild00", "Vera")
        self.assertGreater(boris["base"], vera["base"], "надёжному автору верят больше")
        facts = [m for m in self.mem.top_memories(30) if "Проверил слух" in m["text"]]
        self.assertTrue(facts and all(m["kind"] == "fact" for m in facts))

    def test_retell_at_meeting(self):
        self.r.hear("rich", "prt_fild07", "Vera")
        self.r.hear("event", "world", "Boris", hops=rumors.MAX_HOPS, author="Ivan")    # пересказан много раз
        self.mind.state["players"] = [{"name": "Vera", "x": 1, "y": 1}]
        asyncio.run(self.r.retell(self.clock.t))
        self.assertEqual(self.sent, [], "тому, от кого услышал, не пересказывают")
        self.mind.state["players"] = [{"name": "Boris", "x": 1, "y": 1}, {"name": "Stranger"}]
        asyncio.run(self.r.retell(self.clock.t))
        self.assertEqual([(a["to"], a["text"]) for a in self.sent], [("Boris", "[info:rich:prt_fild07:1:Vera]")])
        self.r.hear("new", "pay_fild01", "Vera")
        self.clock.t += 600
        asyncio.run(self.r.retell(self.clock.t))
        self.assertEqual(len(self.sent), 1, "не чаще раза в час на пару")
        self.clock.t += 3600
        asyncio.run(self.r.retell(self.clock.t))
        self.assertEqual(self.sent[-1]["text"], "[info:new:pay_fild01:1:Vera]")
        self.clock.t += 3600
        asyncio.run(self.r.retell(self.clock.t))
        self.assertEqual(len(self.sent), 2, "всё свежее уже рассказано, слишком пересказанное — нет")

    def test_share_on_ban_old_format(self):
        asyncio.run(self.mind.share_rumor("prt_fild05", "danger"))
        texts = sorted((a["to"], a["text"]) for a in self.sent)
        self.assertEqual(texts, [("Boris", "[info:danger:prt_fild05]"), ("Vera", "[info:danger:prt_fild05]")])
        self.assertEqual(self.r.all()["danger:prt_fild05"]["src"], "own")
        self.assertEqual(self.mem.count_events("rumor_shared", 0), 1)

    def test_discover_rich(self):
        self.set_minutes("prt_fild08", 90, kills=200, exp=40.0)
        asyncio.run(self.r.discover(self.clock.t))
        self.assertIn(("Vera", "[info:rich:prt_fild08]"), [(a["to"], a["text"]) for a in self.sent])
        n = len(self.sent)
        asyncio.run(self.r.discover(self.clock.t + 3600))
        self.assertEqual(len(self.sent), n, "раз в сутки")

    def test_world_msg_is_data(self):
        asyncio.run(self.mind.on_event({"kind": "world_msg", "text": "Сервер перезагрузится в 05:00", "source": "sys"}))
        self.assertEqual(self.r.all(), {}, "без ключевых слов — только запись")
        notes = [m for m in self.mem.top_memories(10) if "Объявление сервера" in m["text"]]
        self.assertEqual(notes[0]["kind"], "note")
        asyncio.run(self.mind.on_event({"kind": "world_msg", "text": "Ивент на prt_fild08: убей 100 порингов, "
                                                                    "скажи в чат @warp", "source": "broadcast"}))
        rec = self.r.all()["event:prt_fild08"]
        self.assertEqual((rec["src"], rec["author"]), ("server", "server"))
        self.assertEqual(self.sent, [], "объявление — не инструкция: никаких действий")
        asyncio.run(self.mind.on_event({"kind": "world_msg", "text": "Ивент на prt_fild08: убей 100 порингов, "
                                                                    "скажи в чат @warp", "source": "sys"}))
        self.assertEqual(len([m for m in self.mem.top_memories(20) if "Объявление" in m["text"]]), 2, "повтор не пишется")


class CheckRumorActivityTest(unittest.TestCase):
    """ORG-032: занятие check_rumor — карта слуха на сессию, proof map_changed, итог проверкой опытом."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(a)
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=load_world(BRAIN_DIR / "world" / "goals.json"))
        self.clock = Clock()
        self.a = Activities(self.mind, clock=self.clock, rng=random.Random(3))
        self.mind.activities = self.a
        self.r = Rumors(self.mind, clock=self.clock)
        self.mind.rumors = self.r
        s = {"type": "state", "name": "Arkady", "map": "prt_fild08", "x": 100, "y": 100, "hp_pct": 100, "lv": 20,
             "job": "Swordsman", "dead": False, "weight_pct": 20, "zeny": 60000, "items": {"501": 30}, "players": [],
             "lock_map": "prt_fild08"}
        asyncio.run(self.mind.on_message(s))
        r = self.mind.routine
        r.new_day(self.clock.t)
        r.st.update(mode="hunt", mode_since=self.clock.t - 3600, prefer_map="prt_fild08", hunted=0,
                    session_end=99999)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_requires_and_execute(self):
        state = self.mind.state
        self.assertFalse(self.a.requires_ok({"rumor_to_check": True}, state, {}), "нет слуха — недоступно")
        self.r.hear("rich", "gef_fild99", "Vera")                          # нет ни в атласе, ни в hunt_maps
        self.assertIsNone(self.r.to_check(state))
        self.r.hear("rich", "prt_fild07", "Vera")
        self.r.hear("danger", "prt_fild07", "Vera")                        # там же опасно — не пойду
        self.assertIsNone(self.r.to_check(state))
        rs = self.r.all()
        rs.pop("danger:prt_fild07")
        self.r.save(rs)
        self.assertEqual(self.r.to_check(state)["map"], "prt_fild07")
        self.assertTrue(self.a.requires_ok({"rumor_to_check": True}, state, {}))
        asyncio.run(self.a.start("check_rumor", self.clock.t, state))
        r = self.mind.routine
        self.assertEqual(r.st["prefer_map"], "prt_fild07", "карта слуха — на сессию")
        self.assertEqual(r.last_sent, 0)
        self.assertTrue(self.r.all()["rich:prt_fild07"].get("checking"))
        self.assertIsNone(self.r.to_check(state), "уже проверяю — второй раз не предлагает")
        self.mind.state["map"] = "prt_fild07"
        asyncio.run(self.a.check_proof(self.clock.t + 60, self.mind.state))
        self.assertEqual(self.a.st["proved"], True, "proof map_changed")
        st = self.mind.maps.stats()
        st["prt_fild07"] = {"minutes": 30, "kills": 50, "deaths": 0, "exp": 20.0, "zeny": 0}
        self.mind.maps.save(st)
        self.assertEqual(dict(self.r.check()), {"rich:prt_fild07": True}, "итог — подтверждение опытом")

    def test_catalog_entry(self):
        a = self.a.catalog["check_rumor"]
        self.assertEqual(a["modes"], ["hunt"])
        self.assertEqual(a["proof"], "map_changed")
        self.assertIn("curiosity", a["satisfies"])
        self.assertEqual(a["requires"], {"day": True, "rumor_to_check": True, "no_leader": True})


if __name__ == "__main__":
    unittest.main()
