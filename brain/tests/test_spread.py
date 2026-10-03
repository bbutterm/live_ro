"""Риск R2 IDEAS2 «всё у фонтана»: у Пронтеры отдых (homes.json rest = routine.town), рыночная площадь (market_day),
вечерний круг (tradition → social.points) и точки прогулок social.points — РАЗНЫЕ проходимые места; фонтан в
прогулках — самый лёгкий вес.

Клетки — db/re/map_cache.dat rAthena (±2 проходимы), NPC и варпы из скриптов, которые грузит map-server, — не ближе
4 клеток (рыночная площадь — строже, tests/test_market.PointTest).
Запуск: cd brain && LIVE_RO_RATHENA=… python3 -m unittest -v tests.test_spread
"""
import json
import re
import struct
import unittest
import zlib
from pathlib import Path

from live_brain.routine import load_world
from tests.test_market import RATHENA, loaded_npc_files

BRAIN_DIR = Path(__file__).resolve().parents[1]
WORLD = load_world(BRAIN_DIR / "world" / "goals.json")
HOMES = json.loads((BRAIN_DIR / "world" / "homes.json").read_text(encoding="utf-8"))
APART = 10            # разные места: дальше, чем видно друг друга (social.near_cells 6) и радиус отдыха
NPC_MIN = 4


def places():
    """{имя: (x, y)} — все места Пронтеры, куда жителей ведут механизмы города."""
    social = WORLD["social"]["points"]
    rest = HOMES["towns"]["prontera"]["rest"]
    out = {f"social.{k}": (p["x"], p["y"]) for k, p in social.items() if p.get("map", "prontera") == "prontera"}
    out["rest"] = (rest["x"], rest["y"])
    m = WORLD["market_day"]["point"]
    out["market"] = (m["x"], m["y"])
    return out


class SpreadTest(unittest.TestCase):
    def test_rest_is_home_and_routine(self):
        rest = HOMES["towns"]["prontera"]["rest"]
        self.assertTrue(rest, "у Пронтеры есть своя точка отдыха")
        town = WORLD["routine"]["town"]
        self.assertEqual((town["map"], town["x"], town["y"]), ("prontera", rest["x"], rest["y"]),
                         "без модуля home отдых тот же, что в доме")

    def test_places_apart(self):
        pts = places()
        circle = WORLD["tradition"]["point"]
        self.assertIn(f"social.{circle}", pts, "вечерний круг — точка social.points")
        names = sorted(pts)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                (ax, ay), (bx, by) = pts[a], pts[b]
                self.assertGreaterEqual(max(abs(ax - bx), abs(ay - by)), APART, f"{a} и {b} — одно место")

    def test_fountain_lightest_in_walks(self):
        w = WORLD["social"]["point_weights"]
        self.assertTrue(all(w["fountain"] < v for k, v in w.items() if k != "fountain"), w)

    @unittest.skipUnless((RATHENA / "db/re/map_cache.dat").exists(), "нет upstream/rathena")
    def test_cells_walkable_and_free(self):
        raw = (RATHENA / "db/re/map_cache.dat").read_bytes()
        _size, count = struct.unpack("<IH", raw[:6])
        off, cache = 8, {}
        for _ in range(count):
            n = raw[off:off + 12].split(b"\0")[0].decode()
            xs, ys, ln = struct.unpack("<hhi", raw[off + 12:off + 20])
            cache[n] = (xs, raw[off + 20:off + 20 + ln])
            off += 20 + ln
        xs, z = cache["prontera"]
        cells = zlib.decompress(z)
        npcs = []
        for f in loaded_npc_files():
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"^prontera,(\d+),(\d+)(?:,\d+)?\t(script|shop|warp|duplicate\([^)]*\))\t", line)
                if m:
                    npcs.append((int(m.group(1)), int(m.group(2)), line[:40]))
        self.assertGreater(len(npcs), 50, "скрипты сервера найдены")
        pts = places()
        new = {k: pts[k] for k in ("rest", "market")}          # фонтан и старые точки social — как были (D10)
        for name, (x, y) in pts.items():
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    self.assertIn(cells[(y + dy) * xs + x + dx], (0, 3), f"{name}: {x + dx},{y + dy}")
        for name, (x, y) in new.items():
            near = [n for n in npcs if max(abs(n[0] - x), abs(n[1] - y)) < NPC_MIN]
            self.assertEqual(near, [], f"{name}: NPC ближе {NPC_MIN} клеток")


if __name__ == "__main__":
    unittest.main()
