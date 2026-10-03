"""Социальная жизнь жителей (social.py) без сети: городской распорядок, разговоры, реакции, отношения.

Запуск: cd brain && python3 -m unittest -v tests.test_social
"""
import asyncio
import json
import random
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import Routine, load_world
from live_brain.safety import SafetyPolicy
from live_brain.social import TAG, Social
from tests.worldtime import shift_time

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZ = timezone(timedelta(hours=WORLD["timezone_offset_hours"]))


def at_hour(hour):
    """Сегодняшнее время мира в hour:00 (события памяти пишутся настоящим временем — день тот же)."""
    d = datetime.now(TZ).replace(hour=hour, minute=0, second=0, microsecond=0)
    return d.timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeMind:
    def __init__(self, bot, peer, mem, clock):
        self.persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())
        name = self.persona["name"]
        self.mem = mem
        self.state = {"name": name, "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "dead": False,
                      "lv": 30, "sitting": True, "lock_map": "prontera", "lock_x": 156, "lock_y": 185,
                      "players": [], "party_members": []}
        self.ctx = SimpleNamespace(peers={peer}, name=name, last={})
        self.s = SimpleNamespace(llm_enabled=False)
        self.fresh_state = True
        self.active_plan = None
        self.plans = SimpleNamespace(store=SimpleNamespace(active=lambda: self.active_plan))
        self.blocker = None
        self.life = SimpleNamespace(current="RESTING")
        self.safety = SafetyPolicy(self.persona["hunt_maps"], peers={peer}, extra_point_maps=["prontera"])
        self.out, self.rejected, self.decisions, self.triggers = [], [], [], []
        self.routine = Routine(self, WORLD, rng=random.Random(1), clock=clock)
        self.routine.st = {"day": "x", "mode": "town", "arrived": True, "hunted": 3 * 3600, "budget": 5 * 3600,
                           "rest_until": clock() + 3 * 3600, "session_end": 0}
        self.social = Social(self, WORLD, clock=clock, rng=random.Random(7))

    def may_move(self, owner):
        return self.blocker is None, self.blocker

    def trigger(self, reason, context=None, kind="event"):
        self.triggers.append((reason, context, kind))

    async def execute(self, actions, source, reason, protocol=False):
        for a in actions:
            clean, why = self.safety.check(a, self.state, now=self.social.clock(), protocol=protocol)
            if why:
                self.rejected.append((a, why))
                continue
            self.out.append(clean)
            k = clean["action"]
            if k == "meet_point":
                self.state.update(lock_map=clean["map"], lock_x=clean["x"], lock_y=clean["y"])
            elif k == "sit":
                self.state["sitting"] = True
            elif k == "stand":
                self.state["sitting"] = False

    def write_decision(self, rec):
        self.decisions.append(rec)

    def actions(self, kind=None):
        return [a for a in self.out if kind is None or a["action"] == kind]


class SocialTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock(at_hour(14))
        self.a = FakeMind("bot01", "Vera", Memory(Path(self.tmp.name) / "a.sqlite"), self.clock)
        self.v = FakeMind("bot02", "Arkady", Memory(Path(self.tmp.name) / "v.sqlite"), self.clock)

    def tearDown(self):
        self.a.mem.close()
        self.v.mem.close()
        self.tmp.cleanup()

    def run_(self, coro):
        return asyncio.run(coro)

    def tick(self, *minds):
        for m in minds:
            self.run_(m.social.tick())

    def meet(self):
        """Оба жителя у фонтана в 2 клетках друг от друга."""
        self.a.state["players"] = [{"name": "Vera", "x": 158, "y": 186, "lv": 30}]
        self.v.state.update(x=158, y=186, players=[{"name": "Arkady", "x": 156, "y": 185, "lv": 30}])

    def deliver(self, src, dst):
        sent = src.actions("whisper")
        src.out = [a for a in src.out if a["action"] != "whisper"]
        for w in sent:
            self.assertLessEqual(len(w["text"]), 100)
            self.run_(dst.social.on_tag(src.state["name"], w["text"]))
        return sent

    def converse(self, max_rounds=8):
        """Обмен до тишины: реплики доходят, отложенные ответы отправляются после задержки."""
        log = []
        for _ in range(max_rounds):
            got = self.deliver(self.a, self.v) + self.deliver(self.v, self.a)
            log += got
            self.clock.t += 10
            self.tick(self.a, self.v)
            if not got and not self.a.actions("whisper") and not self.v.actions("whisper"):
                break
        return log

    # ---------- городской распорядок ----------

    def test_walk_only_in_town_and_arrived(self):
        m = self.a
        self.tick(m)                                      # первый тик назначает время прогулки
        self.assertEqual(m.actions("meet_point"), [])
        self.clock.t += 26 * 60
        self.tick(m)
        mp = m.actions("meet_point")
        self.assertEqual(len(mp), 1)
        self.assertEqual(m.actions()[0]["action"], "stand")   # после sit OpenKore не ходит к lockMap
        points = WORLD["social"]["points"]
        spot = m.social.spot
        self.assertIn(spot, points)
        self.assertNotEqual(spot, "fountain")             # из центра — к другой точке
        self.assertEqual((mp[0]["x"], mp[0]["y"]), (points[spot]["x"], points[spot]["y"]))
        self.assertEqual(m.routine.town["x"], points[spot]["x"])   # распорядок не тянет назад
        # дошёл — садится
        m.state.update(x=points[spot]["x"] + 1, y=points[spot]["y"])
        self.clock.t += 60
        self.tick(m)
        self.assertEqual(m.actions()[-1]["action"], "sit")
        # режим охоты — исходная точка отдыха возвращена, прогулок нет
        m.routine.st["mode"] = "hunt"
        m.out.clear()
        self.clock.t += 30 * 60
        self.tick(m)
        self.assertEqual(m.out, [])
        self.assertEqual(m.routine.town, WORLD["routine"]["town"])

    def test_no_walk_before_arrival_plan_or_arbiter(self):
        m = self.a
        m.routine.st.update(arrived=False, rest_until=float("inf"))
        self.tick(m)
        self.clock.t += 60 * 60
        self.tick(m)
        self.assertEqual(m.out, [])
        m.routine.st["arrived"] = True
        m.active_plan = {"id": "p1"}                      # план встречи важнее прогулки
        self.tick(m)
        self.clock.t += 60 * 60
        self.tick(m)
        self.assertEqual(m.out, [])
        m.active_plan = None
        m.blocker = "economy"                             # арбитр: телом владеет передача вещей
        self.clock.t += 60 * 60
        self.tick(m)
        self.assertEqual(m.out, [])
        m.blocker = None
        self.tick(m)
        self.assertEqual(len(m.actions("meet_point")), 1)

    def test_no_walk_right_before_rest_ends(self):
        m = self.a
        m.routine.st["rest_until"] = self.clock.t + 20 * 60
        self.tick(m)
        self.clock.t = m.routine.st["rest_until"] - 4 * 60          # до конца отдыха 4 мин (< 5)
        m.social.next_walk = self.clock.t - 1
        self.tick(m)
        self.assertEqual(m.actions("meet_point"), [])
        m.routine.st["rest_until"] = float("inf")                  # норма выбрана — отдых до завтра
        self.tick(m)
        self.assertEqual(len(m.actions("meet_point")), 1)

    def test_character_point_preferences(self):
        counts = {"bot01": {}, "bot02": {}}
        for mind, key in ((self.a, "bot01"), (self.v, "bot02")):
            for i in range(400):
                mind.social.spot = None
                mind.social.rng = random.Random(i)
                name, _ = mind.social.pick_point({"map": "prontera", "players": []})
                counts[key][name] = counts[key].get(name, 0) + 1
        self.assertGreater(counts["bot01"]["tools"], counts["bot01"]["church"])
        self.assertGreater(counts["bot02"]["church"], counts["bot02"]["tools"])
        self.assertGreater(counts["bot02"]["kafra"], counts["bot01"]["kafra"])

    def test_points_inside_prontera(self):
        for name, p in WORLD["social"]["points"].items():
            self.assertEqual(p["map"], "prontera")
            self.assertTrue(0 < p["x"] < 312 and 0 < p["y"] < 392, name)   # размер prontera 312x392
            clean, why = self.a.safety.check({"action": "meet_point", **p}, self.a.state, protocol=True)
            self.assertIsNone(why)

    def test_goes_to_friend(self):
        m = self.v
        m.mem.update_relation("Arkady", 2)
        m.mem.update_relation("Arkady", 2)                # друг: шанс выше
        hits = 0
        for i in range(100):
            m.social.spot = None
            m.social.rng = random.Random(i)
            name, why = m.social.pick_point({"map": "prontera", "players": [{"name": "Arkady", "x": 239, "y": 311}]})
            hits += name == "church" and why.startswith("там")
        self.assertGreater(hits, 50)

    # ---------- разговоры ----------

    def test_dialogue_two_exchanges(self):
        self.meet()
        self.tick(self.a, self.v)                         # Arkady (меньшее имя) заговаривает первым
        first = self.a.actions("whisper")
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0]["text"].endswith("[chat:hello:1]"))
        self.assertEqual(self.v.actions("whisper"), [])   # Vera ждёт, не начинает одновременно
        self.assertIn({"action": "emote", "id": 12, "emotion": 12}, self.a.out)   # приветствие с эмоцией
        log = self.converse()
        steps = [int(TAG.search(w["text"]).group(2)) for w in log]
        self.assertEqual(steps, [1, 2, 3, 4])             # 2 обмена и тишина
        self.assertEqual(TAG.search(log[-1]["text"]).group(1), "bye")
        for w in log:
            self.assertLessEqual(len(TAG.sub("", w["text"]).strip()), 80)
        # через 10 минут — не повторяют (не чаще раза в 15 мин на пару)
        self.clock.t += 10 * 60
        self.tick(self.a, self.v)
        self.assertEqual(self.a.actions("whisper") + self.v.actions("whisper"), [])

    def test_pair_gap_depends_on_relation(self):
        self.meet()
        self.tick(self.a)
        self.converse()
        self.clock.t += 16 * 60                           # не друзья: 15 * 1.5 = 22.5 мин
        self.tick(self.a)
        self.assertEqual(self.a.actions("whisper"), [])
        for _ in range(2):
            self.a.mem.update_relation("Vera", 2)         # друзья (affinity 4): раз в 15 мин
        self.tick(self.a)
        self.assertEqual(len(self.a.actions("whisper")), 1)

    def test_no_reply_after_last_step_and_loop_guard(self):
        self.run_(self.v.social.on_tag("Arkady", "Пока [chat:bye:4]"))
        self.clock.t += 20
        self.tick(self.v)
        self.assertEqual(self.v.actions("whisper"), [])
        for i in range(5):                                 # навязчивые шаги 1 подряд — не больше 2 ответов
            self.run_(self.v.social.on_tag("Arkady", f"Привет {i} [chat:hello:1]"))
            self.clock.t += 20
            self.tick(self.v)
        self.assertEqual(len(self.v.actions("whisper")), 2)

    def test_ignores_strangers_tags(self):
        self.run_(self.v.social.on_tag("Stranger", "Привет [chat:hello:1]"))
        self.clock.t += 20
        self.tick(self.v)
        self.assertEqual(self.v.out, [])

    def test_no_repeated_phrases(self):
        s = self.a.social
        facts = s.facts("Vera")
        options = self.a.persona["phrases"]["hello"]
        got = [s.phrase("hello", facts) for _ in range(len(options))]
        self.assertEqual(len(set(got)), len(options))      # все варианты, прежде чем повторить
        nxt = s.phrase("hello", facts)
        self.assertNotEqual(nxt, got[-1])                  # и не та же подряд

    def test_llm_gives_reason_instead_of_template(self):
        self.meet()
        self.a.s.llm_enabled = True
        self.tick(self.a)
        self.assertEqual(self.a.actions("whisper"), [])
        self.assertEqual(len(self.a.triggers), 1)
        self.assertEqual(self.a.triggers[0][2], "chat")
        self.assertEqual(self.a.triggers[0][1], {"from": "Vera"})
        self.run_(self.a.social.on_tag("Vera", "Привет! [chat:hello:1]"))
        self.clock.t += 20
        self.tick(self.a)
        self.assertEqual(self.a.actions("whisper"), [])
        self.assertIn("Vera (житель) пишет мне", self.a.triggers[-1][0])

    def test_night_is_quieter(self):
        self.clock.t = at_hour(3)
        self.a.state["sitting"] = False
        rest = self.a.routine.town                       # ночью сидят у точки отдыха (R2: она не у фонтана)
        self.a.state.update(x=rest["x"], y=rest["y"])
        self.tick(self.a)
        self.assertEqual(self.a.actions(), [{"action": "sit"}])   # ночью сидят
        self.a.out.clear()
        self.clock.t += 60 * 60
        self.tick(self.a)
        self.assertEqual(self.a.actions("meet_point"), [])         # и не гуляют
        self.assertGreaterEqual(self.a.social.pair_gap("Vera", self.clock.t),
                                3 * self.a.social.pair_gap("Vera", at_hour(14)))

    def test_busy_no_smalltalk(self):
        self.meet()
        self.a.life.current = "FIGHTING"
        self.tick(self.a)
        self.assertEqual(self.a.actions("whisper"), [])

    # ---------- эмоции и safety ----------

    def test_emote_safety(self):
        ok, why = self.a.safety.check({"action": "emote", "id": 12}, self.a.state, protocol=True)
        self.assertEqual(ok, {"action": "emote", "id": 12, "emotion": 12})   # society: emotion — для моста
        self.assertIsNotNone(self.a.safety.check({"action": "emote", "id": 12}, self.a.state)[1])   # не модели
        self.assertIsNotNone(self.a.safety.check({"action": "emote", "id": 6}, self.a.state, protocol=True)[1])
        self.assertIsNotNone(self.a.safety.check({"action": "emote", "id": "12"}, self.a.state, protocol=True)[1])
        self.assertIsNotNone(self.a.safety.check({"action": "emote", "id": 12}, {"dead": True}, protocol=True)[1])
        sp = SafetyPolicy(["prt_fild08"])
        now = time.time()
        res = [sp.check({"action": "emote", "id": 3}, {}, now=now + i, protocol=True)[1] for i in range(8)]
        self.assertEqual(sum(r is None for r in res), 6)                # лимит эмоций за 10 минут

    # ---------- факты памяти и реакции ----------

    def test_facts_from_memory(self):
        m = self.a
        for _ in range(3):
            m.mem.add_event("kill", {"monster": "Poring", "map": "prt_fild08"})
        m.mem.add_event("loot", {"item": "Jellopy", "amount": 1})
        m.mem.add_event("level_up", {"level": 31})
        m.mem.add_event("died", {"map": "prt_fild07"})
        f = m.social.facts("Vera")
        self.assertEqual((f["kills"], f["loot"], f["lv"], f["death_map"], f["hours"]), (3, 1, 31, "prt_fild07", 3))
        self.assertEqual(m.social.choose_topic("Vera", f, self.clock.t), "death")   # гибель — первой
        self.assertEqual(m.social.choose_topic("Vera", f, self.clock.t), "level")
        text = m.social.phrase("death", f)
        self.assertIn("prt_fild07", text)
        text = m.social.phrase("hunt", f)
        self.assertIn("3", text)
        # о чём уже рассказал сегодня — не повторяет
        rest = {m.social.choose_topic("Vera", f, self.clock.t) for _ in range(3)}
        self.assertEqual(rest, {"hunt", "loot", "tired"})
        self.assertEqual(m.social.choose_topic("Vera", f, self.clock.t), "weather")

    def test_topic_step_uses_facts(self):
        self.meet()
        self.a.routine.st["hunted"] = 0                    # у Arkady за день нет фактов — погода
        self.v.mem.add_event("level_up", {"level": 32})
        self.tick(self.a)
        log = self.converse()
        third = log[2]["text"]                             # Arkady: тема по фактам (у него фактов нет)
        self.assertIn("[chat:weather:3]", third)
        # Vera рассказывает о своём уровне, Arkady поздравляет
        self.v.out.clear()
        self.run_(self.v.social.on_level_up({"level": 32}))
        lv = self.v.actions("whisper")
        self.assertEqual(len(lv), 1)
        self.assertIn("32", lv[0]["text"])
        self.assertTrue(lv[0]["text"].endswith("[chat:level:3]"))
        self.deliver(self.v, self.a)
        self.clock.t += 10
        self.tick(self.a)
        self.assertTrue(self.a.actions("whisper")[0]["text"].endswith("[chat:congrats:4]"))

    def test_reactions(self):
        a = self.a
        self.run_(a.social.on_support({"kind": "support", "skill": "AL_HEAL", "from": "Vera", "to": "Arkady",
                                       "amount": 120}))
        thanks = a.actions("whisper")
        self.assertEqual(len(thanks), 1)
        self.assertTrue(thanks[0]["text"].endswith("[chat:thanks:4]"))
        self.run_(a.social.on_support({"kind": "support", "skill": "AL_HEAL", "from": "Vera", "to": "Arkady",
                                       "amount": 90}))
        self.assertEqual(len(a.actions("whisper")), 1)     # не чаще react_gap
        self.clock.t += 11                                 # safety: одному жителю не чаще раза в 10 с
        self.run_(a.social.on_peer_dead("Vera"))
        cond = a.actions("whisper")[-1]
        self.assertTrue(cond["text"].endswith("[chat:condolence:4]"))
        # уровень жителя вырос по данным игры — поздравить
        a.state["players"] = [{"name": "Vera", "x": 100, "y": 100, "lv": 30}]
        self.tick(a)
        a.state["players"] = [{"name": "Vera", "x": 100, "y": 100, "lv": 31}]
        self.clock.t += 11
        self.tick(a)
        congrats = a.actions("whisper")[-1]["text"]
        self.assertTrue(congrats.endswith("[chat:congrats:4]"))

    def test_together_affinity_once_per_day(self):
        self.meet()
        a = self.a
        a.routine.st["mode"] = "hunt"                     # и на охоте рядом — тоже вместе
        for _ in range(31 * 60 // 5):
            self.clock.t += 5
            self.tick(a)
        self.assertEqual(a.mem.relation("Vera")["affinity"], 1)
        for _ in range(60 * 60 // 5):
            self.clock.t += 5
            self.tick(a)
        self.assertEqual(a.mem.relation("Vera")["affinity"], 1)   # не чаще раза в сутки

    def test_ignored_message_changes_nothing(self):
        self.meet()
        self.tick(self.a)                                  # Arkady поздоровался, Vera не ответила
        self.clock.t += 60
        self.tick(self.a)
        self.assertIsNone(self.a.mem.relation("Vera"))


class MindRoutingTest(unittest.TestCase):
    """mind.py: метка [chat:] идёт в social, а не в gate/LLM; эмоция проходит в тело."""

    def setUp(self):
        shift_time(self)                     # timefix: полдень мира — ночью (1–7 ч) social не шлёт эмоции
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(action):
            self.sent.append(action)
            return len(self.sent)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=WORLD)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_tag_routed_to_social(self):
        self.assertIsNotNone(self.mind.social)
        asyncio.run(self.mind.on_message({"type": "event", "kind": "chat_private", "from": "Vera",
                                          "text": "Привет, Arkady! [chat:hello:1]"}))
        self.assertIsNone(self.mind.pending)                       # не повод для LLM
        self.assertEqual(len(self.mind.social.queue), 1)           # ответ по правилу, с задержкой
        self.mind.social.queue[0] = (0,) + self.mind.social.queue[0][1:]
        self.mind.state = {"name": "Arkady", "map": "prontera", "x": 156, "y": 185, "dead": False}
        self.mind.fresh_state = True
        asyncio.run(self.mind.social.tick())
        kinds = [a["action"] for a in self.sent]
        self.assertIn("whisper", kinds)
        self.assertIn("emote", kinds)
        w = next(a for a in self.sent if a["action"] == "whisper")
        self.assertTrue(w["text"].endswith("[chat:hello:2]"))


if __name__ == "__main__":
    unittest.main()
