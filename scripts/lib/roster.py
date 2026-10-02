#!/usr/bin/env python3
"""Реестр жителей live_ro (ORG-040..044): brain/world/roster.json.

Использование (обычно через scripts/lab):
  roster.py check [--repo DIR] [--bots "bot01 bot02"]     согласованность реестра, профилей и персон
  roster.py sync [--repo DIR] [--write]                   residents/dealAuto_names в bots/*/control/config.txt
  roster.py residents BOT [--repo DIR] [--bots "..."]     значения для рендера (то же, что делает render.py)
  roster.py new BOT TEMPLATE NAME [--repo DIR] [--sex M|F] [--persona FILE] [--home TOWN]
                                                          новый житель из bots/templates (active: false)
  roster.py birth BOT [--repo DIR]                        проверка готовности профиля к рождению (без БД)
  roster.py shift --max N --bots "..." [--repo DIR] [--now UNIX]
                                                          ORG-044: кого держать онлайн (бодрствующих первыми)

Ничего не пишет в БД и не читает секретов: учётные данные — только BOTNN_USER/PASS/SEX в env-файле на VPS.
residents — активные жители кроме себя; в runtime-конфиге (render.py) ещё и ∩ LAB_BOTS. dealAuto_names
никогда не бывает пустым: пустой список в OpenKore значит «принимать сделки от всех»
(upstream/openkore/src/AI/CoreLogic.pm:1048 `!$config{dealAuto_names} || existsInList(...)`).
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import json
import re
import shutil
import sys

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{3,22}$")    # rAthena: 4..23, char_name_letters (char_athena.conf:149,164)
BOT_RE = re.compile(r"^bot\d+$")
LIST_KEYS = ("residents", "dealAuto_names")
TOWNS = ("prontera",)            # точки social.points в brain/world/goals.json есть только в Пронтере
QUEST_TAG = re.compile(r"\(квест (\w+)")
PLUGIN = "autoCreate"


class RosterError(Exception):
    pass


def repo_root():
    return Path(__file__).resolve().parents[2]


def roster_path(repo):
    return Path(repo) / "brain" / "world" / "roster.json"


def bot_key(bot):
    m = re.search(r"\d+", bot)
    return (int(m.group()) if m else 0, bot)


def load(repo):
    """Весь документ реестра или None, если файла нет."""
    p = roster_path(repo)
    if not p.is_file():
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc.get("residents"), dict):
        raise RosterError(f"{p}: нет объекта residents")
    return doc


def save(repo, doc):
    doc["residents"] = {b: doc["residents"][b] for b in sorted(doc["residents"], key=bot_key)}
    roster_path(repo).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_bots(text):
    if text is None:
        return None
    return [b for b in text.replace(",", " ").strip().strip("\"'").split() if b]


def others(residents, bot, lab_bots=None):
    """Имена активных жителей, кроме bot; при lab_bots — только запущенные (ORG-004)."""
    out = []
    for b in sorted(residents, key=bot_key):
        r = residents[b]
        if b == bot or not r.get("active"):
            continue
        if lab_bots is not None and b not in lab_bots:
            continue
        out.append(r["name"])
    return out


def runtime_values(residents, bot, lab_bots=None):
    """residents/dealAuto_names для runtime-конфига. dealAuto_names: запущенные жители, иначе все активные."""
    res = others(residents, bot, lab_bots)
    deal = res or others(residents, bot)
    if not deal:
        raise RosterError(f"{bot}: нет других активных жителей — пустой dealAuto_names принимал бы сделки от всех")
    return {"residents": ",".join(res), "dealAuto_names": ",".join(deal)}


def static_values(residents, bot):
    """Что лежит в Git (bots/<bot>/control/config.txt): все активные жители, кроме себя."""
    names = ",".join(others(residents, bot))
    return {"residents": names, "dealAuto_names": names}


# ---------- config.txt OpenKore ----------

def _top_level(lines):
    """(индекс, ключ) строк верхнего уровня: блоки `key value {` ... `}` пропускаются."""
    depth = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if depth == 0:
            yield i, s.split(None, 1)[0]
        if s.endswith("{"):
            depth += 1
        elif s == "}":
            depth = max(0, depth - 1)


def read_values(text, keys):
    lines = text.splitlines()
    out = {}
    for i, key in _top_level(lines):
        if key in keys and key not in out:
            parts = lines[i].strip().split(None, 1)
            out[key] = parts[1].strip() if len(parts) > 1 else ""
    return out


def set_values(text, values, append=False):
    """Заменяет первые строки верхнего уровня `key ...` на `key value`. Возвращает (текст, отсутствующие ключи)."""
    lines = text.splitlines(keepends=True)
    seen = set()
    for i, key in _top_level(lines):
        if key in values and key not in seen:
            v = values[key]
            lines[i] = f"{key} {v}\n" if v != "" else f"{key}\n"
            seen.add(key)
    missing = [k for k in values if k not in seen]
    if append and missing:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append("\n# live_ro: житель из шаблона (scripts/lib/roster.py new)\n")
        for k in missing:
            lines.append(f"{k} {values[k]}\n" if values[k] != "" else f"{k}\n")
        missing = []
    return "".join(lines), missing


# ---------- проверка ----------

def check(repo, lab_bots=None):
    """(problems, notes): problems — расхождения, которые надо исправить; notes — справка."""
    repo = Path(repo)
    problems, notes = [], []
    doc = load(repo)
    if doc is None:
        return [], ["реестра brain/world/roster.json нет — жители берутся из config.txt и brain/personas (как раньше)"]
    res = doc["residents"]
    names = {}
    for bot, r in sorted(res.items(), key=lambda kv: bot_key(kv[0])):
        if not BOT_RE.match(bot):
            problems.append(f"{bot}: ключ реестра должен быть botNN")
            continue
        for f in ("name", "job", "template", "persona", "home_town", "active"):
            if f not in r:
                problems.append(f"{bot}: нет поля {f}")
        name = r.get("name", "")
        if not NAME_RE.match(name or ""):
            problems.append(f"{bot}: имя '{name}' — нужно 4-23 латинских букв/цифр, первая буква")
        if name.lower() in names:
            problems.append(f"{bot}: имя {name} уже у {names[name.lower()]}")
        names[name.lower()] = bot
        if r.get("template") and not (repo / "bots" / "templates" / r["template"] / "template.json").is_file():
            problems.append(f"{bot}: нет шаблона bots/templates/{r['template']}")
        persona = repo / "brain" / "personas" / f"{r.get('persona', bot)}.json"
        if persona.is_file():
            pname = json.loads(persona.read_text(encoding="utf-8")).get("name")
            if pname != name:
                problems.append(f"{bot}: в {persona.relative_to(repo)} name={pname}, в реестре {name}")
        else:
            problems.append(f"{bot}: нет персоны {persona.relative_to(repo)}")
        cfg = repo / "bots" / bot / "control" / "config.txt"
        if not cfg.is_file():
            problems.append(f"{bot}: нет профиля bots/{bot}/control/config.txt")
            continue
        text = cfg.read_text(encoding="utf-8")
        have = read_values(text, LIST_KEYS + ("autoCreate", "autoCreate_name"))
        want = static_values(res, bot)
        for k in LIST_KEYS:
            if k not in have:
                problems.append(f"{bot}: в config.txt нет строки {k}")
            elif _names(have[k]) != _names(want[k]):
                problems.append(f"{bot}: {k} '{have[k]}', по реестру '{want[k]}' (scripts/lab roster sync)")
        if have.get("autoCreate") == "1" and have.get("autoCreate_name") != name:
            problems.append(f"{bot}: autoCreate_name '{have.get('autoCreate_name')}' != {name} (рендер подставит из реестра)")
    if lab_bots is not None:
        for b in lab_bots:
            if b not in res:
                notes.append(f"{b} из LAB_BOTS нет в реестре: residents/dealAuto_names берутся из его config.txt как есть")
            elif not res[b].get("active"):
                notes.append(f"{b} ({res[b]['name']}) в LAB_BOTS, но active: false — другие жители его не видят")
        for b in sorted(res, key=bot_key):
            if b in lab_bots:
                try:
                    v = runtime_values(res, b, lab_bots)
                    notes.append(f"{b} при start: residents '{v['residents']}', dealAuto_names '{v['dealAuto_names']}'")
                except RosterError as e:
                    problems.append(str(e))
    return problems, notes


def _names(value):
    return sorted(n for n in re.split(r"\s*,\s*", value.strip()) if n)


def sync(repo, write=False):
    """Правит residents/dealAuto_names в bots/*/control/config.txt по реестру. Возвращает список изменений."""
    repo = Path(repo)
    doc = load(repo)
    if doc is None:
        raise RosterError("нет brain/world/roster.json")
    changes = []
    for bot in sorted(doc["residents"], key=bot_key):
        cfg = repo / "bots" / bot / "control" / "config.txt"
        if not cfg.is_file():
            continue
        text = cfg.read_text(encoding="utf-8")
        want = static_values(doc["residents"], bot)
        if not want["dealAuto_names"]:
            raise RosterError(f"{bot}: нет других активных жителей — dealAuto_names был бы пустым (сделки от всех)")
        have = read_values(text, LIST_KEYS)
        diff = {k: v for k, v in want.items() if _names(have.get(k, "")) != _names(v) or k not in have}
        if not diff:
            continue
        new, missing = set_values(text, diff)
        if missing:
            raise RosterError(f"{bot}: в config.txt нет строк {missing}")
        for k, v in diff.items():
            changes.append(f"{bot}: {k} '{have.get(k, '')}' -> '{v}'")
        if write:
            cfg.write_text(new, encoding="utf-8")
    return changes


# ---------- новый житель (ORG-041) ----------

def load_template(repo, template):
    p = Path(repo) / "bots" / "templates" / template / "template.json"
    if not p.is_file():
        avail = sorted(d.name for d in (Path(repo) / "bots" / "templates").iterdir() if (d / "template.json").is_file())
        raise RosterError(f"нет шаблона {template}; есть: {', '.join(avail)}")
    return json.loads(p.read_text(encoding="utf-8"))


def strip_quest_items(text, keep):
    """items_control: строки «не продавать — квест X» общего профиля сбрасываются в «продавать», если X не в keep."""
    out = []
    for line in text.splitlines(keepends=True):
        m = QUEST_TAG.search(line)
        if m and m.group(1).lower() not in keep and re.match(r"^\d+\s", line):
            item = line.split(None, 1)[0]
            comment = line.split("#", 1)[1].split("—")[0].strip()
            line = f"{item} 0 0 1 # {comment}\n"
        out.append(line)
    return "".join(out)


def add_plugin(sys_text):
    lines = sys_text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("loadPlugins_list "):
            items = line.split(None, 1)[1].strip().split(",")
            if PLUGIN not in items:
                lines[i] = "loadPlugins_list " + ",".join(items + [PLUGIN]) + "\n"
            return "".join(lines)
    raise RosterError("в sys.txt нет loadPlugins_list")


def _rename(obj, old, new):
    if not old or old == new:
        return obj
    if isinstance(obj, str):
        return re.sub(rf"\b{re.escape(old)}\b", new, obj)
    if isinstance(obj, list):
        return [_rename(x, old, new) for x in obj]
    if isinstance(obj, dict):
        return {k: _rename(v, old, new) for k, v in obj.items()}
    return obj


def new_resident(repo, bot, template, name, sex=None, persona_file=None, home=None):
    """Создаёт bots/<bot>/control (+tables), brain/personas/<bot>.json и запись реестра (active: false).
    Возвращает список созданных путей (относительно repo). Секретов не пишет: username/password пустые."""
    repo = Path(repo)
    if not BOT_RE.match(bot):
        raise RosterError(f"имя профиля '{bot}' — нужно botNN")
    if not NAME_RE.match(name):
        raise RosterError(f"имя '{name}' — нужно 4-23 латинских букв/цифр, первая буква (rAthena char_name_letters)")
    doc = load(repo)
    if doc is None:
        raise RosterError("нет brain/world/roster.json")
    res = doc["residents"]
    if bot in res:
        raise RosterError(f"{bot} уже есть в реестре ({res[bot]['name']})")
    if any(r["name"].lower() == name.lower() for r in res.values()):
        raise RosterError(f"имя {name} уже занято жителем реестра")
    dest = repo / "bots" / bot
    persona_dst = repo / "brain" / "personas" / f"{bot}.json"
    if dest.exists() or persona_dst.exists():
        raise RosterError(f"bots/{bot} или brain/personas/{bot}.json уже существует — ничего не меняю")
    tpl = load_template(repo, template)
    sex = (sex or tpl.get("sex") or "M").upper()
    if sex not in ("M", "F"):
        raise RosterError("пол: M или F")
    home = home or tpl.get("home_town") or "prontera"
    if home not in TOWNS:
        raise RosterError(f"home_town {home}: точки отдыха есть только в {', '.join(TOWNS)} (goals.json social.points)")
    if persona_file:
        persona = json.loads(Path(persona_file).read_text(encoding="utf-8"))
    elif tpl.get("persona"):
        persona = json.loads((repo / "bots" / "templates" / template / tpl["persona"]).read_text(encoding="utf-8"))
    else:
        raise RosterError(f"у шаблона {template} нет персоны: укажите --persona FILE (например, своя копия brain/personas/bot01.json)")
    old_name = persona.get("name")
    persona = _rename(persona, old_name, name)
    persona["name"] = name
    for key in ("name", "character", "speech", "goals", "hunt_maps"):
        if key not in persona:
            raise RosterError(f"персона без поля {key}")

    base = repo / "bots" / tpl.get("base", "bot01")
    peers = others(res, bot)
    if not peers:
        raise RosterError("в реестре нет активных жителей — dealAuto_names был бы пустым (сделки от всех)")
    created = []
    try:
        shutil.copytree(base / "control", dest / "control")
        if (base / "tables").is_dir():
            shutil.copytree(base / "tables", dest / "tables")
        cfg = dest / "control" / "config.txt"
        # username/password не трогаем: в Git они пустые (scripts/check.py), значения подставляет рендер из env.
        values = {"char": "0", "residents": ",".join(peers), "dealAuto_names": ",".join(peers),
                  "autoCreate": "1", "autoCreate_name": name, "autoCreate_slot": "0", "autoCreate_sex": sex}
        values.update({k: str(v) for k, v in (tpl.get("config") or {}).items()})
        text, _ = set_values(cfg.read_text(encoding="utf-8"), values, append=True)
        cfg.write_text(text, encoding="utf-8")
        ic = dest / "control" / "items_control.txt"
        if ic.is_file():
            ic.write_text(strip_quest_items(ic.read_text(encoding="utf-8"),
                                            [q.lower() for q in tpl.get("keep_quest_items", [])]), encoding="utf-8")
        st = dest / "control" / "sys.txt"
        st.write_text(add_plugin(st.read_text(encoding="utf-8")), encoding="utf-8")
        persona_dst.write_text(json.dumps(persona, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created += [f"bots/{bot}/control", f"brain/personas/{bot}.json"]
        if (dest / "tables").is_dir():
            created.append(f"bots/{bot}/tables")
        res[bot] = {"name": name, "job": tpl["job"], "template": template, "persona": bot,
                    "home_town": home, "active": False, "born": None}
        save(repo, doc)
        created.append("brain/world/roster.json")
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        persona_dst.unlink(missing_ok=True)
        raise
    return created


# ---------- рождение (ORG-043): проверки без БД ----------

def birth_check(repo, bot, today=None):
    """(problems, notes) по профилю, персоне и реестру. БД и env здесь не трогаются."""
    repo = Path(repo)
    problems, notes = [], []
    doc = load(repo)
    if doc is None or bot not in doc["residents"]:
        return [f"{bot} нет в реестре: scripts/lab new-resident {bot} <шаблон> <Имя>"], notes
    r = doc["residents"][bot]
    p_all, _ = check(repo)
    problems += [p for p in p_all if p.startswith(f"{bot}:")]
    cfg = repo / "bots" / bot / "control" / "config.txt"
    if cfg.is_file():
        v = read_values(cfg.read_text(encoding="utf-8"), ("autoCreate", "autoCreate_name", "username", "password"))
        if v.get("username") or v.get("password"):
            problems.append(f"{bot}: username/password в config.txt должны быть пустыми (их подставляет рендер из env)")
        if v.get("autoCreate") != "1":
            notes.append(f"{bot}: autoCreate выключен — персонажа создаёт оператор через tmux")
    today = today or datetime.now(timezone.utc).date()
    recent = []
    for b, o in doc["residents"].items():
        try:
            born = datetime.strptime(o.get("born") or "", "%Y-%m-%d").date()
        except ValueError:
            continue
        if b != bot and (today - born).days < 7:
            recent.append(f"{o['name']} ({o['born']})")
    if recent:
        notes.append("ВНИМАНИЕ: темп ORG-043 — не чаще одного жителя в неделю; недавно родились: " + ", ".join(recent))
    notes.append(f"{bot}: {r['name']}, {r['job']} (шаблон {r['template']}), дом {r['home_town']}, active {r['active']}")
    return problems, notes


# ---------- диспетчер смен (ORG-044) ----------

def asleep(persona, now, tz_hours):
    """Спит ли житель в момент now (UNIX) по persona.sleep: [start, start + max(hours)) местного времени мира."""
    sl = persona.get("sleep")
    if not sl:
        return False
    tz = timezone(timedelta(hours=tz_hours))
    local = datetime.fromtimestamp(now, tz)
    h, m = map(int, sl["start"].split(":"))
    length = max(sl["hours"]) * 3600
    for days in (0, -1):
        start = (local + timedelta(days=days)).replace(hour=h, minute=m, second=0, microsecond=0)
        if start <= local < start + timedelta(seconds=length):
            return True
    return False


def shift(repo, bots, max_online, now):
    """Кого держать онлайн: бодрствующие по хронотипу, по порядку LAB_BOTS, не больше max_online.
    Спящих не будим. Возвращает (allowed, info) — info: bot -> 'awake'|'asleep'|'limit'."""
    repo = Path(repo)
    goals = repo / "brain" / "world" / "goals.json"
    tz_hours = json.loads(goals.read_text(encoding="utf-8")).get("timezone_offset_hours", 0) if goals.is_file() else 0
    doc = load(repo)
    res = doc["residents"] if doc else {}
    allowed, info = [], {}
    for b in bots:
        pname = res.get(b, {}).get("persona", b)
        pf = repo / "brain" / "personas" / f"{pname}.json"
        persona = json.loads(pf.read_text(encoding="utf-8")) if pf.is_file() else {}
        if asleep(persona, now, tz_hours):
            info[b] = "asleep"
        elif len(allowed) < max_online:
            allowed.append(b)
            info[b] = "awake"
        else:
            info[b] = "limit"
    return allowed, info


# ---------- CLI ----------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="roster.py", description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=str(repo_root()))
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--bots")
    s = sub.add_parser("sync")
    s.add_argument("--write", action="store_true")
    r = sub.add_parser("residents")
    r.add_argument("bot")
    r.add_argument("--bots")
    n = sub.add_parser("new")
    n.add_argument("bot")
    n.add_argument("template")
    n.add_argument("name")
    n.add_argument("--sex")
    n.add_argument("--persona")
    n.add_argument("--home")
    b = sub.add_parser("birth")
    b.add_argument("bot")
    sh = sub.add_parser("shift")
    sh.add_argument("--max", type=int, required=True)
    sh.add_argument("--bots", required=True)
    sh.add_argument("--now", type=float)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            problems, notes = check(a.repo, parse_bots(a.bots))
            for x in notes:
                print(f"  [INFO] {x}")
            for x in problems:
                print(f"  [FAIL] {x}")
            if not problems:
                print("  [OK]   реестр жителей согласован с профилями и персонами")
            return 1 if problems else 0
        if a.cmd == "sync":
            changes = sync(a.repo, a.write)
            for x in changes:
                print(("исправлено: " if a.write else "расхождение: ") + x)
            if not changes:
                print("residents/dealAuto_names совпадают с реестром")
            elif not a.write:
                print("применить: scripts/lab roster sync --write (меняет bots/*/control/config.txt в checkout; затем commit)")
            return 0
        if a.cmd == "residents":
            doc = load(a.repo)
            if doc is None or a.bot not in doc["residents"]:
                return 2
            for k, v in runtime_values(doc["residents"], a.bot, parse_bots(a.bots)).items():
                print(f"{k} {v}")
            return 0
        if a.cmd == "new":
            for p in new_resident(a.repo, a.bot, a.template, a.name, a.sex, a.persona, a.home):
                print(f"создано/изменено: {p}")
            print(f"{a.bot}: active: false. Дальше: docs/POPULATION.md, scripts/lab birth {a.bot}")
            return 0
        if a.cmd == "birth":
            problems, notes = birth_check(a.repo, a.bot)
            for x in notes:
                print(f"  [INFO] {x}")
            for x in problems:
                print(f"  [FAIL] {x}")
            return 1 if problems else 0
        if a.cmd == "shift":
            import time
            allowed, info = shift(a.repo, parse_bots(a.bots), a.max, a.now if a.now is not None else time.time())
            for bot, st in info.items():
                print(f"{bot} {st}")
            return 0
    except RosterError as e:
        print(f"roster: {e}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
