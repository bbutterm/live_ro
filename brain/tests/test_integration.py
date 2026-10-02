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
    loot = {"501": 30, "4001": 1, "909": 40}
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
            persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
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
        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        self.mind = Mind(Settings.from_env({}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=WORLD)

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
        self.state()
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
            persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
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
