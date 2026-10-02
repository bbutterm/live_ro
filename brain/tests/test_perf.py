"""perf: тест-страж производительности мозга (docs/PERF.md).

Новый модуль не должен незаметно «съесть» VPS: настоящий Mind со всеми модулями на синтетическом потоке тела
(tools/profile_step.py, сценарий day) в установившемся режиме укладывается в бюджет SQL и коммитов на тик,
ни один модуль не делает больше MODULE_SQL запросов за тик, а модули с TICK_EVERY тикают не чаще объявленного.
Время на тик здесь не проверяется (зависит от машины) — его меряет tools/profile_step.py.

Если тест упал после добавления модуля: посмотрите `python3 tools/profile_step.py --statements 20` —
какой запрос повторяется каждый тик; читайте kv/отношения через mind.mem (кэш такта), тяжёлое — раз в N секунд
(TICK_EVERY или свой next_tick), поток событий — по курсору id.

Запуск: cd brain && python3 -m unittest -v tests.test_perf
"""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from live_brain import modules
from live_brain.memory import Memory
from live_brain.plans import PlanStore
from tools.profile_step import SqlCounter, run_day

TICK_SQL = 20          # запросов SQL на тик (среднее, установившийся режим; сейчас ≈ 11)
TICK_COMMITS = 1.0     # коммитов на тик (среднее; сейчас ≈ 0,3)
MODULE_SQL = 4         # запросов на тик у одного модуля (сейчас максимум ≈ 2: routine, crew)
SECONDS, WARMUP = 1200, 300


class SteadyStateBudgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stats, cls.mind = run_day(seconds=SECONDS, warmup=WARMUP)
        cls.summary = cls.stats.summary()

    def test_sql_per_tick(self):
        s = self.summary
        self.assertGreater(s["ticks"], SECONDS - WARMUP - 60, "замер прошёл весь период")
        self.assertLessEqual(s["q_per_tick"], TICK_SQL, f"SQL на тик: {s['q_per_tick']:.1f} (docs/PERF.md)")
        self.assertLessEqual(s["c_per_tick"], TICK_COMMITS, f"коммитов на тик: {s['c_per_tick']:.2f}")

    def test_sql_per_module(self):
        n = self.summary["ticks"]
        heavy = {k: round(v / n, 2) for k, v in self.stats.mod_q.items() if v / n > MODULE_SQL}
        self.assertEqual(heavy, {}, "модуль делает слишком много SQL за тик (кэш такта mind.mem, TICK_EVERY)")

    def test_tick_every_respected(self):
        n = self.summary["ticks"]
        checked = 0
        for attr, every in modules.REGISTRY.every.items():
            if getattr(self.mind, attr, None) is None:
                continue
            checked += 1
            calls = self.stats.mod_calls.get(attr, 0)
            self.assertLessEqual(calls, n / every + 1, f"{attr}: {calls} вызовов tick за {n} с при TICK_EVERY={every}")
        self.assertGreater(checked, 5, "модули с TICK_EVERY включены")


class Every:
    """Модуль без своего next_tick: срок ведёт реестр."""
    ATTR, TICK_ORDER, TICK_EVERY = "every", 10, 10

    def __init__(self, log):
        self.log = log

    def tick(self):
        self.log.append(("every", time.time()))


class Own:
    """Модуль со своим next_tick (как aims, dream, rivalry): реестр его уважает, сброс next_tick = 0 работает."""
    ATTR, TICK_ORDER, TICK_EVERY = "own", 20, 30

    def __init__(self, log):
        self.log = log
        self.next_tick = 0.0

    def tick(self):
        now = time.time()
        if now < self.next_tick:
            raise AssertionError("реестр позвал tick раньше next_tick")
        self.next_tick = now + 30
        self.log.append(("own", now))


class Plain:
    ATTR, TICK_ORDER = "plain", 30

    def __init__(self, log):
        self.log = log

    def tick(self):
        self.log.append(("plain", time.time()))


class RegistryTickEveryTest(unittest.TestCase):
    def test_registry_throttles_and_honours_next_tick(self):
        reg = modules.Registry((Every, Own, Plain))
        self.assertEqual(reg.every, {"every": 10, "own": 30})
        log = []
        mind = SimpleNamespace(every=Every(log), own=Own(log), plain=Plain(log))
        t = [1000.0]
        with mock.patch("time.time", lambda: t[0]):
            for i in range(60):
                if i == 45:
                    mind.own.next_tick = 0           # сброс срока модулем/тестом: следующий такт — вызов
                asyncio.run(reg.tick(mind))
                t[0] += 1
        calls = lambda name: [ts - 1000 for n, ts in log if n == name]
        self.assertEqual(calls("plain"), list(range(60)), "без TICK_EVERY — каждый такт")
        self.assertEqual(calls("every"), [0, 10, 20, 30, 40, 50])
        self.assertEqual(calls("own"), [0, 30, 45])


class TickCacheTest(unittest.TestCase):
    """Кэш такта (Memory.tick, PlanStore.cache) сквозной: поведение то же, что без него."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "m.sqlite"
        self.mem = Memory(self.path)
        self.addCleanup(self.mem.close)

    def test_kv_and_relations_coherent(self):
        mem = self.mem
        mem.set("a", {"x": 1})
        mem.touch_relation("Vera")
        with SqlCounter() as c:
            c.attach(mem.db)
            with mem.tick():
                v = mem.get("a")
                v["x"] = 99                                   # изменение результата не портит кэш
                self.assertEqual(mem.get("a"), {"x": 1})
                mem.set("a", {"x": 2})
                self.assertEqual(mem.get("a"), {"x": 2})
                self.assertIsNone(mem.get("нет"))
                self.assertEqual(mem.get("нет", 5), 5)
                self.assertEqual(mem.relation("Vera")["affinity"], 0)
                mem.update_relation("Vera", 2)
                self.assertEqual(mem.relation("Vera")["affinity"], 2)
                self.assertEqual(mem.relation("Vera")["affinity"], 2)
            self.assertEqual(sum(c.commits.values()), 1, "один коммит на такт")
        # между тактами память правят мимо методов (тесты, другие процессы) — следующий такт это видит
        mem.db.execute("UPDATE kv SET value = '3' WHERE key = 'a'")
        mem.db.commit()
        with mem.tick():
            self.assertEqual(mem.get("a"), 3)

    def test_deferred_commit_visible_to_other_connections_after_tick(self):
        import sqlite3
        other = sqlite3.connect(str(self.path))
        self.addCleanup(other.close)
        with self.mem.tick():
            self.mem.set("k", 1)
            self.mem.add_event("e", {})
        self.assertEqual(other.execute("SELECT value FROM kv WHERE key = 'k'").fetchone(), ("1",))
        self.assertEqual(other.execute("SELECT COUNT(*) FROM events WHERE kind = 'e'").fetchone(), (1,))
        self.mem.set("outside", 1)                              # вне такта — коммит сразу, как раньше
        self.assertEqual(other.execute("SELECT value FROM kv WHERE key = 'outside'").fetchone(), ("1",))

    def test_set_changed_skips_identical_snapshot(self):
        with SqlCounter() as c:
            c.attach(self.mem.db)
            for _ in range(3):
                self.mem.set_changed("last_state", {"hp": 1})
            self.mem.set_changed("last_state", {"hp": 2})
        self.assertEqual(sum(c.commits.values()), 2)
        self.assertEqual(self.mem.get("last_state"), {"hp": 2})
        self.mem.set("last_state", {"hp": 1})                  # обычный set сбрасывает память set_changed
        self.mem.set_changed("last_state", {"hp": 2})
        self.assertEqual(self.mem.get("last_state"), {"hp": 2})

    def test_kind_ts_index_only_for_count_events(self):
        """Частичный индекс events_kind_ts видит только count_events: планы остальных запросов прежние."""
        from live_brain.memory import KIND_TS_WHERE
        db = self.mem.db
        plan = lambda sql, args: " ".join(r[-1] for r in db.execute("EXPLAIN QUERY PLAN " + sql, args))
        self.assertIn("events_kind_ts", plan(f"SELECT COUNT(*) FROM events WHERE kind = ? AND ts >= ? "
                                             f"AND {KIND_TS_WHERE}", ("kill", 0)))
        for sql, args in (("SELECT COUNT(*) FROM events WHERE kind = ? AND ts >= ?", ("kill", 0)),
                          ("SELECT id FROM events WHERE id > ? AND kind IN ('a', 'b') ORDER BY id", (5,)),
                          ("SELECT data FROM events WHERE ts >= ? AND kind IN ('a', 'b')", (0,)),
                          ("SELECT MAX(ts) FROM events WHERE kind = ?", ("a",))):
            self.assertNotIn("events_kind_ts", plan(sql, args), sql)
        self.mem.add_event("kill", {})
        self.assertEqual(self.mem.count_events("kill", 0), 1)

    def test_plan_cache_invalidated_by_writes(self):
        store = PlanStore(self.mem.db)
        store.cache(True)
        self.assertIsNone(store.active())
        store.create(id="p1", kind="meet", partner="Vera", role="proposer", map="prontera", x=1, y=1,
                     status="planned")
        self.assertEqual(store.active()["id"], "p1")
        store.update("p1", status="failed")
        self.assertIsNone(store.active())
        store.cache(False)


if __name__ == "__main__":
    unittest.main()
