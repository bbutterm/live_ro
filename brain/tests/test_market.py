"""Рыночный день (market.py, ORG-071, ТЗ Т-27): пороги дня, сбор на площадь, лавка Merchant, итог в летописи.

Настоящий Mind (мир goals.json, два жителя) с подменными часами модуля и календаря; поддельное тело только
записывает действия. Суббота мира — 2026-10-03 (часовой пояс goals.json: +3).
Запуск: cd brain && python3 -m unittest -v tests.test_market
"""
import asyncio
import copy
import json
import os
import re
import struct
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain.chronicle import LINES
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.market import MarketDay
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT = BRAIN_DIR.parent
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
TZ = timezone(timedelta(hours=WORLD.get("timezone_offset_hours", 0)))
SAT = datetime(2026, 10, 3, 12, tzinfo=TZ).timestamp()        # суббота, полдень мира
FRI, SUN = SAT - 86400, SAT + 86400
RATHENA = Path(os.environ.get("LIVE_RO_RATHENA") or ROOT / "upstream" / "rathena")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Lab:
    def __init__(self, world=None, env=None, t=SAT):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out = []

        async def send(a):
            self.out.append(dict(a))
            return len(self.out)

        persona = json.loads((BRAIN_DIR / "personas" / "bot01.json").read_text())
        persona.pop("sleep", None)
        self.mem = Memory(root / "m.sqlite")
        self.clock = Clock(t)
        self.mind = Mind(Settings.from_env(env or {}), persona, self.mem, send, root / "d.jsonl", RuleGate(),
                         peers={"Arkady", "Vera"}, world=world or copy.deepcopy(WORLD))
        self.md = self.mind.market_day
        if self.md:
            self.md.clock = self.clock
            self.mind.economy.clock = self.clock
        if self.mind.social:
            self.mind.social.clock = self.clock

    def state(self, **kw):
        s = {"type": "state", "name": "Arkady", "map": "prontera", "x": 156, "y": 185, "hp_pct": 100, "lv": 30,
             "dead": False, "weight_pct": 10, "zeny": 60000, "items": {"501": 30}, "players": []}
        s.update(kw)
        asyncio.run(self.mind.on_message(s))

    def town(self):
        self.mind.routine.st.update(mode="town", arrived=True)

    def tick(self):
        asyncio.run(self.md.tick())

    def events(self, kind):
        return [json.loads(d) for (d,) in self.mem.db.execute("SELECT data FROM events WHERE kind = ? ORDER BY id",
                                                              (kind,))]

    def close(self):
        self.mem.close()
        self.tmp.cleanup()


class ThresholdsTest(unittest.TestCase):
    def setUp(self):
        self.lab = Lab()

    def tearDown(self):
        self.lab.close()

    def test_market_day_and_back(self):
        lab, m = self.lab, self.lab.mind
        base_gap, base_trades = m.economy.market["offer_gap_minutes"], m.economy.market["trades_per_day"]
        base_lot, base_post = m.economy.prices.cfg["valuable_lot"], m.orders.cfg["post_gap_hours"] if m.orders else None
        self.assertTrue(lab.md.is_market(SAT))
        self.assertFalse(lab.md.is_market(FRI))
        lab.state()
        lab.tick()
        self.assertEqual(m.economy.market["offer_gap_minutes"], base_gap * 0.5)
        self.assertEqual(m.economy.market["trades_per_day"], base_trades + 4)
        self.assertEqual(m.economy.market["shop_hours"], 2)
        self.assertEqual(m.economy.prices.cfg["valuable_lot"], base_lot * 0.5)
        self.assertEqual(m.society.cfg["room_sign_chance"], 0.9)
        self.assertEqual(m.social.cfg["point_weights"]["market"], 6)
        if m.orders:
            self.assertEqual(m.orders.cfg["post_gap_hours"], base_post * 0.5)
        lab.clock.t = SUN
        lab.tick()
        self.assertEqual(m.economy.market["offer_gap_minutes"], base_gap)
        self.assertEqual(m.economy.market["trades_per_day"], base_trades)
        self.assertEqual(m.economy.prices.cfg["valuable_lot"], base_lot)
        self.assertEqual(m.social.cfg["point_weights"]["market"], 0, "в обычный день площадь — не цель прогулки")
        self.assertNotEqual(m.society.cfg["room_sign_chance"], 0.9)

    def test_point_in_social_copy(self):
        m = self.lab.mind
        self.assertEqual(m.social.cfg["points"]["market"]["x"], WORLD["market_day"]["point"]["x"])
        self.assertNotIn("market", WORLD["social"]["points"], "общий словарь мира не тронут")

    def test_walk_prefers_market(self):
        lab, m = self.lab, self.lab.mind
        lab.state()
        lab.tick()
        picks = [m.social.pick_point({"map": "prontera", "x": 237, "y": 310, "players": []})[0] for _ in range(300)]
        self.assertGreater(picks.count("market") / len(picks), 0.4, "в рыночный день площадь — чаще всего")
        lab.clock.t = SUN
        lab.tick()
        picks = [m.social.pick_point({"map": "prontera", "x": 237, "y": 310, "players": []})[0] for _ in range(100)]
        self.assertNotIn("market", picks)


class GatherTest(unittest.TestCase):
    def setUp(self):
        self.lab = Lab()

    def tearDown(self):
        self.lab.close()

    def test_once_per_day(self):
        lab = self.lab
        lab.state()
        lab.tick()
        self.assertFalse(lab.events("market_day_open"), "не в городе — не идёт")
        lab.town()
        lab.mind.social.next_walk = SAT + 3600
        lab.tick()
        self.assertEqual(len(lab.events("market_day_open")), 1)
        self.assertEqual(lab.mind.social.next_walk, SAT, "прогулка — сразу")
        lab.tick()
        self.assertEqual(len(lab.events("market_day_open")), 1, "один раз за день")
        lab.clock.t = SUN
        lab.tick()
        self.assertEqual(len(lab.events("market_day_open")), 1, "в воскресенье — нет")

    def test_merchant_shop_at_market(self):
        lab = self.lab
        lab.town()
        vend = {"can": 1, "open": 0, "overcharge": 0, "cart": {"4001": 1}, "slots": 3}
        lab.state(vend=vend, x=150, y=170)
        lab.mem.set("econ_shop_ts", SAT - 60)           # лавку открывал только что — economy сама бы ждала
        lab.tick()
        asyncio.run(lab.mind.economy.tick())
        self.assertFalse([a for a in lab.out if a["action"] == "offer_shop"], "не у площади — ждём")
        p = WORLD["market_day"]["point"]
        lab.state(vend=vend, x=p["x"], y=p["y"] + 1)
        lab.tick()
        asyncio.run(lab.mind.economy.tick())
        self.assertEqual(len([a for a in lab.out if a["action"] == "offer_shop"]), 1, "у площади — лавка здесь")
        lab.out.clear()
        lab.mem.set("econ_shop_ts", SAT - 60)
        lab.tick()
        asyncio.run(lab.mind.economy.tick())
        self.assertFalse([a for a in lab.out if a["action"] == "offer_shop"], "один раз за день")


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.lab = Lab(t=SAT)

    def tearDown(self):
        self.lab.close()

    def add(self, kind, data, ts):
        self.lab.mem.add_event(kind, data)
        self.lab.mem.db.execute("UPDATE events SET ts = ? WHERE id = (SELECT MAX(id) FROM events)", (ts,))
        self.lab.mem.db.commit()

    def test_summary_next_day(self):
        lab = self.lab
        lab.state()
        lab.tick()                                            # суббота: запомнил день
        self.add("trade_sold", {"peer": "Vera", "item": "909", "amount": 10, "price": 300, "paid": 300}, SAT)
        self.add("trade_bought", {"peer": "Vera", "item": "938", "amount": 5, "price": 200}, SAT + 3600)
        self.add("vend_sold", {"zeny": 1500}, SAT + 7200)
        self.add("trade_sold", {"peer": "Vera", "item": "909", "amount": 1, "price": 50}, FRI)   # пятница — не в счёт
        lab.tick()
        self.assertFalse(lab.events("market_day_summary"), "день не кончился")
        lab.clock.t = SUN
        lab.tick()
        rows = lab.events("market_day_summary")
        self.assertEqual(len(rows), 1)
        d = rows[0]
        self.assertEqual((d["date"], d["deals"], d["sold"], d["bought"], d["vend"]), ("2026-10-03", 2, 1, 1, 1500))
        self.assertEqual(LINES["market_day_summary"](d), "рыночный день: 2 сделок (продал 1, купил 1, из лавки 1500z)")
        lab.clock.t = SUN + 3600
        lab.tick()
        self.assertEqual(len(lab.events("market_day_summary")), 1, "итог — один раз")

    def test_no_summary_after_ordinary_day(self):
        lab = self.lab
        lab.clock.t = FRI
        lab.state()
        lab.tick()
        lab.clock.t = SAT
        lab.tick()
        self.assertFalse(lab.events("market_day_summary"), "пятница не рыночная — итога нет")


class SwitchTest(unittest.TestCase):
    def test_disabled(self):
        w = copy.deepcopy(WORLD)
        w["market_day"] = dict(w.get("market_day") or {}, enabled=False)
        lab = Lab(world=w)
        try:
            self.assertIsNone(lab.mind.market_day)
            self.assertNotIn("market", lab.mind.social.cfg["points"])
        finally:
            lab.close()
        lab = Lab(env={"BRAIN_DISABLE": "market_day"})
        try:
            self.assertIsNone(lab.mind.market_day)
        finally:
            lab.close()
        lab = Lab(env={"BRAIN_DISABLE": "calendar"})
        try:
            self.assertIsNone(lab.mind.market_day, "без календаря — нет рыночного дня")
        finally:
            lab.close()


def loaded_npc_files():
    """Скрипты, которые грузит map-server renewal: npc/re/scripts_main.conf с импортами (npc:/import:)."""
    files, seen = [], set()

    def conf(path):
        if path in seen or not path.exists():
            return
        seen.add(path)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*(npc|import):\s*(\S+)", line.split("//")[0])
            if m:
                conf(RATHENA / m.group(2)) if m.group(1) == "import" else files.append(RATHENA / m.group(2))
    conf(RATHENA / "npc/re/scripts_main.conf")
    return [f for f in files if f.exists()]


@unittest.skipUnless((RATHENA / "db/re/map_cache.dat").exists(), "нет upstream/rathena")
class PointTest(unittest.TestCase):
    """Площадь: клетки ±2 проходимы, NPC (лавка/комната: min_npc_vendchat_distance 3) и варпы далеко."""

    def cells(self, m):
        out = {}
        for name in ("db/map_cache.dat", "db/re/map_cache.dat"):
            raw = (RATHENA / name).read_bytes()
            _size, count = struct.unpack("<IH", raw[:6])
            off = 8
            for _ in range(count):
                n = raw[off:off + 12].split(b"\0")[0].decode()
                xs, ys, ln = struct.unpack("<hhi", raw[off + 12:off + 20])
                out[n] = (xs, ys, raw[off + 20:off + 20 + ln])
                off += 20 + ln
        xs, ys, z = out[m]
        return xs, zlib.decompress(z)

    def test_point(self):
        p = WORLD["market_day"]["point"]
        xs, cells = self.cells(p["map"])
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                self.assertIn(cells[(p["y"] + dy) * xs + p["x"] + dx], (0, 3), f"{p['x'] + dx},{p['y'] + dy}")
        near, files = [], loaded_npc_files()
        self.assertGreater(len(files), 100, "скрипты сервера найдены")
        for f in files:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"^prontera,(\d+),(\d+)(?:,\d+)?\t(script|shop|warp|duplicate\([^)]*\))\t", line)
                if m and max(abs(int(m.group(1)) - p["x"]), abs(int(m.group(2)) - p["y"])) <= 5:
                    near.append((f.name, line[:40]))
        self.assertEqual(near, [], "рядом с площадью нет NPC и варпов (≤ 5 клеток)")


if __name__ == "__main__":
    unittest.main()
