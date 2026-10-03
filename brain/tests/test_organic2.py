"""Метрики органичности v2 и детектор машинности (ORG-113/114, Т-40): синтетическая память и журналы.

Запуск: cd brain && python3 -m unittest -v tests.test_organic2
"""
import contextlib
import io
import json
import random
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import dashboard, organic, world_bus
from live_brain.__main__ import organic_metrics
from live_brain.memory import Memory

NOW = 1790000000.0                      # 2026-09-21, фиксированное «сейчас»
DAY = 86400
SINCE = NOW - DAY
CFG = organic.load_config()


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def dec(t, source, actions, reason="", **extra):
    return dict({"type": "decision", "source": source, "reason": reason, "actions": actions, "rejected": [],
                 "ts": iso(t)}, **extra)


def move(x, y, m="prontera"):
    return {"action": "meet_point", "map": m, "x": x, "y": y}


def whisper(to, text):
    return {"action": "whisper", "to": to, "text": text}


def add(mem, t, kind, data=None):
    mem.db.execute("INSERT INTO events (ts, kind, data) VALUES (?, ?, ?)", (t, kind, json.dumps(data or {})))


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def make_lab(root):
    """Arkady (bot01) и Vera (bot02) за сутки до NOW; bot03 — память без decisions.jsonl."""
    s = SINCE
    h = 3600
    a_rows = [
        dec(s - 100, "routine", [move(156, 185)], "распорядок: отдых в prontera"),       # до окна: владелец начала
        dec(s + 12 * h, "social", [{"action": "stand"}, move(170, 200)], "общение: иду к Kafra"),
        dec(s + 18 * h, "routine", [{"action": "hunt", "map": "prt_fild08"}], "распорядок: охота на prt_fild08"),
        {"type": "activity", "chosen": "stroll", "current": "rest", "ts": iso(s + 4 * h)},
        {"type": "activity", "chosen": "rest", "current": "stroll", "ts": iso(s + 5 * h)},
        dec(s + 3 * h, "social", [{"action": "emote", "id": 2}], "общение: эмоция level"),
        dec(s + 2 * h, "gossip", [whisper("Vera", "Слышал про Payon? [gossip:payon]")]),
        dec(s + 2 * h + 120, "gossip", [whisper("Vera", "Да-да, про Payon [gossip:payon]")]),
        {"type": "social", "event": "said", "to": "Vera", "topic": "condolence", "ts": iso(s + 7 * h)},
    ]
    for i in range(6):
        a_rows.append(dec(s + h + 60 * i, "social", [whisper("Vera", f"Привет {i} [chat:hello:1]")]))
    a_rows.append(dec(s + h + 30, "social", [whisper("Vera", "ага [chat:hello:2]")]))
    a_rows.append(dec(s + h + 40, "economy", [whisper("Vera", "[deal:offer:501:3]")]))
    v_rows = [
        dec(s - 50, "routine", [move(156, 185)], "распорядок: отдых в prontera"),
        dec(s + 2 * h + 60, "gossip", [whisper("Arkady", "Правда? [gossip:payon]")]),
        dec(s + 2 * h + 180, "gossip", [whisper("Arkady", "Пойдём? [gossip:payon]")]),
        {"type": "activity", "chosen": "gathering", "current": "rest", "ts": iso(s + 20 * h)},
        dec(s + 20 * h + 30, "social", [{"action": "stand"}, move(156, 186)], "общение: иду к фонтану"),
    ]
    write_jsonl(root / "state" / "bot01" / "decisions.jsonl", a_rows)
    write_jsonl(root / "state" / "bot02" / "decisions.jsonl", v_rows)
    for bot, name, peer in (("bot01", "Arkady", "Vera"), ("bot02", "Vera", "Arkady"), ("bot03", "Mila", "Vera")):
        (root / "state" / bot).mkdir(parents=True, exist_ok=True)
        mem = Memory(root / "state" / bot / "memory.sqlite")
        mem.set("last_state", {"name": name, "zeny": 60000})
        mem.set("needs", {"wealth": 0.0, "social": 0.5})
        if bot == "bot01":
            mem.set("relation_log", {peer: [{"ts": s + h, "delta": 1, "note": "рядом"},
                                            {"ts": s + 2 * h, "delta": 1, "note": "рядом"},
                                            {"ts": s - 2 * DAY, "delta": 2, "note": "давно"}],
                                     "Stranger": [{"ts": s + h, "delta": 2, "note": "человек"}]})
            add(mem, s - 100, "routine_wake", {"hunted_min": 0, "budget_min": 240})
            add(mem, s + 6 * h, "routine_service", {"hunted_min": 60, "budget_min": 240})
            add(mem, s + 20 * h, "routine_bedtime", {"hunted_min": 200, "budget_min": 240})
            for n, t in (("rest", 1), ("stroll", 4), ("rest", 5)):
                add(mem, s + t * h, "activity", {"name": n})
            for topic, text in (("weather", "Хорошая погода"), ("weather", "Хорошая погода"),
                                ("weather", "Дождь"), ("hello", "Привет")):
                add(mem, s + 2 * h, "social_said", {"peer": peer, "topic": topic, "text": text})
            add(mem, s + 9 * h, "social_walk", {"point": "fountain"})
            add(mem, s + 10 * h, "social_walk", {"point": "cathedral"})
            add(mem, s + 8 * h, "level_up", {"level": 30})
            for i in range(250):
                add(mem, s + 18 * h + i, "kill", {"monster": "Poring", "map": "prt_fild08"})
            add(mem, s + 9 * h, "npc_sold", {"zeny": 1000})
            add(mem, s + 9 * h, "npc_bought", {"zeny": 300})
        if bot == "bot02":
            add(mem, s + h, "activity", {"name": "rest"})
        mem.db.commit()
        mem.close()
    bus = world_bus.WorldBus(root / "state" / "shared" / "world.sqlite", "Vera")
    bus.publish("death_report", {"map": "pay_fild01"}, importance=4, now=s + 6 * h)
    bus.publish("director", {"kind": "quiet"}, importance=1, now=s + 5 * h)
    bus.publish("director", {"kind": "quiet"}, importance=1, now=s + 15 * h)
    bus.close()


class PureFunctionsTest(unittest.TestCase):
    def test_regularity(self):
        self.assertLess(organic.regularity([i * 60 for i in range(20)]), 0.05)     # ровный таймер
        rng = random.Random(7)
        t, ts = 0.0, []
        for _ in range(400):
            t += rng.expovariate(1 / 600)
            ts.append(t)
        self.assertAlmostEqual(organic.regularity(ts), 1.0, delta=0.15)          # пуассоновский поток
        self.assertIsNone(organic.regularity([1, 2, 3, 4]))

    def test_body_owners_sum_and_cut(self):
        rows = [dec(SINCE - 3600, "routine", [move(1, 1)]),                         # до окна — владелец начала
                dec(SINCE + 6 * 3600, "social", [move(2, 2)]),
                dec(SINCE + 12 * 3600, "routine", [{"action": "say", "text": "x"}]),  # не тело — не режет
                dec(NOW + 3600, "healer", [move(3, 3)])]                             # после окна — не считается
        own = organic.body_owners(rows, SINCE, NOW, CFG)
        self.assertAlmostEqual(sum(own.values()), 1.0, places=3)
        self.assertAlmostEqual(own["routine"], 0.25, places=3)
        self.assertAlmostEqual(own["social"], 0.75, places=3)
        self.assertNotIn("healer", own)
        half = organic.body_owners(rows, SINCE + 6 * 3600, SINCE + 12 * 3600, CFG)
        self.assertEqual(half, {"social": 1.0})
        self.assertEqual(organic.body_owners([], SINCE, NOW, CFG), {})

    def test_body_owner_by_reason_and_activity(self):
        rows = [dec(SINCE, "social", [move(1, 1)], "общение: иду к лекарю Vera у собора"),
                {"type": "activity", "chosen": "gathering", "ts": iso(SINCE + 3600)},
                dec(SINCE + 3660, "social", [move(156, 185)], "общение: иду к фонтану")]
        own = organic.body_owners(rows, SINCE, SINCE + 7200, CFG)
        self.assertEqual(set(own), {"healer", "tradition"})

    def test_hotspot_share(self):
        rows = [dec(SINCE, "routine", [move(156, 185)]), dec(SINCE + 3600, "social", [move(170, 200)]),
                dec(SINCE + 7200, "routine", [{"action": "hunt", "map": "prt_fild08"}])]
        hs = CFG["hotspot"]
        self.assertAlmostEqual(organic.hotspot_share(rows, hs, 6, SINCE, SINCE + 4 * 3600, CFG), 0.5)
        self.assertIsNone(organic.hotspot_share([], hs, 6, SINCE, NOW, CFG))

    def test_speech_kinds(self):
        rows = [dec(SINCE + 1, "social", [whisper("Vera", "Привет [chat:hello:1]")]),
                dec(SINCE + 2, "social", [whisper("Vera", "И тебе [chat:hello:2]")]),
                dec(SINCE + 3, "gossip", [whisper("Vera", "Слух [gossip:payon]")]),
                dec(SINCE + 4, "economy", [whisper("Vera", "[deal:offer:501:1]")]),
                dec(SINCE + 5, "llm", [whisper("Human", "hi")]),
                dec(SINCE - 5, "social", [whisper("Vera", "старое [chat:hello:1]")])]
        sp = organic.speech(rows, SINCE, NOW, None, CFG)
        self.assertEqual((sp["opened"], sp["replied"], sp["protocol"], sp["total"]), (2, 1, 2, 5))
        self.assertEqual(sp["by_kind"], {"chat": 2, "gossip": 1, "deal": 1, "free": 1})
        self.assertEqual(organic.speech(rows, SINCE, NOW, {"Vera"}, CFG)["total"], 4)

    def test_pingpong(self):
        def w(t, who, to):
            return dict(dec(t, "gossip", [whisper(to, "x [gossip:payon]")]), _who=who)
        fast = [w(0, "A", "B"), w(60, "B", "A"), w(120, "A", "B"), w(180, "B", "A")]
        found = organic.pingpong(fast, 600, 3)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["label"], found[0]["count"]), ("gossip", 4))
        slow = [w(0, "A", "B"), w(3600, "B", "A"), w(7200, "A", "B"), w(10800, "B", "A")]
        self.assertEqual(organic.pingpong(slow, 600, 3), [])
        chat = [w(0, "A", "B"), w(30, "B", "A")]                                   # обычный ответ — не пинг-понг
        self.assertEqual(organic.pingpong(chat, 600, 3), [])

    def test_similarity(self):
        self.assertAlmostEqual(organic.similarity({"a": 1, "b": 2}, {"a": 2, "b": 4}), 1.0)
        self.assertEqual(organic.similarity({"a": 1}, {"b": 1}), 0.0)
        self.assertIsNone(organic.similarity({}, {"a": 1}))
        v = organic.resident_vector([(SINCE, "activity", {"name": "rest"}), (SINCE, "social_said", {"topic": "x"})])
        self.assertAlmostEqual(sum(v.values()), 2.0)                               # группы нормированы отдельно

    def test_grade_bounds(self):
        g = lambda k, v, ctx=None: organic.grade(k, v, CFG, ctx)          # noqa: E731
        self.assertEqual(g("M2", 2.9), "dead")
        self.assertEqual(g("M2", 3), "alive")
        self.assertEqual(g("M2", 40), "alive")
        self.assertEqual(g("M2", 40.1), "noisy")
        self.assertEqual(g("M13", 0.39), "alive")
        self.assertEqual(g("M13", 0.4), "noisy")
        self.assertEqual(g("M9", 0.19), "noisy")
        self.assertEqual(g("M9", 0.2), "alive")
        self.assertEqual(g("M10", 0.75), "alive")
        self.assertEqual(g("M10", 0.76), "noisy")
        self.assertEqual(g("M18", 1.21), "noisy")
        self.assertEqual(g("M18", 0.09), "noisy")
        self.assertEqual(g("M16", 0), "dead")
        self.assertEqual(g("M15", 2), "noisy")
        self.assertEqual(g("M20", 1), "noisy")
        self.assertEqual(g("M7", 5, {"M7_share": 0.9, "M7_starts": 31}), "noisy")
        self.assertEqual(g("M7", 5, {"M7_share": 0.9, "M7_starts": 30}), "alive")
        self.assertEqual(g("M12", 0.95, {"M1": 0.3}), "dead")
        self.assertEqual(g("M12", 0.95, {"M1": 0.9}), "alive")
        self.assertEqual(g("M12", 0.1), "noisy")
        self.assertIsNone(g("M2", None))
        self.assertIsNone(g("M99", 1))

    def test_day_grade(self):
        self.assertEqual(organic.day_grade({"M1": "dead", "M7": "dead"}, CFG)[0], "dead")
        self.assertEqual(organic.day_grade({"M2": "noisy", "M6": "noisy", "M15": "noisy"}, CFG)[0], "noisy")
        self.assertEqual(organic.day_grade({"M2": "noisy", "M6": "noisy"}, CFG)[0], "alive")
        self.assertEqual(organic.day_grade({"M2": "dead", "M16": "dead"}, CFG)[0], "dead")
        self.assertEqual(organic.day_grade({"M2": "dead", "M16": "dead"}, CFG, quiet=True)[0], "alive")

    def test_hunt_ratio_with_day_reset(self):
        rows = [(SINCE - 10, "routine_wake", {"hunted_min": 100, "budget_min": 200}),
                (SINCE + 10, "routine_service", {"hunted_min": 150, "budget_min": 200}),
                (SINCE + 20, "routine_wake", {"hunted_min": 0, "budget_min": 200}),       # новый день
                (SINCE + 30, "routine_service", {"hunted_min": 50, "budget_min": 200})]
        self.assertEqual(organic.hunt_ratio(rows, SINCE, NOW, None, NOW + 3600), 0.5)  # 50 + 0 + 50
        self.assertIsNone(organic.hunt_ratio([], SINCE, NOW, None, NOW + 3600))

    def test_sleep_and_awake_hours(self):
        rows = [(SINCE + 3600, "routine_sleep", {}), (SINCE + 3 * 3600, "routine_wake", {})]
        sl = organic.sleep_intervals(rows, SINCE, SINCE + 4 * 3600)
        self.assertEqual(sl, [(SINCE + 3600, SINCE + 3 * 3600)])
        self.assertEqual(len(organic.awake_hours(sl, SINCE, SINCE + 4 * 3600)), 2)

    def test_tracebacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "brain.log"
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(SINCE + 100))
            old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(SINCE - 100))
            p.write_text(f"{old} mind: x\nTraceback (most recent call last):\n  boom\n"
                         f"{stamp} mind: y\nTraceback (most recent call last):\n  boom\n", encoding="utf-8")
            self.assertEqual(organic.tracebacks(p, SINCE, NOW), 1)
            self.assertEqual(organic.tracebacks(Path(tmp) / "none.log", SINCE, NOW), 0)


class LabTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        make_lab(self.root)

    def report(self):
        return organic.world_report(self.root, SINCE, NOW, ["bot01", "bot02", "bot03", "bot09"], CFG, now=NOW)

    def test_world_report_metrics(self):
        rep = self.report()
        self.assertEqual(rep["missing"], ["bot09"])
        a = rep["residents"]["bot01"]
        m = a["metrics"]
        self.assertEqual(a["name"], "Arkady")
        self.assertAlmostEqual(sum(a["owners"].values()), 1.0, places=3)
        self.assertAlmostEqual(a["owners"]["routine"], 0.75, places=3)
        self.assertAlmostEqual(a["owners"]["social"], 0.25, places=3)
        self.assertEqual(m["M1"], 0.83)                         # 200 из 240 мин
        self.assertEqual(m["M2"], 8)                            # 6 приветствий + 2 сплетни
        self.assertEqual(m["M3"], 10)                           # + ответ + протокол сделки
        self.assertEqual(a["extra"]["deeds"], 3)                # уровень + 250 побед // 100
        self.assertEqual(m["M4"], round(8 / 3, 2))
        self.assertEqual(m["M5"], 25)                           # 1 повтор из 4
        self.assertEqual(m["M6"], 0.75)
        self.assertEqual(a["grades"]["M6"], "noisy")
        self.assertEqual(m["M7"], 2)
        self.assertEqual(a["grades"]["M7"], "dead")
        self.assertEqual(m["M8"], round(2 / 24, 2))
        self.assertIsNotNone(m["M9"])
        self.assertAlmostEqual(m["M10"], round(12 / 18, 3))    # 12 ч у фонтана из 18 ч в городе
        self.assertEqual(m["M11"], 2)
        self.assertEqual(m["M12"], round(21 / 24, 2))          # шёпоты в 1-й и 2-й час, эмоция в 3-й
        self.assertEqual(m["M13"], 0.25)
        self.assertEqual(m["M14"], round(1 / 18, 1))
        self.assertEqual(m["M17"], 2)                           # только жители, только окно
        self.assertEqual(a["grades"]["M17"], "noisy")
        self.assertEqual(m["M18"], 0.3)
        self.assertEqual(m["M20"], 1)
        self.assertEqual(m["M22"], 0)
        v = rep["residents"]["bot02"]
        self.assertIn("tradition", v["owners"])                 # вечерний круг через social.visit
        w = rep["world"]["metrics"]
        self.assertEqual(w["M15"], 2)
        self.assertEqual(rep["world"]["grades"]["M15"], "noisy")
        self.assertEqual(w["M16"], 1)                           # смерть Vera → соболезнование Arkady
        self.assertEqual(w["M19"], 1.0)
        self.assertEqual(w["M20"], 1)
        self.assertIsNotNone(w["M21"])
        self.assertIn(rep["world"]["grade"], ("dead", "alive", "noisy"))
        for r in rep["residents"].values():
            self.assertIn(r["grade"], ("dead", "alive", "noisy"))

    def test_no_decisions_skips_speech_and_body(self):
        m = self.report()["residents"]["bot03"]
        for k in ("M2", "M3", "M4", "M8", "M9", "M10", "M12", "M13", "M14"):
            self.assertIsNone(m["metrics"][k], k)
            self.assertIsNone(m["grades"][k], k)
        self.assertEqual(m["owners"], {})

    def test_read_only(self):
        before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.root.rglob("*") if p.is_file()}
        self.report()
        after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in before}      # -wal/-shm ro-чтения не в счёт
        self.assertEqual(before, after)
        self.assertFalse((self.root / "run" / "alerts.log").exists())

    def test_rotated_decisions_are_read(self):
        d = self.root / "state" / "bot01"
        (d / "decisions.jsonl").rename(d / "decisions.jsonl.1")
        write_jsonl(d / "decisions.jsonl", [dec(SINCE + 23 * 3600, "social", [whisper("Vera", "x [chat:bye:1]")])])
        self.assertEqual(self.report()["residents"]["bot01"]["metrics"]["M2"], 9)

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = organic.main(list(args) + ["--lab-root", str(self.root), "--bots", "bot01", "bot02",
                                              "--now", str(NOW)])
        return code, out.getvalue()

    def test_cli_table_json_and_alerts(self):
        code, text = self.run_cli("all", "--days", "1")
        self.assertEqual(code, 0)
        self.assertIn("M2 шёпотов начато", text)
        self.assertIn("кто вёл тело", text)
        self.assertIn("пинг-понг [gossip:]", text)
        code, text = self.run_cli("Vera", "--json", "--days", "2")
        data = json.loads(text)
        self.assertEqual(list(data["residents"]), ["bot02"])
        self.assertEqual(data["days"], 2)
        code, text = self.run_cli("all", "--alerts")
        log = (self.root / "run" / "alerts.log").read_text(encoding="utf-8").splitlines()
        self.assertTrue(log)
        rec = json.loads(log[0])
        self.assertEqual(set(rec), {"ts", "bot", "kind", "text"})
        self.assertTrue(rec["kind"].startswith("organic:"))
        self.run_cli("all", "--alerts")                        # повтор в тот же день — без дублей
        self.assertEqual(len((self.root / "run" / "alerts.log").read_text(encoding="utf-8").splitlines()), len(log))
        self.assertEqual(self.run_cli("bot77")[0], 2)

    def test_alert_needs_two_days(self):
        today = {"residents": {"b": {"name": "A", "grades": {"M2": "noisy", "M7": "dead"},
                                     "metrics": {"M2": 50, "M7": 1}}},
                 "world": {"grades": {"M15": "noisy"}, "metrics": {"M15": 3}}}
        prev = {"residents": {"b": {"name": "A", "grades": {"M2": "noisy", "M7": "alive"}}},
                "world": {"grades": {"M15": "alive"}, "metrics": {}}}
        items = organic.alert_lines(today, prev, CFG)
        self.assertEqual([(w, k) for w, k, _, _ in items], [("A", "M2")])
        self.assertEqual(len(organic.write_alerts(self.root, items, CFG, NOW)), 1)
        self.assertEqual(organic.write_alerts(self.root, items, CFG, NOW + 60), [])
        self.assertEqual(len(organic.write_alerts(self.root, items, CFG, NOW + DAY)), 1)   # новый день — снова

    def test_dashboard_band(self):
        data = dashboard.collect(self.root, ["bot01", "bot02"], time.strftime("%Y-%m-%d", time.gmtime(NOW - 1)),
                                 0, now=NOW - 1)
        self.assertIsNotNone(data["organic2"])
        page = dashboard.render(data)
        self.assertIn("Кто вёл тело", page)
        self.assertIn('class="band"', page)
        self.assertIn("M13", page)


class OrganicMetricsCompatTest(unittest.TestCase):
    def test_old_keys_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(Path(tmp) / "memory.sqlite")
            add(mem, NOW - 10, "level_up", {"level": 2})
            mem.db.commit()
            m = organic_metrics(mem, NOW - DAY, now=NOW)
            mem.close()
        for k in ("занятий", "мест в городе", "реплик без LLM", "из них о событиях", "сон, ч", "вызовов моделей",
                  "дневников", "открытых мест", "карт в альбоме", "трофеев за период", "видов в бестиарии",
                  "открытий первым", "разнообразие занятий", "повторов реплик, %"):
            self.assertIn(k, m)
        self.assertEqual(m["дел"], 1)


if __name__ == "__main__":
    unittest.main()
