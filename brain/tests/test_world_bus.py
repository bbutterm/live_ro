"""Шина событий мира (world_bus.py, ORG-045): публикация, курсор, дубли, очистка, хроника, мозг.

Запуск: cd brain && python3 -m unittest -v tests.test_world_bus
"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import world_bus
from live_brain.chronicle import chronicle
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.world_bus import KEEP_DAYS, WorldBus

BRAIN_DIR = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class WorldBusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state" / "shared" / "world.sqlite"
        self.clock = Clock()
        self.a = WorldBus(self.path, "Arkady", clock=self.clock)
        self.v = WorldBus(self.path, "Vera", clock=self.clock)

    def tearDown(self):
        self.a.close()
        self.v.close()
        self.tmp.cleanup()

    def test_publish_and_read_by_cursor(self):
        self.a.publish("level_up", {"level": 42}, 3)
        self.v.publish("death_report", {"map": "prt_fild08"}, 4)
        self.a.publish("gift_given", {"peer": "Vera"}, 1)
        others = self.a.read(others=True)
        self.assertEqual([e["bot"] for e in others], ["Vera"], "свои события житель не читает как новости")
        allv = self.v.read(after_id=0)
        self.assertEqual(len(allv), 3)
        cur = allv[0]["id"]
        self.assertEqual([e["kind"] for e in self.v.read(after_id=cur)], ["death_report", "gift_given"])
        self.assertEqual(len(self.v.read(min_importance=3)), 2, "фильтр по важности")
        self.assertEqual(self.a.last_id(), allv[-1]["id"])

    def test_event_dedup_and_period(self):
        self.assertIsNotNone(self.a.publish("event", {"text": "Ивент в Пронтере"}, 3))
        self.assertIsNone(self.v.publish("event", {"text": "Ивент в Пронтере"}, 3), "то же объявление — одна запись")
        self.clock.t += 700
        self.assertIsNotNone(self.v.publish("event", {"text": "Ивент в Пронтере"}, 3), "через 10+ мин — снова")
        rows = world_bus.read_period(self.path, self.clock.t - 800, self.clock.t + 1)
        self.assertEqual(len(rows), 2)

    def test_prune_and_limits(self):
        self.a.publish("level_up", {"level": 1}, 9, now=self.clock.t - (KEEP_DAYS + 1) * 86400)
        self.a.publish("level_up", {"level": 2}, 0)
        self.a.prune(self.clock.t)
        rows = self.a.read()
        self.assertEqual([e["data"]["level"] for e in rows], [2], "старше 30 дней — удалено")
        self.assertEqual(rows[0]["importance"], 1, "важность в пределах 1..5")
        self.a.publish("diary", {"text": "x" * 5000})
        big = self.a.read()[-1]["data"]
        self.assertTrue(big.get("truncated"))
        self.assertLessEqual(len(json.dumps(big, ensure_ascii=False)), world_bus.MAX_DATA)

    def test_lab_path(self):
        self.assertEqual(world_bus.lab_path("/lab/state/bot01/decisions.jsonl"),
                         Path("/lab/state/shared/world.sqlite"))
        self.assertIsNone(world_bus.lab_path("/tmp/x/d.jsonl"), "не раскладка лаборатории — шина не включается")


class MindBusTest(unittest.TestCase):
    """Два мозга в раскладке лаборатории: публикация из памяти, новости другого жителя, хроника."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.minds = {}
        for bot, name in (("bot01", "Arkady"), ("bot02", "Vera")):
            sd = self.root / "state" / bot
            sd.mkdir(parents=True)
            persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())

            async def send(a):
                return 1

            mem = Memory(sd / "memory.sqlite")
            m = Mind(Settings.from_env({}), persona, mem, send, sd / "decisions.jsonl", RuleGate(),
                     peers={"Arkady", "Vera"})
            m.state = {"name": name, "lv": 42}
            self.minds[name] = m

    def tearDown(self):
        for m in self.minds.values():
            m.world.bus.close()
            m.mem.close()
        self.tmp.cleanup()

    def test_pump_poll_prompt_chronicle(self):
        a, v = self.minds["Arkady"], self.minds["Vera"]
        self.assertIsNotNone(a.world, "раскладка state/<bot> — шина включена")
        a.mem.add_event("level_up", {"level": 1})                  # до первого запуска — история не переносится
        self.assertEqual(a.world.pump(), 0)
        v.world.poll()                                              # курсор чтения Vera — на текущий конец шины
        a.mem.add_event("kill", {"monster": "Poring"})
        a.mem.add_event("level_up", {"level": 43})
        a.mem.add_event("death_report", {"map": "prt_fild08", "cause": "Lunatic"})
        a.learn_hunt_map("pay_fild01")
        a.mem.add_event("routine_grow", {"hunted_min": 0, "budget_min": 0})
        self.assertEqual(a.world.pump(), 3, "kill — не публикуется; уровень, смерть, новое место — да")
        self.assertEqual(a.world.pump(), 0, "курсор не повторяет")
        new = v.world.poll()
        self.assertEqual([e["kind"] for e in new], ["level_up", "death_report", "hunt_map_new"])
        self.assertEqual(new[-1]["data"], {"map": "pay_fild01"})
        news = v.world.summary()
        self.assertTrue(any("Arkady: погиб на prt_fild08 (бил Lunatic)" in n for n in news), news)
        user = json.loads(v.build_prompt("тест", {})[1]["content"])
        self.assertIn("новости_мира", user)
        self.assertTrue(any("достиг 43 уровня" in n for n in user["новости_мира"]))
        self.assertIsNone(a.world.summary(), "свои события — не новости")

        asyncio.run(a.on_event({"kind": "world_msg", "text": "Ивент: нашествие порингов!", "source": "sys"}))
        asyncio.run(v.on_event({"kind": "world_msg", "text": "Ивент: нашествие порингов!", "source": "sys"}))
        a.world.pump()
        v.world.pump()
        events = [e for e in a.world.bus.read() if e["kind"] == "event"]
        self.assertEqual(len(events), 1, "одно объявление от двух жителей — одна запись")

        day = time.strftime("%Y-%m-%d", time.gmtime())
        text = chronicle(self.root, ["bot01", "bot02"], day=day, tz_hours=0)
        self.assertIn("## События мира", text)
        self.assertIn("Arkady: осваивает новое место охоты Южный лес Пайона", text)   # places: ORG-084
        self.assertIn("объявление сервера: «Ивент: нашествие порингов!»", text)
        self.assertIn("Arkady: погиб на Южном поле Пронтеры (бил Lunatic) !", text, "важность 4 — отметка")   # places:

    def test_bus_error_does_not_break_tick(self):
        a = self.minds["Arkady"]
        a.world.bus.close()
        a.world.tick()                                               # закрытая БД — предупреждение, не исключение
        a.world.bus = WorldBus(self.root / "state" / "shared" / "world.sqlite", "Arkady")


def _open_bus(path, barrier, out):                     # flaky: процесс-житель, открывающий шину при запуске
    barrier.wait()
    try:
        bus = WorldBus(path, "x")
        out.put(bus.db.execute("PRAGMA journal_mode").fetchone()[0])
    except Exception as e:                              # noqa: BLE001 — причина уходит в сообщение теста
        out.put(f"{type(e).__name__}: {e}")


class ConcurrentCreateTest(unittest.TestCase):
    """flaky: MeetingTest (два настоящих мозга) изредка падал — один мозг умирал при старте с «database is
    locked» на PRAGMA journal_mode=WAL новой общей шины (оба жителя создают state/shared/world.sqlite
    одновременно; busy-обработчик SQLite эту смену режима не ждёт). Здесь два процесса стартуют по барьеру."""

    def test_two_residents_create_bus_at_once(self):
        import multiprocessing as mp
        ctx = mp.get_context("fork")
        with tempfile.TemporaryDirectory() as tmp:
            results = []
            for i in range(30):                        # без повтора ≈ треть прогонов падала (замер 78/200)
                barrier, out = ctx.Barrier(2), ctx.Queue()
                procs = [ctx.Process(target=_open_bus, args=(str(Path(tmp) / f"w{i}.sqlite"), barrier, out))
                         for _ in range(2)]
                for p in procs:
                    p.start()
                for p in procs:
                    p.join(30)
                results += [out.get(timeout=5) for _ in procs]
            self.assertEqual(set(results), {"wal"}, [r for r in results if r != "wal"][:3])

    def test_enable_wal_retries_only_lock(self):
        import sqlite3

        class Db:
            def __init__(self, errors):
                self.errors = list(errors)

            def execute(self, sql):
                if self.errors:
                    raise sqlite3.OperationalError(self.errors.pop(0))
                return self

            def fetchone(self):
                return ("wal",)

        naps = []
        self.assertEqual(world_bus.enable_wal(Db(["database is locked"] * 3), sleep=naps.append), "wal")
        self.assertEqual(len(naps), 3)
        with self.assertRaises(sqlite3.OperationalError):          # не блокировка — сразу наружу
            world_bus.enable_wal(Db(["disk I/O error"]), sleep=naps.append)
        with self.assertRaises(sqlite3.OperationalError):          # блокировка дольше timeout — наружу
            world_bus.enable_wal(Db(["database is locked"] * 1000), timeout=0, sleep=naps.append)


if __name__ == "__main__":
    unittest.main()
