"""Сплетни о жителях и репутация (gossip.py, ORG-056), история и остывание отношений (W5, W6).

Настоящий Mind (Arkady) с поддельным телом и шиной мира во временном каталоге; Vera, Bram, Ilsa — другие жители
(писатели той же шины), Stranger — посторонний игрок. Время модуля — подменные часы от настоящего времени
(отношения в памяти пишутся с time.time()). Запуск: cd brain && python3 -m unittest -v tests.test_gossip
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import gossip as G
from live_brain import world_bus
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import KEEP_EVENTS, Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.safety import MAX_TEXT
from live_brain.social import TAG as CHAT_TAG

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
PEERS = {"Arkady", "Vera", "Bram", "Ilsa"}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class GossipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        persona.setdefault("traits", {})["generosity"] = 0.3
        self.mem = Memory(root / "m.sqlite")
        self.path = root / "world.sqlite"
        self.bus = world_bus.WorldBus(self.path, "Arkady")
        self.others = {n: world_bus.WorldBus(self.path, n) for n in ("Vera", "Bram", "Ilsa")}
        self.root = root
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers=PEERS, world=WORLD, world_bus_db=self.bus)
        self.mind.safety.whisper_gap, self.mind.safety.whisper_limit = 0, 10 ** 6
        self.clock = Clock(time.time())
        self.g = self.mind.gossip
        self.g.clock = self.clock
        self.mind.social.clock = self.clock
        self.mind.social.rng = random.Random(2)
        self.mind.social.grammar = None   # grammar: здесь — точные фразы персоны (ORG-065 — test_grammar)
        self.mind.state = {"name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 40,
                           "dead": False, "players": []}
        self.mind.fresh_state = True

    def tearDown(self):
        for b in [self.bus, *self.others.values()]:
            b.close()
        self.mem.close()
        self.tmp.cleanup()

    def run_(self, coro):
        return asyncio.run(coro)

    def tick(self, dt=60):
        self.clock.t += dt
        self.g.next_tick = 0
        self.run_(self.g.tick())

    def whisper(self, frm, text):
        self.run_(self.mind.on_event({"kind": "chat_private", "from": frm, "text": text}))

    def whispers(self):
        return [x for x in self.sent if x["action"] == "whisper"]

    def decisions(self):
        path = self.root / "d.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def near(self, *names):
        self.mind.state["players"] = [{"name": n, "x": 157, "y": 185} for n in names]

    # ---------- факты ----------

    def test_module_registered_and_switch(self):
        self.assertIsNotNone(self.g)
        self.assertIn("gossip", self.mind.social.topics)
        m = Mind(Settings.from_env({"BRAIN_DISABLE": "gossip"}), self.mind.persona, Memory(":memory:"),
                 lambda a: None, self.root / "x.jsonl", RuleGate(), peers=PEERS, world=WORLD)
        self.assertIsNone(m.gossip)
        w = json.loads(json.dumps(WORLD))
        w["gossip"] = {"enabled": False}
        m2 = Mind(Settings.from_env({}), self.mind.persona, Memory(":memory:"), lambda a: None,
                  self.root / "y.jsonl", RuleGate(), peers=PEERS, world=w)
        self.assertIsNone(m2.gossip)

    def test_own_fact_heal_gives_reputation(self):
        self.tick()                                              # первый запуск: курсоры
        self.mem.add_event("heal_confirmed", {"from": "Vera", "to": "Arkady", "amount": 300})
        self.mem.add_event("trade_debt", {"peer": "Bram", "item": "501", "amount": 5, "price": 50})
        self.mem.add_event("gift_received", {"peer": "Stranger"})           # посторонний — не житель
        self.tick()
        k = self.g.known()
        self.assertEqual(k["Vera:heal"]["src"], "own")
        self.assertEqual(self.g.text(k["Vera:heal"]), "подлечил(а) меня в бою")
        self.assertGreater(self.g.reputation("Vera"), 0.9)
        self.assertLess(self.g.reputation("Bram"), -0.9)
        self.assertFalse(any(r["who"] == "Stranger" for r in k.values()))
        self.mem.add_event("heal_confirmed", {"from": "Vera", "to": "Arkady", "amount": 100})
        self.tick()
        self.assertEqual(k["Vera:heal"]["n"], 2)
        self.assertGreater(self.g.reputation("Vera"), 1.2)                   # повтор весомее
        self.assertIn("сам(а) видел(а)", self.g.reasons("Vera")[0])

    def test_seed_last_week_quietly(self):
        self.mem.add_event("heal_confirmed", {"from": "Ilsa", "to": "Arkady", "amount": 50})
        self.tick()
        self.assertIn("Ilsa:heal", self.g.known())
        self.assertFalse(any(d.get("type") == "gossip" and d.get("event") == "fact" for d in self.decisions()))

    def test_bus_news(self):
        self.tick()
        t = self.clock.t
        self.others["Vera"].publish("level_up", {"level": 42}, 3, now=t)
        self.others["Bram"].publish("heal_confirmed", {"from": "Ilsa", "to": "Bram", "amount": 80}, 2, now=t)
        self.others["Bram"].publish("heal_confirmed", {"from": "Arkady", "to": "Bram", "amount": 80}, 2, now=t)
        self.others["Ilsa"].publish("gift_given", {"peer": "Stranger"}, 2, now=t)
        self.bus.publish("level_up", {"level": 50}, 3, now=t)                      # о себе — нет
        self.tick()
        k = self.g.known()
        self.assertEqual((k["Vera:level"]["src"], self.g.text(k["Vera:level"])), ("news", "дорос(ла) до 42 уровня"))
        self.assertEqual(self.g.text(k["Ilsa:heal"]), "подлечил(а) Bram в бою")
        self.assertNotIn("Arkady:heal", k)
        self.assertEqual(self.g.text(k["Ilsa:gift"]), "выручает жителей вещами")    # посторонний не назван
        self.assertNotIn("Stranger", json.dumps(k, ensure_ascii=False))

    # ---------- услышать ----------

    def test_heard_with_trust_not_affinity(self):
        self.mem.update_relation("Bram", 1, "знакомы")
        self.whisper("Vera", G.tag("kind", "Bram", 1, "Ilsa", "gift"))
        rec = self.g.known()["Bram:gift"]
        self.assertEqual((rec["src"], rec["from"], rec["author"], rec["hops"]), ("peer", "Vera", "Ilsa", 1))
        self.assertAlmostEqual(rec["base"], 0.3, places=3)
        self.assertEqual(self.mem.relation("Bram")["affinity"], 1)          # знакомому — только rep
        self.assertGreater(self.g.reputation("Bram"), 0)
        self.assertIn("от Vera (пересказ)", self.g.reasons("Bram")[0])
        self.assertTrue(any(d.get("event") == "heard" for d in self.decisions()))
        self.assertFalse(any(w for w in self.whispers()))                   # метка не ушла в gate/LLM-ответ

    def test_ignored_tags(self):
        self.whisper("Vera", G.tag("kind", "Arkady", 0, "Vera", "heal"))     # обо мне
        self.whisper("Vera", G.tag("kind", "Bram", 1, "Arkady", "heal"))     # моя же вернулась
        self.whisper("Vera", G.tag("mean", "Stranger", 0, "Vera", "debt"))   # не житель
        self.whisper("Vera", G.tag("kind", "Vera", 0, "Vera", "heal"))       # сам о себе
        self.whisper("Vera", "[gossip:kind:Bram:0:Vera:debt]")               # вид не тот
        self.assertEqual(self.g.known(), {})

    def test_better_version_kept(self):
        self.mem.update_relation("Vera", 2)
        self.mem.update_relation("Vera", 2)                                  # друг
        self.whisper("Vera", G.tag("brave", "Bram", 0, "Vera", "job"))
        self.whisper("Ilsa", G.tag("brave", "Bram", 2, "Vera", "job"))
        rec = self.g.known()["Bram:job"]
        self.assertEqual((rec["from"], rec["src"], rec["base"]), ("Vera", "friend", 0.7))
        self.assertEqual(rec["heard_from"], ["Ilsa", "Vera"])

    # ---------- пересказать ----------

    def test_topic_phrase_and_tag(self):
        self.whisper("Vera", G.tag("kind", "Bram", 0, "Vera", "heal"))
        f = self.mind.social.provide("gossip", "Ilsa", self.clock.t)
        self.assertEqual((f["who"], f["_key"]), ("Bram", "gossip_kind"))
        self.assertIsNone(self.mind.social.provide("gossip", "Vera", self.clock.t))   # от неё и слышал
        self.assertIsNone(self.mind.social.provide("gossip", "Bram", self.clock.t))   # о нём самом
        self.run_(self.mind.social.say("Ilsa", "gossip", 3))
        w = self.whispers()[-1]["text"]
        self.assertTrue(w.endswith("[chat:gossip:3]"), w)
        self.assertLessEqual(len(CHAT_TAG.sub("", w).strip()), 60)
        self.assertIn("Bram", w)
        self.run_(self.g.tick())
        t = self.whispers()[-1]["text"]
        self.assertEqual(t, "[gossip:kind:Bram:1:Vera:heal]")
        self.assertLessEqual(len(t), MAX_TEXT)
        self.assertIsNone(self.mind.social.provide("gossip", "Ilsa", self.clock.t + 7200))   # уже рассказал
        self.assertEqual(G.parse(t)["hops"], 1)

    def test_listener_replies_and_records(self):
        self.whisper("Vera", "Говорят, Bram лечит жителей в бою. [chat:gossip:3]")
        self.clock.t += 10
        self.run_(self.mind.social.flush(self.clock.t))
        w = self.whispers()[-1]["text"]
        self.assertTrue(w.endswith("[chat:gossip:4]"), w)
        self.assertIn(CHAT_TAG.sub("", w).strip(), G.PHRASES["gossip_re"])

    def test_no_bad_about_friend_and_generous(self):
        self.mem.update_relation("Bram", 2)
        self.mem.update_relation("Bram", 2)
        self.whisper("Vera", G.tag("mean", "Bram", 0, "Vera", "debt"))
        self.whisper("Vera", G.tag("mean", "Ilsa", 0, "Vera", "drop"))
        self.assertLess(self.g.reputation("Bram"), 0)                         # сам знает — но не разносит
        self.assertIsNone(self.g.pick("Ilsa", self.clock.t))
        self.assertEqual(self.g.pick("Bram", self.clock.t), "Ilsa:drop")
        self.g.mind.needs.t["generosity"] = 0.9                               # щедрый плохого не пересказывает
        self.assertIsNone(self.g.pick("Bram", self.clock.t))

    def test_silent_retell_with_llm(self):
        self.mind.social.llm = lambda: True
        self.tick()
        self.mem.add_event("heal_confirmed", {"from": "Vera", "to": "Arkady", "amount": 300})
        self.near("Bram")
        self.tick()
        self.assertEqual(self.whispers()[-1]["to"], "Bram")
        self.assertEqual(self.whispers()[-1]["text"], "[gossip:kind:Vera:0:Arkady:heal]")
        n = len(self.whispers())
        self.tick()                                                           # pair_gap — не чаще раза в час
        self.assertEqual(len(self.whispers()), n)

    def test_hops_limit_and_decay(self):
        self.whisper("Vera", G.tag("luck", "Bram", 3, "Ilsa", "pet"))
        self.assertIsNone(self.g.pick("Bram", self.clock.t))                  # hops 3 — дальше не идёт
        rec = self.g.known()["Bram:pet"]
        t0 = self.g.trust(rec, self.clock.t)
        self.assertAlmostEqual(self.g.trust(rec, self.clock.t + 7 * 86400), t0 / 2, places=2)
        self.clock.t += 30 * 86400
        self.g.forget(self.clock.t)
        self.assertNotIn("Bram:pet", self.g.known())

    # ---------- знакомство, история и остывание ----------

    def test_first_impression_once(self):
        self.tick()
        self.whisper("Vera", G.tag("kind", "Ilsa", 0, "Vera", "heal"))
        self.whisper("Bram", G.tag("kind", "Ilsa", 0, "Bram", "gift"))
        self.assertIsNone(self.mem.relation("Ilsa"))
        self.mem.touch_relation("Ilsa")                                       # знакомство
        self.tick()
        self.assertEqual(self.mem.relation("Ilsa")["affinity"], 1)
        self.assertIn("по слухам", self.mem.relation_log("Ilsa")[-1]["note"])
        self.tick()
        self.assertEqual(self.mem.relation("Ilsa")["affinity"], 1)            # один раз

    def test_old_acquaintance_no_impression(self):
        self.mem.touch_relation("Ilsa")
        self.tick()                                                           # знакомы до модуля... с этой секунды
        self.g.st["impressed"] = []
        self.g.st["since"] = time.time() + 10
        self.whisper("Vera", G.tag("kind", "Ilsa", 0, "Vera", "heal"))
        self.tick()
        self.assertEqual(self.mem.relation("Ilsa")["affinity"], 0)

    def test_relation_log_keeps_reasons(self):
        self.mem.update_relation("Vera", 1, "выручил, когда мне не хватало")
        self.mem.update_relation("Vera", 1, "провели время вместе")
        self.mem.update_relation("Vera", -1, "недоплатил за товар")
        log_ = self.mem.relation_log("Vera")
        self.assertEqual([h["note"] for h in log_], ["выручил, когда мне не хватало", "провели время вместе",
                                                     "недоплатил за товар"])
        self.assertEqual([h["affinity"] for h in log_], [1, 2, 1])
        self.assertEqual(self.mem.relation("Vera")["note"], "недоплатил за товар")
        for i in range(60):
            self.mem.update_relation("Vera", 0, f"причина {i}")
        self.assertEqual(len(self.mem.relation_log("Vera")), 40)

    def test_cooling_and_floor(self):
        self.mem.update_relation("Vera", 2, "подарок")
        self.mem.update_relation("Vera", 2, "лечение")                        # 4, две причины — опора 0
        self.mem.update_relation("Bram", -2, "подвёл")
        self.tick()
        self.assertEqual(self.mem.relation("Vera")["affinity"], 4)            # общались недавно
        self.clock.t = time.time() + 15 * 86400
        self.g.st["cool_check"] = 0
        self.run_(self.g.tick())
        self.assertEqual(self.mem.relation("Vera")["affinity"], 3)
        self.assertEqual(self.mem.relation("Bram")["affinity"], -1)           # обида остывает
        self.assertTrue(self.mem.relation_log("Vera")[-1]["note"].startswith(G.COOL_NOTE))
        self.clock.t += 86400
        self.g.st["cool_check"] = 0
        self.tick(0)
        self.assertEqual(self.mem.relation("Vera")["affinity"], 3)            # не чаще раза в cool_days
        # опора: много причин — дружба не остывает до нуля
        for i in range(6):
            self.mem.update_relation("Ilsa", 1, f"доброе дело {i}")
        self.assertEqual(self.mem.relation("Ilsa")["affinity"], 6)
        for k in range(8):
            self.clock.t += 15 * 86400
            self.g.st["cool_check"] = 0
            self.tick(0)
        self.assertEqual(self.mem.relation("Ilsa")["affinity"], 2)

    def test_quarrel_grudge_not_cooled(self):
        self.mem.update_relation("Bram", -2, "подвёл")
        self.mind.society.st["quarrel"]["Bram"] = {"since": 0, "last": 0, "cause": "подвёл"}
        self.clock.t = time.time() + 15 * 86400
        self.tick(0)
        self.assertEqual(self.mem.relation("Bram")["affinity"], -2)

    def test_keep_events_w6(self):
        for kind in ("society_quarrel", "society_reconciled", "job_changed", "pet_hatched", "gift_received"):
            self.assertIn(kind, KEEP_EVENTS)
        self.mem.add_event("society_reconciled", {"peer": "Vera"})
        self.mem.db.execute("UPDATE events SET ts = ?", (time.time() - 200 * 86400,))
        self.mem.prune()
        self.assertEqual(self.mem.count_events("society_reconciled", 0), 1)

    def test_prompt_summary(self):
        self.tick()
        self.mem.add_event("heal_confirmed", {"from": "Vera", "to": "Arkady", "amount": 300})
        self.tick()
        s = self.g.summary()
        self.assertIn("Vera", s)
        self.assertGreater(s["Vera"]["репутация"], 0)

    def test_phrases_fit(self):
        for key, pool in G.PHRASES.items():
            for p in pool:
                text = p.format(who="Abcdefgh", what="x" * G.WHAT_MAX)
                self.assertLessEqual(len(text), 60, (key, text))
        for code, (_, generic, _, own) in G.WHAT.items():
            self.assertLessEqual(len(generic), G.WHAT_MAX)
            self.assertLessEqual(len(own or ""), G.WHAT_MAX)
            t = G.tag(G.WHAT[code][0], "A" * 23, 9, "B" * 23, code)
            self.assertLessEqual(len(t), MAX_TEXT)
            self.assertIsNotNone(G.parse(t))


if __name__ == "__main__":
    unittest.main()
