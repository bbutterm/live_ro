"""Сериал-хроника: эпизод недели (ORG-091). Только чтение памяти жителей и шины мира, правила без LLM.

Владелец раз в неделю читает «серию» мира: заголовок, 3–5 сцен по сюжетным линиям недели и «в следующей
серии» — незавершённые линии. Источники — как у chronicle.py/dashboard.py: события state/<bot>/memory.sqlite
и state/shared/world.sqlite в режиме mode=ro (шина — только для жителей, чьей памяти здесь нет). Ничего не
придумывается: каждая сцена кончается ссылками на факты «дата время · житель · вид события».

Сюжетные линии (арки) — правила по событиям недели:
    quarrel    society_quarrel → society_reconciled (пара; открыта, если мира нет);
    career     career_stage_done ×N → job_changed (открыта, если профессия не сменилась);
    pet        pet_tamed → pet_hatched (открыта: яйцо есть, питомца нет);
    death      death_report/died → первая победа/уровень/экспедиция после (возвращение; открыта — не вернулся);
    expedition explore_start → explore_found/arrived → explore_done/returned (открыта — не вернулся);
    rivalry    rival_chosen, rival_overtook (пара);
    aims       aim_new → aim_done/aim_result (открыта — цели не выполнены);
    tradition  tradition_stage, tradition_gathering (весь мир).
Вес арки — по силе сюжета (WEIGHT + число событий); сцены — сначала разные виды арок, затем по весу; меньше
scenes_min арок — сцены добираются заметными одиночными событиями (уровень, гильдия, слух).
Номер серии — номер недели от первой недели, где в памяти или шине есть события.

LLM (по умолчанию выкл.): один вызов на неделю окрашивает текст серии, только если явно попросили
(--llm или goals.json "episode": {"llm": true}), BRAIN_LLM включён и общий бюджет жителей (budget.py:
state/shared/budget.sqlite, BRAIN_GLOBAL_DAILY_*) даёт резерв. Ответ проверяется как дневник (routine.diary_only):
нет чисел и латинских имён, которых нет в фактах; иначе — текст правил. Результат кэшируется в
run/episode-<неделя>.json — повторный запуск не тратит вызов.

    python3 -m live_brain.episode [2026-W40|2026-10-02] [--llm] --lab-root /opt/ro-bot-lab --bots bot01 bot02
    scripts/lab episode [НЕДЕЛЯ] [--llm]
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import world_bus

WORLD = Path(__file__).resolve().parents[1] / "world"
DEFAULTS = {"enabled": True, "llm": False, "scenes_min": 3, "scenes_max": 5}
KINDS = ("society_quarrel", "society_reconciled", "career_stage_done", "job_changed", "pet_tamed", "pet_hatched",
         "pet_gone", "death_report", "died", "kill", "level_up", "explore_start", "explore_found", "explore_arrived",
         "explore_done", "explore_returned", "rival_chosen", "rival_overtook", "aim_new", "aim_done", "aim_result",
         "tradition_stage", "tradition_gathering", "guild_founded", "guild_joined", "rumor_checked", "map_banned",
         "card_found", "trophy_rare")                                         # collect: ORG-074
BUS_KINDS = {"place_found": "explore_found", "tradition": "tradition_stage", "rumor_checked": "rumor_checked",
             **{k: k for k in ("society_quarrel", "society_reconciled", "career_stage_done", "job_changed",
                               "pet_tamed", "pet_hatched", "death_report", "level_up", "rival_overtook", "aim_new",
                               "aim_done", "aim_result", "guild_founded", "guild_joined", "map_banned",
                               "card_found", "trophy_rare")}}                  # collect: ORG-074
WEIGHT = {"quarrel": 8, "career": 6, "pet": 6, "death": 6, "expedition": 4, "rivalry": 4, "aims": 3,
          "tradition": 4, "single": 1}
RETURN_KINDS = ("kill", "level_up", "explore_start", "explore_found")
DEDUP_SEC = 120                   # одно событие из двух источников (тело + модуль) — одно
DEDUP_KINDS = ("pet_tamed", "pet_hatched", "job_changed")
LLM_MAX_TOKENS = 900
LLM_MAX_TEXT = 4000


# ---------------- время ----------------

def week_bounds(week=None, tz_hours=0, now=None):
    """Неделя: «2026-W40», дата «2026-10-02» (неделя с этим днём) или None — текущая. (start, end, метка, tz)."""
    tz = timezone(timedelta(hours=tz_hours))
    if week:
        m = re.fullmatch(r"(\d{4})-W(\d{1,2})", week.strip())
        d = (datetime.fromisocalendar(int(m[1]), int(m[2]), 1) if m
             else datetime.strptime(week.strip(), "%Y-%m-%d"))
    else:
        d = datetime.fromtimestamp(now or time.time(), tz).replace(tzinfo=None)
    d = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=tz)
    y, w, _ = d.isocalendar()
    start = d.timestamp()
    return start, start + 7 * 86400, f"{y}-W{w:02d}", tz


def _loads(text):
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


# ---------------- факты ----------------

def read_memory(db_path, start, end):
    """Имя жителя, события недели нужных видов и самое раннее событие памяти (для номера серии)."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        row = db.execute("SELECT value FROM kv WHERE key = 'last_state'").fetchone()
        name = (_loads(row[0]) if row else {}).get("name")
        rows = db.execute(f"SELECT ts, kind, data FROM events WHERE ts >= ? AND ts < ? AND kind IN "
                          f"({', '.join('?' * len(KINDS))}) ORDER BY ts", (start, end, *KINDS)).fetchall()
        first = db.execute("SELECT MIN(ts) FROM events").fetchone()[0]
    finally:
        db.close()
    return name, rows, first


def bus_first(path):
    if not Path(path).exists():
        return None
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        return db.execute("SELECT MIN(ts) FROM world_events").fetchone()[0]
    except sqlite3.OperationalError:
        return None
    finally:
        db.close()


def gather(lab_root, bots, start, end):
    """Факты недели: [{ts, who, kind, data}] по времени, имена жителей, самое раннее событие мира."""
    facts, names, firsts, missing = [], [], [], []
    for bot in bots:
        db = Path(lab_root) / "state" / bot / "memory.sqlite"
        if not db.exists():
            missing.append(bot)
            continue
        try:
            name, rows, first = read_memory(db, start, end)
        except sqlite3.Error:
            missing.append(bot)
            continue
        name = name or bot
        names.append(name)
        if first:
            firsts.append(first)
        last = {}
        for ts, kind, data in rows:
            d = _loads(data)
            if kind == "pet_hatched" and d.get("ok") is False:
                continue
            if kind in DEDUP_KINDS and ts - last.get(kind, -1e18) < DEDUP_SEC:
                continue                               # тело (pet_hatched) + модуль (pets.note) — одно событие
            last[kind] = ts
            facts.append({"ts": ts, "who": name, "kind": kind, "data": d})
    bus = Path(lab_root) / "state" / "shared" / "world.sqlite"
    first = bus_first(bus)
    if first:
        firsts.append(first)
    known = set(names)
    for e in world_bus.read_period(bus, start, end):   # житель без памяти здесь (другой хост) — по шине
        kind = BUS_KINDS.get(e["kind"])
        if kind and e["bot"] not in known:
            facts.append({"ts": e["ts"], "who": e["bot"], "kind": kind, "data": e["data"] or {}})
            if e["bot"] not in names:
                names.append(e["bot"])
    facts.sort(key=lambda f: f["ts"])
    return facts, names, (min(firsts) if firsts else None), missing


# ---------------- арки ----------------

def _arc(kind, who, title, weight, facts, lines, nxt=None):
    return {"kind": kind, "who": who, "title": title, "weight": weight, "facts": facts, "lines": lines,
            "next": nxt, "ts": facts[0]["ts"] if facts else 0}


def arcs(facts, fmt):
    """Сюжетные линии недели по правилам. fmt(ts) -> «дд.мм» для текста сцены."""
    by_who = {}
    for f in facts:
        by_who.setdefault(f["who"], []).append(f)
    out = []
    out += quarrel_arcs(facts, fmt)
    out += rivalry_arcs(facts, fmt)
    out += tradition_arcs(facts, fmt)
    for who, fs in by_who.items():
        for build in (career_arc, pet_arc, death_arcs, expedition_arcs, aims_arc):
            got = build(who, fs, fmt)
            out += got if isinstance(got, list) else ([got] if got else [])
    return out


def quarrel_arcs(facts, fmt):
    pairs = {}
    for f in facts:
        if f["kind"] in ("society_quarrel", "society_reconciled") and f["data"].get("peer"):
            pairs.setdefault(tuple(sorted((f["who"], str(f["data"]["peer"])))), []).append(f)
    out = []
    for (a, b), fs in pairs.items():
        q = next((f for f in fs if f["kind"] == "society_quarrel"), None)
        if not q:
            continue                                   # примирение без ссоры этой недели — хвост прошлой серии
        r = next((f for f in fs if f["kind"] == "society_reconciled" and f["ts"] >= q["ts"]), None)
        x, y = q["who"], q["data"]["peer"]
        lines = [f"{fmt(q['ts'])} {x} и {y} поссорились: {q['data'].get('cause') or 'без объяснений'}."]
        if r:
            lines.append(f"{fmt(r['ts'])} помирились ({r['data'].get('occasion') or 'без повода'}).")
            out.append(_arc("quarrel", [a, b], f"Ссора и примирение: {a} и {b}", WEIGHT["quarrel"] + 1, [q, r], lines))
        else:
            out.append(_arc("quarrel", [a, b], f"Ссора {a} и {b}", WEIGHT["quarrel"], [q], lines,
                            f"помирятся ли {a} и {b}?"))
    return out


def rivalry_arcs(facts, fmt):
    pairs = {}
    for f in facts:
        if f["kind"] in ("rival_chosen", "rival_overtook") and f["data"].get("rival"):
            pairs.setdefault(tuple(sorted((f["who"], str(f["data"]["rival"])))), []).append(f)
    out = []
    for (a, b), fs in pairs.items():
        over = [f for f in fs if f["kind"] == "rival_overtook"]
        chosen = [f for f in fs if f["kind"] == "rival_chosen"][:1]
        lines = [f"{fmt(f['ts'])} {f['who']} выбрал(а) соперником {f['data']['rival']}." for f in chosen]
        lines += [f"{fmt(f['ts'])} {f['who']} обогнал(а) {f['data']['rival']} {f['data'].get('label') or ''} "
                  f"({f['data'].get('mine')} против {f['data'].get('theirs')})." for f in over[:3]]
        nxt = f"{over[-1]['data']['rival']} попробует отыграться" if over else None
        out.append(_arc("rivalry", [a, b], f"Соперники: {a} и {b}", WEIGHT["rivalry"] + min(3, len(over)),
                        (chosen + over)[:4], [" ".join(x.split()) for x in lines], nxt))
    return out


def tradition_arcs(facts, fmt):
    stages, seen = [], set()
    met = {f["data"].get("day") for f in facts if f["kind"] == "tradition_gathering" and f["data"].get("met")}
    for f in facts:
        text = f["data"].get("text") or f["data"].get("label")
        if f["kind"] == "tradition_stage" and text and text not in seen:
            seen.add(text)
            stages.append(f)
    if not stages and not met:
        return []
    lines = [f"{fmt(f['ts'])} {f['data'].get('text') or f['data'].get('label')}." for f in stages[:3]]
    gather_facts = [f for f in facts if f["kind"] == "tradition_gathering" and f["data"].get("met")]
    if met:
        lines.append(f"Вечерний круг у фонтана собирался дней: {len(met - {None}) or len(met)}.")
    who = sorted({f["who"] for f in stages + gather_facts})
    return [_arc("tradition", who, "Вечерний круг у фонтана", WEIGHT["tradition"] + len(stages),
                 (stages + gather_facts[:1])[:4], lines)]


def career_arc(who, fs, fmt):
    stages = [f for f in fs if f["kind"] == "career_stage_done"]
    job = next((f for f in fs if f["kind"] == "job_changed"), None)
    if not stages and not job:
        return None
    lines = [f"{fmt(f['ts'])} {who} прошёл(ла) этап карьеры {f['data'].get('stage') or ''}".rstrip() + "."
             for f in stages[:2]]
    if job:
        lines.append(f"{fmt(job['ts'])} {who} сменил(а) профессию: {job['data'].get('from')} → {job['data'].get('to')}.")
        return _arc("career", [who], f"{who} становится {job['data'].get('to')}", WEIGHT["career"] + 3,
                    (stages[:2] + [job]), lines)
    return _arc("career", [who], f"Путь {who} к новой профессии", WEIGHT["career"] + len(stages[:2]) - 1,
                stages[:2], lines, f"{who} на пути к новой профессии")


def pet_arc(who, fs, fmt):
    tamed = next((f for f in fs if f["kind"] == "pet_tamed"), None)
    hatched = next((f for f in fs if f["kind"] == "pet_hatched"), None)
    if not tamed and not hatched:
        return None
    lines, used = [], []
    if tamed:
        lines.append(f"{fmt(tamed['ts'])} {who} приручил(а) {tamed['data'].get('name') or 'зверька'} — яйцо питомца.")
        used.append(tamed)
    if hatched:
        lines.append(f"{fmt(hatched['ts'])} у {who} вылупился питомец: {hatched['data'].get('name') or '?'}.")
        used.append(hatched)
        return _arc("pet", [who], f"Новый друг {who}", WEIGHT["pet"] + 1, used, lines)
    return _arc("pet", [who], f"{who} и яйцо питомца", WEIGHT["pet"], used, lines, f"вылупится ли питомец у {who}?")


def death_arcs(who, fs, fmt):
    deaths, last = [], -1e18
    for f in fs:
        if f["kind"] in ("death_report", "died"):
            if f["ts"] - last < DEDUP_SEC:             # died от тела + death_report разбора — одна смерть
                if f["kind"] == "death_report":
                    deaths[-1] = f
                continue
            deaths.append(f)
            last = f["ts"]
    if not deaths:
        return None
    d = deaths[-1]
    back = next((f for f in fs if f["kind"] in RETURN_KINDS and f["ts"] > d["ts"] + DEDUP_SEC), None)
    cause = d["data"].get("cause")
    many = f" (за неделю гибелей: {len(deaths)})" if len(deaths) > 1 else ""
    lines = [f"{fmt(d['ts'])} {who} погиб(ла) на {d['data'].get('map') or '?'}"
             + (f", бил {cause}" if cause else "") + f"{many}."]
    if back:
        how = {"kill": "снова на охоте", "level_up": f"и взял(а) {back['data'].get('level')} уровень",
               "explore_start": "и отправился(лась) в экспедицию", "explore_found": "и открыл(а) новое место"}
        lines.append(f"{fmt(back['ts'])} {who} вернулся(ась) в строй — {how[back['kind']]}.")
        return _arc("death", [who], f"{who}: гибель и возвращение", WEIGHT["death"] + 1, [d, back], lines)
    return _arc("death", [who], f"Гибель {who}", WEIGHT["death"], [d], lines, f"{who} ещё не вернулся(ась) после гибели")


def expedition_arcs(who, fs, fmt):
    trips, cur = [], None
    for f in fs:
        k, m = f["kind"], f["data"].get("map")
        if k == "explore_start" or (cur is None and k in ("explore_found", "explore_arrived")):
            cur = {"map": m, "facts": [f], "found": [], "end": None}
            trips.append(cur)
            if k != "explore_start":
                cur["facts"] = []
        if cur is None:
            continue
        if k == "explore_found":
            cur["found"].append(f)
        if k in ("explore_found", "explore_arrived") and f not in cur["facts"]:
            cur["facts"].append(f)
        if k in ("explore_done", "explore_returned"):
            cur["end"] = cur["end"] or f
            if f not in cur["facts"]:
                cur["facts"].append(f)
            if k == "explore_returned":
                cur = None
    out = []
    for t in trips:
        if not t["facts"]:
            continue
        lines = []
        for f in t["facts"][:4]:
            m = f["data"].get("map") or t["map"] or "?"
            lines.append({"explore_start": f"{fmt(f['ts'])} {who} отправился(лась) исследовать {m}.",
                          "explore_arrived": f"{fmt(f['ts'])} {who} добрался(лась) до {m}.",
                          "explore_found": f"{fmt(f['ts'])} {who} открыл(а) {m} — новое место мира.",
                          "explore_done": f"{fmt(f['ts'])} экспедиция на {m} окончена"
                                          + (f": {f['data'].get('why')}" if f["data"].get("why") else "") + ".",
                          "explore_returned": f"{fmt(f['ts'])} {who} вернулся(ась) в город."}[f["kind"]])
        m = t["map"] or (t["found"][0]["data"].get("map") if t["found"] else "?")
        title = f"{who} открывает {m}" if t["found"] else f"Экспедиция {who} на {m}"
        nxt = None if t["end"] else f"вернётся ли {who} из {m}?"
        out.append(_arc("expedition", [who], title, WEIGHT["expedition"] + 2 * len(t["found"]),
                        t["facts"][:4], lines, nxt))
    return out


def aims_arc(who, fs, fmt):
    new = [f for f in fs if f["kind"] == "aim_new"]
    done = [f for f in fs if f["kind"] == "aim_done"]
    result = [f for f in fs if f["kind"] == "aim_result"]
    if not (new or done or result):
        return None
    lines = []
    if new:
        lines.append(f"{fmt(new[0]['ts'])} {who} поставил(а) цели недели: "
                     + "; ".join(str(f["data"].get("text")) for f in new[:3]) + ".")
    for f in done[:2]:
        lines.append(f"{fmt(f['ts'])} {who} выполнил(а) цель: {f['data'].get('text')}.")
    fail = [f for f in result if not f["data"].get("done")]
    for f in fail[:1]:
        lines.append(f"{fmt(f['ts'])} итог: {f['data'].get('text')} — {f['data'].get('progress')} из "
                     f"{f['data'].get('target')}.")
    texts_done = {f["data"].get("text") for f in done} | {f["data"].get("text") for f in result if f["data"].get("done")}
    left = [f["data"].get("text") for f in new if f["data"].get("text") not in texts_done]
    nxt = f"{who}: «{left[0]}» ещё впереди" if left and not result else None
    return _arc("aims", [who], f"Цели недели {who}", WEIGHT["aims"] + 2 * len(done),
                (new[:1] + done[:2] + fail[:1]) or result[:1], lines, nxt)


SINGLE = {   # заметные одиночные события — если сюжетных линий меньше scenes_min
    "guild_founded": (5, lambda w, d: f"{w} основал(а) гильдию {d.get('name')}."),
    "guild_joined": (3, lambda w, d: f"{w} вступил(а) в гильдию {d.get('name')}."),
    "card_found": (4, lambda w, d: f"{w} нашёл(шла) карту {d.get('name')}"                   # collect: ORG-074
                                   + (" — первую в жизни." if d.get("first") else ".")),
    "trophy_rare": (3, lambda w, d: f"{w} добыл(а) редкость: {d.get('name')}."),              # collect:
    "level_up": (2, lambda w, d: f"{w} достиг(ла) {d.get('level')} уровня."),
    "rumor_checked": (2, lambda w, d: f"{w} проверил(а) слух о {d.get('map')}: "
                                      + ("подтвердился." if d.get("ok") else "не подтвердился.")),
    "map_banned": (2, lambda w, d: f"{w} бросил(а) охоту на {d.get('map')} после гибелей."),
}


def singles(facts, fmt, used):
    best = {}
    for f in facts:
        if f["kind"] not in SINGLE or id(f) in used:
            continue
        key = (f["who"], f["kind"])
        if f["kind"] == "level_up":                    # из уровней — последний (наибольший)
            best[key] = f
        else:
            best.setdefault(key, f)
    out = []
    for (who, kind), f in best.items():
        w, text = SINGLE[kind]
        try:
            line = text(who, f["data"])
        except (TypeError, AttributeError):
            continue
        out.append(_arc("single", [who], line.rstrip("."), w, [f], [f"{fmt(f['ts'])} {line}"]))
    return sorted(out, key=lambda a: (-a["weight"], a["ts"]))


def pick(all_arcs, n):
    """Сцены: сначала по одной арке каждого вида (сильнейшие виды раньше), затем остальные по весу."""
    ranked = sorted(all_arcs, key=lambda a: (-a["weight"], a["ts"]))
    chosen, kinds = [], set()
    for a in ranked:
        if a["kind"] not in kinds and len(chosen) < n:
            chosen.append(a)
            kinds.add(a["kind"])
    for a in ranked:
        if len(chosen) >= n:
            break
        if a not in chosen:
            chosen.append(a)
    return chosen


# ---------------- серия ----------------

def build(lab_root, bots, week=None, tz_hours=0, now=None, cfg=None):
    """Серия недели (dict) — для текста (render_text) и дашборда (dashboard.py)."""
    cfg = dict(DEFAULTS, **(cfg or {}))
    start, end, label, tz = week_bounds(week, tz_hours, now)
    facts, names, first, missing = gather(lab_root, bots, start, end)
    fmt = lambda ts: datetime.fromtimestamp(ts, tz).strftime("%d.%m")
    found = arcs(facts, fmt)
    scenes = pick(found, cfg["scenes_max"])
    if len(scenes) < cfg["scenes_min"]:
        used = {id(f) for a in scenes for f in a["facts"]}
        scenes += singles(facts, fmt, used)[:cfg["scenes_min"] - len(scenes)]
    number = 1
    if first:
        w0 = week_bounds(datetime.fromtimestamp(first, tz).strftime("%Y-%m-%d"), tz_hours)[0]
        number = max(1, int(round((start - w0) / (7 * 86400))) + 1)
    title = scenes[0]["title"] if scenes else "Тихая неделя"
    out = []
    for a in scenes:
        refs = [{"ts": f["ts"], "when": datetime.fromtimestamp(f["ts"], tz).strftime("%Y-%m-%d %H:%M"),
                 "who": f["who"], "kind": f["kind"]} for f in a["facts"]]
        out.append({"kind": a["kind"], "title": a["title"], "text": " ".join(a["lines"]), "refs": refs})
    nxt = [a["next"] for a in sorted(found, key=lambda a: (-a["weight"], a["ts"])) if a.get("next")][:4]
    days = (datetime.fromtimestamp(start, tz).strftime("%d.%m"), datetime.fromtimestamp(end - 1, tz).strftime("%d.%m"))
    return {"number": number, "week": label, "days": days, "tz_hours": tz_hours, "title": title,
            "residents": names, "missing": missing, "scenes": out, "next": nxt, "facts": len(facts),
            "arcs": len(found)}


def render_text(ep, colored=None):
    out = [f"# Серия {ep['number']}. {ep['title']}",
           f"Неделя {ep['week']} ({ep['days'][0]}–{ep['days'][1]}, UTC{ep['tz_hours']:+d}). "
           f"Жители: {', '.join(ep['residents']) or '—'}."]
    if ep["missing"]:
        out.append(f"Нет памяти: {', '.join(ep['missing'])}.")
    if colored:
        out += ["", "## Пересказ", colored]
    if not ep["scenes"]:
        out += ["", "Сюжетных линий за неделю нет: в памяти жителей и шине мира нет событий недели."]
    for i, s in enumerate(ep["scenes"], 1):
        out += ["", f"## Сцена {i}. {s['title']}", s["text"],
                "Факты: " + "; ".join(f"{r['when']} · {r['who']} · {r['kind']}" for r in s["refs"])]
    if ep["next"]:
        out += ["", "## В следующей серии"] + [f"- {n}" for n in ep["next"]]
    return "\n".join(out)


# ---------------- LLM-окраска (по умолчанию выкл.) ----------------

def only_facts(text, facts):
    """Числа и латинские имена пересказа — только из фактов (как дневник ORG-049). Список лишнего."""
    from .routine import LATIN_NAME, NUM
    nums = set(NUM.findall(facts))
    names = set(LATIN_NAME.findall(facts))
    return sorted({n for n in NUM.findall(text) if n not in nums} | (set(LATIN_NAME.findall(text)) - names))


def colorize(ep, settings, budget=None, call=None):
    """Один вызов модели: (текст, None) или (None, почему нет). Бюджет — общий (budget.SharedBudget)."""
    from . import llm
    call = call or llm.chat
    if not settings.llm_enabled:
        return None, settings.llm_off_reason
    call_id = None
    if budget is not None:
        call_id, why = budget.reserve("openrouter", settings.global_daily_limit, settings.global_daily_usd_limit)
        if why:
            return None, why
    facts = render_text(ep)
    messages = [
        {"role": "system", "content": "Ты — летописец мира Ragnarok Online. Перескажи серию недели живо, "
                                      "5–8 предложений, по-русски. Используй ТОЛЬКО факты из текста: не добавляй "
                                      "событий, чисел и имён. Ответ — JSON {\"text\": \"пересказ\"}."},
        {"role": "user", "content": facts[:6000]}]
    try:
        text, usage, _ = call(settings, messages, json_mode=True, max_tokens=LLM_MAX_TOKENS)
    except llm.LLMError as e:
        if budget is not None:
            budget.settle(call_id, 0)
        return None, f"ошибка LLM: {e}"
    if budget is not None:
        budget.settle(call_id, (usage or {}).get("cost"))
    story = " ".join(str(llm.parse_json_object(text).get("text") or "").split())[:LLM_MAX_TEXT]
    if not story:
        return None, "модель не вернула текст"
    extra = only_facts(story, facts)
    if extra:
        return None, f"пересказ отклонён — нет в фактах: {', '.join(extra[:5])}"
    return story, None


def colored_cached(lab_root, ep, settings_loader, budget_loader):
    """Окраска с кэшем run/episode-<неделя>.json: один вызов на неделю. (текст|None, заметка)."""
    path = Path(lab_root) / "run" / f"episode-{ep['week']}.json"
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("text") and cached.get("title") == ep["title"]:
            return cached["text"], "окраска из кэша"
    except (OSError, ValueError):
        pass
    settings = settings_loader()
    if settings is None:
        return None, "нет env-файла с настройками LLM"
    budget = budget_loader()
    try:
        text, why = colorize(ep, settings, budget)
    finally:
        if budget is not None:
            budget.close()
    if text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"week": ep["week"], "title": ep["title"], "text": text, "ts": time.time()},
                                   ensure_ascii=False), encoding="utf-8")
        return text, "окраска LLM (один вызов)"
    return None, why


def world_cfg(path):
    try:
        world = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        world = {}
    return world.get("timezone_offset_hours", 0), dict(DEFAULTS, **(world.get("episode") or {}))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("week", nargs="?", help="2026-W40 или дата недели 2026-10-02; по умолчанию текущая")
    p.add_argument("--lab-root", required=True)
    p.add_argument("--bots", nargs="+", required=True)
    p.add_argument("--world", default=str(WORLD / "goals.json"))
    p.add_argument("--env", help="env-файл лаборатории (BRAIN_LLM, ключ) — нужен только для --llm")
    p.add_argument("--llm", action="store_true", help="окрасить серию одним вызовом LLM (если включён и есть бюджет)")
    a = p.parse_args(argv)
    tz_hours, cfg = world_cfg(a.world)
    if not cfg.get("enabled", True):
        print("серия недели выключена (goals.json episode.enabled = false)")
        return 0
    try:
        ep = build(a.lab_root, a.bots, a.week, tz_hours, cfg=cfg)
    except ValueError:
        print(f"неделя «{a.week}»: нужно 2026-W40 или 2026-10-02", file=sys.stderr)
        return 2
    colored, note = None, None
    if a.llm or cfg.get("llm"):
        def settings_loader():
            from .config import Settings, load_env
            return Settings.from_env(load_env(a.env)) if a.env and Path(a.env).exists() else None

        def budget_loader():
            from .budget import SharedBudget
            return SharedBudget(Path(a.lab_root) / "state" / "shared" / "budget.sqlite", "episode")
        colored, note = colored_cached(a.lab_root, ep, settings_loader, budget_loader)
    print(render_text(ep, colored))
    if note:
        print(f"\n({note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
