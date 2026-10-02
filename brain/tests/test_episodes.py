"""«Помнишь?» — эпизоды пары и воспоминания вслух (episodes.py, ORG-055).

Настоящий Mind с поддельным телом; события памяти пишутся настоящим временем, «через 3 суток» — подменные часы.
Запуск: cd brain && python3 -m unittest -v tests.test_episodes
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import episodes as ep_mod
from live_brain.config import Settings
from live_brain.episodes import Episodes, plural_days
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.social import TAG

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
DAY = 86400


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class EpisodesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock(time.time())
        self.sent = {}
        self.a = self.make("bot01", "Arkady")

    def make(self, bot, name, mem=None):
        sent = self.sent.setdefault(name, [])

        async def send(a):
            sent.append(dict(a))
            return len(sent)

        persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        persona.pop("sleep", None)
        mem = mem or Memory(self.root / f"{bot}.sqlite")
        mind = Mind(Settings.from_env({}), persona, mem, send, self.root / f"{bot}.jsonl", RuleGate(),
                    peers={"Arkady", "Vera"}, world=WORLD)
        mind.episodes.clock = self.clock
        mind.social.clock = self.clock
        mind.social.rng = random.Random(2)
        mind.social.grammar = None   # grammar: здесь — точные фразы персоны (ORG-065 — test_grammar)
        mind.safety.whisper_gap = 0
        mind.state = {"name": name, "map": "prontera", "x": 156, "y": 185, "dead": False, "lv": 30, "players": []}
        mind.fresh_state = True
        return mind

    def tearDown(self):
        for m in getattr(self, "minds", []):
            m.mem.close()
        self.a.mem.close()
        self.tmp.cleanup()

    def tick(self, m=None):
        m = m or self.a
        m.episodes.next_tick = 0
        m.episodes.tick()

    def heal(self, mem, frm="Vera", to="Arkady", amount=120):
        mem.add_event("heal_confirmed", {"from": frm, "to": to, "amount": amount, "hp_before": 10, "hp_max": 300})

    # ---------- эпизоды из событий ----------

    def test_episode_from_event_no_duplicates(self):
        self.heal(self.a.mem)
        self.tick()
        self.tick()                                             # курсор: второй раз не читает
        eps = self.a.episodes.with_peer("Vera")
        self.assertEqual(len(eps), 1)
        self.assertEqual((eps[0]["kind"], eps[0]["map"], eps[0]["weight"]), ("heal", "prontera", 5))
        self.heal(self.a.mem)                                   # то же за сутки — счётчик, а не новый эпизод
        self.tick()
        eps = self.a.episodes.with_peer("Vera")
        self.assertEqual((len(eps), eps[0]["times"]), (1, 2))
        self.a.mem.add_event("meeting_confirmed", {"plan": 1, "partner": "Vera", "map": "prt_fild08", "x": 1, "y": 1})
        self.a.mem.add_event("gift_given", {"peer": "Stranger", "item": "501", "amount": 1})   # не житель
        self.tick()
        self.assertEqual([e["kind"] for e in self.a.episodes.with_peer("Vera")], ["meet", "heal"])
        self.assertEqual(self.a.episodes.with_peer("Stranger"), [])

    def test_heal_not_to_me_is_not_episode(self):
        self.heal(self.a.mem, frm="Vera", to="Ilsa")
        self.tick()
        self.assertEqual(self.a.episodes.all(), [])

    def test_recall_timing(self):
        self.heal(self.a.mem)
        self.tick()
        e = self.a.episodes
        self.assertIsNone(e.recall("Vera", self.clock.t + DAY))          # моложе 2 суток
        got = e.recall("Vera", self.clock.t + 3 * DAY)
        self.assertEqual(got["kind"], "heal")
        e.mark_recalled(got["id"], self.clock.t + 3 * DAY)
        self.assertIsNone(e.recall("Vera", self.clock.t + 6 * DAY))      # вспоминали — не раньше 7 суток
        self.assertIsNotNone(e.recall("Vera", self.clock.t + 11 * DAY))

    def test_quarrel_hides_peace_and_death(self):
        self.a.mem.add_event("society_reconciled", {"peer": "Vera", "occasion": "подарок"})
        self.tick()
        now = self.clock.t + 3 * DAY
        self.assertEqual(self.a.episodes.recall("Vera", now)["kind"], "peace")
        self.a.society.st["quarrel"]["Vera"] = {"since": now, "why": "тест", "occasion": None}
        self.assertTrue(self.a.society.quarrel("Vera"))
        self.assertIsNone(self.a.episodes.recall("Vera", now))

    def test_limit(self):
        e = self.a.episodes
        eps = [{"id": f"meet:Vera:{i}", "ts": self.clock.t - i * 3 * DAY, "last": 0, "kind": "meet", "peer": "Vera",
                "map": "prontera", "weight": 3, "times": 1, "recalled": None} for i in range(ep_mod.MAX_EPISODES)]
        e.save(eps)
        self.a.mem.add_event("heal_confirmed", {"from": "Vera", "to": "Arkady", "amount": 5})
        self.tick()
        got = e.all()
        self.assertEqual(len(got), ep_mod.MAX_EPISODES)
        self.assertIn("heal", [x["kind"] for x in got])                   # новое и тяжёлое осталось
        self.assertNotIn(f"meet:Vera:{ep_mod.MAX_EPISODES - 1}", [x["id"] for x in got])   # самое старое ушло

    def test_restart_keeps_episodes_and_cursor(self):
        self.heal(self.a.mem)
        self.tick()
        b = self.make("bot01", "Arkady", mem=self.a.mem)
        self.tick(b)
        self.assertEqual(len(b.episodes.all()), 1)
        self.assertEqual(b.mem.get("episodes_cursor"), self.a.mem.get("episodes_cursor"))

    def test_plural(self):
        self.assertEqual([plural_days(n) for n in (1, 2, 5, 11, 21, 22, 25)],
                         ["1 день", "2 дня", "5 дней", "11 дней", "21 день", "22 дня", "25 дней"])

    # ---------- вспомнить вслух ----------

    def whispers(self, name):
        return [a["text"] for a in self.sent[name] if a["action"] == "whisper"]

    def test_phrase_has_days_and_map(self):
        self.a.mem.add_event("meeting_confirmed", {"plan": 1, "partner": "Vera", "map": "prt_fild08", "x": 1, "y": 1})
        self.tick()
        self.clock.t += 3 * DAY + 60
        self.assertTrue(asyncio.run(self.a.social.say("Vera", "remember", 3)))
        text = self.whispers("Arkady")[-1]
        self.assertTrue(text.endswith("[chat:remember:3]"), text)
        self.assertIn("3", text)
        clean = TAG.sub("", text).strip()
        opts = [p.format(name="Vera", ago="3 дня назад", days=3, map="prt_fild08")
                for p in self.a.persona["phrases"]["remember_meet"]]
        self.assertIn(clean, opts)
        self.assertIsNone(self.a.episodes.recall("Vera", self.clock.t))   # вспомнил — отмечено

    def test_heal_then_meeting_three_days_later(self):
        """Сценарий ТЗ: Vera вылечила Arkady; через 3 суток встреча — «помнишь?» вместо привета, ответ remember_re."""
        v = self.make("bot02", "Vera")
        self.minds = [v]
        v.mem.add_event("heal_given", {"from": "Vera", "to": "Arkady", "amount": 120})   # у лекаря
        self.heal(self.a.mem)                                                             # у получателя
        self.tick(v)
        self.tick()
        self.clock.t += 3 * DAY + 600
        v.social.topics["remember"]["chance"] = 1.0
        r = v.routine
        r.new_day(self.clock.t)
        r.st.update(mode="town", arrived=True, rest_until=self.clock.t + 3600, mode_since=self.clock.t)
        v.social.near_since = {"Arkady": self.clock.t - 600}
        v.state["players"] = [{"name": "Arkady", "x": 157, "y": 185, "lv": 30}]
        asyncio.run(v.social.chat(self.clock.t, v.state))
        text = self.whispers("Vera")[-1]
        self.assertTrue(text.endswith("[chat:remember:1]"), text)
        self.assertIn(TAG.sub("", text).strip(),
                      [p.format(name="Arkady", ago="3 дня назад", days=3, map="prontera")
                       for p in v.persona["phrases"]["remember_healed"]])
        asyncio.run(self.a.social.on_tag("Vera", text))
        self.clock.t += 10
        asyncio.run(self.a.social.flush(self.clock.t))
        reply = self.whispers("Arkady")[-1]
        self.assertTrue(reply.endswith("[chat:remember:2]"), reply)
        self.assertIn(TAG.sub("", reply).strip(),
                      [p.format(name="Vera") for p in self.a.persona["phrases"]["remember_re"]])
        # Arkady помнит своё: Vera его лечила — remember_heal
        self.a.social.topics["remember"]["chance"] = 1.0
        self.assertEqual(self.a.social.provide("remember", "Vera", self.clock.t)["_key"], "remember_heal")

    def test_chance_not_every_time(self):
        self.heal(self.a.mem)
        self.tick()
        now = self.clock.t + 3 * DAY
        self.a.social.rng = random.Random(11)
        self.a.social.grammar = None   # grammar: здесь — точные фразы персоны (ORG-065 — test_grammar)
        hits = sum("remember" in self.a.social.registry_topics("Vera", now, opener=True) for _ in range(200))
        self.assertTrue(40 < hits < 110, hits)                               # шанс 0.35


if __name__ == "__main__":
    unittest.main()
