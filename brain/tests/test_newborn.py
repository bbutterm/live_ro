"""Рождение жителя (newborn): старт, Novice -> первая профессия, выбор пути по цели жителя.

Сверка со скриптами rAthena и данными OpenKore (поля, portals.txt) идёт, если рядом есть сабмодули
(upstream/rathena, upstream/openkore) или заданы LIVE_RO_RATHENA / LIVE_RO_OPENKORE; иначе пропускается.
Это проверка разбора скриптов и данных, а не прохождения в игре. Ответы в меню и координаты NPC новых
путей сверяет общий тест tests/test_progression.py (ScriptRefsTest, FieldsTest).

Запуск: cd brain && python3 -m unittest -v tests.test_newborn
"""
import asyncio
import collections
import gzip
import json
import os
import re
import struct
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace

from live_brain import progression as P
from live_brain.career import Career
from live_brain.memory import Memory

ROOT = Path(__file__).resolve().parents[2]
RATHENA = Path(os.environ.get("LIVE_RO_RATHENA") or ROOT / "upstream" / "rathena")
OPENKORE = Path(os.environ.get("LIVE_RO_OPENKORE") or ROOT / "upstream" / "openkore")
DATA = P.load()
FIRST = {k: p for k, p in DATA["paths"].items() if p["from"] == "Novice"}
TARGETS = {"Swordsman": "swordman", "Acolyte": "acolyte", "Merchant": "merchant", "Archer": "archer",
           "Thief": "thief", "Mage": "mage"}
NOVICE = {"name": "Odette", "job": "Novice", "lv": 14, "job_lv": 10, "zeny": 300, "sex": "Female",
          "skill_points": 0, "map": "prontera", "items": {}}
HAVE_RATHENA = (RATHENA / "npc/re/jobs/1-1/mage.txt").exists()
HAVE_BOTH = HAVE_RATHENA and (OPENKORE / "fields" / "prontera.fld2.gz").exists()


def script_line(ref):
    m = re.match(r"([^:]+):(\d+)", ref)
    return (RATHENA / m.group(1)).read_text(encoding="utf-8", errors="replace").splitlines()[int(m.group(2)) - 1]


# ---------- карты rAthena (map_cache.dat) и OpenKore (fields/*.fld2.gz) ----------

_CACHE = {}


def rathena_maps():
    """Карты сервера renewal: db/re/map_cache.dat важнее db/map_cache.dat (src/map/map.cpp:3921-3925)."""
    if not _CACHE:
        for name in ("db/map_cache.dat", "db/re/map_cache.dat"):
            raw = (RATHENA / name).read_bytes()
            _size, count = struct.unpack("<IH", raw[:6])
            off = 8
            for _ in range(count):
                m = raw[off:off + 12].split(b"\0")[0].decode()
                xs, ys, ln = struct.unpack("<hhi", raw[off + 12:off + 20])
                _CACHE[m] = (xs, ys, raw[off + 20:off + 20 + ln])
                off += 20 + ln
    return _CACHE


def rathena_walkable(m, x, y):
    xs, ys, z = rathena_maps()[m]
    cells = zlib.decompress(z)
    return 0 <= x < xs and 0 <= y < ys and cells[y * xs + x] in (0, 3)      # 0 — земля, 3 — мелкая вода


_FIELDS = {}


def field(m):
    if m not in _FIELDS:
        raw = gzip.open(OPENKORE / "fields" / f"{m}.fld2.gz").read()
        w, h = struct.unpack("<HH", raw[:4])
        _FIELDS[m] = (w, h, raw[4:])
    return _FIELDS[m]


def ok_walkable(m, x, y):
    w, h, cells = field(m)
    return 0 <= x < w and 0 <= y < h and bool(cells[y * w + x] & 1)


def dims_match(m):
    xs, ys, _ = rathena_maps()[m]
    w, h, _ = field(m)
    return (xs, ys) == (w, h)


def reach(m, start, goal, radius):
    """Есть ли путь по полю OpenKore от клетки start (или соседней в 2 клетках) до клетки в radius от goal."""
    w, h, cells = field(m)
    sx, sy = start
    q = collections.deque((sx + dx, sy + dy) for dx in range(-2, 3) for dy in range(-2, 3)
                          if ok_walkable(m, sx + dx, sy + dy))
    seen = set(q)
    gx, gy = goal
    while q:
        x, y = q.popleft()
        if abs(x - gx) <= radius and abs(y - gy) <= radius:
            return True
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                n = (x + dx, y + dy)
                if n not in seen and ok_walkable(m, *n):
                    seen.add(n)
                    q.append(n)
    return False


def openkore_portals():
    out = set()
    for line in (OPENKORE / "tables" / "portals.txt").read_text(errors="replace").splitlines():
        p = line.split()
        if len(p) == 6 and not line.startswith("#"):
            out.add((p[0], int(p[1]), int(p[2]), p[3], int(p[4]), int(p[5])))
    return out


def rathena_warps():
    """warp-NPC из файлов, которые грузит renewal (тот же порядок чтения, что scripts/gen_atlas.py)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from gen_atlas import loaded_files
    rx = re.compile(r"^([\w@-]+),(\d+),(\d+),\d+\s+warp2?\s+\S+\s+\d+,\d+,([\w@-]+),(\d+),(\d+)")
    out = []
    for f in loaded_files(RATHENA):
        for line in (RATHENA / f).read_text(encoding="utf-8", errors="replace").splitlines():
            m = rx.match(line)
            if m:
                a = m.groups()
                out.append((a[0], int(a[1]), int(a[2]), a[3], int(a[4]), int(a[5])))
    return out


# ---------- данные ----------

class FirstJobDataTest(unittest.TestCase):
    def test_six_paths(self):
        self.assertEqual(set(FIRST), set(TARGETS.values()))
        self.assertEqual(DATA["classes"]["Novice"]["next_by_target"], TARGETS)
        for key, p in FIRST.items():
            self.assertEqual(TARGETS[p["to"]], key)
            self.assertTrue(p["script"].startswith("npc/re/jobs/1-1/"), key)
            self.assertEqual((p["requirements"]["job_lv"], p["requirements"]["skill_points"]), (10, 0))
            self.assertIn(p["route"]["status"], ("ok", "blocked"))
            if p["route"]["status"] == "blocked":
                self.assertTrue(p["route"].get("why"), key)
            self.assertIn(p["to"], DATA["classes"], f"{key}: класс после смены не описан")
            stage = p["stages"][0]
            self.assertEqual(stage["success"]["job"], p["to"], "успех — профессия по имени OpenKore")

    def test_stage_ids_do_not_clash_with_second_jobs(self):
        """career хранит пройденные этапы без пути (done): id первых профессий не должны совпасть с Knight/Priest."""
        second = {s["id"] for k, p in DATA["paths"].items() if k not in FIRST for s in p["stages"]}
        for key, p in FIRST.items():
            self.assertFalse({s["id"] for s in p["stages"]} & second, key)

    def test_templates_have_paths(self):
        for tpl in sorted((ROOT / "bots" / "templates").glob("*/template.json")):
            job = json.loads(tpl.read_text(encoding="utf-8"))["job"]
            self.assertIn(job, TARGETS, f"{tpl.parent.name}: цели {job} нет в classes.Novice.next_by_target")

    def test_start_override_file(self):
        o = DATA["start"]["override"]
        text = (ROOT / o["file"]).read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("//")]
        self.assertEqual(lines, ["start_point: {},{},{}".format(*o["point"])])
        tmpl = (ROOT / "server/conf/import-tmpl/char_conf.txt").read_text(encoding="utf-8")
        enabled = bool(re.search(r"^start_point\s*:", tmpl, re.M))
        self.assertEqual(enabled, o["enabled"], "progression.json start.override.enabled не совпадает с import-tmpl")


# ---------- скрипты rAthena ----------

@unittest.skipUnless(HAVE_RATHENA, "нет upstream/rathena")
class FirstJobScriptTest(unittest.TestCase):
    def test_scripts_loaded_in_renewal(self):
        self.assertIn("import: npc/re/scripts_jobs.conf", (RATHENA / "npc/re/scripts_main.conf").read_text())
        conf = (RATHENA / "npc/re/scripts_jobs.conf").read_text()
        for p in FIRST.values():
            self.assertIn(f"npc: {p['script']}", conf)
        self.assertFalse((RATHENA / "npc/jobs/1-1").exists(), "появились pre-renewal 1-1 — пересверить")

    def test_requirement_is_basic_skill(self):
        line = script_line("npc/other/Global_Functions.txt:627")
        self.assertIn('getskilllv("NV_BASIC") > 8', line)
        self.assertIn("function\tscript\tF_CanChangeJob", script_line("npc/other/Global_Functions.txt:626"))
        self.assertIn("basic_skill_check: yes", script_line("conf/battle/player.conf:47"))
        for key, p in FIRST.items():
            self.assertIn('callfunc("F_CanChangeJob")', script_line(p["requirements"]["refs"][0]), key)

    def test_success_and_reward_lines(self):
        for key, p in FIRST.items():
            rathena = DATA["classes"][p["to"]]["rathena"]
            self.assertIn(f'callfunc "Job_Change",Job_{rathena};', script_line(p["stages"][0]["success"]["ref"]), key)
            iid = next(iter(p["reward"]["items"]))
            self.assertIn(f"getitem {iid},1", script_line(p["reward"]["ref"]), key)

    def test_no_quest_log_in_first_jobs(self):
        """Успех — только по профессии: скрипты 1-1 (кроме паломников Priest) журнал квестов не трогают."""
        for key, p in FIRST.items():
            text = (RATHENA / p["script"]).read_text(encoding="utf-8", errors="replace")
            if key == "acolyte":
                text = text.split("prt_fild03,365,255")[0]
            self.assertNotRegex(text, r"setquest|changequest|completequest", key)

    def test_thief_two_npcs(self):
        self.assertIn("set q_job_thief,1;", script_line("npc/re/jobs/1-1/thief.txt:153"))
        self.assertIn("if(q_job_thief == 1)", script_line("npc/re/jobs/1-1/thief.txt:158"))

    def test_start_point_and_academy_refs(self):
        self.assertTrue(script_line("conf/char_athena.conf:115").startswith("start_point: iz_int,18,26:iz_int01,18,26"))
        self.assertIn('strcmpi(w1, "start_point") == 0', script_line("src/char/char.cpp:3008"))
        self.assertIn("import: conf/import/char_conf.txt", script_line("conf/char_athena.conf:305"))
        ex = DATA["start"]["academy_exit"]
        self.assertEqual(ex["status"], "blocked")
        self.assertIn("#room_out\t1,1,iz_int,51,30", script_line("npc/re/warps/cities/izlude.txt:57"))
        self.assertIn("#ship_out", script_line("npc/re/warps/cities/izlude.txt:69"))
        self.assertIn("warp .@map$,85,107;", script_line("npc/re/warps/cities/izlude.txt:75"))
        self.assertIn("#intro_to_izlude", script_line("npc/re/warps/cities/izlude.txt:83"))
        self.assertIn("warp .@map$,196,209;", script_line("npc/re/warps/cities/izlude.txt:106"))
        self.assertIn("savepoint .@map$,128,142,1,1;", script_line("npc/re/warps/cities/izlude.txt:107"))


# ---------- данные OpenKore против сервера ----------

@unittest.skipUnless(HAVE_BOTH, "нет upstream/rathena или upstream/openkore")
class MapsTest(unittest.TestCase):
    def test_academy_field_mismatch(self):
        """Почему из iz_int бот не выйдет: поле OpenKore другое; iz_int01 совпадает с картой сервера."""
        self.assertFalse(dims_match("iz_int"))
        self.assertFalse(ok_walkable("iz_int", 18, 26))
        self.assertTrue(rathena_walkable("iz_int", 18, 26))
        xs, ys, z = rathena_maps()["iz_int"]
        srv = zlib.decompress(z)
        w, h, ok = field("iz_int01")
        self.assertEqual((w, h), (xs, ys))
        self.assertTrue(all((srv[i] in (0, 3)) == bool(ok[i] & 1) for i in range(w * h)),
                        "iz_int01.fld2 больше не совпадает с iz_int сервера — пересмотреть обход field_iz_int")
        self.assertTrue(dims_match("int_land"))

    def test_izlude_rebuilt(self):
        self.assertFalse(dims_match("izlude"), "izlude OpenKore совпал с сервером — пересмотреть blocked у swordman")
        self.assertTrue(dims_match("izlude_a"))
        self.assertEqual(FIRST["swordman"]["route"]["status"], "blocked")

    def test_start_point_walkable(self):
        m, x, y = DATA["start"]["override"]["point"]
        self.assertTrue(rathena_walkable(m, x, y))
        self.assertTrue(ok_walkable(m, x, y))
        self.assertTrue(dims_match(m))
        tmpl = (ROOT / "server/conf/import-tmpl/char_conf.txt").read_text(encoding="utf-8")
        for mm in re.finditer(r"^start_point\s*:\s*(\S+)", tmpl, re.M):
            for point in mm.group(1).split(":"):
                pm, px, py = point.split(",")
                self.assertTrue(rathena_walkable(pm, int(px), int(py)) and ok_walkable(pm, int(px), int(py)), point)

    def test_routes(self):
        """Каждый переход маршрута есть в portals.txt OpenKore и как warp сервера; путь по полям связный.
        Для ok-путей все карты маршрута совпадают по размеру с картами сервера."""
        ports = openkore_portals()
        warps = rathena_warps()
        for key, p in FIRST.items():
            r = p["route"]
            m, x, y = r["from"]
            for hop in r["hops"]:
                self.assertIn(tuple(hop), ports, f"{key}: перехода нет в portals.txt")
                sm, sx, sy, dm, _dx, _dy = hop
                srv = [w for w in warps if w[0] == sm and w[3] == dm and abs(w[1] - sx) <= 3 and abs(w[2] - sy) <= 3]
                if r["status"] == "ok":
                    self.assertTrue(dims_match(sm), f"{key}: карта {sm} OpenKore не совпадает с сервером")
                    self.assertTrue(srv, f"{key}: у сервера нет warp {sm} {sx},{sy} -> {dm}")
                    self.assertEqual(m, sm)
                    self.assertTrue(reach(sm, (x, y), (sx, sy), 1), f"{key}: {sm} {x},{y} -> {sx},{sy} не связно")
                    m, x, y = dm, srv[0][4], srv[0][5]            # клетка прибытия — по серверу
                else:
                    m, x, y = hop[3], hop[4], hop[5]
            steps = p["stages"][0]["steps"]
            first_talk = next(i for i, st in enumerate(steps) if st["do"] == "talk")
            moves = steps[:first_talk]
            self.assertTrue(moves and all(st["do"] == "move" for st in moves), key)
            route_maps = {r["from"][0]} | {h[3] for h in r["hops"]}
            for st in moves:                                   # промежуточные точки — на картах маршрута
                self.assertIn(st["map"], route_maps, f"{key}: точка {st['map']} не на маршруте")
            final = moves[-1]
            if r["status"] == "ok":
                self.assertEqual(m, final["map"], key)
                self.assertTrue(dims_match(m), key)
                self.assertTrue(reach(m, (x, y), (final["x"], final["y"]), 0), f"{key}: до NPC не дойти")
        izl = [h for h in FIRST["swordman"]["route"]["hops"] if h[0] == "izlude"][0]
        self.assertFalse([w for w in warps if w[0] == "izlude" and w[3] == "izlude_in"
                          and abs(w[1] - izl[1]) <= 3 and abs(w[2] - izl[2]) <= 3],
                         "у сервера появился warp izlude из portals.txt OpenKore — пересмотреть blocked")


# ---------- выбор пути ----------

class ChoiceTest(unittest.TestCase):
    def test_target_selects_path(self):
        for target, key in TARGETS.items():
            self.assertEqual(P.path_for(NOVICE, DATA, target), key)
        self.assertIsNone(P.path_for(NOVICE, DATA))
        self.assertIsNone(P.path_for(NOVICE, DATA, "Knight"))
        self.assertEqual(P.path_for({"job": "Swordsman"}, DATA, "Mage"), "knight", "цель важна только для Novice")

    def test_mage_action(self):
        act = P.stage_action(NOVICE, DATA, target="Mage")
        self.assertEqual((act["action"], act["path"], act["stage"]), ("job_change", "mage", "first_job"))
        self.assertEqual(act["steps"][0], {"do": "move", "map": "geffen_in", "x": 163, "y": 124, "time_limit": 1800})
        self.assertEqual(act["success"]["job"], "Mage")
        talk = act["steps"][1]
        self.assertEqual(P.choose_answer(talk["answers"], ["I want to be a Mage", "What are the requirements to be a Mage?",
                                                           "Nothing, thanks."]), 1)
        self.assertEqual(P.choose_answer(talk["answers"], ["I want to be a Mage.", "Nothing, thanks."]), 1)
        self.assertEqual(P.plan(NOVICE, DATA, target="Mage")["next"]["kind"], "quest_stage")

    def test_not_ready(self):
        self.assertIsNone(P.stage_action(dict(NOVICE, job_lv=9), DATA, target="Acolyte"))
        self.assertIsNone(P.stage_action(dict(NOVICE, skill_points=1), DATA, target="Acolyte"))
        g = P.plan(dict(NOVICE, job_lv=6), DATA, target="Acolyte")
        self.assertEqual((g["path"], g["next"]["kind"], g["next"]["target"]), ("acolyte", "job_lv", 10))
        self.assertIn("Novice -> Acolyte", P.summary(dict(NOVICE, job_lv=6), DATA, target="Acolyte"))

    def test_blocked_routes_no_action(self):
        for target in ("Swordsman", "Thief"):
            self.assertIsNone(P.stage_action(NOVICE, DATA, target=target), target)
            g = P.plan(NOVICE, DATA, target=target)
            self.assertTrue(g["next"].get("blocked"), target)
            self.assertIn("не запускается", g["next"]["text"])

    def test_academy_blocks_everything(self):
        st = dict(NOVICE, map="iz_int02", job_lv=1, lv=1)
        self.assertIsNone(P.stage_action(dict(st, job_lv=10), DATA, target="Acolyte"))
        g = P.plan(st, DATA, target="Acolyte")
        self.assertTrue(g["blocked"])
        self.assertIn("учебный полигон iz_int02", g["next"]["text"])

    def test_no_target(self):
        g = P.plan(NOVICE, DATA)
        self.assertIsNone(g["path"])
        self.assertIn("первая профессия не выбрана", g["next"]["text"])
        g = P.plan(dict(NOVICE, job_lv=4), DATA)
        self.assertEqual((g["next"]["kind"], g["next"]["target"]), ("job_lv", 10))

    def test_second_jobs_unchanged(self):
        st = {"job": "Acolyte", "lv": 45, "job_lv": 40, "skill_points": 0, "quests": [], "items": {}}
        self.assertEqual(P.stage_action(st, DATA)["path"], "priest")
        self.assertIsNone(P.plan({"job": "Mage", "lv": 30, "job_lv": 20}, DATA)["path"])

    def test_target_job_from_roster(self):
        self.assertEqual(P.target_job("Arkady"), "Swordsman")
        self.assertEqual(P.target_job("vera"), "Acolyte")
        self.assertIsNone(P.target_job("Nobody"))
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "brain" / "world"
            world.mkdir(parents=True)
            (world / "roster.json").write_text(json.dumps({"residents": {
                "bot05": {"name": "Odette", "template": "mage"}}}), encoding="utf-8")
            tpl = Path(tmp) / "bots" / "templates" / "mage"
            tpl.mkdir(parents=True)
            (tpl / "template.json").write_text(json.dumps({"job": "Mage"}), encoding="utf-8")
            self.assertEqual(P.target_job("Odette", world), "Mage", "без job в реестре — job шаблона")


class CareerTargetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.tmp.name) / "m.sqlite")
        self.sent = []

        async def execute(actions, source, reason, protocol=False):
            self.sent.extend(actions)

        self.mind = SimpleNamespace(
            mem=self.mem, fresh_state=True, execute=execute, write_decision=lambda d: None,
            persona={"name": "Vera"},
            state=dict(NOVICE, job_change={"running": False, "skill_points": 0, "quests": [], "items": {}}),
            routine=SimpleNamespace(in_town_mode=True, st={"arrived": True}),
            plans=SimpleNamespace(store=SimpleNamespace(active=lambda: None)),
            may_move=lambda owner: (True, None), alert=lambda *a, **k: None)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_roster_target(self):
        c = Career(self.mind, {"auto_job_change": True}, clock=time.time)
        asyncio.run(c.tick())
        self.assertEqual(c.target, "Acolyte")
        self.assertEqual([(a["path"], a["stage"]) for a in self.sent], [("acolyte", "first_job")])
        self.assertIn("Novice -> Acolyte", self.mem.get("career")["text"])

    def test_persona_job_overrides(self):
        self.mind.persona = {"name": "Vera", "job": "Mage"}
        c = Career(self.mind, {"auto_job_change": True}, clock=time.time)
        asyncio.run(c.tick())
        self.assertEqual([a["path"] for a in self.sent], ["mage"])

    def test_without_flag_only_goal(self):
        c = Career(self.mind, {"auto_job_change": False}, clock=time.time)
        asyncio.run(c.tick())
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
