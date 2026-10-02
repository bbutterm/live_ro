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



class BodyOwnershipTest(unittest.TestCase):
    """Стыки «за тело»: сделка между жителями держит тело; сон (relog), квест профессии и чужие модули ждут."""

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
