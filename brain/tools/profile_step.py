#!/usr/bin/env python3
"""perf: замер тика мозга — настоящий Mind со всеми модулями на синтетическом потоке тела (docs/PERF.md).

Сценарии (тела — из tests/test_integration.py, часы реплея подменяют time.time):
  day  — один житель (SimBody): город → охота группой с Vera, kill-события, смерть; шина мира своя;
  two  — два жителя в одном городе (TownWorld) на общей шине мира: лекарь, заказы, сделки.
Что меряется на каждом тике (mind.step() + on_message входящих за эту секунду):
  - время тика (среднее / 95-й перцентиль / максимум), по модулям реестра (обёртка над module.tick);
  - SQL: число выполненных запросов и коммитов (sqlite3 trace callback на всех соединениях, которые открыл мозг),
    по модулям; коммит — COMMIT или запись в соединении без транзакции (autocommit шины мира);
  - --cprofile: топ функций по cumulative.
«Установившийся режим» — тики после прогрева (--warmup секунд игрового времени).

Запуск:  cd brain && python3 tools/profile_step.py [--scenario day|two|all] [--seconds 3600] [--cprofile]
         python3 tools/profile_step.py --markdown        # таблица «модуль → мс/тик, запросов/тик» для docs/PERF.md
Тест-страж (tests/test_perf.py) использует SqlCounter/instrument/run_day отсюда.
"""
import argparse
import asyncio
import cProfile
import json
import pstats
import random
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from unittest import mock

BRAIN = Path(__file__).resolve().parents[1]
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

from live_brain.config import Settings          # noqa: E402
from live_brain.gate import RuleGate            # noqa: E402
from live_brain.memory import Memory            # noqa: E402
from live_brain.mind import Mind                # noqa: E402
from live_brain.world_bus import WorldBus       # noqa: E402

WRITES = ("INSERT", "UPDATE", "DELETE", "REPLACE")
SKIP = ("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "PRAGMA")


class SqlCounter:
    """Счётчик SQL всех соединений sqlite3, открытых внутри `with`: запросы и коммиты по «месту» (self.where)."""

    def __init__(self):
        self.where = "core"
        self.queries = defaultdict(int)
        self.commits = defaultdict(int)
        self.statements = defaultdict(int)      # текст запроса -> сколько раз (для поиска горячих)
        self.conns = []
        self._patch = None

    def total(self):
        return sum(self.queries.values()), sum(self.commits.values())

    def attach(self, conn):
        def trace(sql):
            head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
            if head == "COMMIT":
                self.commits[self.where] += 1
                return
            if head in SKIP:
                return
            self.queries[self.where] += 1
            self.statements[" ".join(sql.split())[:120]] += 1
            if head in WRITES and conn.isolation_level is None and not conn.in_transaction:
                self.commits[self.where] += 1           # autocommit: каждая запись — своя транзакция
        conn.set_trace_callback(trace)
        self.conns.append(conn)
        return conn

    def __enter__(self):
        real = sqlite3.connect

        def connect(*args, **kwargs):
            return self.attach(real(*args, **kwargs))
        self._patch = mock.patch("sqlite3.connect", connect)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


class Probe:
    """Время и SQL по модулям: обёртка над module.tick каждого модуля реестра (вызывается только когда реестр тикает)."""

    def __init__(self, counter):
        self.counter = counter
        self.ms = defaultdict(float)
        self.peak = defaultdict(float)          # самый долгий вызов tick за замер, мс
        self.measuring = False
        self.calls = defaultdict(int)

    def wrap(self, mind):
        for attr in mind.registry.ticks:
            module = getattr(mind, attr, None)
            if module is None:
                continue
            orig = module.tick

            def tick(*a, _orig=orig, _attr=attr, **kw):
                return self._run(_attr, _orig, a, kw)
            module.tick = tick

    def _run(self, attr, fn, a, kw):
        prev = self.counter.where
        self.counter.where = attr
        t = time.perf_counter()
        r = fn(*a, **kw)
        if asyncio.iscoroutine(r):
            return self._await(attr, prev, t, r)
        self._done(attr, prev, t)
        return r

    def _done(self, attr, prev, t):
        took = (time.perf_counter() - t) * 1000
        self.ms[attr] += took
        if self.measuring:
            self.peak[attr] = max(self.peak[attr], took)
        self.calls[attr] += 1
        self.counter.where = prev

    async def _await(self, attr, prev, t, coro):
        try:
            return await coro
        finally:
            self._done(attr, prev, t)


class Stats:
    def __init__(self):
        self.tick_ms = []
        self.q = []
        self.c = []
        self.mod_ms = defaultdict(float)
        self.mod_q = defaultdict(int)
        self.mod_c = defaultdict(int)
        self.mod_calls = defaultdict(int)
        self.mod_peak = defaultdict(float)
        self.ticks = 0

    def summary(self):
        ms = sorted(self.tick_ms)
        n = len(ms) or 1
        return {"ticks": len(ms), "mean_ms": sum(ms) / n, "p95_ms": ms[int(0.95 * (len(ms) - 1))] if ms else 0,
                "max_ms": ms[-1] if ms else 0, "q_per_tick": sum(self.q) / n, "c_per_tick": sum(self.c) / n,
                "q_max": max(self.q or [0]), "c_max": max(self.c or [0])}


class Harness:
    """Общий цикл: тик мира/тела -> on_message -> step; замеры после прогрева."""

    def __init__(self, counter, warmup):
        self.counter = counter
        self.warmup = warmup
        self.stats = Stats()
        self.probes = []

    def snapshot(self):
        mod_ms, mod_q, mod_c, calls = defaultdict(float), defaultdict(int), defaultdict(int), defaultdict(int)
        for p in self.probes:
            for k, v in p.ms.items():
                mod_ms[k] += v
            for k, v in p.calls.items():
                calls[k] += v
        for k, v in self.counter.queries.items():
            mod_q[k] += v
        for k, v in self.counter.commits.items():
            mod_c[k] += v
        return mod_ms, mod_q, mod_c, calls

    async def tick(self, t, t0, work):
        """work() — корутина одного тика (сообщения + step всех жителей)."""
        q0, c0 = self.counter.total()
        before = self.snapshot() if t - t0 >= self.warmup else None
        for p in self.probes:
            p.measuring = before is not None
            for k, v in p.peak.items():
                self.stats.mod_peak[k] = max(self.stats.mod_peak[k], v)
        start = time.perf_counter()
        await work()
        took = (time.perf_counter() - start) * 1000
        if before is None:
            return
        q1, c1 = self.counter.total()
        st = self.stats
        st.tick_ms.append(took)
        st.q.append(q1 - q0)
        st.c.append(c1 - c0)
        after = self.snapshot()
        for i, dst in enumerate((st.mod_ms, st.mod_q, st.mod_c, st.mod_calls)):
            for k, v in after[i].items():
                dst[k] += v - before[i].get(k, 0)
        st.mod_ms["(всё вне модулей)"] += took - sum(after[0][k] - before[0].get(k, 0) for k in after[0])


HISTORY_KINDS = ("activity", "peer_nearby", "chat_private", "trade_sold", "routine_town", "level_up", "died",
                 "healer_heal", "gift_received", "gossip_heard", "aim_done", "explore_found", "meeting_confirmed")


def fill_history(mem, t0, days=14, seed=1):
    """perf: память «прожившего» жителя — kill/loot раз в 30 с по 8 ч охоты в день за days суток и прочие виды
    (≈ 60 событий в час): таблица events не пустая, как на VPS через пару недель."""
    rng = random.Random(seed)
    rows = []
    for d in range(days, 0, -1):
        day0 = t0 - d * 86400
        for i in range(0, 8 * 3600, 30):
            ts = day0 + 9 * 3600 + i
            rows.append((ts, "kill", json.dumps({"monster": "Lunatic", "map": "prt_fild08"})))
            if i % 90 == 0:
                rows.append((ts + 1, "loot", json.dumps({"item": 909, "amount": 1})))
        for i in range(0, 24 * 3600, 60):
            rows.append((day0 + i, rng.choice(HISTORY_KINDS), json.dumps({"name": "Vera", "map": "prontera"})))
    mem.db.executemany("INSERT INTO events (ts, kind, data) VALUES (?, ?, ?)", rows)
    mem.db.commit()
    return len(rows)


def _world():
    from tests.test_integration import WORLD
    return WORLD


def run_day(seconds=3600, warmup=300, start_hour=12, seed=5, counter=None, on_mind=None, history=0):
    """Один житель (SimBody) со всеми модулями, seconds секунд игрового времени. -> (Stats, mind)."""
    from tests.test_integration import BRAIN_DIR, TZ, Clock, SimBody
    counter = counter or SqlCounter()
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    t0 = datetime(2025, 3, 2, start_hour, 0, tzinfo=TZ).timestamp()
    clock, rng = Clock(), random.Random(seed)
    clock.t = t0
    body = SimBody(t0)
    h = Harness(counter, warmup)
    with counter, mock.patch("time.time", clock):
        mem = Memory(root / "m.sqlite")
        if history:
            fill_history(mem, t0, days=history)
        bus = WorldBus(root / "w.sqlite", "Arkady")
        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        mind = None

        async def send(a):
            body.apply(dict(a))
            return 1

        mind = Mind(Settings.from_env({}), persona, mem, send, root / "d.jsonl", RuleGate(),
                    peers={"Arkady", "Vera"}, world=_world(), world_bus_db=bus)
        mind.routine.rng = mind.activities.rng = random.Random(7)
        if on_mind:
            on_mind(mind)
        probe = Probe(counter)
        probe.wrap(mind)
        h.probes.append(probe)

        async def work():
            body.tick(clock.t, rng)
            for m in body.msgs:
                prev, counter.where = counter.where, "on_message"
                await mind.on_message(m)
                counter.where = prev
            body.msgs.clear()
            await mind.step()

        async def loop():
            body.event(kind="chat_private", **{"from": "Vera"}, text="[crew:pref:prt_fild08:38]")
            while clock.t < t0 + seconds:
                await h.tick(clock.t, t0, work)
                if body.asleep:
                    break
                clock.t += 1
        asyncio.run(loop())
        mem.close()
        bus.close()
    tmp.cleanup()
    return h.stats, mind


def run_two(seconds=3600, warmup=300, start_hour=10, counter=None, history=0):
    """Два жителя в городе (TownWorld) на общей шине мира. Статистика — на тик мира (оба step)."""
    from tests.test_integration import BRAIN_DIR, TZ, Clock, TownWorld
    counter = counter or SqlCounter()
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    t0 = datetime(2025, 3, 4, start_hour, 0, tzinfo=TZ).timestamp()
    clock = Clock()
    clock.t = t0
    world = TownWorld(t0)
    pet = {"has": True, "running": False, "items": {}, "eggs": [], "near": {}}
    world.add("Arkady", job="Swordsman", lv=41, hp_pct=45, items={"501": 30, "909": 40, "4001": 1}, pet=dict(pet))
    world.add("Vera", job="Acolyte", lv=38, sex="Female", x=160, y=190, items={"501": 30}, pet=dict(pet),
              support_skills={"AL_HEAL": 10, "AL_BLESSING": 5, "AL_INCAGI": 3})
    vera_world = json.loads(json.dumps(_world()))
    vera_world.setdefault("economy", {}).setdefault("market", {})["wish"] = ["909"]
    h = Harness(counter, warmup)
    minds, closers = {}, []
    with counter, mock.patch("time.time", clock):
        for name, bot, w in (("Arkady", "bot01", _world()), ("Vera", "bot02", vera_world)):
            mem = Memory(root / f"{name}.sqlite")
            if history:
                fill_history(mem, t0, days=history)
            bus = WorldBus(root / "world.sqlite", name)
            closers += [mem, bus]
            persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text())

            def make_send(n):
                async def send(a):
                    world.apply(n, dict(a))
                    return 1
                return send
            minds[name] = Mind(Settings.from_env({}), persona, mem, make_send(name), root / f"{name}.jsonl",
                               RuleGate(), peers={"Arkady", "Vera"}, world=w, world_bus_db=bus)
        for m in minds.values():
            m.routine.rng = m.activities.rng = random.Random(3)
            m.routine.new_day(t0, keep_mode="town")
            m.routine.st["rest_until"] = t0 + 6 * 3600
            probe = Probe(counter)
            probe.wrap(m)
            h.probes.append(probe)

        async def work():
            world.tick(clock.t)
            for name, m in minds.items():
                msgs = sorted((x for x in world.inbox[name] if x["ts"] <= clock.t), key=lambda x: x["ts"])
                world.inbox[name] = [x for x in world.inbox[name] if x["ts"] > clock.t]
                prev, counter.where = counter.where, "on_message"
                for msg in msgs:
                    await m.on_message(msg)
                counter.where = prev
                await m.step()

        async def loop():
            started = False
            while clock.t < t0 + seconds:
                await h.tick(clock.t, t0, work)
                if not started and clock.t >= t0 + 300:
                    started = True
                    await minds["Vera"].activities.start("healer_post", clock.t, minds["Vera"].state)
                    minds["Vera"].activities.next_decide = clock.t + 3600
                clock.t += 1
        asyncio.run(loop())
        for c in closers:
            c.close()
    tmp.cleanup()
    return h.stats, minds


def report(name, st, per=1, markdown=False, top=None):
    s = st.summary()
    n = s["ticks"] or 1
    lines = []
    if markdown:
        lines.append(f"**{name}**: тиков {s['ticks']}, тик среднее {s['mean_ms']:.2f} мс, p95 {s['p95_ms']:.2f} мс, "
                     f"макс {s['max_ms']:.1f} мс; SQL {s['q_per_tick']:.1f} запросов/тик (макс {s['q_max']}), "
                     f"коммитов {s['c_per_tick']:.2f}/тик (макс {s['c_max']})")
        lines.append("")
        lines.append("| модуль | мс/тик | макс мс | запросов/тик | коммитов/тик | вызовов tick/тик |")
        lines.append("|---|---:|---:|---:|---:|---:|")
    else:
        lines.append(f"== {name}: тиков {s['ticks']}  среднее {s['mean_ms']:.2f} мс  p95 {s['p95_ms']:.2f} мс  "
                     f"макс {s['max_ms']:.1f} мс  SQL {s['q_per_tick']:.1f}/тик (макс {s['q_max']})  "
                     f"коммитов {s['c_per_tick']:.2f}/тик (макс {s['c_max']})")
        lines.append(f"{'модуль':<22}{'мс/тик':>9}{'макс мс':>9}{'SQL/тик':>9}{'COMMIT/тик':>12}{'tick/тик':>10}")
    keys = set(st.mod_ms) | set(st.mod_q) | set(st.mod_c)
    rows = sorted(keys, key=lambda k: (-(st.mod_ms.get(k, 0)), k))
    for k in rows[:top] if top else rows:
        vals = (st.mod_ms.get(k, 0) / n, st.mod_q.get(k, 0) / n, st.mod_c.get(k, 0) / n, st.mod_calls.get(k, 0) / n)
        peak = st.mod_peak.get(k)
        peak_s = f"{peak:.1f}" if peak is not None else "—"
        if markdown:
            lines.append(f"| {k} | {vals[0]:.3f} | {peak_s} | {vals[1]:.2f} | {vals[2]:.3f} | {vals[3]:.2f} |")
        else:
            lines.append(f"{k:<22}{vals[0]:>9.3f}{peak_s:>9}{vals[1]:>9.2f}{vals[2]:>12.3f}{vals[3]:>10.2f}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", choices=("day", "two", "all"), default="all")
    ap.add_argument("--seconds", type=int, default=3600, help="игрового времени, с (1 тик = 1 с)")
    ap.add_argument("--warmup", type=int, default=300, help="прогрев, с (не входит в замер)")
    ap.add_argument("--cprofile", action="store_true", help="топ-30 функций по cumulative (cProfile)")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--history", type=int, default=0, help="заполнить память событиями за N суток (≈ 2,4 тыс./сут)")
    ap.add_argument("--statements", type=int, default=0, help="показать N самых частых SQL-запросов")
    args = ap.parse_args()
    runs = (("day", run_day), ("two", run_two)) if args.scenario == "all" else \
        ((args.scenario, run_day if args.scenario == "day" else run_two),)
    for name, fn in runs:
        counter = SqlCounter()
        prof = cProfile.Profile() if args.cprofile else None
        if prof:
            prof.enable()
        st, _ = fn(seconds=args.seconds, warmup=args.warmup, counter=counter, history=args.history)
        if prof:
            prof.disable()
        title = {"day": "один житель (SimBody, охота)", "two": "два жителя в городе (TownWorld, общая шина)"}[name]
        if args.history:
            title += f", память за {args.history} сут"
        print(report(title, st, markdown=args.markdown))
        print()
        if args.statements:
            for sql, k in sorted(counter.statements.items(), key=lambda x: -x[1])[:args.statements]:
                print(f"{k:>8}  {sql}")
            print()
        if prof:
            pstats.Stats(prof).sort_stats("cumulative").print_stats(30)


if __name__ == "__main__":
    main()
