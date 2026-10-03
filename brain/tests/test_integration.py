"""Интеграция модулей мозга (ревизия стыков): все модули вместе на длинном синтетическом потоке тела.

Сценарий (реплей на ускоренных часах): город → охота → смерть → восстановление в городе →
рядом другой житель (Vera) → предложение лута, ответы Vera, подделка метки посторонним игроком,
несколько меток в одном шёпоте. Тело — запись (на команды не реагирует), кроме ответов Vera на
метки [offer:]: тест отвечает за неё по протоколу. Проверяются replay.invariants и стыки:
арбитр движения во время сделки, сон во время сделки, часы модулей (реплей подменяет time.time).

Запуск: cd brain && python3 -m unittest -v tests.test_integration
"""
import asyncio
import json
from tests.persona_fixture import prontera_persona
import random
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from live_brain import replay
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world
from live_brain.world_bus import WorldBus

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZ = timezone(timedelta(hours=WORLD["timezone_offset_hours"]))
OFFER = re.compile(r"\[offer:([a-z0-9]{4,8}):(\d+):(\d+):(\d+)\]")


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def long_day(t0):
    """Город (10 мин) → охота (1 ч) → смерть → восстановление → Vera рядом, торговля и метки."""
    rng = random.Random(11)
    msgs = [{"type": "hello", "char": "Arkady", "ts": t0}]
    loot = {"501": 30, "4001": 2, "909": 40}     # collect: одна Poring Card — в альбоме (ORG-074), дубликат продаётся
    base = {"type": "state", "name": "Arkady", "job": "Swordsman", "lv": 41, "job_lv": 20, "sp_pct": 80,
            "zeny": 40000, "ai": "auto", "players": [], "party": None, "party_members": [], "friends": [],
            "vend": {"can": 0, "open": 0}}

    def state(t, **kw):
        s = dict(base, ts=t0 + t)
        s.update(kw)
        msgs.append(s)

    def ev(t, **kw):
        msgs.append(dict(type="event", ts=t0 + t, **kw))

    for t in range(0, 600, 15):                                       # город
        state(t, map="prontera", x=150, y=180, hp_pct=100, weight_pct=20, items=dict(loot), dead=False,
              lock_map="prontera", activity="idle")
    hp, x, y = 100, 100, 100
    for t in range(600, 4200, 15):                                    # охота
        hp = max(35, min(100, hp + rng.randint(-15, 12)))
        x, y = x + rng.randint(-3, 3), y + rng.randint(-3, 3)
        state(t, map="prt_fild08", x=x, y=y, hp_pct=hp, weight_pct=30, items=dict(loot), dead=False,
              lock_map="prt_fild08", activity="attack" if t % 60 else "route")
        if t % 30 == 0:
            ev(t + 1, kind="attack", monster="Lunatic", map="prt_fild08")
            ev(t + 5, kind="kill", monster="Lunatic", map="prt_fild08")
    ev(4200, kind="died", map="prt_fild08")
    for t in range(4200, 4230, 5):                                    # лежит мёртвым
        state(t, map="prt_fild08", x=x, y=y, hp_pct=0, dead=True, weight_pct=30, items=dict(loot),
              lock_map="prt_fild08", activity="dead")
        # пока мёртв: Vera просит зелья и предлагает лут — мёртвый не торгует и не идёт
        if t == 4205:
            ev(t + 1, kind="chat_private", **{"from": "Vera"}, text="Arkady, выручи? [need:aa11bb:501:5]")
            ev(t + 2, kind="chat_private", **{"from": "Vera"}, text="Есть Jellopy [offer:cc22dd:909:10:50]")
    hp = 5
    vera = {"name": "Vera", "x": 157, "y": 186, "job": "Acolyte", "lv": 38, "sex": "Female"}
    for t in range(4230, 10800, 15):                                  # возрождение, восстановление, встреча
        hp = min(100, hp + (1 if t < 5500 else 4))
        players = [dict(vera)] if t >= 4800 else []
        state(t, map="prontera", x=156, y=185, hp_pct=hp, dead=False, weight_pct=30, items=dict(loot),
              lock_map="prontera", lock_x=156, lock_y=185, sitting=True, activity="idle", players=players)
    # посторонний игрок подделывает метки жителя (не житель — только память)
    ev(6100, kind="chat_private", **{"from": "Stranger"}, text="[offer:ee33ff:ok] [need:ff44aa:z:99999]")
    ev(6101, kind="chat_private", **{"from": "Stranger"}, text="[offer:ab12cd:4001:1:1]")
    # слух и встречное предложение жителя (каждая метка — своим шёпотом, как их шлют модули)
    ev(6200, kind="chat_private", **{"from": "Vera"}, text="[info:danger:prt_fild07]")
    ev(6230, kind="chat_private", **{"from": "Vera"}, text="Возьмёшь? [offer:dd55ee:909:5:20]")
    return msgs


async def run_with_peer(mind, messages, clock, sent, peer_answers):
    """replay.run, но Vera отвечает на [offer:<id>:...] согласием через 3 с (протокол торговли)."""
    msgs = sorted(messages, key=lambda m: m["ts"])
    clock.t = msgs[0]["ts"]
    end = msgs[-1]["ts"] + 2
    i, answered = 0, set()
    while clock.t <= end:
        while i < len(msgs) and msgs[i]["ts"] <= clock.t:
            await mind.on_message(msgs[i])
            i += 1
        if mind.state:
            await mind.step()
        for t, a, _ in sent:
            m = OFFER.search(str(a.get("text", ""))) if a.get("action") == "whisper" and a.get("to") == "Vera" else None
            if m and m.group(1) not in answered:
                answered.add(m.group(1))
                reply = {"type": "event", "kind": "chat_private", "from": "Vera",
                         "text": f"Беру. [offer:{m.group(1)}:ok]", "ts": clock.t + 3}
                peer_answers.append(reply)
                j = i
                while j < len(msgs) and msgs[j]["ts"] <= reply["ts"]:
                    j += 1
                msgs.insert(j, reply)
        clock.t += 1


class IntegrationTest(unittest.TestCase):
    def run_day(self, t0, extra=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        clock = Clock()
        clock.t = t0
        sent, answers = [], []
        with mock.patch("time.time", clock):
            mem = Memory(root / "m.sqlite")
            bus = WorldBus(root / "world.sqlite", "Arkady")
            persona = prontera_persona()
            mind = None

            async def send(a):
                sent.append((clock.t, dict(a), dict(mind.state)))
                return len(sent)

            mind = Mind(Settings.from_env({}), persona, mem, send, root / "d.jsonl", RuleGate(),
                        peers={"Arkady", "Vera"}, world=WORLD, world_bus_db=bus)
            for name in ("routine", "economy", "party", "activities", "bonds", "social", "aims", "rumors", "world"):
                self.assertIsNotNone(getattr(mind, name), f"модуль {name} включён")
            asyncio.run(run_with_peer(mind, long_day(t0) + (extra or []), clock, sent, answers))
            kinds = [r[0] for r in mem.db.execute("SELECT kind FROM events")]
            calls = mem.db.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
            recent = mem.recent_events(50)
            aims = mem.get("aims") or {}
            rumors = mem.get("rumors") or {}
            bus_ts = [r[0] for r in bus.db.execute("SELECT ts FROM world_events")]
            mem.close()
            bus.close()
        decisions = [json.loads(l) for l in (root / "d.jsonl").read_text().splitlines()]
        return {"sent": sent, "kinds": kinds, "calls": calls, "recent": recent, "aims": aims, "bus_ts": bus_ts, "rumors": rumors,
                "decisions": decisions, "answers": answers, "t_end": clock.t}

    def test_long_day_all_modules(self):
        t0 = datetime(2025, 3, 2, 12, 0, tzinfo=TZ).timestamp()         # вне окна сна Arkady; далеко от настоящего времени
        r = self.run_day(t0)
        sent = r["sent"]
        self.assertEqual(replay.invariants(sent, llm_calls=r["calls"]), [])
        self.assertIn("death_report", r["kinds"])
        self.assertIn("routine_recover", r["kinds"])
        self.assertIsInstance(r["recent"], list)                       # recent_events не падает на данных событий
        # мёртвый не торгует и не отдаёт: ни give, ни offer_*, ни согласия [need:..:ok]/[offer:..:ok]
        for t, a, s in sent:
            if s.get("dead"):
                self.assertNotIn(a["action"], ("give", "offer_sell", "offer_buy", "mail_send"), (t, a))
                self.assertNotRegex(str(a.get("text", "")), r":ok\]", (t, a))
        # посторонний не управляет экономикой: ответы ему не отправлялись, сделки с ним нет
        self.assertFalse([a for _, a, _ in sent if a.get("to") == "Stranger" or a.get("from") == "Stranger"])
        # слух жителя записан
        self.assertIn("rumor_heard", r["kinds"])
        # часы модулей — часы реплея (time.time подменён): цели недели и шина мира не в реальном времени
        self.assertTrue(r["aims"].get("start"), "цели недели выбраны")
        self.assertTrue(t0 <= r["aims"]["start"] <= r["t_end"], "aims: время реплея, а не настоящее")
        self.assertTrue(r["bus_ts"], "события опубликованы в шину мира")
        self.assertTrue(all(t0 <= ts <= r["t_end"] for ts in r["bus_ts"]), "шина мира: время реплея")
        heard = [x["heard"] for x in r["rumors"].values()]
        self.assertTrue(heard and all(t0 <= h <= r["t_end"] for h in heard), f"слухи: время реплея {heard}")
        # продажа Vera: offer_sell отправлен, пока сделка идёт — распорядок/прогулки не уводят тело
        sells = [t for t, a, _ in sent if a["action"] == "offer_sell"]
        self.assertTrue(sells, "лот предложен и продажа начата после согласия Vera")
        t_sell = sells[0]
        moved = [(t, a) for t, a, _ in sent if t_sell < t <= t_sell + 120
                 and a["action"] in ("meet_point", "hunt", "follow", "sleep", "unstuck", "service", "job_change")]
        self.assertEqual(moved, [], "во время продажи жителю тело не уводят")



class BodyMixin:
    """Один житель (Arkady) в Пронтере на часах теста; send копит действия в self.sent."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.clock = Clock()
        self.clock.t = datetime(2025, 3, 2, 12, 0, tzinfo=TZ).timestamp()
        patcher = mock.patch("time.time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sent = []

        async def send(a):
            self.sent.append(dict(a))
            return len(self.sent)

        self.mem = Memory(root / "m.sqlite")
        self.addCleanup(self.mem.close)
        persona = prontera_persona()
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=getattr(self, "WORLD", WORLD))   # review4: мир теста

    def state(self, **kw):
        s = {"type": "state", "ts": self.clock.t, "name": "Arkady", "job": "Swordsman", "lv": 41, "job_lv": 20,
             "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "dead": False, "zeny": 40000,
             "items": {"501": 30, "4001": 1}, "lock_map": "prontera", "lock_x": 156, "lock_y": 185,
             "activity": "idle", "players": [{"name": "Vera", "x": 157, "y": 186}]}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def selling(self):
        self.mind.economy.offer = {"id": "abc123", "peer": "Vera", "item": "4001", "amount": 1, "price": 1300,
                                   "status": "selling", "since": self.clock.t}

    def moves(self):
        return [a for a in self.sent if a["action"] in replay.MOVES + ("meet_point", "sleep")]


class BodyOwnershipTest(BodyMixin, unittest.TestCase):
    """Стыки «за тело»: сделка между жителями держит тело; сон (relog), квест профессии и чужие модули ждут."""

    def test_trade_holds_body_against_routine(self):
        """Продажа жителю (offer_sell идёт, тело подходит к покупателю) — распорядок не уводит тело на охоту."""
        self.state()
        r = self.mind.routine
        r.new_day(self.clock.t)                                    # режим hunt: распорядок хочет на охоту
        self.selling()
        self.assertFalse(self.mind.may_move("routine")[0], "арбитр: телом владеет сделка")
        r.last_sent = 0
        asyncio.run(r.enforce(self.clock.t, self.mind.state))
        self.assertEqual(self.moves(), [])
        self.mind.economy.offer = None                             # покупатель ждёт продавца на месте
        self.mind.economy.buying = {"id": "abc124", "peer": "Vera", "item": "4001", "amount": 1, "price": 1300,
                                    "status": "waiting", "since": self.clock.t, "base": 1}
        self.assertFalse(self.mind.may_move("routine")[0], "арбитр: покупатель ждёт продавца")
        self.mind.economy.buying = None
        self.state(buy={"id": "x", "from": "Vera", "item": 4001, "amount": 1, "price": 1, "phase": "wait"})
        self.assertFalse(self.mind.may_move("routine")[0], "арбитр: плагин сообщает покупку (state.buy)")

    def test_no_sleep_relog_during_trade_or_mail(self):
        """Сон = relog: посреди сделки или почты не засыпать — уснуть после неё."""
        self.clock.t = datetime(2025, 3, 2, 2, 40, tzinfo=TZ).timestamp()      # окно сна Arkady (с 02:30)
        rest = self.mind.routine.town                                          # у точки отдыха (R2: не у фонтана)
        self.state(x=rest["x"], y=rest["y"], lock_x=rest["x"], lock_y=rest["y"])
        r = self.mind.routine
        r.new_day(self.clock.t, keep_mode="town")
        r.st["arrived"] = True
        self.selling()
        asyncio.run(r.tick())
        self.assertNotIn("sleep", [a["action"] for a in self.sent], "relog посреди продажи")
        self.mind.economy.offer = None
        self.mind.economy.mailing = {"to": "Vera", "kind": "gift", "item": 501, "amount": 5, "zeny": 0,
                                     "since": self.clock.t}
        self.clock.t += 1
        asyncio.run(r.tick())
        self.assertNotIn("sleep", [a["action"] for a in self.sent], "relog посреди письма")
        self.mind.economy.mailing = None
        self.clock.t += 1
        asyncio.run(r.tick())
        self.assertIn("sleep", [a["action"] for a in self.sent], "после сделки — спать")

    def test_job_change_waits_for_trade(self):
        """Квест профессии (этап job_change уводит тело к NPC) не начинается посреди сделки с жителем."""
        career = self.mind.career
        career.cfg = dict(career.cfg, auto_job_change=True)
        self.state()
        r = self.mind.routine
        r.new_day(self.clock.t, keep_mode="town")
        r.st["arrived"] = True
        self.selling()
        stage = {"action": "job_change", "path": "knight", "stage": "s1", "steps": [{"do": "wait"}]}
        with mock.patch("live_brain.progression.stage_action", return_value=stage):
            asyncio.run(career.tick())
            self.assertNotIn("job_change", [a["action"] for a in self.sent], "квест посреди продажи")
            self.mind.economy.offer = None
            asyncio.run(career.tick())
        self.assertIn("job_change", [a["action"] for a in self.sent], "после сделки — можно")

    def test_no_trade_during_job_quest_or_sleep(self):
        """Просьба/предложение жителя во время квеста профессии или сна — вежливый отказ, тело не уходит."""
        self.state(job_change={"running": True, "stage": "s1"})
        econ = self.mind.economy
        self.assertIsNotNone(econ.refuse_reason("Vera", "501", 5), "просьба во время квеста")
        self.assertIsNotNone(econ.offer_refuse_reason("Vera", "4001", 1, 100), "сделка во время квеста")
        self.state()
        self.mind.routine.new_day(self.clock.t, keep_mode="town")
        self.mind.routine.st["mode"] = "sleep"
        self.assertIsNotNone(econ.refuse_reason("Vera", "501", 5), "просьба, когда ложусь спать")


    def test_town_service_and_shop_wait_for_trade(self):
        """Покупатель ждёт продавца на месте: распорядок не ведёт его к NPC (service) и не открывает лавку."""
        self.state(weight_pct=60, vend={"can": 1, "open": 0, "slots": 3})
        r = self.mind.routine
        r.new_day(self.clock.t, keep_mode="town")
        r.st.update(arrived=True, rest_until=float("inf"))
        self.mind.economy.buying = {"id": "abc124", "peer": "Vera", "item": "4001", "amount": 1, "price": 1300,
                                    "status": "waiting", "since": self.clock.t, "base": 1}
        asyncio.run(r.in_town(self.clock.t, self.mind.state))
        self.assertEqual([a["action"] for a in self.sent if a["action"] in ("service", "shop_open")], [])
        self.mind.economy.buying = None
        asyncio.run(r.in_town(self.clock.t, self.mind.state))
        self.assertIn("service", [a["action"] for a in self.sent], "после сделки — по делам")


    def test_second_mail_is_taken_later(self):
        """Два письма с вложением сразу (подарок и «Итог недели»): плагин сообщает о каждом один раз —
        второе не должно потеряться, пока забираю первое."""
        self.state()
        econ = self.mind.economy
        for mid in (11, 12):
            asyncio.run(self.mind.on_message({"type": "event", "kind": "mail_received", "mail_id": mid,
                                              "from": "Vera", "title": "Подарок", "attach": 1}))
        takes = lambda: [a["mail_id"] for a in self.sent if a["action"] == "mail_take"]
        self.assertEqual(takes(), [11])
        asyncio.run(self.mind.on_message({"type": "event", "kind": "mail_taken", "mail_id": 11, "from": "Vera",
                                          "ok": True, "zeny": 0, "items": [{"id": 501, "amount": 5}]}))
        self.clock.t += 1
        self.state()
        asyncio.run(econ.tick())
        self.assertEqual(takes(), [11, 12], "второе письмо забрано после первого")


class NewModulesJointsTest(BodyMixin, unittest.TestCase):
    """review2: стыки новых модулей за тело — дом (Kafra через jobChange), экспедиция, карьера, сон."""

    def town_rest(self):
        self.state()
        r = self.mind.routine
        r.new_day(self.clock.t, keep_mode="town")
        r.st.update(arrived=True, rest_until=self.clock.t + 3600)
        return r

    def trip(self):
        t = self.clock.t
        self.mind.explorer.st["trip"] = {"map": "prt_fild01", "x": None, "y": None, "kind": "field", "hops": 1,
                                         "phase": "go", "started": t, "deadline": t + 2400, "stay": 600,
                                         "led_by": None, "new": True}

    def jobs(self):
        return [a for a in self.sent if a["action"] == "job_change"]

    def test_home_waits_for_expedition(self):
        """Экспедиция вышла из города (ещё в Пронтере): дом не уводит тело к Kafra посреди неё."""
        self.town_rest()
        self.trip()
        asyncio.run(self.mind.home.tick())
        self.assertEqual(self.jobs(), [], "Kafra посреди экспедиции")
        self.mind.explorer.st["trip"] = None
        asyncio.run(self.mind.home.tick())
        self.assertEqual([a["path"] for a in self.jobs()], ["home"], "после экспедиции — сохраниться")

    def test_one_job_change_per_tick(self):
        """Карьера и дом — один плагин jobChange: в одном такте (state ещё без running) уходит один этап."""
        self.town_rest()
        career = self.mind.career
        career.cfg = dict(career.cfg, auto_job_change=True)
        stage = {"action": "job_change", "path": "knight", "stage": "s1", "steps": [{"do": "wait"}]}
        with mock.patch("live_brain.progression.stage_action", return_value=stage):
            asyncio.run(career.tick())
        asyncio.run(self.mind.home.tick())
        self.assertEqual([a["path"] for a in self.jobs()], ["knight"], "второй этап отклонит плагин")

    def test_job_change_sent_holds_body(self):
        """Этап отправлен, а state ещё без job_change.running: арбитр уже не даёт тело распорядку/экспедиции."""
        self.town_rest()
        asyncio.run(self.mind.home.tick())
        self.assertEqual(len(self.jobs()), 1)
        self.assertFalse(self.mind.may_move("routine")[0], "тело у этапа Kafra")
        self.clock.t += 120
        self.state()
        self.assertTrue(self.mind.may_move("routine")[0], "этап так и не начался — тело свободно")

    def test_no_chat_room_when_expedition_starts(self):
        """Экспедиция начата в этом такте (тело ещё сидит в городе): общество не открывает вывеску — чат-комната
        остановила бы тело (pc_cant_act), а мост закрыл бы её лишь по route AI."""
        self.town_rest()
        soc = self.mind.society
        soc.next_room = self.clock.t - 1
        soc.rng = mock.Mock(random=lambda: 0.0, uniform=lambda lo, hi: hi, choice=lambda xs: xs[0])
        self.trip()
        asyncio.run(soc.rooms(self.clock.t, self.mind.state))
        self.assertNotIn("chat_room", [a["action"] for a in self.sent], "вывеска посреди экспедиции")
        self.mind.explorer.st["trip"] = None
        soc.next_room = self.clock.t - 1
        asyncio.run(soc.rooms(self.clock.t, self.mind.state))
        self.assertIn("chat_room", [a["action"] for a in self.sent], "без экспедиции — можно")

    def tame_ready(self, **kw):
        """Охота, рядом монстр любимца и предмет приручения (state.pet от плагина pets)."""
        pets = self.mind.pets
        pets.caring = lambda: True
        self.mind.interests = None                     # interests: ORG-103 — Arkady приручает лишь с шансом 0.2
        self.state(map="prt_fild08", lock_map="prt_fild08", lock_x=None, lock_y=None, x=100, y=100)
        fav = pets.favorites()
        self.assertTrue(fav, "у Arkady есть любимцы на картах охоты")
        pet = {"has": False, "running": None, "items": {str(fav[0]["tame"]): 1}, "eggs": [],
               "near": {str(fav[0]["id"]): 3}}
        self.state(map="prt_fild08", lock_map="prt_fild08", lock_x=None, lock_y=None, x=100, y=100, pet=pet, **kw)
        r = self.mind.routine
        r.new_day(self.clock.t)                                    # режим hunt
        pets.setup_epoch = self.mind.epoch
        return pets

    def test_no_tame_during_pause(self):
        """Пауза (поводок группы) и приручение оба сохраняют/возвращают attackAuto: приручение посреди паузы
        вернуло бы attackAuto 1 после resume навсегда (conf пишет config.txt) — тело перестаёт охотиться."""
        pets = self.tame_ready(paused=True)
        asyncio.run(pets.tick())
        self.assertNotIn("pet_tame", [a["action"] for a in self.sent], "приручение во время паузы")
        self.clock.t += 60
        self.state(**{k: v for k, v in self.mind.state.items() if k not in ("paused", "goal")}, paused=False)
        asyncio.run(pets.tick())
        self.assertIn("pet_tame", [a["action"] for a in self.sent], "без паузы — можно")

    def test_no_pause_during_tame(self):
        """Обратный порядок: идёт приручение (state.pet.running) — лидер не ставит паузу поводка, ждёт конца."""
        far = {"name": "Vera", "online": True, "map": "prt_fild07", "x": 50, "y": 50, "hp_pct": 90}
        me = {"name": "Arkady", "online": True, "map": "prt_fild08", "x": 100, "y": 100}
        kw = dict(map="prt_fild08", lock_map="prt_fild08", lock_x=None, lock_y=None, x=100, y=100,
                  party="LR_Arkady", party_members=[me, far], players=[])
        self.state(pet={"has": False, "running": "tame"}, **kw)
        self.mind.routine.new_day(self.clock.t)
        asyncio.run(self.mind.party.tick())
        self.assertNotIn("pause", [a["action"] for a in self.sent], "пауза посреди приручения")
        self.state(pet={"has": False, "running": None}, **kw)
        asyncio.run(self.mind.party.tick())
        self.assertIn("pause", [a["action"] for a in self.sent], "после приручения — ждать отставшего")

    def test_foreign_tags_ignored(self):
        """Метки [explore:]/[crew:]/[party:]/[chat:] от постороннего — не протокол; [explore:] от не-лидера — тоже."""
        self.town_rest()
        self.state(party="LR_Arkady", party_members=[{"name": "Vera", "online": True, "map": "prontera",
                                                      "x": 157, "y": 186}])
        ev = lambda frm, text: asyncio.run(self.mind.on_message(
            {"type": "event", "kind": "chat_private", "from": frm, "text": text, "ts": self.clock.t}))
        ev("Stranger", "[explore:trip:prt_fild01] [crew:pref:prt_fild01:1] [party:recover:] [chat:sorry:4]")
        ev("Vera", "[explore:trip:prt_fild01]")                   # Arkady — сам лидер: участник не ведёт
        self.assertIsNone(self.mind.explorer.trip)
        self.assertEqual(self.mind.crew.st.get("prefs", {}), {})
        self.assertFalse([a for a in self.sent if a.get("to") == "Stranger" or a["action"] in replay.MOVES])

    def test_nap_waits_for_expedition(self):
        """Сторож просит уснуть раньше посреди экспедиции: сначала вернуться (экспедиция прервана), не relog в поле."""
        r = self.town_rest()
        self.trip()
        self.state(map="prt_fild01", x=200, y=200, lock_map="prt_fild01", lock_x=None, lock_y=None)
        self.assertIsNone(r.request_sleep(2))
        asyncio.run(r.tick())
        self.assertNotIn("sleep", [a["action"] for a in self.sent], "relog посреди экспедиции")
        asyncio.run(self.mind.explorer.tick())
        self.assertIsNone(self.mind.explorer.trip, "экспедиция прервана: пора спать")
        self.clock.t += 1
        asyncio.run(r.tick())
        self.assertNotIn("sleep", [a["action"] for a in self.sent], "сначала дойти до города")
        self.assertIn("meet_point", [a["action"] for a in self.sent], "распорядок ведёт в город")


class SimBody:
    """review2: отзывчивое тело для долгого дня — исполняет команды мозга (lockMap, точка, follow, jobChange, чат-комната,
    relog) упрощённо: переход на другую карту TRAVEL с, по карте — WALK с. Vera — в группе, рядом с Arkady."""
    TRAVEL, WALK, QUEST = 90, 20, 25
    SAVE = ("prontera", 116, 73)                      # homes.json: savepoint Kafra Пронтеры

    def __init__(self, t0):
        self.t = t0
        self.msgs = []                                  # события/state к отправке мозгу
        self.s = {"type": "state", "name": "Arkady", "job": "Swordsman", "lv": 41, "job_lv": 20, "sp_pct": 80,
                  "zeny": 40000, "ai": "auto", "map": "prontera", "x": 150, "y": 180, "hp_pct": 100, "dead": False,
                  "weight_pct": 20, "items": {"501": 30, "4001": 1, "909": 40}, "lock_map": "prontera",
                  "lock_x": None, "lock_y": None, "activity": "idle", "players": [], "friends": [],
                  "party": "LR_Arkady", "party_members": [], "vend": {"can": 0, "open": 0}, "sitting": False,
                  "follow": None, "chat_room": None,
                  "pet": {"has": False, "running": False, "items": {}, "eggs": [], "near": {}}}
        self.go = None                                  # (когда прибуду, карта, x, y)
        self.quest = None                               # (когда закончится этап, path, stage, id)
        self.dead_until = None
        self.asleep = False

    def event(self, **kw):
        self.msgs.append(dict(type="event", ts=self.t, **kw))

    def apply(self, a):
        kind = a.get("action")
        s = self.s
        if kind in replay.MOVES + ("meet_point", "sleep", "shop_open"):
            s["chat_room"] = None                       # brainBridge: chat leave перед движением
        if kind in ("meet_point", "hunt", "explore"):
            s.update(lock_map=a["map"], lock_x=a.get("x"), lock_y=a.get("y"), sitting=False)
            self.route()
        elif kind == "follow":
            s["follow"] = a.get("to")
        elif kind == "unfollow":
            s["follow"] = None
        elif kind == "sit":
            s["sitting"] = True
        elif kind == "chat_room":
            s["chat_room"] = a.get("title") if a.get("op") == "open" else None
        elif kind == "job_change":
            self.quest = (self.t + self.QUEST, a.get("path"), a.get("stage"), a.get("id"))
            s["job_change"] = {"running": True, "stage": a.get("stage")}
            s.update(lock_map=None, lock_x=None, lock_y=None)
        elif kind == "sleep":
            self.asleep = True

    def route(self):
        s = self.s
        if s["dead"] or self.quest or not s.get("lock_map"):
            return
        x, y = (s["lock_x"], s["lock_y"]) if s.get("lock_x") is not None else (100, 100)
        if s["map"] == s["lock_map"] and (s.get("lock_x") is None or max(abs(s["x"] - x), abs(s["y"] - y)) <= 3):
            self.go = None
            return
        dt = self.WALK if s["map"] == s["lock_map"] else self.TRAVEL
        if not self.go or self.go[1:] != (s["lock_map"], x, y):
            self.go = (self.t + dt, s["lock_map"], x, y)

    def tick(self, t, rng):
        self.t = t
        s = self.s
        if self.dead_until and t >= self.dead_until:          # возрождение у точки сохранения
            self.dead_until = None
            s.update(dead=False, hp_pct=5, map=self.SAVE[0], x=self.SAVE[1], y=self.SAVE[2], activity="idle")
            self.go = None
        if self.quest and t >= self.quest[0]:
            _, path, stage, qid = self.quest
            self.quest = None
            s["job_change"] = {"running": False}
            s.update(map="prontera", x=144, y=87, lock_map="prontera", lock_x=156, lock_y=185)   # плагин вернул lockMap
            self.event(kind="job_change_result", id=qid, path=path, stage=stage, step=2, ok=True, reason="ok")
        if not s["dead"] and not self.quest:
            self.route()
            if self.go and t >= self.go[0]:
                _, m, x, y = self.go
                s.update(map=m, x=x, y=y)
                self.go = None
            hunting = s["map"] != "prontera" and s["map"] == s.get("lock_map") and not self.go
            s["activity"] = "attack" if hunting and int(t) % 60 < 40 else ("route" if self.go else "idle")
            if hunting and int(t) % 30 == 0:
                s["x"] += rng.randint(-3, 3)
                s["y"] += rng.randint(-3, 3)
                s["hp_pct"] = max(60, min(100, s["hp_pct"] + rng.randint(-10, 8)))
                self.event(kind="kill", monster="Lunatic", map=s["map"])
            elif int(t) % 10 == 0:
                s["hp_pct"] = min(100, s["hp_pct"] + 2)
        vera = {"name": "Vera", "online": True, "map": s["map"], "x": s["x"] + 2, "y": s["y"] + 1, "hp_pct": 90,
                "lv": 38, "visible": True}
        s["party_members"] = [dict(vera, name="Arkady", x=s["x"], y=s["y"], lv=41), vera]
        s["players"] = [{"name": "Vera", "x": vera["x"], "y": vera["y"], "job": "Acolyte", "lv": 38, "sex": "Female"}]
        self.msgs.append(dict(s, ts=t))

    def die(self):
        self.s.update(dead=True, hp_pct=0, activity="dead")
        self.go = None
        self.dead_until = self.t + 20
        self.event(kind="died", map=self.s["map"])

class LongDayAllModulesTest(unittest.TestCase):
    """review2: долгий день со ВСЕМИ новыми модулями (дом, группа и crew, питомец, общество, занятия и цепочки,
    экспедиция) на отзывчивом теле: город → сохранение у Kafra → охота группой → экспедиция → тревога и гибель там →
    возрождение дома → вечер → сон. Экспедицию начинает тест (как занятие explore каталога), остальное — модули."""

    def test_city_kafra_hunt_expedition_alarm_sleep(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        t0 = datetime(2025, 3, 2, 17, 0, tzinfo=TZ).timestamp()        # Arkady спит с 02:30
        clock, rng = Clock(), random.Random(5)
        clock.t = t0
        body = SimBody(t0)
        sent, marks = [], {}
        with mock.patch("time.time", clock):
            mem = Memory(root / "m.sqlite")
            persona = prontera_persona()
            mind = None

            async def send(a):
                sent.append((clock.t, dict(a), dict(mind.state)))
                body.apply(dict(a, id=len(sent)))
                return len(sent)

            mind = Mind(Settings.from_env({}), persona, mem, send, root / "d.jsonl", RuleGate(),
                        peers={"Arkady", "Vera"}, world=WORLD, world_bus_db=WorldBus(root / "w.sqlite", "Arkady"))
            mind.routine.rng = mind.activities.rng = random.Random(7)
            for name in ("home", "crew", "pets", "society", "activities", "explorer", "party", "routine"):
                self.assertIsNotNone(getattr(mind, name), f"модуль {name} включён")
            mind.routine.new_day(t0, keep_mode="town")                   # день начинается в городе
            mind.routine.st["rest_until"] = t0 + 15 * 60

            async def day():
                body.event(kind="chat_private", **{"from": "Vera"}, text="[crew:pref:prt_fild08:38]")
                while clock.t < t0 + 12 * 3600:
                    body.tick(clock.t, rng)
                    for m in body.msgs:
                        await mind.on_message(m)
                    body.msgs.clear()
                    await mind.step()
                    ex, r = mind.explorer, mind.routine
                    if "hunted" not in marks and r.st.get("mode") == "hunt" and body.s["map"] == r.hunt_map():
                        marks["hunted"] = clock.t
                    if ("hunted" in marks and "expedition" not in marks and r.st.get("mode") == "town"
                            and r.st.get("arrived") and not ex.trip and body.s["hp_pct"] >= 80):
                        why = ex.blocked(mind.state)
                        cands = ex.candidates()
                        if why in (None,) or why.startswith("любопытство"):
                            self.assertTrue(cands, "есть цель экспедиции")
                            marks["expedition"] = clock.t
                            await ex.start(cands[0])
                    trip = ex.trip
                    if trip and trip.get("phase") == "stay" and "alarm" not in marks \
                            and clock.t >= trip["arrived"] + 60:
                        marks["alarm"] = clock.t
                        body.event(kind="danger", map=body.s["map"])
                    if "alarm" in marks and "died" not in marks and clock.t >= marks["alarm"] + 3:
                        marks["died"] = clock.t
                        body.die()
                    if body.asleep:
                        marks.setdefault("sleep", clock.t)
                        if clock.t > marks["sleep"] + 5:
                            break
                    clock.t += 1

            asyncio.run(day())
            kinds = [r[0] for r in mem.db.execute("SELECT kind FROM events ORDER BY id")]
            calls = mem.db.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
            home, explore, crew = mem.get("home") or {}, mem.get("explore") or {}, mem.get("crew") or {}
            mem.close()
        decisions = [json.loads(l) for l in (root / "d.jsonl").read_text().splitlines()]
        acts = [a["action"] for _, a, _ in sent]
        # этапы дня — по порядку
        for k in ("hunted", "expedition", "alarm", "died", "sleep"):
            self.assertIn(k, marks, f"этап {k}: {marks}")
        self.assertIn("home_saved", kinds)
        t_hunt = next(t for t, a, _ in sent if a["action"] == "hunt")
        self.assertTrue([1 for t, a, s in sent if t < t_hunt and a["action"] == "job_change"],
                        "к Kafra — до выхода на охоту")
        self.assertFalse([a for t, a, s in sent if (s.get("job_change") or {}).get("running")
                          and a["action"] in replay.MOVES + ("meet_point", "sleep")], "этап Kafra держит тело")
        for k in ("explore_start", "explore_arrived", "explore_done", "explore_returned", "home_respawn"):
            self.assertIn(k, kinds)
        done = next(d for d in decisions if d.get("event") == "explore_done")
        self.assertIn("опасность", done["why"])
        self.assertEqual(done["rumor"], "danger")
        # инварианты реплея: мёртвому не двигаться, не больше SPAM_PER_MIN в минуту, без LLM — ноль вызовов
        self.assertEqual(replay.invariants(sent, llm_calls=calls), [])
        # один этап jobChange (дом), не посреди экспедиции
        self.assertEqual([a.get("path") for a in (x for _, x, _ in sent) if a["action"] == "job_change"], ["home"])
        # экспедиция: lockMap цели, после неё распорядок вернул город (точка отдыха)
        t_exp = [t for t, a, _ in sent if a["action"] == "explore"]
        self.assertTrue(t_exp)
        self.assertTrue([1 for t, a, _ in sent if t > marks["died"] and a["action"] == "meet_point"
                         and a["map"] == "prontera"], "после экспедиции lockMap снова город")
        # пока экспедиция шла (до тревоги) — ни охоты, ни прогулок, ни Kafra, ни сна
        during = [a["action"] for t, a, _ in sent if marks["expedition"] < t < marks["alarm"]]
        for bad in ("hunt", "meet_point", "job_change", "sleep", "service", "chat_room"):
            self.assertNotIn(bad, during, f"{bad} посреди экспедиции")
        # сон — в городе, после него тело не двигают
        t_sleep, _, s_sleep = next(x for x in sent if x[1]["action"] == "sleep")
        self.assertEqual(s_sleep.get("map"), "prontera")
        self.assertFalse([a for t, a, _ in sent if t > t_sleep and a["action"] in replay.MOVES + ("meet_point",)])
        # группа: подтверждена, лидер объявлял режим, crew говорил в чате группы
        self.assertIn("party_confirmed", kinds)
        self.assertIn("party_say", acts)
        # часы новых модулей — часы реплея
        end = clock.t
        self.assertTrue(t0 <= home["saved"]["ts"] <= end, "home: время реплея")
        self.assertTrue(t0 <= explore["last"] <= end, "explore: время реплея")
        self.assertTrue(all(t0 <= p["ts"] <= end for p in crew.get("prefs", {}).values()), "crew: время реплея")


class Review3JointsTest(BodyMixin, unittest.TestCase):
    """review3: стыки модулей раунда 5 (сплетни, привычки, лекарь, заказы, мечта, копилка, режиссёр) с ядром."""

    def test_recent_events_survive_kind_ts_in_data(self):
        """Данные событий с ключами kind/ts (gossip_heard, habit_formed, explore_found) не роняют recent_events и промпт."""
        self.state()
        mem = self.mem
        mem.add_event("gossip_heard", {"from": "Vera", "who": "Bob", "what": "heal", "kind": "kind", "hops": 0})
        mem.add_event("habit_formed", {"kind": "after", "what": "stroll", "cond": "hunt"})
        mem.add_event("explore_found", {"map": "prt_fild01", "hops": 1, "kind": "field", "ts": 5})
        rows = mem.recent_events(10)
        kinds = [r["kind"] for r in rows]
        self.assertEqual(kinds[-3:], ["gossip_heard", "habit_formed", "explore_found"], "вид события не затёрт данными")
        self.assertNotEqual(rows[-1]["ts"], 5, "время события не затёрто данными")
        msgs = self.mind.build_prompt("тест", {})
        self.assertIn("gossip_heard", msgs[1]["content"])


    def test_order_does_not_sell_album_card(self):
        """Заказ на карту: единственная копия альбома (collection.sellable) не уходит исполнителю заказа — как и в for_sale."""
        self.state(items={"501": 30, "4001": 1})
        coll, orders = self.mind.collection, self.mind.orders
        self.assertIsNotNone(coll)
        self.assertIsNotNone(orders)
        coll.seed()
        self.assertIn("4001", coll.album(), "карта рюкзака — в альбоме")
        self.assertEqual([lot for lot in self.mind.economy.for_sale(self.mind.state) if lot[0] == "4001"], [],
                         "рынок альбомную карту не продаёт")
        o = {"id": "abc123", "customer": "Vera", "item": "4001", "name": "Poring Card", "n": 1, "reward": 100000,
             "until": self.clock.t + 3600}
        self.assertIsNotNone(orders.can_fill(o, self.mind.state), "заказ не берут ради карты из альбома")
        self.state(items={"501": 30, "4001": 2})
        self.assertIsNone(orders.can_fill(o, self.mind.state), "дубликат — можно")


    def test_album_card_kept_before_seed(self):
        """Первый такт мозга: рынок (economy, такт 20) раньше коллекции, альбом ещё не засеян — карта рюкзака
        всё равно считается альбомной (одна копия остаётся), а не уходит первому встречному жителю."""
        self.state(items={"501": 30, "4001": 1})
        coll = self.mind.collection
        self.assertIsNone(coll.st.get("cursor"), "альбом ещё не засеян")
        self.assertEqual(coll.sellable({"4001": 1, "909": 5}), {"4001": 0, "909": 5})
        self.assertEqual([lot for lot in self.mind.economy.for_sale(self.mind.state) if lot[0] == "4001"], [])

    def test_relation_log_bounded_by_names(self):
        """kv relation_log (W5) не растёт без предела по числу имён: модель даёт отношения к любым игрокам."""
        from live_brain.memory import RELATION_LOG
        for i in range(400):
            self.mem.update_relation(f"Stranger{i}", 1, "болтали")
        self.mem.update_relation("Vera", 1, "провели время вместе")
        log = self.mem.get("relation_log")
        self.assertLessEqual(len(log), 250, "имён в истории отношений — ограниченно")
        self.assertIn("Vera", log, "свежая пара остаётся")
        self.assertLessEqual(max(len(v) for v in log.values()), RELATION_LOG)


# review4: модули раунда 7, выключенные по умолчанию, — включены (синтетическое тело, не игра)
WORLD7 = dict(WORLD, **{k: dict(WORLD.get(k) or {}, enabled=True) for k in ("spar", "trek", "herbal", "refine")})


class Review4JointsTest(BodyMixin, unittest.TestCase):
    """review4: стыки модулей раунда 7 (спарринг, поход, травник, стрелы, заточка, рынок, взгляд, достижения) с ядром."""
    WORLD = WORLD7

    def ev(self, **kw):
        asyncio.run(self.mind.on_message(dict(type="event", ts=self.clock.t, **kw)))

    def kinds(self):
        return [r[0] for r in self.mem.db.execute("SELECT kind FROM events ORDER BY id")]

    def test_spar_fall_is_not_death(self):
        """Упал(а) в дружеском спарринге на арене (nopenalty) — не гибель: ни разбора смерти (он идёт в шину мира и
        летопись), ни [party:dead:] группе, ни счёта смертей распорядка, ни блокировки следующего спарринга (hurt)."""
        self.state(party="LR_Arkady", party_members=[{"name": "Vera", "online": True, "map": "prontera"}])
        spar = self.mind.spar
        self.assertIsNotNone(spar)
        spar.st["cur"] = {"peer": "Vera", "role": "first", "phase": "fight", "room": "Prontera",
                          "ts": self.clock.t, "started": self.clock.t}
        self.state(map="pvp_y_8-1", x=156, y=185, hp_pct=40, spar={"running": True, "phase": "fight", "to": "Vera"})
        self.clock.t += 5
        self.ev(kind="died", map="pvp_y_8-1")
        kinds = self.kinds()
        self.assertNotIn("death_report", kinds, "падение на арене — не разбор смерти")
        self.assertEqual(self.mem.count_events("died", 0), 0, "смертей не прибавилось")
        self.assertFalse([a for a in self.sent if "[party:dead:" in str(a.get("text", ""))], "группе не «погиб»")
        self.assertIn("spar_fall", kinds, "падение записано как факт спарринга")
        self.ev(kind="spar_result", to="Vera", outcome="down", reason="упал(а) на арене", room="Prontera")
        self.assertIsNone(spar.cur)
        self.assertIn("spar_bout", self.kinds())
        # обычная смерть вне спарринга — по-прежнему смерть
        self.state(map="prt_fild08", x=100, y=100, hp_pct=10, spar={"running": False})
        self.ev(kind="died", map="prt_fild08")
        self.assertIn("death_report", self.kinds())

    def test_market_keeps_craft_and_spar_items(self):
        """Рынок жителей и лавка (economy.for_sale) не продают то, что держат модули раунда 7: крыло бабочки —
        единственный выход с арены спарринга; травы/бутылки травника и материалы Roberto (state.craft.kept —
        craft_setup keep держит их только от NPC-продажи OpenKore, не от [offer:] и лавки)."""
        self.state(items={"501": 30, "602": 2, "509": 40, "713": 10, "4001": 2},
                   craft={"items": {}, "kept": [509, 713]})
        lots = {lot[0] for lot in self.mind.economy.for_sale(self.mind.state)}
        self.assertNotIn("602", lots, "крыло для арены не продаётся")
        self.assertNotIn("509", lots, "травы травника не продаются")
        self.assertNotIn("713", lots, "бутылки травника не продаются")
        self.assertIn("4001", lots, "прочее ценное — продаётся")

    def test_spar_holds_body_against_quest_modules(self):
        """Спарринг владеет телом как «plan» — тем же владельцем, которого спрашивают дом (Kafra), карьера, травник,
        стрелы и заточка (may_move("plan")): без отдельной проверки они слали свой этап jobChange посреди похода
        к Gate Keeper. И наоборот: поход (trek) держит тело — спарринг не начинается."""
        self.state()
        r = self.mind.routine
        r.new_day(self.clock.t, keep_mode="town")
        r.st.update(arrived=True, rest_until=self.clock.t + 3600)
        home = self.mind.home
        self.assertIsNone(home.why_not_now(self.clock.t, self.mind.state), "без спарринга дом пошёл бы к Kafra")
        self.mind.spar.st["cur"] = {"peer": "Vera", "role": "first", "phase": "going", "room": "Prontera",
                                    "ts": self.clock.t, "started": self.clock.t}
        self.assertIsNotNone(home.why_not_now(self.clock.t, self.mind.state), "спарринг идёт — дом ждёт")
        self.clock.t += 5
        asyncio.run(self.mind.step())
        self.assertFalse([a for a in self.sent if a["action"] in ("job_change", "refine")], self.sent)
        self.mind.spar.st["cur"] = None
        self.mind.trek.st["trip"] = {"target": "payon", "legs": [], "i": 0, "phase": "saved", "led_by": None,
                                     "members": ["Vera"], "started": self.clock.t}
        self.state(items={"501": 30, "602": 2})
        self.assertEqual(self.mind.spar.blocker(self.clock.t), "busy", "поход идёт — на арену не зову")

    def test_tick_exception_keeps_memory_consistent(self):
        """perf: исключение в модуле посреди такта — записи такта до него зафиксированы (как при коммите на запись),
        кэш такта сброшен (следующий такт читает БД), счётчик вложенности вернулся к нулю."""
        import sqlite3
        self.state()

        async def boom():
            self.mem.set("review4_probe", {"n": 1})
            self.mem.update_relation("Vera", 1, "до сбоя")
            raise RuntimeError("модуль упал")

        self.mind.gaze.tick = boom
        self.clock.t += 5
        with self.assertRaises(RuntimeError):
            asyncio.run(self.mind.step())
        self.assertEqual(self.mem._scope, 0)
        other = sqlite3.connect(str(self.mem.path))
        try:
            row = other.execute("SELECT value FROM kv WHERE key = 'review4_probe'").fetchone()
        finally:
            other.close()
        self.assertEqual(json.loads(row[0]), {"n": 1}, "запись такта до сбоя зафиксирована")
        self.mem.db.execute("UPDATE kv SET value = '{\"n\": 2}' WHERE key = 'review4_probe'")
        with self.mem.tick():
            self.assertEqual(self.mem.get("review4_probe"), {"n": 2}, "кэш прошлого такта не устарел")

    def test_trek_gather_vs_kafra_stage(self):
        """Поход: пока группа собирается (gather, explorer ещё свободен), дом не шлёт этап Kafra; если этап jobChange
        всё же идёт (карьера, отправлен только что), поход не выводит тело плечом-экспедицией из-под этапа."""
        self.state()
        r = self.mind.routine
        r.new_day(self.clock.t, keep_mode="town")
        r.st.update(arrived=True, rest_until=self.clock.t + 3600)
        trek = self.mind.trek
        legs = trek.legs_to("payon") or [{"to": "payon", "hops": 3, "path": ["prontera", "prt_fild08", "payon"]}]
        trek.st["trip"] = {"target": "payon", "legs": legs, "i": 0, "phase": "gather", "led_by": None,
                           "members": ["Vera"], "invited": ["Vera"], "started": self.clock.t,
                           "until": self.clock.t + 600}
        self.assertIsNotNone(self.mind.home.why_not_now(self.clock.t, self.mind.state), "сбор похода — дом ждёт")
        self.mind.job_change_sent = self.clock.t                     # этап карьеры отправлен в этом такте
        asyncio.run(trek.begin(self.clock.t))
        self.assertFalse([a for a in self.sent if a["action"] == "explore"], self.sent)
        self.assertIsNone(self.mind.explorer.trip)

    def test_foreign_job_change_path_not_career(self):
        """Итог этапа jobChange чужого модуля (herbal/arrows/trek), когда сам модуль выключен (перезапуск с
        BRAIN_DISABLE, правка goals.json), — не этап карьеры: career не пишет «этап пройден» и не копит провалы."""
        self.state()
        for attr in ("herbal", "arrows", "trek"):
            setattr(self.mind, attr, None)
        before = dict(self.mind.career.st)
        for path in ("herbal", "arrows", "trek"):
            self.ev(kind="job_change_result", path=path, stage="x", ok=False, reason="timeout")
            self.ev(kind="job_change_result", path=path, stage="x", ok=True, reason="ok")
        kinds = self.kinds()
        self.assertNotIn("career_stage_done", kinds)
        self.assertNotIn("career_stage_failed", kinds)
        self.assertEqual(self.mind.career.st.get("fails", 0), before.get("fails", 0))

    def test_model_cannot_forge_protocol_tags(self):
        """Шёпот модели (LLM/JEV) со служебной меткой ([offer:..:ok], [spar:yield], [trek:no:..], [mentor:grad]) —
        подделка протокола другому жителю (например, по наущению постороннего в личке): не отправляется."""
        self.state()
        for text in ("Беру. [offer:ab12cd:ok]", "Сдаюсь [spar:yield]", "[trek:no:payon]", "Ты молодец [mentor:grad]"):
            self.clock.t += 60                                       # не упираться в паузу между шёпотами
            asyncio.run(self.mind.execute([{"action": "whisper", "to": "Vera", "text": text}], source="llm",
                                          reason="тест"))
        self.assertFalse([a for a in self.sent if a["action"] == "whisper"], self.sent)
        self.clock.t += 60
        asyncio.run(self.mind.execute([{"action": "whisper", "to": "Vera", "text": "Привет! Как охота?"}],
                                      source="llm", reason="тест"))
        self.assertEqual(len([a for a in self.sent if a["action"] == "whisper"]), 1, "обычная реплика уходит")


class SimBody7(SimBody):
    """review4: SimBody + плагин spar упрощённо: spar -> через TRAVEL с на арене (spar_step arena/fight), затем падение
    (died на pvp_y_8-1, spar_result down) и возрождение у точки сохранения; look_at — без следствий."""
    ARENA = "pvp_y_8-1"

    def __init__(self, t0):
        super().__init__(t0)
        self.s["items"]["602"] = 2                       # Butterfly Wing — выход с арены
        self.spar = None                                  # (этап, когда, соперник)

    def apply(self, a):
        if a.get("action") == "spar":
            self.spar = ["going", self.t + self.TRAVEL, a.get("to")]
            self.s["spar"] = {"running": True, "phase": "gate", "to": a.get("to"), "room": a.get("room")}
            return
        if a.get("action") == "spar_stop" and self.spar:
            self.spar = None
            self.s["spar"] = {"running": False}
            self.event(kind="spar_result", outcome="stopped", reason=a.get("why"), to=None)
            return
        super().apply(a)

    def tick(self, t, rng):
        sp = self.spar
        if sp and t >= sp[1]:
            if sp[0] == "going":
                self.s.update(map=self.ARENA, x=156, y=185, lock_map=None)
                self.event(kind="spar_step", phase="arena", room="Prontera", to=sp[2])
                self.event(kind="spar_step", phase="fight", room="Prontera", to=sp[2])
                sp[:2] = ["fight", t + 30]
            elif sp[0] == "fight":
                self.spar = None
                self.die()                                # died на арене (map pvp_y_8-1)
                self.s["spar"] = {"running": False}
                self.event(kind="spar_result", outcome="down", reason="упал(а) на арене", to=sp[2], room="Prontera")
        super().tick(t, rng)
        if self.s["map"] == self.ARENA:
            self.s["players"] = [{"name": "Vera", "x": 157, "y": 186, "job": "Acolyte", "lv": 38}]


class LongDayRound7Test(unittest.TestCase):
    """review4: долгий рыночный день со ВСЕМИ модулями раундов 5–7 (включая выключенные по умолчанию spar, trek,
    herbal, refine) на отзывчивом синтетическом теле: город и рынок, достижения, разговоры (взгляд), спарринг с
    падением на арене, охота, вечер. Это проверка стыков на заглушке тела, а не игра."""

    def test_market_day_spar_achievements_gaze(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        base = datetime(2025, 3, 1, 10, 0, tzinfo=TZ).timestamp()
        clock, rng = Clock(), random.Random(3)
        clock.t = base
        sent, marks = [], {}
        with mock.patch("time.time", clock):
            mem = Memory(root / "m.sqlite")
            persona = prontera_persona()
            mind, body = None, None

            async def send(a):
                sent.append((clock.t, dict(a), dict(mind.state)))
                body.apply(dict(a, id=len(sent)))
                return len(sent)

            mind = Mind(Settings.from_env({}), persona, mem, send, root / "d.jsonl", RuleGate(),
                        peers={"Arkady", "Vera"}, world=WORLD7, world_bus_db=WorldBus(root / "w.sqlite", "Arkady"))
            for name in ("home", "routine", "economy", "party", "crew", "social", "society", "gossip", "habits",
                         "healer", "orders", "dream", "savings", "memoir", "mentor", "bestiary", "places",
                         "market_day", "refine", "gaze", "spar", "achieve", "herbal", "arrows", "trek", "director"):
                self.assertIsNotNone(getattr(mind, name, None), f"модуль {name} включён")
            t0 = next(base + d * 86400 for d in range(14) if mind.market_day.is_market(base + d * 86400))
            clock.t = t0
            body = SimBody7(t0)
            mind.routine.rng = mind.activities.rng = random.Random(7)
            mind.spar.rng = random.Random(1)
            mind.spar.willing = lambda peer, now: True               # согласие по характеру — не предмет теста
            for _ in range(2):
                mem.update_relation("Vera", 2, "давняя подруга")         # друг: affinity >= friend_min
            mind.routine.new_day(t0, keep_mode="town")
            mind.routine.st["rest_until"] = t0 + 3 * 3600
            trades_base = mind.market_day.base["trades_per_day"]

            async def day():
                body.event(kind="achievement_list", points=10, rank=1, done=[[1, int(t0) - 86400, 1]])
                while clock.t < t0 + 6 * 3600:
                    body.tick(clock.t, rng)
                    t = clock.t - t0
                    if t == 300:
                        body.event(kind="achievement", id=2, at=int(clock.t), reward=0, points=20, rank=1)
                    if t % 90 == 0 and t < 2 * 3600 and body.s["map"] == "prontera":
                        body.event(kind="chat_private", **{"from": "Vera"}, text=f"Как дела? [chat:hello:{t % 3 + 1}]")
                    if "yes" not in marks and [1 for _, a, _ in sent if "[spar:ask]" in str(a.get("text", ""))]:
                        marks["yes"] = clock.t                        # Vera отвечает на вызов Arkady согласием
                        body.event(kind="chat_private", **{"from": "Vera"}, text="Давай! [spar:yes]")
                    if mind.market_day.active:
                        marks.setdefault("market", mind.economy.market["trades_per_day"])
                    for m in body.msgs:
                        await mind.on_message(m)
                    body.msgs.clear()
                    await mind.step()
                    clock.t += 1

            asyncio.run(day())
            kinds = [r[0] for r in mem.db.execute("SELECT kind FROM events ORDER BY id")]
            calls = mem.db.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
            bus_kinds = [r[0] for r in mind.world.bus.db.execute("SELECT kind FROM world_events")]
            mem.close()
        decisions = [json.loads(l) for l in (root / "d.jsonl").read_text().splitlines()]
        acts = [a["action"] for _, a, _ in sent]
        bad = replay.invariants(sent, llm_calls=calls)
        if bad:
            m = int(bad[0].split()[1].rstrip(":")) if bad[0].startswith("минута") else None
            print([(t - t0, a) for t, a, _ in sent if m is not None and int(t // 60) == m])
        self.assertEqual(bad, [])
        # рыночный день: пороги стояли, без двойного множителя
        self.assertEqual(marks.get("market"), trades_base + WORLD7["market_day"]["trades_bonus"])
        # достижения: первый список молча, новое — объявлено
        self.assertIn("achievement_done", kinds)
        self.assertEqual([d.get("id") for d in decisions if d.get("type") == "achieve" and d.get("event") == "done"], [2])
        # взгляд: не чаще gap_seconds
        looks = [t for t, a, _ in sent if a["action"] == "look_at"]
        self.assertTrue(looks, "поворачивался к собеседнице")
        self.assertTrue(all(b - a >= mind.gaze.cfg["gap_seconds"] for a, b in zip(looks, looks[1:])), looks)
        # спарринг: согласие, поход, падение на арене — не гибель (ни разбора смерти, ни слуха, ни [party:dead:])
        self.assertIn("yes", marks, [d for d in decisions if d.get("type") == "spar"])
        self.assertIn("[spar:in:Prontera]", " ".join(str(a.get("text", "")) for _, a, _ in sent), "ждёт на арене")
        self.assertIn("spar", acts)
        self.assertIn("spar_fall", kinds)
        self.assertIn("spar_bout", kinds)
        self.assertNotIn("died", kinds)
        self.assertNotIn("death_report", kinds)
        self.assertNotIn("death_report", bus_kinds)
        self.assertFalse([a for a in acts if a == "whisper"] and
                         [1 for _, a, _ in sent if "[party:dead:" in str(a.get("text", ""))])
        # пока тело у плагина spar — мозг не двигает его (мост отклонил бы, но и не шлёт)
        self.assertFalse([(t, a) for t, a, s in sent if (s.get("spar") or {}).get("running")
                          and a["action"] in replay.MOVES + ("meet_point", "sleep", "job_change", "refine")])


class TownWorld:
    """review3: два отзывчивых тела в одном городе (Arkady и Vera) — каждое ведёт свой настоящий Mind, шина мира общая.
    Мир исполняет команды упрощённо: точка (meet_point) — дойти за WALK с; шёпот жителю — событие chat_private у
    адресата через 1 с; skill_on_player — цель видна, жива и не дальше 9 клеток -> событие support обоим (Heal +30 % HP);
    offer_sell + встречный offer_buy — обмен предмета на зени, give_result продавцу и buy_result покупателю."""
    WALK = 15

    def __init__(self, t0):
        self.t = t0
        self.bodies = {}
        self.inbox = {}                                  # имя -> [сообщения мозгу]
        self.go = {}                                     # имя -> (когда, x, y)
        self.trades = {}                                 # (продавец, покупатель, предмет) -> {sell, buy}

    def add(self, name, **kw):
        s = {"type": "state", "name": name, "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "sp_pct": 90,
             "dead": False, "zeny": 40000, "ai": "auto", "weight_pct": 20, "items": {}, "lock_map": "prontera",
             "lock_x": None, "lock_y": None, "activity": "idle", "players": [], "friends": [], "party": None,
             "party_members": [], "vend": {"can": 0, "open": 0}, "sitting": False, "chat_room": None, "lv": 40,
             "job_lv": 20}
        s.update(kw)
        self.bodies[name] = s
        self.inbox[name] = []

    def event(self, name, **kw):
        self.inbox[name].append(dict(type="event", ts=self.t, **kw))

    def near(self, a, b, cells):
        sa, sb = self.bodies[a], self.bodies[b]
        return sa["map"] == sb["map"] and max(abs(sa["x"] - sb["x"]), abs(sa["y"] - sb["y"])) <= cells

    def apply(self, who, a):
        s = self.bodies[who]
        kind = a.get("action")
        other = a.get("to") or a.get("from")
        if kind in replay.MOVES + ("meet_point", "sleep", "shop_open", "skill_on_player"):
            s["chat_room"] = None                        # brainBridge: chat leave перед движением и кастом
        if kind == "meet_point":
            s.update(lock_map=a["map"], lock_x=a["x"], lock_y=a["y"], sitting=False)
            self.go[who] = (self.t + self.WALK, a["x"], a["y"])
        elif kind == "sit":
            s["sitting"] = True
        elif kind == "stand":
            s["sitting"] = False
        elif kind == "chat_room":
            s["chat_room"] = a.get("title") if a.get("op") == "open" else None
        elif kind == "whisper" and other in self.bodies:
            self.inbox[other].append({"type": "event", "kind": "chat_private", "from": who, "text": a["text"],
                                      "ts": self.t + 1})
        elif kind == "skill_on_player" and other in self.bodies:
            t = self.bodies[other]
            if self.near(who, other, 9) and not t["dead"] and s["sp_pct"] >= 10:
                s["sp_pct"] -= 5
                amount = None
                if a["skill"] == "AL_HEAL":
                    amount = min(30, 100 - t["hp_pct"]) * 10 or 1
                    t["hp_pct"] = min(100, t["hp_pct"] + 30)
                for n in (who, other):
                    self.event(n, kind="support", skill=a["skill"], **{"from": who}, to=other, amount=amount)
        elif kind in ("offer_sell", "offer_buy") and other in self.bodies:
            seller, buyer = (who, other) if kind == "offer_sell" else (other, who)
            key = (seller, buyer, str(a["item"]))
            self.trades.setdefault(key, {})[kind] = dict(a)
            tr = self.trades[key]
            if "offer_sell" in tr and "offer_buy" in tr:
                del self.trades[key]
                sb, bb = self.bodies[seller], self.bodies[buyer]
                item, n, price = str(a["item"]), int(tr["offer_sell"]["amount"]), int(tr["offer_sell"]["price"])
                ok = self.near(seller, buyer, 5) and sb["items"].get(item, 0) >= n and bb["zeny"] >= price
                if ok:
                    sb["items"][item] -= n
                    bb["items"][item] = bb["items"].get(item, 0) + n
                    sb["zeny"] += price
                    bb["zeny"] -= price
                self.event(seller, kind="give_result", to=buyer, item=int(item), amount=n, price=price,
                           paid=price if ok else 0, ok=ok, reason=None if ok else "далеко")
                self.event(buyer, kind="buy_result", **{"from": seller}, item=int(item), amount=n, ok=ok)

    def tick(self, t):
        self.t = t
        for name, s in self.bodies.items():
            g = self.go.get(name)
            if g and t >= g[0]:
                s.update(x=g[1], y=g[2])
                del self.go[name]
            s["activity"] = "route" if name in self.go else "idle"
            if int(t) % 30 == 0:
                s["hp_pct"] = min(100, s["hp_pct"] + 1)
                s["sp_pct"] = min(100, s["sp_pct"] + 1)
        for name, s in self.bodies.items():
            s["players"] = [{"name": o, "x": self.bodies[o]["x"], "y": self.bodies[o]["y"], "job": self.bodies[o]["job"],
                             "lv": self.bodies[o]["lv"], "sex": self.bodies[o].get("sex")}
                            for o in self.bodies if o != name and self.near(name, o, 14)]
            if int(t) % 2 == 0:
                self.inbox[name].append(dict(s, ts=t, items=dict(s["items"])))


class TwoResidentsDayTest(unittest.TestCase):
    """review3: долгий день в городе с модулями раунда 5 на двух настоящих мозгах и общей шине мира: Vera (Acolyte,
    щедрая) — лекарь у собора, Arkady (ранен) — пациент; Vera заказывает Jellopy (market.wish), Arkady (у него 40)
    берёт заказ и продаёт сделкой рынка; мечта, копилка, сплетни, привычки, режиссёр, толпа — работают сами.
    Проверки: replay.invariants у обоих, лекарь лечил только на посту, заказ выполнен одной сделкой (без двойной
    продажи), промпт и recent_events у обоих строятся, часы модулей — часы реплея, мотивы в разумных пределах."""

    def test_healer_orders_dream_gossip_director(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        t0 = datetime(2025, 3, 4, 10, 0, tzinfo=TZ).timestamp()
        clock = Clock()
        clock.t = t0
        world = TownWorld(t0)
        pet = {"has": True, "running": False, "items": {}, "eggs": [], "near": {}}     # питомец есть — не заказывают корм
        world.add("Arkady", job="Swordsman", lv=41, hp_pct=45, items={"501": 30, "909": 40, "4001": 1}, pet=dict(pet))
        world.add("Vera", job="Acolyte", lv=38, sex="Female", x=160, y=190, items={"501": 30}, pet=dict(pet),
                  support_skills={"AL_HEAL": 10, "AL_BLESSING": 5, "AL_INCAGI": 3})
        vera_world = json.loads(json.dumps(WORLD))
        vera_world.setdefault("economy", {}).setdefault("market", {})["wish"] = ["909"]
        sent = {"Arkady": [], "Vera": []}
        minds, mems, buses = {}, {}, {}
        weighted_max = [0.0]
        with mock.patch("time.time", clock):
            for name, bot, w in (("Arkady", "bot01", WORLD), ("Vera", "bot02", vera_world)):
                mems[name] = Memory(root / f"{name}.sqlite")
                buses[name] = WorldBus(root / "world.sqlite", name)
                persona = prontera_persona(bot)

                def make_send(n):
                    async def send(a):
                        sent[n].append((clock.t, dict(a), dict(minds[n].state)))
                        world.apply(n, dict(a))
                        return len(sent[n])
                    return send

                minds[name] = Mind(Settings.from_env({}), persona, mems[name], make_send(name), root / f"{name}.jsonl",
                                   RuleGate(), peers={"Arkady", "Vera"}, world=w, world_bus_db=buses[name])
            arkady, vera = minds["Arkady"], minds["Vera"]
            for m in minds.values():
                for name in ("healer", "orders", "dream", "savings", "gossip", "habits", "director", "memoir"):
                    self.assertIsNotNone(getattr(m, name), f"модуль {name} включён у {m.persona['name']}")
                m.routine.rng = m.activities.rng = random.Random(3)
                for name in ("hunt_early", "explore"):           # день в городе: охота и экспедиции — в других тестах
                    m.activities.catalog.pop(name, None)
                m.routine.new_day(t0, keep_mode="town")
                m.routine.st["rest_until"] = t0 + 6 * 3600
            marks = {}

            async def day():
                while clock.t < t0 + 4 * 3600:
                    world.tick(clock.t)
                    for name, m in minds.items():
                        msgs = sorted((x for x in world.inbox[name] if x["ts"] <= clock.t), key=lambda x: x["ts"])
                        world.inbox[name] = [x for x in world.inbox[name] if x["ts"] > clock.t]
                        for msg in msgs:
                            await m.on_message(msg)
                        await m.step()
                        if int(clock.t) % 300 == 0:
                            weighted_max[0] = max([weighted_max[0]] + list(m.needs.weighted().values()))
                    if "post" not in marks and clock.t >= t0 + 300:
                        marks["post"] = clock.t                # лекарь встаёт на пост (занятие каталога — тестом)
                        await vera.activities.start("healer_post", clock.t, vera.state)
                        vera.activities.next_decide = clock.t + 3600
                    clock.t += 1

            asyncio.run(day())
            prompts = {n: m.build_prompt("проверка", {}) for n, m in minds.items()}
            recent = {n: mems[n].recent_events(50) for n in minds}
            kinds = {n: [r[0] for r in mems[n].db.execute("SELECT kind FROM events ORDER BY id")] for n in minds}
            calls = {n: mems[n].db.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] for n in minds}
            bus_all = [dict(zip(("ts", "bot", "kind"), r)) for r in
                       buses["Arkady"].db.execute("SELECT ts, bot, kind FROM world_events ORDER BY id")]
            dream = {n: mems[n].get("dream") or {} for n in minds}
            for m in mems.values():
                m.close()
            for b in buses.values():
                b.close()
        end = clock.t
        for n in minds:
            self.assertEqual(replay.invariants(sent[n], llm_calls=calls[n]), [], n)
            self.assertIsInstance(recent[n], list)
            self.assertEqual(len(prompts[n]), 2, f"промпт {n} строится")
            self.assertTrue(dream[n].get("kind"), f"мечта {n} выбрана")
            self.assertTrue(t0 <= dream[n]["since"] <= end, f"мечта {n}: время реплея")
        self.assertTrue(all(t0 <= r["ts"] <= end for r in bus_all), "шина мира: время реплея")
        self.assertLess(weighted_max[0], 5.0, f"мотивы в пределах: {weighted_max[0]}")
        # лекарь: пост открыт, Heal Arkady — только с поста, вывеска не мешала касту
        self.assertIn("post", marks)
        casts = [(t, a, s) for t, a, s in sent["Vera"] if a["action"] == "skill_on_player"]
        self.assertTrue(casts, "лекарь лечил")
        post = (237, 310)
        for t, a, s in casts:
            self.assertEqual(s.get("map"), "prontera")
            self.assertLessEqual(max(abs(s["x"] - post[0]), abs(s["y"] - post[1])), 4, f"каст не с поста {t}")
            self.assertFalse(s.get("dead"))
        self.assertIn("healer_post_start", kinds["Vera"])
        self.assertIn("healer_heal", kinds["Vera"])
        self.assertIn("healer_asked", kinds["Arkady"])
        # заказ: Vera заказала, Arkady взял и выполнил одной сделкой
        self.assertIn("order_posted", kinds["Vera"])
        self.assertIn("order_taken", kinds["Arkady"])
        self.assertIn("order_done", kinds["Arkady"])
        self.assertEqual(kinds["Arkady"].count("trade_sold"), 1, "Jellopy продан один раз")
        self.assertIn("order_closed", kinds["Vera"])
        self.assertEqual(world.bodies["Arkady"]["items"]["909"] + world.bodies["Vera"]["items"].get("909", 0), 40,
                         "предметы не размножились")
        # единственная Poring Card — в альбоме (collection): её не предлагают даже в первом такте до засева альбома
        self.assertFalse([a for _, a, _ in sent["Arkady"] if ":4001:" in str(a.get("text", ""))
                          or (a["action"] == "offer_sell" and str(a.get("item")) == "4001")])


class ProtocolSurfaceTest(unittest.TestCase):
    def test_every_safe_action_has_bridge_branch(self):
        """Действие, которое safety пропускает, исполняет brainBridge.pl (и наоборот: у моста нет лишних веток)."""
        from live_brain import safety
        pl = (BRAIN_DIR.parent / "bots" / "plugins" / "brainBridge" / "brainBridge.pl").read_text(encoding="utf-8")
        body = pl[pl.index("sub actionToCommand"):pl.index("sub handleLine")]
        bridge = set(re.findall(r"\$kind eq '([a-z_]+)'", body))
        self.assertEqual(set(safety.ACTIONS + safety.PLAN_ACTIONS), bridge)

    def test_moving_market_actions_are_moves(self):
        """replay.invariants: offer_sell ведёт тело к покупателю — мёртвому его слать нельзя."""
        self.assertIn("offer_sell", replay.MOVES)
        bad = replay.invariants([(0, {"action": "offer_sell"}, {"dead": True})])
        self.assertTrue(bad)


if __name__ == "__main__":
    unittest.main()
