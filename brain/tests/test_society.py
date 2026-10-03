"""Социальная ткань (society.py, ORG-022/026/027): эмоции на события, чат-комнаты-вывески, ссоры и примирения.

Всё — на настоящем Mind (safety, планы, группа, общение) с поддельным телом: действия, ушедшие в мост,
собираются в self.sent. Время модуля — подменные часы; события памяти пишутся настоящим временем.
Запуск: cd brain && python3 -m unittest -v tests.test_society
"""
import asyncio
import json
from tests.persona_fixture import prontera_persona
import random
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain.bonds import Bonds
from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.party import Party
from live_brain.routine import load_world
from live_brain.safety import SafetyPolicy
from live_brain.society import Society, fit_title

from tests.worldtime import no_quiet_days

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZ = timezone(timedelta(hours=WORLD["timezone_offset_hours"]))
DAY = 86400


def at_hour(hour):
    return datetime.now(TZ).replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class SocietyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        persona = prontera_persona()
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=WORLD)
        self.dec = root / "d.jsonl"
        self.clock = Clock(at_hour(14))
        self.s = Society(self.mind, WORLD, clock=self.clock, rng=random.Random(5))
        no_quiet_days(self.mind)                         # hush: тест не о тихих днях (ORG-110)
        self.mind.society = self.s
        self.mind.social.clock = self.clock
        self.state(map="prontera", x=156, y=185, lock_map="prontera", lock_x=156, lock_y=185)
        r = self.mind.routine
        r.new_day(self.clock.t)
        r.st.update(mode="town", arrived=True, rest_until=self.clock.t + 3 * 3600, mode_since=self.clock.t)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    # ---------- помощники ----------

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prt_fild08", "x": 100, "y": 100, "hp_pct": 100, "lv": 41,
             "job": "Swordsman", "dead": False, "weight_pct": 20, "zeny": 60000, "items": {"501": 30}, "players": []}
        s.update(getattr(self, "_last", {}))
        s.update(kw)
        self._last = {k: v for k, v in s.items() if k != "type"}
        asyncio.run(self.mind.on_message(s))

    def event(self, kind, **data):
        asyncio.run(self.mind.on_message({"type": "event", "kind": kind, **data}))

    def tick(self):
        asyncio.run(self.s.tick())

    def vera_near(self, x=158, y=186):
        self.state(players=[{"name": "Vera", "x": x, "y": y, "lv": 40}])

    def actions(self, kind):
        return [a for a in self.sent if a["action"] == kind]

    def decisions(self, kind=None):
        rows = [json.loads(l) for l in self.dec.read_text(encoding="utf-8").splitlines()] if self.dec.exists() else []
        return [r for r in rows if r.get("type") == "society" and (kind is None or r.get("event") == kind)]

    def aff(self):
        return (self.mem.relation("Vera") or {}).get("affinity", 0)

    def refuse(self, why="самому мало"):
        """Я попросил у Vera, она отказала (шёпот тела с меткой [need:..:no])."""
        self.mem.add_event("gift_asked", {"peer": "Vera", "item": "501", "amount": 5})
        self.event("chat_private", **{"from": "Vera", "text": f"Извини, не могу: {why}. [need:ab12:no]"})
        self.tick()

    # ---------- эмоции (ORG-022) ----------

    def test_emote_on_trade_only_when_peer_near(self):
        self.mem.add_event("trade_sold", {"peer": "Vera", "item": "909", "amount": 10, "price": 500})
        self.tick()
        self.assertEqual(self.actions("emote"), [], "Vera не видна — эмоцию никто не увидит")
        self.vera_near()
        self.mem.add_event("trade_sold", {"peer": "Vera", "item": "909", "amount": 10, "price": 500})
        self.tick()
        em = self.actions("emote")
        self.assertEqual(len(em), 1)
        self.assertEqual(em[0]["emotion"], 18, "удачная сделка — heh; номер в поле emotion (id — номер действия)")
        self.assertEqual(self.mem.count_events("society_emote", 0), 1, "эмоция связана с событием в памяти")

    def test_emote_gap_shared_with_social_and_same_emote_limit(self):
        self.vera_near()
        self.mind.social.last_emote = self.clock.t - 30            # social только что махнул рукой
        self.mem.add_event("gift_received", {"peer": "Vera", "item": "501", "amount": 5})
        self.tick()
        self.assertEqual(self.actions("emote"), [], "общий промежуток с social.py")
        self.clock.t += 200
        self.mem.add_event("meeting_confirmed", {"partner": "Vera", "plan": "x", "map": "prontera"})
        self.tick()
        self.assertEqual([a["emotion"] for a in self.actions("emote")], [12], "встреча — wav")
        self.assertEqual(self.mind.social.last_emote, self.clock.t, "social тоже знает о моей эмоции")
        self.clock.t += 200
        self.mem.add_event("meeting_confirmed", {"partner": "Vera", "plan": "y", "map": "prontera"})
        self.tick()
        self.assertEqual(len(self.actions("emote")), 1, "та же эмоция тому же жителю — не чаще 10 мин")

    def test_friend_death_nearby_sob_even_at_night(self):
        self.clock.t = at_hour(3)
        self.vera_near()
        self.mem.add_event("trade_bought", {"peer": "Vera", "item": "909", "amount": 1, "price": 5})
        self.tick()
        self.assertEqual(self.actions("emote"), [], "ночью — без весёлых эмоций")
        self.mem.add_event("party_member_dead", {"who": "Vera", "map": "prontera", "can_resurrect": False})
        self.tick()
        self.assertEqual([a["emotion"] for a in self.actions("emote")], [28], "гибель друга рядом — sob")

    def test_history_before_start_is_not_replayed(self):
        self.vera_near()
        self.mem.add_event("gift_received", {"peer": "Vera", "item": "501", "amount": 5})
        s2 = Society(self.mind, WORLD, clock=self.clock)
        self.mind.society = s2
        asyncio.run(s2.tick())
        self.assertEqual(self.actions("emote"), [], "старые события — не повод")

    # ---------- ссоры (ORG-027) ----------

    def test_refusal_drops_relation_busy_does_not(self):
        self.refuse("иду на встречу")
        self.assertEqual(self.aff(), 0, "занят — не обида")
        self.refuse("самому мало")
        self.assertEqual(self.aff(), -1)
        self.assertEqual(len(self.decisions("society_relation_drop")), 1)
        self.event("chat_private", **{"from": "Vera", "text": "Нет. [need:cd34:no]"})
        self.tick()
        self.assertEqual(self.aff(), -1, "отказ без моей просьбы (чужой rid, давно) — не в счёт")

    def test_two_refusals_make_quarrel_and_party_skips(self):
        party = Party(self.mind)
        self.assertIn("Vera", party.mates())
        self.refuse()
        self.assertFalse(self.s.quarrel("Vera"))
        self.refuse("этим не делюсь")
        self.assertEqual(self.aff(), -2)
        self.assertTrue(self.s.quarrel("Vera"), "affinity <= -2 после события — ссора")
        self.assertEqual(len(self.decisions("society_quarrel")), 1)
        self.assertNotIn("Vera", party.mates(), "в ссоре — в группу не зовём (HOSTILE −3 ещё не достигнут)")
        self.refuse()
        self.assertEqual(self.aff(), -2, "не больше drops_per_day снижений в сутки")
        self.assertTrue(self.decisions("drop_capped"))
        self.assertIn("в_ссоре", self.mind.build_prompt("x", {})[1]["content"])

    def test_debt_already_counted_by_economy_only_checks_threshold(self):
        self.mem.update_relation("Vera", -2, "недоплатил за товар")         # economy.py: −1 (и было −1)
        self.mem.add_event("trade_debt", {"peer": "Vera", "item": "909", "amount": 10, "price": 900, "paid": 100})
        self.tick()
        self.assertEqual(self.aff(), -2, "второй раз не снижаем")
        self.assertTrue(self.s.quarrel("Vera"))

    def test_broken_meeting_after_agreement_drops(self):
        store = self.mind.plans.store
        p = store.create(id="aa01", kind="meet", partner="Vera", role="proposer", map="prontera", x=150, y=180,
                         status="planned", phase="awaiting_answer")
        store.update("aa01", note="Vera согласился", status="executing", phase="moving")
        store.update("aa01", note="x", status="failed", phase=None, result="не дождался Vera у точки за 240 с")
        self.mem.add_event("plan_failed", {"plan": p["id"], "partner": "Vera", "map": "prontera", "x": 150, "y": 180})
        q = store.create(id="aa02", kind="meet", partner="Vera", role="proposer", map="prontera", x=150, y=180,
                         status="failed", phase=None, result="Vera не ответил за 120 с")
        self.mem.add_event("plan_failed", {"plan": q["id"], "partner": "Vera", "map": "prontera", "x": 150, "y": 180})
        self.tick()
        self.assertEqual(self.aff(), -1, "не пришёл после согласия — −1; не ответил (офлайн?) — нет")

    def test_party_refused_event_from_body(self):
        self.event("party_refused", name="Vera", code=1)
        self.tick()
        self.assertEqual(self.aff(), -1)

    def test_reconcile_after_cooling_with_occasion(self):
        self.vera_near()
        self.refuse()
        self.refuse("этим не делюсь")
        self.assertTrue(self.s.quarrel("Vera"))
        self.assertEqual([a["emotion"] for a in self.actions("emote")], [9], "ссора — «...»")
        self.mem.add_event("gift_received", {"peer": "Vera", "item": "501", "amount": 5})
        self.tick()
        self.assertTrue(self.s.quarrel("Vera"), "повод есть, но ещё не остыл")
        self.clock.t += 2 * DAY + 60
        self.tick()
        self.assertFalse(self.s.quarrel("Vera"))
        self.assertEqual(self.aff(), 0, "шаг к нулю (до +2)")
        sorry = [a for a in self.actions("whisper") if "[chat:sorry:4]" in a["text"]]
        self.assertEqual(len(sorry), 1, "реплика-извинение с меткой, на неё не отвечают")
        self.assertEqual(self.decisions("society_reconciled")[0]["occasion"], "подарок от него")
        self.assertIn(17, [a["emotion"] for a in self.actions("emote")], "примирение — sry")
        self.assertIn("Vera", Party(self.mind).mates(), "после примирения — снова в группе")

    def test_new_conflict_resets_cooling_and_occasion(self):
        self.vera_near()
        self.refuse()
        self.refuse("этим не делюсь")
        self.mem.add_event("gift_given", {"peer": "Vera", "item": "501", "amount": 5})
        self.tick()
        self.clock.t += DAY + 3600
        self.refuse("этим не делюсь")                                    # новый день — новый конфликт
        self.clock.t += DAY + 3600
        self.tick()
        self.assertTrue(self.s.quarrel("Vera"), "повод был до нового конфликта, остывание заново")

    def test_apology_from_peer_is_occasion(self):
        self.refuse()
        self.refuse("этим не делюсь")
        self.clock.t += 2 * DAY + 60
        self.event("chat_private", **{"from": "Vera", "text": "Прости, Arkady. Мир? [chat:sorry:4]"})
        self.vera_near()
        self.tick()
        self.assertFalse(self.s.quarrel("Vera"))

    def test_social_cold_in_quarrel(self):
        self.refuse()
        self.refuse("этим не делюсь")
        social = self.mind.social
        self.assertFalse(asyncio.run(social.say("Vera", "hello", 1)), "первым не пишу")
        self.assertTrue(asyncio.run(social.say("Vera", "weather", 3)))
        w = self.actions("whisper")[-1]
        self.assertTrue(w["text"].endswith("[chat:cold:4]"), "холодно и без продолжения")
        self.assertLess(len(w["text"]), 40)

    def test_chronicle_lines(self):
        for kind in ("society_quarrel", "society_reconciled", "society_room_opened", "society_relation_drop"):
            self.assertIn(kind, LINES)
        self.assertEqual(LINES["society_quarrel"]({"peer": "Vera", "cause": "отказал в просьбе"}),
                         "поссорился с Vera: отказал в просьбе")

    # ---------- чат-комнаты (ORG-026) ----------

    def open_room(self):
        self.s.cfg["room_chance"] = 1.0
        self.mind.social.next_walk = self.clock.t + 3600
        self.tick()                                      # первый тик — отсчёт
        self.clock.t += 91 * 60
        self.mind.social.next_walk = self.clock.t + 3600
        self.tick()
        return self.actions("chat_room")

    def test_room_open_confirm_close_before_hunt(self):
        rooms = self.open_room()
        self.assertEqual(len(rooms), 1)
        self.assertEqual(rooms[0]["op"], "open")
        title = rooms[0]["title"]
        self.assertLessEqual(len(title.encode("utf-8")), 36)
        self.assertNotIn("#", title)
        self.state(chat_room=title)
        self.tick()
        self.assertEqual(len(self.decisions("society_room_opened")), 1, "открыта — по данным тела")
        self.mind.routine.st["rest_until"] = self.clock.t + 120          # скоро на охоту
        self.tick()
        self.assertEqual(self.actions("chat_room")[-1], {"action": "chat_room", "op": "close"})
        self.assertIn("скоро на охоту", self.decisions("society_room_closed")[0]["why"])

    def test_room_not_confirmed_is_failure(self):
        self.open_room()
        self.clock.t += 61
        self.tick()
        self.assertEqual(len(self.decisions("society_room_failed")), 1)
        self.assertIsNone(self.s.room)

    def test_room_closed_by_body_before_movement(self):
        rooms = self.open_room()
        self.state(chat_room=rooms[0]["title"])
        self.tick()
        self.state(chat_room=None)                                       # мост закрыл перед движением
        self.tick()
        self.assertEqual(self.decisions("society_room_closed")[0]["why"], "тело")

    def test_no_room_outside_town_or_with_plan(self):
        self.state(map="prt_fild08")
        self.assertEqual(self.open_room(), [])
        self.state(map="prontera")
        self.s.next_room = None
        self.mind.plans.store.create(id="bb01", kind="meet", partner="Vera", role="proposer", map="prontera",
                                     x=150, y=180, status="planned", phase="awaiting_answer")
        self.assertEqual(self.open_room(), [])

    def test_title_signs(self):
        self.s.cfg["room_sign_chance"] = 1.0
        titles = {self.s.title({"party": None, "items": {}}) for _ in range(20)}
        self.assertTrue(any(t.startswith("Ищу группу на ") for t in titles), titles)
        self.assertTrue(all(len(t.encode("utf-8")) <= 36 for t in titles))
        self.s.cfg["room_sign_chance"] = 0.0
        self.assertEqual(self.s.title({"party": None}), "Отдыхаю")

    def test_fit_title(self):
        self.assertEqual(fit_title("Ищу группу на prt_fild08"), "Ищу группу на prt_fild08")
        long = fit_title("Продаю Очень Длинное Название Предмета Номер")
        self.assertLessEqual(len(long.encode("utf-8")), 36)
        self.assertFalse(long.endswith(" "))
        self.assertEqual(fit_title('Продаю "#1"'), "Продаю 1")

    def test_safety_chat_room(self):
        sp = SafetyPolicy(["prt_fild08"], extra_point_maps=["prontera"])
        town = {"map": "prontera"}
        ok = sp.check({"action": "chat_room", "op": "open", "title": "Отдыхаю", "limit": 5}, town, now=1000,
                      protocol=True)
        self.assertEqual(ok, ({"action": "chat_room", "op": "open", "title": "Отдыхаю", "limit": 5}, None))
        self.assertIsNotNone(sp.check({"action": "chat_room", "op": "open", "title": "Отдыхаю"}, town,
                                      now=1100, protocol=True)[1], "не чаще раза в 10 мин")
        self.assertIsNotNone(sp.check({"action": "chat_room", "op": "open", "title": "Отдыхаю"},
                                      {"map": "prt_fild08"}, now=5000, protocol=True)[1], "только в городе")
        self.assertIsNotNone(sp.check({"action": "chat_room", "op": "open", "title": "Я" * 19}, town,
                                      now=9000, protocol=True)[1], "38 байт")
        self.assertIsNotNone(sp.check({"action": "chat_room", "op": "open", "title": "a#b"}, town,
                                      now=9000, protocol=True)[1], "#")
        self.assertIsNotNone(sp.check({"action": "chat_room", "op": "open", "title": "Отдыхаю"}, town,
                                      now=9000)[1], "модели — нельзя")
        self.assertIsNone(sp.check({"action": "chat_room", "op": "close"}, {"dead": True}, protocol=True)[1],
                          "закрыть — всегда")

    # ---------- поза: посидеть вместе (ORG-022 + ORG-023) ----------

    def test_sit_together_keeps_body_at_meeting_point(self):
        b = Bonds(self.mind, clock=self.clock)
        asyncio.run(b.after_meeting({"partner": "Vera", "map": "prontera", "x": 150, "y": 180}))
        r = self.mind.routine
        self.assertEqual((r.town["x"], r.town["y"]), (150, 180), "распорядок держит тело у точки встречи")
        self.assertIn({"action": "sit"}, self.sent, "sit — без команды sit OpenKore (мост: sitAuto_idle)")
        self.clock.t += 16 * 60
        b.check_together(self.clock.t)
        self.assertEqual(r.town, r.cfg["town"], "после — обычная точка отдыха")


if __name__ == "__main__":
    unittest.main()
