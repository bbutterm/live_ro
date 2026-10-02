#!/usr/bin/env python3
"""NB-1/NB-2: tables/portals.txt OpenKore, сверенный с renewal-сервером rAthena (только чтение upstream).

Зачем. OpenKore строит маршрут по tables/portals.txt и полям fields/*.fld2.gz. В renewal izlude перестроен,
а переходы izlude в portals.txt старые; телепорты Kafra в portals.txt взяты с iRO (другие меню и цены).
Сабмодуль upstream/openkore не правим: генератор строит ПОЛНУЮ копию portals.txt (upstream + правки) для
профиля бота. OpenKore ищет таблицы сначала в bots/<bot>/tables (scripts/lab: --tables=профиль:upstream,
src/Settings.pm:_findFileFromFolders — берётся первый найденный файл целиком, слияния нет), поэтому копия
заменяет upstream-файл полностью. В bots/<bot>/tables/portals.txt лежит одна строка
`!include ../../common/tables/portals.txt` (src/Utils/TextReader.pm: !include относительно файла).

Что меняется (всё остальное — строка в строку как в upstream):
  1. Карты SCOPE (izlude, его копии izlude_a..d, izlude_in, учебный полигон iz_int*/int_land*, morocc):
     все простые переходы (6 полей, без диалога) с этих карт и на эти карты заменяются warp-NPC rAthena
     из файлов, которые грузит renewal (gen_atlas.loaded_files: тот же обход npc/re/scripts_main.conf).
     Клетка назначения — клетка прибытия по серверу (последние два числа warp).
  2. Переходы-скрипты учебного полигона (OnTouch без меню): iz_int* -> int_land*, int_land* -> izlude*
     (ACADEMY_TOUCH, строки скрипта сверяются при генерации).
  3. NPC-переходы (с диалогом) с карт SCOPE и KAFRA_MAPS: если на сервере нет NPC в этой клетке — строка убирается
     (OpenKore ищет NPC по точной клетке, src/Task/TalkNPC.pm:findTarget). NPC-переходы НА карты SCOPE:
     убирается строка, клетка прибытия которой непроходима на сервере.
  4. Kafra на картах KAFRA_MAPS: телепорты пересобраны из скриптов rAthena: NPC с callfunc "F_KafSet" и
     "F_Kafra" (npc/kafras/kafras.txt), меню F_Kafra (номер пункта «Use Teleport Service»), список и цены
     F_KafSet по карте NPC, клетки прибытия F_KafTele (npc/kafras/functions_kafras.txt); VIP_SCRIPT из
     src/config/core.hpp (1 — цены x2). Диалог: «c r<телепорт> c r<город>» (next, меню, next, меню).
  5. NPC_WARPS — бесплатные переходы по диалогу: «Continental Guard» (npc/re/quests/quests_morocc.txt:28-58,
     вход в moc_fild20 вместо закомментированных warp npc/re/warps/fields/morroc_fild.txt:18) и MocConGuard
     (npc/quests/quests_morocc.txt:894, moc_fild20 -> morocc 160,61). Диалог выводится из скрипта (walk_dialog).
Убранные строки upstream остаются в файле закомментированными (`#live_ro- ... # причина`).

Использование:
    scripts/gen_portals.py [--rathena upstream/rathena] [--openkore upstream/openkore] \
        [--out bots/common/tables/portals.txt] [--check] [--report]
--check — код выхода 1, если файл --out отличается от того, что построил бы генератор.
--report — отчёт по-русски (что убрано, добавлено, какие поля OpenKore совпадают с картами сервера).
"""
import argparse
import gzip
import re
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_atlas import HEADER, brace_delta, loaded_files, parse_pos, strip_comments, unique_name  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ("izlude", "izlude_a", "izlude_b", "izlude_c", "izlude_d", "izlude_in",
         "iz_int", "iz_int01", "iz_int02", "iz_int03", "iz_int04",
         "int_land", "int_land01", "int_land02", "int_land03", "int_land04", "morocc")
KAFRA_MAPS = ("prontera", "izlude", "morocc")
OUT = ROOT / "bots" / "common" / "tables" / "portals.txt"
WARPS_FILE = "npc/re/warps/cities/izlude.txt"
# OnTouch-скрипты полигона: (карта, x, y, куда, x, y, строка с warp, ожидаемый текст)
ACADEMY_TOUCH = [(f"iz_int{n}", 56, 15, f"int_land{n}", 85, 107, 75, "warp .@map$,85,107;")
                 for n in ("", "01", "02", "03", "04")] + \
                [(f"int_land{n}", 49, 57, f"izlude{s}", 196, 209, 106, "warp .@map$,196,209;")
                 for n, s in (("", ""), ("01", "_a"), ("02", "_b"), ("03", "_c"), ("04", "_d"))]
# NPC с бесплатным переходом по диалогу: (метка скрипта, ответы по порядку, зачем). Диалог выводит walk_dialog.
NPC_WARPS = [("Continental Guard#man", ["Enter the Field."],
              "вход в moc_fild20 вместо закомментированных warp, morroc_fild.txt:18"),
             ("MocConGuard", ["Ask What Happened", "Ask About Guard's Location", "Please do."],
              "из moc_fild20 в morocc, ветка rebirth_moc_edq == 0")]
ACADEMY_LINES = {"iz_int": (69, 81, "#ship_out"), "int_land": (83, 113, "#intro_to_izlude")}


# ---------- сервер ----------

def server_maps(rd):
    """{карта: (ширина, высота, клетки)}; db/re/map_cache.dat важнее db/map_cache.dat (src/map/map.cpp)."""
    out = {}
    for name in ("db/map_cache.dat", "db/re/map_cache.dat"):
        raw = (rd / name).read_bytes()
        _size, count = struct.unpack("<IH", raw[:6])
        off = 8
        for _ in range(count):
            m = raw[off:off + 12].split(b"\0")[0].decode()
            xs, ys, ln = struct.unpack("<hhi", raw[off + 12:off + 20])
            out[m] = (xs, ys, raw[off + 20:off + 20 + ln])
            off += 20 + ln
    return out


class Server:
    def __init__(self, rd):
        self.rd = Path(rd)
        self.raw = server_maps(self.rd)
        self.cells = {}
        self.files = list(loaded_files(self.rd))

    def grid(self, m):
        if m not in self.cells:
            if m not in self.raw:
                self.cells[m] = None
            else:
                xs, ys, z = self.raw[m]
                self.cells[m] = (xs, ys, zlib.decompress(z))
        return self.cells[m]

    def walkable(self, m, x, y):
        g = self.grid(m)
        return bool(g) and 0 <= x < g[0] and 0 <= y < g[1] and g[2][y * g[0] + x] in (0, 3)   # 0 земля, 3 вода

    def npc_files(self):
        """(файл, номер строки, заголовок, тело) объявлений верхнего уровня в загружаемых файлах."""
        for f in self.files:
            path = self.rd / f
            if not path.is_file():
                continue
            text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
            lines = text.splitlines()
            i = 0
            while i < len(lines):
                line = lines[i].rstrip()
                no = i + 1
                i += 1
                if "\t" not in line or line.startswith((" ", "\t")):
                    continue
                body = []
                if line.endswith("{") or line.endswith("{}"):
                    depth = brace_delta(line)
                    while depth > 0 and i < len(lines):
                        body.append(lines[i])
                        depth += brace_delta(lines[i])
                        i += 1
                yield f, no, line, "\n".join(body)

    def scan(self):
        """warp-NPC, все NPC с координатами и Kafra (с аргументами F_Kafra)."""
        warps, npcs, kafra_src, dups = [], {}, {}, []
        self.scripts = {}                                   # метка -> (тело, ссылка, позиция или None)
        for f, no, line, body in self.npc_files():
            m = HEADER.match(line)
            if not m:
                continue
            typ, name, rest = m["type"].strip(), m["name"].strip(), (m["rest"] or "").strip()
            pos = parse_pos(m["pos"])
            ref = f"{f}:{no}"
            if pos:
                npcs.setdefault((pos[0], pos[1], pos[2]), []).append((name, ref))
            if typ in ("warp", "warp2") and pos:
                p = rest.split(",")
                if len(p) >= 5:
                    warps.append((pos[0], pos[1], pos[2], p[2].strip(), int(p[3]), int(p[4]), ref))
            if typ == "script":
                self.scripts[unique_name(name)] = (body, ref, pos)
            if typ == "script" and 'callfunc "F_KafSet"' in body:
                k = re.search(r'callfunc\s+"F_Kafra"\s*,\s*(\d+)\s*,\s*(\d+)', body)
                if k:
                    info = {"welcome": int(k.group(1)), "menu": int(k.group(2)), "npc": name, "ref": ref,
                            "own_next": "next;" in body[:k.start()]}       # своё «next» до F_Kafra — лишнее «c»
                    kafra_src[unique_name(name)] = info
                    if pos:
                        kafra_src[(pos[0], pos[1], pos[2])] = info
            elif typ.startswith("duplicate(") and pos:
                dups.append((typ[len("duplicate("):-1].strip(), pos, name, ref))
        self.dups = dups
        kafras = [dict(v, map=k[0], x=k[1], y=k[2]) for k, v in kafra_src.items() if isinstance(k, tuple)]
        for src, pos, name, ref in dups:
            if src in kafra_src:
                kafras.append(dict(kafra_src[src], map=pos[0], x=pos[1], y=pos[2], npc=name,
                                   ref=f"{ref} (duplicate {kafra_src[src]['ref']})"))
        return warps, npcs, kafras


def walk_dialog(body, answers):
    """Диалог скрипта по выбранным пунктам: «next;» -> c, select в switch -> rN и переход к case N+1, до
    первого warp "карта",x,y. Только для линейных веток (switch/case); close; до warp — ошибка."""
    tok = re.finditer(r'next;|(switch\s*\(\s*)?select\("([^"]*)"\)|case (\d+):|\bclose;|'
                      r'warp\s+"(\w+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*;', body)
    steps, ai, skip = [], 0, None
    for t in tok:
        if skip is not None:
            if t.group(3) and int(t.group(3)) == skip:
                skip = None
            continue
        if t.group(0) == "next;":
            steps.append("c")
        elif t.group(2) is not None:
            assert t.group(1), "select вне switch — разбор не поддержан"
            opts = t.group(2).split(":")
            n = opts.index(answers[ai])
            ai += 1
            steps.append(f"r{n}")
            skip = n + 1
        elif t.group(0) == "close;":
            raise AssertionError("close; до warp — ответы не ведут к переходу")
        elif t.group(4):
            assert ai == len(answers), "не все ответы использованы"
            return " ".join(steps), (t.group(4), int(t.group(5)), int(t.group(6)))
    raise AssertionError("warp не найден")


def npc_warp_portals(srv):
    """NPC-переходы из NPC_WARPS: скрипт и все его duplicate на сервере -> (src, x, y, dst, x, y, диалог, ссылка)."""
    out = []
    for label, answers, why in NPC_WARPS:
        body, ref, pos = srv.scripts[label]
        steps, (dm, dx, dy) = walk_dialog(body, answers)
        places = ([(pos, ref)] if pos else []) + [(p, r) for src, p, _n, r in srv.dups if src == label]
        assert places, f"{label}: нет NPC на картах"
        for p, r in places:
            out.append((p[0], p[1], p[2], dm, dx, dy, steps, f"{r} ({label} {ref}: {why})"))
    return sorted(out)


def kafra_functions(rd):
    """F_Kafra: меню по menu_num; F_KafSet: карта -> [(город, цена)]; F_KafTele: город -> (карта, x, y)."""
    f = "npc/kafras/functions_kafras.txt"
    raw = (rd / f).read_text(encoding="utf-8", errors="replace")
    text = strip_comments(raw)
    lines = raw.splitlines()

    def line_of(pattern):
        return next(i + 1 for i, ln in enumerate(lines) if re.search(pattern, ln))

    kafra = text[text.find("function\tscript\tF_Kafra\t"):text.find("function\tscript\tF_KafStor")]
    menus = {}
    for m in re.finditer(r"(case\s+(\d+)\s*:|default\s*:)\s*setarray\s+\.@K_Menu0\$\[0\]\s*,([^;]*);", kafra):
        names = re.findall(r'"([^"]+)"', m.group(3))
        menus["default" if m.group(2) is None else int(m.group(2))] = names
    # одна «next» после приветствия, затем select меню (functions_kafras.txt, F_Kafra)
    assert re.search(r"switch\(\.@welcome\).*?\n\s*\}\s*\n\s*next;", kafra, re.S), "F_Kafra: нет next после приветствия"
    tele = text[text.find("function\tscript\tF_KafTele"):text.find("function\tscript\tF_KafCart")]
    assert re.search(r'mes "your destination\.";\s*next;\s*\.@j = select\(\s*implode\(@wrpC\$', tele), \
        "F_KafTele: ожидались mes, next, select(@wrpC$)"
    dest = {}
    for m in re.finditer(r'@wrpD\$\[\.@j\] == "([^"]+)"\)(.*?)warp "(\w+)",\s*(\d+),\s*(\d+)', tele, re.S):
        if "else if" not in m.group(2):                       # Izlude: первая ветка — checkre(0) (renewal)
            dest.setdefault(m.group(1), (m.group(3), int(m.group(4)), int(m.group(5))))
    kafset_at = text.find("function\tscript\tF_KafSet")
    kafset = text[kafset_at:]
    lists = {}
    for m in re.finditer(r'\.@map\$ == "(\w+)"\s*\)\s*\{(.*?)\}', kafset, re.S):
        names = re.search(r"@wrpD\$\[0\],([^;]*);", m.group(2))
        prices = re.search(r"@wrpP\[0\],([^;]*);", m.group(2))
        if names and prices:
            lists[m.group(1)] = list(zip(re.findall(r'"([^"]+)"', names.group(1)),
                                         [int(p) for p in re.findall(r"\d+", prices.group(1))]))
    refs = {fn: "%s:%d" % (f, line_of("^function\tscript\t" + fn + "\t")) for fn in ("F_Kafra", "F_KafTele", "F_KafSet")}
    return menus, lists, dest, refs


def vip_script(rd):
    m = re.search(r"^#define VIP_SCRIPT (\d)", (rd / "src/config/core.hpp").read_text(errors="replace"), re.M)
    return int(m.group(1)) if m else 0


def kafra_portals(srv, kafras, maps):
    """Строки portals.txt для Kafra на картах maps: [(src, x, y, dst, x, y, цена, билет, шаги, ссылка)]."""
    menus, lists, dest, refs = kafra_functions(srv.rd)
    mult = 2 if vip_script(srv.rd) else 1
    out = []
    for k in sorted(kafras, key=lambda k: (k["map"], k["x"], k["y"])):
        if k["map"] not in maps or k["map"] not in lists:
            continue
        assert not k["own_next"], f"{k['ref']}: next до F_Kafra — диалог не «c rN c rM», пересверить"
        menu = menus.get(k["menu"], menus["default"]) if k["welcome"] != 2 else None
        if not menu or "Use Teleport Service" not in menu:
            continue
        t = menu.index("Use Teleport Service")
        for d, (city, price) in enumerate(lists[k["map"]]):
            if city not in dest:
                continue
            tm, tx, ty = dest[city]
            out.append((k["map"], k["x"], k["y"], tm, tx, ty, price * mult, 1, f"c r{t} c r{d}",
                        f"{k['ref']} {k['npc'].split('::')[0].split('#')[0].strip()}; {refs['F_KafSet']} {city} {price}z; {refs['F_KafTele']}"))
    return out


def academy_portals(srv):
    """OnTouch-переходы учебного полигона (без меню, если нет квеста 21008), строки скрипта сверяются."""
    lines = (srv.rd / WARPS_FILE).read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for src, x, y, dst, tx, ty, wline, text in ACADEMY_TOUCH:
        assert text in lines[wline - 1], f"{WARPS_FILE}:{wline}: нет «{text}» — пересверить ACADEMY_TOUCH"
        a, b, name = ACADEMY_LINES["iz_int" if src.startswith("iz_int") else "int_land"]
        hdr = next((i + 1 for i in range(a - 1, b) if lines[i].startswith(f"{src},{x},{y},0\t")), None)
        assert hdr and name in lines[hdr - 1], f"{WARPS_FILE}: нет {name} в {src} {x},{y}"
        out.append((src, x, y, dst, tx, ty, f"{WARPS_FILE}:{hdr} {name} OnTouch, warp :{wline}"))
    return out


# ---------- OpenKore ----------

PORTAL_RE = re.compile(r"^([\w|@-]+)\s(\d{1,3})\s(\d{1,3})\s([\w|@-]+)\s(\d{1,3})\s(\d{1,3})\s?(.*)")


def parse_portal_line(line):
    """Как FileParsers.pm:parsePortals: (src, x, y, dst, x, y, misc) или None."""
    if line.startswith("#"):
        return None
    line = re.sub(r"\s+", " ", line.replace("\r", "")).strip()
    line = re.sub(r"(.*)[\s\t]+#.*$", r"\1", line)
    m = PORTAL_RE.match(line)
    if not m:
        return None
    g = m.groups()
    return g[0], int(g[1]), int(g[2]), g[3], int(g[4]), int(g[5]), g[6].strip()


def checked(entry, ref, misc):
    """Строка с комментарием-ссылкой. parsePortals срезает комментарий жадно — с ПОСЛЕДНЕГО « #»
    (FileParsers.pm: s/(.*)[\\s\\t]+#.*$/$1/), поэтому '#' внутри ссылки убираем и проверяем разбор."""
    line = f"{entry} # {ref.replace('#', '')}"
    p = parse_portal_line(line)
    assert p and p[6] == misc and " ".join(map(str, p[:6])) == " ".join(entry.split()[:6]), line
    return line


def build(rd, ok, scope=SCOPE, kafra_maps=KAFRA_MAPS):
    """Возвращает (текст файла, отчёт-список строк)."""
    srv = Server(rd)
    warps, npcs, kafras = srv.scan()
    scope = set(scope)
    report = []
    # 1-2. переходы сервера на картах scope
    seen, add = set(), []
    for w in warps + [a[:6] + (a[6],) for a in academy_portals(srv)]:
        key = w[:6]
        if (w[0] in scope or w[3] in scope) and key not in seen:
            seen.add(key)
            add.append(w)
    kafra = kafra_portals(srv, kafras, kafra_maps)
    kafra_dest = {(k[3], k[4], k[5]) for k in kafra}
    kafra_at = {(k["map"], k["x"], k["y"]) for k in kafras if k["map"] in kafra_maps}
    guards = npc_warp_portals(srv)
    guard_at = {g[:3] for g in guards}

    def near_kafra_dest(dm, dx, dy):
        return any(dm == m and abs(dx - x) <= 3 and abs(dy - y) <= 3 for m, x, y in kafra_dest)

    out = ["# live_ro: portals.txt для renewal-сервера rAthena. СГЕНЕРИРОВАН scripts/gen_portals.py — не правьте руками.",
           "# Основа — upstream/openkore/tables/portals.txt; правки: переходы карт " + ", ".join(sorted(scope)) + ";",
           "# Kafra на " + ", ".join(kafra_maps) + ". Описание — docs/PROGRESSION.md §9 (NB-1, NB-2).",
           ""]
    removed = []
    for line in (Path(ok) / "tables" / "portals.txt").read_text(encoding="utf-8", errors="replace").splitlines():
        p = parse_portal_line(line)
        why = None
        if p:
            sm, sx, sy, dm, dx, dy, misc = p
            if (sm, sx, sy) in guard_at:
                why = "NPC-переход по диалогу: пересобран из скрипта rAthena (ниже)"
            elif not misc and (sm in scope or dm in scope):
                why = "простой переход карты, сверенной с warp rAthena (замена ниже)"
            elif misc and (sm, sx, sy) in kafra_at:
                why = "телепорт Kafra: пересобран из скриптов rAthena (ниже)"
            elif misc and (sm in scope or sm in kafra_maps) and (sm, sx, sy) not in npcs:
                why = f"на сервере нет NPC в клетке {sm} {sx},{sy}" + (
                    " (старая Kafra)" if near_kafra_dest(dm, dx, dy) else "")
            elif misc and dm in scope and not srv.walkable(dm, dx, dy):
                why = f"клетка прибытия {dm} {dx},{dy} непроходима на сервере"
        if why:
            removed.append((line.strip(), why))
            out.append(f"#live_ro- {line.strip()} # {why}")
        else:
            out.append(line)
    out += ["", "# ---- live_ro: warp-NPC rAthena renewal (клетка назначения — клетка прибытия по серверу) ----"]
    for sm, sx, sy, dm, dx, dy, ref in sorted(add, key=lambda w: (w[0], w[1], w[2], w[3])):
        out.append(checked(f"{sm} {sx} {sy} {dm} {dx} {dy}", ref, ""))
    out += ["", "# ---- live_ro: NPC-переходы по диалогу без платы (NPC_WARPS, диалог из скрипта rAthena) ----"]
    for sm, sx, sy, dm, dx, dy, steps, ref in guards:
        out.append(checked(f"{sm} {sx} {sy} {dm} {dx} {dy} 0 {steps}", ref, f"0 {steps}"))
    out += ["", "# ---- live_ro: Kafra (F_Kafra/F_KafSet/F_KafTele rAthena): цена, билет 7060, диалог ----"]
    for sm, sx, sy, dm, dx, dy, cost, ticket, steps, ref in kafra:
        out.append(checked(f"{sm} {sx} {sy} {dm} {dx} {dy} {cost} {ticket} {steps}", ref, f"{cost} {ticket} {steps}"))
    report.append(f"убрано строк upstream: {len(removed)}; добавлено переходов rAthena: {len(add)}; "
                  f"NPC-переходов: {len(guards)}; Kafra: {len(kafra)}")
    for line, why in removed:
        report.append(f"  - {line}  [{why}]")
    for w in sorted(add):
        report.append(f"  + {' '.join(map(str, w[:6]))}  [{w[6]}]")
    for g in guards:
        report.append(f"  + {' '.join(map(str, g[:7]))}")
    for k in kafra:
        report.append(f"  + {' '.join(map(str, k[:9]))}")
    return "\n".join(out) + "\n", report


def fields_report(rd, ok, maps):
    """Какое поле OpenKore совпадает с картой сервера (для field_<карта> в servers.txt, src/Field.pm:854)."""
    srv = Server(rd)
    fdir = Path(ok) / "fields"
    rep = []
    for m in maps:
        g = srv.grid(m)
        if not g:
            continue
        best = []
        for f in sorted(fdir.glob("*.fld2.gz")):
            name = f.name[:-len(".fld2.gz")]
            if not re.fullmatch(re.escape(m.rstrip("0123456789").rstrip("_")) + r"(_[a-d]|0[1-4]|-old)?", name) \
                    and name != m:
                continue
            raw = gzip.open(f).read()
            w, h = struct.unpack("<HH", raw[:4])
            if (w, h) != g[:2]:
                continue
            cells = raw[4:]
            diff = sum((g[2][i] in (0, 3)) != bool(cells[i] & 1) for i in range(w * h))
            best.append((diff, name))
        best.sort()
        rep.append(f"{m}: сервер {g[0]}x{g[1]}; совпадающие поля OpenKore (клеток расхождения): " +
                   (", ".join(f"{n} {d}" for d, n in best[:3]) or "нет"))
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rathena", default=str(ROOT / "upstream" / "rathena"))
    ap.add_argument("--openkore", default=str(ROOT / "upstream" / "openkore"))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    text, report = build(Path(a.rathena), Path(a.openkore))
    if a.report:
        print("\n".join(report))
        print("\n".join(fields_report(Path(a.rathena), Path(a.openkore), SCOPE + ("moc_ruins", "prontera"))))
    out = Path(a.out)
    if a.check:
        same = out.is_file() and out.read_text(encoding="utf-8") == text
        print(f"{out}: {'совпадает с генератором' if same else 'УСТАРЕЛ — перезапустите scripts/gen_portals.py'}")
        return 0 if same else 1
    if not a.report:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"записан {out}: " + report[0], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
