"""Реестр тем разговора (ORG-066): питомец, слух, цель недели, новости мира; ответ по теме собеседника.

Настоящий Mind (safety, общение, слухи, цели) с поддельным телом: действия, ушедшие в мост, собираются в sent.
Время общения — подменные часы; лимиты лички safety (настоящее время) в тестах сняты.
Запуск: cd brain && python3 -m unittest -v tests.test_topics
"""
import asyncio
import json
import random
import re
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.safety import MAX_TEXT
from live_brain.social import TAG, fields
from live_brain.topics import NEWS_MAX, WHAT

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT = BRAIN_DIR.parent
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZ = timezone(timedelta(hours=WORLD["timezone_offset_hours"]))
PERSONAS = sorted(BRAIN_DIR.glob("personas/*.json")) + sorted(ROOT.glob("bots/templates/*/persona.json"))
# самые длинные подстановки, какие дают факты и поставщики тем (имена жителей ≤ 8, карты ≤ 12)
LONGEST = {"name": "Abcdefgh", "me": "Abcdefgh", "lv": 99, "peer_lv": 99, "kills": 999, "loot": 999, "hours": 12,
           "map": "prt_fild08aa", "death_map": "prt_fild08aa", "amount": 9999, "pet": "Abcdefghij", "hunger": 100,
           "what": "x" * NEWS_MAX, "kind": "danger", "aim": "сдружиться с Abcdefgh", "pct": 100, "who": "Abcdefgh",
           "weather": "облачно", "days": 30, "ago": "30 дней назад", "item": "Abcdefghijkl"}
NEW_KEYS = ("pet", "pet_re", "rumor", "rumor_re", "aim", "aim_re", "news", "news_re")
NEW_TOPICS = {"pet", "rumor", "aim", "news", "weather", "remember"}   # новые фразы ≤ 60


def at_hour(hour):
    return datetime.now(TZ).replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def make_mind(root, bot, name, peers, clock, sent):
    async def send(a):
        sent.append(dict(a))
        return len(sent)

    persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
    persona.pop("sleep", None)
    mem = Memory(root / f"{bot}.sqlite")
    mind = Mind(Settings.from_env({}), persona, mem, send, root / f"{bot}.jsonl", RuleGate(), peers=peers,
                world=WORLD)
    mind.social.clock = clock
    mind.social.rng = random.Random(3)
    mind.rumors.clock = clock
    mind.safety.whisper_gap, mind.safety.whisper_limit = 0, 10 ** 6
    mind.state = {"name": name, "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 40, "dead": False,
                  "players": []}
    mind.fresh_state = True
    return mind


class TopicsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock(at_hour(14))
        self.sent, self.vsent = [], []
        self.a = make_mind(self.root, "bot01", "Arkady", {"Arkady", "Vera", "Ilsa"}, self.clock, self.sent)
        self.s = self.a.social

    def tearDown(self):
        self.a.mem.close()
        if hasattr(self, "v"):
            self.v.mem.close()
        self.tmp.cleanup()

    def run_(self, coro):
        return asyncio.run(coro)

    def whispers(self, sent=None):
        return [x for x in (self.sent if sent is None else sent) if x["action"] == "whisper"]

    def give_pet(self):
        self.a.state["pet"] = {"has": True, "name": "Poring", "type": 1002, "hungry": 60}

    def new_day(self):
        self.s.st["told"] = {}

    # ---------- 1. поставщики ----------

    def test_none_and_broken_provider(self):
        self.s.register_topic("nothing", lambda peer, now: None)

        def boom(peer, now):
            raise RuntimeError("сломался")

        self.s.register_topic("boom", boom)
        self.s.phrases = dict(self.s.phrases, nothing=["Пусто {name}"], boom=["Бум {name}"])
        facts = self.s.facts("Vera", self.clock.t)
        with self.assertLogs("social", "WARNING"):
            self.assertNotIn("nothing", self.s.registry_topics("Vera", self.clock.t, facts))
            self.assertNotIn("boom", self.s.topic_facts("Vera", self.clock.t))
            for _ in range(6):
                self.assertNotIn(self.s.choose_topic("Vera", facts, self.clock.t), ("nothing", "boom"))
            self.assertTrue(self.run_(self.s.say("Vera", "boom", 3)))      # упавшая тема — не падение разговора
        self.assertNotIn("[chat:boom:", self.whispers()[-1]["text"])
        with self.assertRaises(ValueError):
            self.s.register_topic("Bad-Name", lambda p, n: {})

    # ---------- 2. питомец ----------

    def test_pet_topic(self):
        self.give_pet()
        facts = self.s.facts("Vera", self.clock.t)
        chosen = []
        for _ in range(20):
            self.new_day()
            chosen.append(self.s.choose_topic("Vera", facts, self.clock.t))
        self.assertIn("pet", chosen)
        self.run_(self.s.say("Vera", "pet", 3))
        w = self.whispers()[-1]["text"]
        self.assertIn("Poring", w)
        self.assertTrue(w.endswith("[chat:pet:3]"))
        dec = [json.loads(l) for l in (self.root / "bot01.jsonl").read_text().splitlines()]
        self.assertTrue(any(d.get("type") == "social" and d.get("event") == "said" and d.get("topic") == "pet"
                            for d in dec))
        del self.a.state["pet"]
        self.assertIsNone(self.s.provide("pet", "Vera", self.clock.t))

    # ---------- 3. новости и слухи ----------

    def test_news_not_about_listener_and_not_repeated(self):
        now = time.time()
        self.a.mem.set("world_news", [
            {"ts": now - 600, "bot": "Ilsa", "kind": "level_up", "data": {"level": 31}},
            {"ts": now - 300, "bot": "Ilsa", "kind": "meeting_confirmed", "data": {"partner": "Vera"}},
            {"ts": now - 200, "bot": "Ilsa", "kind": "death_report", "data": {"map": "prt_fild07", "with": "Vera"}},
            {"ts": now - 100, "bot": "Vera", "kind": "level_up", "data": {"level": 40}},
            {"ts": now - 90000, "bot": "Ilsa", "kind": "job_changed", "data": {"to": "Archer"}},
        ])
        self.a.mem.set("known_players", {"Ilsa": {"sex": "Female"}})
        f = self.s.provide("news", "Vera", now)
        self.assertEqual((f["who"], f["what"]), ("Ilsa", "доросла до 31 уровня"))   # не про Vera и не старше суток
        self.run_(self.s.say("Vera", "news", 3, now))
        self.assertIn("Ilsa", self.whispers()[-1]["text"])
        self.assertIsNone(self.s.provide("news", "Vera", now))                   # одно событие — один раз
        g = self.s.provide("news", "Ilsa", now)                                  # Ilsa о себе не слышит
        self.assertIsNotNone(g)
        self.assertEqual(g["who"], "Vera")

    def test_rumor_topic(self):
        r = self.a.rumors
        r.hear("rich", "prt_fild05", "Ilsa", now=self.clock.t)
        r.hear("danger", "prt_fild07", "Vera", now=self.clock.t)            # от самой Vera — ей не пересказывать
        f = self.s.provide("rumor", "Vera", self.clock.t)
        self.assertEqual((f["map"], f["kind"]), ("prt_fild05", "rich"))
        self.run_(self.s.say("Vera", "rumor", 3))
        self.assertIn("prt_fild05", self.whispers()[-1]["text"])
        self.assertIsNone(self.s.provide("rumor", "Vera", self.clock.t))   # уже рассказал
        self.assertIsNone(self.s.provide("rumor", "Ilsa", self.clock.t + 30 * 86400))   # слух выдохся

    def test_aim_topic(self):
        self.a.mem.set("aims", {"start": 0, "until": 9e9, "items": [
            {"kind": "zeny", "text": "накопить ещё 5000 зени", "base": 0, "target": 5000, "progress": 2500, "done": None}]})
        f = self.s.provide("aim", "Vera", self.clock.t)
        self.assertEqual((f["aim"], f["pct"]), ("скопить 5000 зени", 50))

    # ---------- 4. ответ по теме собеседника (W1) ----------

    def test_reply_by_peer_topic(self):
        self.run_(self.s.on_tag("Vera", "Мой Lunatic сегодня смешной [chat:pet:1]"))
        self.clock.t += 10
        self.run_(self.s.flush(self.clock.t))
        w = self.whispers()[-1]["text"]
        self.assertTrue(w.endswith("[chat:pet:2]"), w)
        self.assertIn(TAG.sub("", w).strip(), [p.format(name="Vera") for p in self.s.phrases["pet_re"]])
        # тема шага 3 — тоже ответ по ней (и на этом разговор кончается)
        self.clock.t += 60 * 60
        self.s.st["replies"] = {}
        self.run_(self.s.on_tag("Vera", "Говорят, на prt_fild05 опасно [chat:rumor:3]"))
        self.clock.t += 10
        self.run_(self.s.flush(self.clock.t))
        self.assertTrue(self.whispers()[-1]["text"].endswith("[chat:rumor:4]"))

    def test_reply_without_re_phrases_is_old_behavior(self):
        self.s.phrases = {k: v for k, v in self.s.phrases.items() if k != "pet_re"}
        self.run_(self.s.on_tag("Vera", "Мой Lunatic [chat:pet:1]"))
        self.clock.t += 10
        self.run_(self.s.flush(self.clock.t))
        w = self.whispers()[-1]["text"]
        self.assertNotIn("[chat:pet:", w)
        self.assertTrue(w.endswith(":2]"))

    def test_reply_to_own_reply_is_own_topic(self):
        """На ответ по теме (шаг 2) — не эхо, а своя тема шага 3."""
        self.run_(self.s.on_tag("Vera", "Береги его [chat:pet:2]"))
        self.clock.t += 10
        self.run_(self.s.flush(self.clock.t))
        self.assertNotIn("[chat:pet:3]", self.whispers()[-1]["text"])

    # ---------- 5. длина фраз ----------

    def test_phrases_fit(self):
        for path in PERSONAS:
            p = json.loads(path.read_text(encoding="utf-8"))
            ph = p["phrases"]
            for key in NEW_KEYS:
                self.assertGreaterEqual(len(ph.get(key) or []), 4, f"{path.name}/{p['name']}: {key}")
            for key, options in ph.items():
                self.assertEqual(len(set(options)), len(options), f"{p['name']}: повторы в {key}")
                subs = dict(LONGEST, what=max(WHAT.values(), key=len)) if key.startswith("rumor") else LONGEST
                for o in options:
                    self.assertLessEqual(fields(o), set(LONGEST), f"{p['name']}/{key}: {o}")
                    if key.split("_")[0] not in NEW_TOPICS:
                        continue                                    # прежние фразы safety.fit_text укоротит
                    self.assertLessEqual(len(o), 60, f"{p['name']}/{key}: {o}")
                    text = f"{o.format(**subs)} [chat:{key.split('_')[0][:12]}:4]"
                    self.assertLessEqual(len(text), MAX_TEXT, f"{p['name']}/{key}: {text}")

    # ---------- 6. разговор двух жителей ----------

    def converse(self):
        """Arkady начинает; реплики доходят друг до друга, пока не стихнут."""
        a, v = self.a, self.v
        self.run_(a.social.say("Vera", "hello", 1))
        log = []
        for _ in range(8):
            got = False
            for src, dst, out in ((a, v, self.sent), (v, a, self.vsent)):
                for w in [x for x in out if x["action"] == "whisper"]:
                    log.append(w["text"])
                    self.run_(dst.social.on_tag(src.state["name"], w["text"]))
                    got = True
                out.clear()
            self.clock.t += 10
            for m in (a, v):
                self.run_(m.social.flush(self.clock.t))
            if not got and not self.whispers() and not self.whispers(self.vsent):
                break
        return log

    def test_two_residents_talk_about_world(self):
        self.v = make_mind(self.root, "bot02", "Vera", {"Arkady", "Vera", "Ilsa"}, self.clock, self.vsent)
        self.give_pet()
        self.a.mem.set("aims", {"start": 0, "until": 9e9, "items": [
            {"kind": "level", "text": "поднять уровень", "base": 40, "target": 3, "progress": 1, "done": None}]})
        self.a.rumors.hear("rich", "prt_fild05", "Ilsa", now=self.clock.t)
        now = time.time()
        for m in (self.a, self.v):
            m.mem.set("world_news", [{"ts": now - 60, "bot": "Ilsa", "kind": "pet_hatched", "data": {"name": "Lunatic"}}])
        self.v.state["pet"] = {"has": True, "name": "Lunatic", "hungry": 70}
        topics, replies = [], 0
        for i in range(10):
            self.clock.t += 20 * 60
            for m in (self.a, self.v):
                m.safety.texts = {}
                m.social.st["replies"] = {}
            log = self.converse()
            for text in log:
                m = TAG.search(text)
                if m and m.group(2) == "3":
                    topics.append(m.group(1))
                if m and m.group(2) == "4" and m.group(1) not in ("bye", "congrats", "condolence", "cold"):
                    replies += 1
        self.assertGreaterEqual(len(set(topics) - {"weather"}), 3, topics)
        self.assertGreater(replies, 0, "на тему собеседника отвечают по ней")


if __name__ == "__main__":
    unittest.main()
