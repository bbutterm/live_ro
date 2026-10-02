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
PROFILE = ROOT / "bots" / "bot01"            # таблицы бота: bots/bot01/tables раньше upstream (scripts/lab)
sys.path.insert(0, str(ROOT / "scripts"))
import okroute  # noqa: E402  (тот же разбор portals.txt/servers.txt, что у маршрутизатора)

_OK = {}


def ok_data():
    """Данные OpenKore так, как их видит бот: portals.txt профиля (!include) и field_* из servers.txt."""
    if "d" not in _OK:
        _OK["d"] = okroute.Data(OPENKORE, PROFILE)
    return _OK["d"]


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


def bot_field(m):
    """Поле, которое бот загрузит для карты m (field_<m> из servers.txt профиля, src/Field.pm:854)."""
    return field(ok_data().field_name(m))


def dims_match(m, aliased=False):
    xs, ys, _ = rathena_maps()[m]
    w, h, _ = bot_field(m) if aliased else field(m)
    return (xs, ys) == (w, h)


def reach(m, start, goal, radius, aliased=False):
    """Есть ли путь по полю OpenKore от клетки start (или соседней в 2 клетках) до клетки в radius от goal.
    aliased — поле профиля бота (field_*); тогда клетки должны быть проходимы и на карте сервера."""
    w, h, cells = bot_field(m) if aliased else field(m)
    srv = zlib.decompress(rathena_maps()[m][2]) if aliased else None

    def ok_walkable(m, x, y):                                  # noqa: F811  (локально — выбранное поле)
        good = 0 <= x < w and 0 <= y < h and bool(cells[y * w + x] & 1)
        return good and (srv is None or srv[y * w + x] in (0, 3))
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
    """Простые переходы upstream portals.txt (без профиля)."""
    out = set()
    for line in (OPENKORE / "tables" / "portals.txt").read_text(errors="replace").splitlines():
        p = line.split()
        if len(p) == 6 and not line.startswith("#"):
            out.add((p[0], int(p[1]), int(p[2]), p[3], int(p[4]), int(p[5])))
    return out


def bot_portals():
    """Переходы таблицы бота: {(src, x, y, dst, x, y): [(цена, диалог или None), ...]}. Переходы с
    динамической группой (Eden: [EdenPortalExit] — «вернуться, откуда пришёл») не включаются."""
    out = collections.defaultdict(list)
    for e in ok_data().entries():
        if not e[8]:
            out[e[:6]].append((e[6], e[7]))
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
        self.assertEqual(ex["status"], "ok")
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
        """upstream-поле iz_int другое; бот грузит iz_int01 (field_iz_int), оно совпадает с iz_int сервера."""
        self.assertFalse(dims_match("iz_int"))
        self.assertFalse(ok_walkable("iz_int", 18, 26))
        self.assertTrue(rathena_walkable("iz_int", 18, 26))
        self.assertEqual(ok_data().field_name("iz_int"), "iz_int01")
        for m in ("iz_int", "iz_int01", "iz_int02", "iz_int03", "iz_int04"):
            xs, ys, z = rathena_maps()[m]
            srv = zlib.decompress(z)
            w, h, ok = bot_field(m)
            self.assertEqual((w, h), (xs, ys), m)
            self.assertTrue(all((srv[i] in (0, 3)) == bool(ok[i] & 1) for i in range(w * h)),
                            f"поле {ok_data().field_name(m)} больше не совпадает с {m} сервера — пересмотреть field_iz_int")
        for m in ("int_land", "int_land01", "int_land02", "int_land03", "int_land04"):
            self.assertTrue(dims_match(m, aliased=True), m)

    def test_izlude_rebuilt(self):
        """upstream izlude.fld2 старый (268x268); бот грузит izlude_a (field_izlude), он совпадает по размеру."""
        self.assertFalse(dims_match("izlude"), "izlude OpenKore совпал с сервером — field_izlude можно убрать")
        self.assertEqual(ok_data().field_name("izlude"), "izlude_a")
        for m in ("izlude", "izlude_a", "izlude_b", "izlude_c", "izlude_d"):
            self.assertTrue(dims_match(m, aliased=True), m)
        self.assertEqual(FIRST["swordman"]["route"]["status"], "ok")

    def test_morocc_field(self):
        """Карта morocc сервера — старый Морокко: ближе morocc-old.fld2, чем morocc.fld2 (field_morocc)."""
        def diff(m, f):
            xs, ys, z = rathena_maps()[m]
            srv = zlib.decompress(z)
            w, h, ok = field(f)
            self.assertEqual((w, h), (xs, ys))
            return sum((srv[i] in (0, 3)) != bool(ok[i] & 1) for i in range(w * h))
        self.assertEqual(ok_data().field_name("morocc"), "morocc-old")
        self.assertLess(diff("morocc", "morocc-old") * 5, diff("morocc", "morocc"))

    def test_bot_tables(self):
        """У всех профилей одна таблица переходов и одни field_*; общий файл совпадает с генератором."""
        import gen_portals
        aliases = None
        for tables in sorted((ROOT / "bots").glob("bot*/tables")):
            stub = [ln for ln in (tables / "portals.txt").read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.startswith("#")]
            self.assertEqual(stub, ["!include ../../common/tables/portals.txt"], tables)
            a = okroute.field_aliases(OPENKORE, tables.parent)
            self.assertEqual(a, aliases or a, tables)
            aliases = a
        self.assertEqual(aliases, {"izlude": "izlude_a", "iz_int": "iz_int01", "morocc": "morocc-old"})
        text, _ = gen_portals.build(RATHENA, OPENKORE)
        self.assertEqual((ROOT / "bots/common/tables/portals.txt").read_text(encoding="utf-8"), text,
                         "bots/common/tables/portals.txt устарел: scripts/gen_portals.py")
        self.assertEqual(ok_data().portals_file, PROFILE / "tables" / "portals.txt")

    def test_bot_portals_match_server(self):
        """Простые переходы таблицы бота на картах gen_portals.SCOPE — ровно warp-NPC сервера (и OnTouch полигона);
        Kafra на prontera/izlude/morocc — ровно то, что выводится из F_Kafra/F_KafSet/F_KafTele."""
        import gen_portals
        srv = gen_portals.Server(RATHENA)
        warps, _npcs, kafras = srv.scan()
        want = {w[:6] for w in warps if w[0] in gen_portals.SCOPE or w[3] in gen_portals.SCOPE}
        want |= {a[:6] for a in gen_portals.academy_portals(srv)}
        have = {k for k, v in bot_portals().items() if (k[0] in gen_portals.SCOPE or k[3] in gen_portals.SCOPE)
                and any(st is None for _, st in v)}
        self.assertEqual(have, want)
        kaf = {(k[:6], k[6], k[8]) for k in gen_portals.kafra_portals(srv, kafras, gen_portals.KAFRA_MAPS)}
        have_k = {(k, c, st) for k, v in bot_portals().items() for c, st in v
                  if st and k[0] in gen_portals.KAFRA_MAPS and (k[0], k[1], k[2]) in
                  {(x["map"], x["x"], x["y"]) for x in kafras}}
        self.assertEqual(have_k, kaf)
        self.assertIn((("prontera", 146, 89, "morocc", 156, 46), 1200, "c r2 c r3"), kaf)
        guards = gen_portals.npc_warp_portals(srv)
        self.assertEqual({(g[:6], 0, g[6]) for g in guards},
                         {(k, c, st) for k, v in bot_portals().items() for c, st in v
                          if st and k[:3] in {g[:3] for g in guards}})
        self.assertIn(("moc_fild01", 84, 19, "moc_fild20", 208, 207, "c c r1"), [g[:7] for g in guards])
        self.assertIn(("moc_fild20", 38, 174, "morocc", 160, 61, "c r1 c c c c r1 c c r1"), [g[:7] for g in guards])
        # старые переходы izlude из upstream — в таблице бота их нет
        self.assertIn(("izlude", 52, 140, "izlude_in", 74, 158), openkore_portals())
        self.assertNotIn(("izlude", 52, 140, "izlude_in", 74, 158), bot_portals())

    def test_kafra_dialog_refs(self):
        """Диалог Kafra «c r2 c r3»: F_Kafra default-меню, F_KafSet prontera, F_KafTele morocc 156,46, VIP_SCRIPT 0."""
        self.assertIn('"Save","Use Storage","Use Teleport Service"', script_line("npc/kafras/functions_kafras.txt:142"))
        self.assertIn('"Izlude", "Geffen", "Payon", "Morocc"', script_line("npc/kafras/functions_kafras.txt:614"))
        self.assertIn("600, 1200, 1200, 1200", script_line("npc/kafras/functions_kafras.txt:615"))
        self.assertIn('warp "morocc", 156, 46;', script_line("npc/kafras/functions_kafras.txt:325"))
        self.assertIn('callfunc "F_Kafra",5,0,0,40,800;', "\n".join(
            (RATHENA / "npc/kafras/kafras.txt").read_text().splitlines()[288:300]))
        self.assertTrue(script_line("npc/kafras/kafras.txt:289").startswith("prontera,146,89,6\tscript\tKafra Employee::kaf_prontera5"))
        self.assertIn("#define VIP_SCRIPT 0", script_line("src/config/core.hpp:58"))

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

    def test_okroute_criteria(self):
        """NB-1/NB-2: okroute по таблицам бота находит Пронтера -> гильдия Swordsman (пешком), Пронтера ->
        гильдия Thief (Kafra 1200 z или бесплатно через стражей moc_fild20) и выход iz_int* -> izlude*."""
        d = ok_data()
        self.assertTrue(d.route(("prontera", 156, 180), ("izlude_in", 73, 172), 1))
        self.assertIsNone(d.route(("prontera", 156, 180), ("morocc", 156, 46), 2),
                          "появился путь в Морокко без NPC — пересмотреть NB-2")
        free = d.route(("prontera", 156, 180), ("moc_prydb1", 39, 126), 2, zeny=0)
        self.assertTrue(free)
        self.assertIn(["moc_fild20", "morocc"], [[h[0], h[3]] for h in free], "бесплатный путь — через стражей moc_fild20")
        r = d.route(("prontera", 156, 180), ("moc_prydb1", 39, 126), 2, zeny=1200)
        self.assertTrue(r)
        self.assertEqual(r[0][3:6], ["morocc", 156, 46])
        for n, s in (("", ""), ("01", "_a"), ("02", "_b"), ("03", "_c"), ("04", "_d")):
            self.assertTrue(d.route((f"iz_int{n}", 18, 26), (f"izlude{s}", 128, 142), 2), n)
            self.assertTrue(d.route((f"iz_int{n}", 18, 26), ("prontera", 156, 180), 2), n)
        self.assertIsNone(okroute.Data(OPENKORE).route(("iz_int01", 18, 26), ("izlude_a", 128, 142), 2),
                          "upstream без профиля тоже выводит из полигона — пересмотреть")

    def test_academy_route(self):
        ex = DATA["start"]["academy_exit"]
        ports = bot_portals()
        m, x, y = ex["route"]["from"]
        for hop in ex["route"]["hops"]:
            self.assertIn(tuple(hop), ports)
            sm, sx, sy, dm, dx, dy = hop
            self.assertEqual(m, sm)
            self.assertTrue(reach(sm, (x, y), (sx, sy), 1, aliased=True), f"{sm} {x},{y} -> {sx},{sy}")
            self.assertTrue(rathena_walkable(dm, dx, dy), hop)
            m, x, y = dm, dx, dy
        self.assertEqual([m, x, y][0], ex["route"]["to"][0])
        self.assertTrue(reach(m, (x, y), tuple(ex["route"]["to"][1:]), 0, aliased=True))

    def test_routes(self):
        """Каждый переход маршрута есть в таблице бота и как warp сервера (Kafra — как телепорт из скрипта);
        путь по полям бота связный, все карты маршрута совпадают по размеру с картами сервера."""
        import gen_portals
        ports = bot_portals()
        warps = rathena_warps()
        srv = gen_portals.Server(RATHENA)
        kaf = {(k[:6], k[6], k[8]) for k in gen_portals.kafra_portals(srv, srv.scan()[2], gen_portals.KAFRA_MAPS)}
        for key, p in FIRST.items():
            r = p["route"]
            self.assertEqual(r["status"], "ok", key)
            m, x, y = r["from"]
            for hop in r["hops"]:
                sm, sx, sy, dm, _dx, _dy = hop[:6]
                self.assertIn(tuple(hop[:6]), ports, f"{key}: перехода нет в таблице бота")
                self.assertTrue(dims_match(sm, aliased=True), f"{key}: карта {sm} OpenKore не совпадает с сервером")
                self.assertEqual(m, sm)
                self.assertTrue(reach(sm, (x, y), (sx, sy), 1 if len(hop) == 6 else 2, aliased=sm in ("izlude", "morocc")),
                                f"{key}: {sm} {x},{y} -> {sx},{sy} не связно")
                if len(hop) == 7:                              # NPC-переход: Kafra из скриптов rAthena
                    npc = hop[6]
                    self.assertIn((npc["cost"], npc["steps"]), ports[tuple(hop[:6])], key)
                    self.assertIn((tuple(hop[:6]), npc["cost"], npc["steps"]), kaf, key)
                    self.assertLessEqual(npc["cost"], p["requirements"]["zeny"], f"{key}: зени на Kafra")
                    m, x, y = dm, hop[4], hop[5]
                    continue
                srv_w = [w for w in warps if w[0] == sm and w[3] == dm and abs(w[1] - sx) <= 3 and abs(w[2] - sy) <= 3]
                self.assertTrue(srv_w, f"{key}: у сервера нет warp {sm} {sx},{sy} -> {dm}")
                m, x, y = dm, srv_w[0][4], srv_w[0][5]            # клетка прибытия — по серверу
            steps = p["stages"][0]["steps"]
            first_talk = next(i for i, st in enumerate(steps) if st["do"] == "talk")
            moves = steps[:first_talk]
            self.assertTrue(moves and all(st["do"] == "move" for st in moves), key)
            route_maps = {r["from"][0]} | {h[3] for h in r["hops"]}
            for st in moves:                                   # промежуточные точки — на картах маршрута
                self.assertIn(st["map"], route_maps, f"{key}: точка {st['map']} не на маршруте")
            final = moves[-1]
            self.assertEqual(m, final["map"], key)
            self.assertTrue(dims_match(m, aliased=True), key)
            self.assertTrue(reach(m, (x, y), (final["x"], final["y"]), 0), f"{key}: до NPC не дойти")
            found = ok_data().route(tuple(r["from"]), (final["map"], final["x"], final["y"]), 2,
                                    zeny=p["requirements"]["zeny"] or None)
            self.assertTrue(found, f"{key}: okroute не находит маршрут")


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
        """route.status blocked — этап не запускается, цель честно об этом говорит (механизм остаётся)."""
        self.assertEqual({p["route"]["status"] for p in FIRST.values()}, {"ok"}, "NB-1/NB-2: все пути ok")
        data = json.loads(json.dumps({k: v for k, v in DATA.items() if k != "catalog"}))
        data["catalog"] = DATA["catalog"]
        data["paths"]["swordman"]["route"]["status"] = "blocked"
        data["paths"]["swordman"]["route"]["why"] = "тест"
        self.assertIsNone(P.stage_action(NOVICE, data, target="Swordsman"))
        g = P.plan(NOVICE, data, target="Swordsman")
        self.assertTrue(g["next"].get("blocked"))
        self.assertIn("не запускается", g["next"]["text"])

    def test_swordsman_and_thief_actions(self):
        act = P.stage_action(NOVICE, DATA, target="Swordsman")
        self.assertEqual((act["path"], act["steps"][0]["map"]), ("swordman", "izlude_in"))
        poor = dict(NOVICE, zeny=1199)
        self.assertIsNone(P.stage_action(poor, DATA, target="Thief"), "без 1200 z на Kafra не идти")
        g = P.plan(poor, DATA, target="Thief")
        self.assertEqual((g["next"]["kind"], g["next"]["target"]), ("zeny", 1200))
        self.assertIn("Kafra", g["next"]["text"])
        act = P.stage_action(dict(NOVICE, zeny=1200), DATA, target="Thief")
        self.assertEqual((act["path"], act["steps"][0]["map"], act["steps"][0]["time_limit"]), ("thief", "moc_prydb1", 1800))

    def test_academy_exit(self):
        """Выход из полигона сверен (academy_exit ok): полигон больше не блокирует; со status blocked — блокирует."""
        st = dict(NOVICE, map="iz_int02", job_lv=1, lv=1)
        g = P.plan(st, DATA, target="Acolyte")
        self.assertFalse(g.get("blocked"))
        self.assertEqual(g["next"]["kind"], "job_lv")
        self.assertIsNotNone(P.stage_action(dict(st, job_lv=10), DATA, target="Acolyte"))
        data = dict(DATA, start=dict(DATA["start"], academy_exit=dict(DATA["start"]["academy_exit"], status="blocked")))
        self.assertIsNone(P.stage_action(dict(st, job_lv=10), data, target="Acolyte"))
        g = P.plan(st, data, target="Acolyte")
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
