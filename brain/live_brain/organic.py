"""Метрики органичности v2 и детектор машинности (ORG-113, ORG-114; ТЗ Т-40 в docs/IDEAS2.md §3–4).

Только чтение: state/<bot>/decisions.jsonl (+ ротированные .1….3), state/<bot>/memory.sqlite и
state/shared/world.sqlite открываются в mode=ro; logs/<bot>/brain.log — только для счёта Traceback. Ничего не пишет
в память и шину; единственная запись — строка в run/alerts.log по флагу --alerts (формат AUT-118:
{"ts", "bot", "kind", "text"}). Поведение жителей не меняется.

Метрики M1–M22 и пороги «мёртво / живо / шумно» — brain/world/organic.json (гипотеза до первой недели).
Нет decisions.jsonl — метрики речи и тела пропускаются (None).

    python3 -m live_brain.organic --lab-root /opt/ro-bot-lab --bots bot01 bot02 [BOT|all] [--days N] [--json] [--alerts]
    scripts/lab organic [BOT|all] [--days N] [--json] [--alerts]
"""
import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WORLD = Path(__file__).resolve().parents[1] / "world"
DAY = 86400
GRADE_RU = {"dead": "мёртво", "alive": "живо", "noisy": "шумно", None: "—"}
WORLD_ONLY = ("M15", "M16", "M19", "M21")
OWNER_RU = {"routine": "распорядок", "social": "общение", "tradition": "традиция", "healer": "лекарь",
            "economy": "экономика", "fest": "ивент", "market": "рынок", "director": "режиссёр", "mentor": "наставник",
            "plan": "план", "llm": "модель", "jev": "JEV", "rule": "правило", "operator": "оператор",
            "party": "группа", "crew": "компания", "explore": "экспедиция", "home": "дом", "career": "карьера",
            "unknown": "неизвестно"}
TAG = re.compile(r"\[([a-z_]+)[:\]]")
_TS_CACHE = {}


def load_config(path=None):
    path = Path(path) if path else WORLD / "organic.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"metrics": {}}


def ts_of(row):
    """Время строки журнала в секундах: ts — строка ISO UTC (mind.write_decision) или число."""
    v = row.get("ts") if isinstance(row, dict) else row
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    t = _TS_CACHE.get(v)
    if t is None:
        try:
            t = datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None
        if len(_TS_CACHE) > 200000:
            _TS_CACHE.clear()
        _TS_CACHE[v] = t
    return t


def _actions(row):
    acts = row.get("actions") if row.get("type") == "decision" else None
    return [a for a in acts or [] if isinstance(a, dict)]


# ---------------- тело (ORG-113) ----------------

def _owner(row, last_activity, cfg):
    src = row.get("source") or "unknown"
    if src in ("social", "routine"):
        reason = str(row.get("reason") or "").lower()
        for key, owner in (cfg.get("reason_owners") or {}).items():
            if key in reason:
                return owner
        if last_activity:
            t, name = last_activity
            owner = (cfg.get("activity_owners") or {}).get(name)
            if owner and ts_of(row) - t <= cfg.get("activity_owner_seconds", 180):
                return owner
    return src


def body_intervals(rows, since, until, cfg=None):
    """Интервалы владения телом в [since, until): (начало, конец, источник, цель {map,x,y} | None).
    Интервал начинается с решения, в actions которого есть движение/сидение/сервис/охота, и длится до следующего
    такого решения. Решение до since задаёт владельца начала окна; без него время до первого решения не считается."""
    cfg = cfg if cfg is not None else load_config()
    body = set(cfg.get("body_actions") or ("meet_point", "hunt", "sit", "service", "job_change", "follow"))
    marks, last_act, target = [], None, None
    for r in sorted((r for r in rows if isinstance(r, dict) and ts_of(r) is not None), key=ts_of):
        t = ts_of(r)
        if t >= until:
            break
        if r.get("type") == "activity" and r.get("chosen"):
            last_act = (t, r["chosen"])
            continue
        acts = [a for a in _actions(r) if a.get("action") in body]
        if not acts:
            continue
        for a in acts:
            kind = a.get("action")
            if kind == "meet_point":
                target = {"map": a.get("map"), "x": a.get("x"), "y": a.get("y")}
            elif kind == "hunt":
                target = {"map": a.get("map"), "x": None, "y": None, "hunt": True}
            elif kind in ("service", "job_change", "explore"):
                target = None                      # куда именно — неизвестно
        marks.append((t, _owner(r, last_act, cfg), dict(target) if target else None))
    out = []
    for i, (t, owner, tgt) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else until
        a, b = max(t, since), min(end, until)
        if b > a:
            out.append((a, b, owner, tgt))
    return out


def body_owners(decisions_rows, since, until, cfg=None):
    """Источник -> доля времени владения телом (0..1, сумма 1) в [since, until)."""
    total, by = 0.0, {}
    for a, b, owner, _ in body_intervals(decisions_rows, since, until, cfg):
        by[owner] = by.get(owner, 0.0) + (b - a)
        total += b - a
    return {k: round(v / total, 4) for k, v in sorted(by.items(), key=lambda kv: -kv[1])} if total else {}


def hotspot_share(mem_or_rows, point, cells, since, until, cfg=None):
    """Доля городского времени (интервалы тела с целью на карте точки) у точки (±cells клеток, Чебышёв).
    mem_or_rows — строки decisions.jsonl. None — городского времени нет."""
    rows = mem_or_rows if isinstance(mem_or_rows, list) else []
    town = near = 0.0
    for a, b, _, tgt in body_intervals(rows, since, until, cfg):
        if not tgt or tgt.get("hunt") or tgt.get("map") != point.get("map"):
            continue
        town += b - a
        try:
            if max(abs(int(tgt["x"]) - point["x"]), abs(int(tgt["y"]) - point["y"])) <= cells:
                near += b - a
        except (TypeError, ValueError, KeyError):
            continue
    return round(near / town, 3) if town >= 1800 else None          # меньше 30 мин в городе — не мерило


def town_seconds(rows, town_map, since, until, cfg=None):
    return sum(b - a for a, b, _, tgt in body_intervals(rows, since, until, cfg)
               if tgt and not tgt.get("hunt") and tgt.get("map") == town_map)


# ---------------- речь ----------------

def whispers(decisions_rows, since, until, peers=None):
    """(ts, кому, текст) всех отправленных шёпотов в окне; peers — только жителям."""
    out = []
    for r in decisions_rows:
        t = ts_of(r) if isinstance(r, dict) else None
        if t is None or not since <= t < until:
            continue
        for a in _actions(r):
            if a.get("action") == "whisper" and (peers is None or a.get("to") in peers):
                out.append((t, a.get("to"), str(a.get("text") or "")))
    return out


def speech(decisions_rows, since, until, peers=None, cfg=None):
    """{"opened", "replied", "protocol", "by_kind", "total"}: начатые (метки шага 1, сплетни, info, подначки,
    советы), ответы ([chat:тема:N], N ≥ 2) и протокол (остальное); by_kind — по виду метки (free — без метки)."""
    cfg = cfg if cfg is not None else load_config()
    opened_re = re.compile(cfg.get("opened_tags") or r"\[chat:[a-z_]+:1\]|\[gossip:|\[info:")
    replied_re = re.compile(cfg.get("replied_tags") or r"\[chat:[a-z_]+:[2-9]\]")
    res = {"opened": 0, "replied": 0, "protocol": 0, "by_kind": {}, "total": 0, "opened_ts": []}
    for t, _, text in whispers(decisions_rows, since, until, peers):
        res["total"] += 1
        m = TAG.search(text)
        kind = m.group(1) if m else "free"
        res["by_kind"][kind] = res["by_kind"].get(kind, 0) + 1
        if opened_re.search(text):
            res["opened"] += 1
            res["opened_ts"].append(t)
        elif replied_re.search(text):
            res["replied"] += 1
        else:
            res["protocol"] += 1
    return res


def pingpong(decisions_rows, window=600, min_count=3):
    """Пары жителей, перебрасывающиеся одной меткой (A→B→A→B) за window секунд: смен направления ≥ min_count.
    Строки — decisions.jsonl с полем _who (отправитель; world_report ставит его сам)."""
    groups = {}
    for r in decisions_rows:
        if not isinstance(r, dict) or not r.get("_who"):
            continue
        t = ts_of(r)
        for a in _actions(r):
            if a.get("action") != "whisper" or not a.get("to"):
                continue
            m = TAG.search(str(a.get("text") or ""))
            if not m:
                continue
            key = (tuple(sorted((r["_who"], a["to"]))), m.group(1))
            groups.setdefault(key, []).append((t, r["_who"]))
    found = []
    for (pair, label), msgs in sorted(groups.items()):
        msgs.sort()
        i = 0
        while i < len(msgs):
            switches, j = 0, i + 1
            while j < len(msgs) and msgs[j][0] - msgs[i][0] <= window:
                switches += msgs[j][1] != msgs[j - 1][1]
                j += 1
            if switches >= min_count:
                found.append({"pair": list(pair), "label": label, "count": j - i, "start": msgs[i][0]})
                i = j
            else:
                i += 1
    return found


# ---------------- ритм и сходство ----------------

def regularity(timestamps):
    """CV интервалов (std/mean) между событиями; None при < 5 событиях. Таймер — около 0, жизнь — около 1."""
    ts = sorted(t for t in timestamps if t is not None)
    if len(ts) < 5:
        return None
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    mean = sum(gaps) / len(gaps)
    return round(statistics.pstdev(gaps) / mean, 3) if mean > 0 else None


def similarity(vec_a, vec_b):
    """Косинус двух разреженных векторов жителей ({признак: вес}); None — пустой вектор."""
    if not vec_a or not vec_b:
        return None
    dot = sum(v * vec_b.get(k, 0.0) for k, v in vec_a.items())
    na = math.sqrt(sum(v * v for v in vec_a.values()))
    nb = math.sqrt(sum(v * v for v in vec_b.values()))
    return round(dot / (na * nb), 3) if na and nb else None


def _normalize(counter):
    total = sum(counter.values())
    return {k: v / total for k, v in counter.items()} if total else {}


def resident_vector(mem_rows, decisions_rows=()):
    """Вектор жителя (ORG-115): доли занятий по часам суток, темы речи, точки города, карты охоты — каждая группа
    нормирована отдельно, чтобы ни одна не перевешивала. mem_rows — (ts, kind, data)."""
    groups = {"act": {}, "topic": {}, "point": {}, "map": {}}
    for ts, kind, d in mem_rows:
        if kind == "activity" and d.get("name"):
            k = f"act:{d['name']}@{time.gmtime(ts).tm_hour}"
            groups["act"][k] = groups["act"].get(k, 0) + 1
        elif kind == "social_said" and d.get("topic"):
            groups["topic"]["topic:" + d["topic"]] = groups["topic"].get("topic:" + d["topic"], 0) + 1
        elif kind == "social_walk" and d.get("point"):
            groups["point"]["point:" + d["point"]] = groups["point"].get("point:" + d["point"], 0) + 1
        elif kind == "kill" and d.get("map"):
            groups["map"]["map:" + d["map"]] = groups["map"].get("map:" + d["map"], 0) + 1
    for r in decisions_rows:
        for a in _actions(r):
            if a.get("action") == "hunt" and a.get("map"):
                groups["map"]["map:" + a["map"]] = groups["map"].get("map:" + a["map"], 0) + 1
    vec = {}
    for g in groups.values():
        vec.update(_normalize(g))
    return vec


# ---------------- память ----------------

def _rows(mem, since, until, kinds=None):
    db = getattr(mem, "db", mem)
    sql, args = "SELECT ts, kind, data FROM events WHERE ts >= ? AND ts < ?", [since, until]
    if kinds:
        sql += f" AND kind IN ({', '.join('?' * len(kinds))})"
        args += list(kinds)
    out = []
    for ts, kind, data in db.execute(sql + " ORDER BY ts", args).fetchall():
        try:
            d = json.loads(data) if isinstance(data, str) else (data or {})
        except ValueError:
            d = {}
        out.append((ts, kind, d if isinstance(d, dict) else {}))
    return out


def deeds(mem, since, until, cfg=None):
    """Дела жителя: уровень, смерть, сделка, подарок, лечение, новое место, карта… + 1 за каждые 100 побед."""
    cfg = cfg if cfg is not None else load_config()
    kinds = cfg.get("deed_kinds") or ["level_up", "died", "trade_sold", "trade_bought", "gift_given",
                                       "heal_confirmed", "explore_found", "card_found"]
    db = getattr(mem, "db", mem)
    n = db.execute(f"SELECT COUNT(*) FROM events WHERE ts >= ? AND ts < ? AND kind IN ({', '.join('?' * len(kinds))})",
                   (since, until, *kinds)).fetchone()[0]
    kills = db.execute("SELECT COUNT(*) FROM events WHERE ts >= ? AND ts < ? AND kind = 'kill'",
                       (since, until)).fetchone()[0]
    return int(n) + int(kills) // int(cfg.get("kills_per_deed") or 100)


def sleep_intervals(mem_rows, since, until, sleeping_now=False):
    """Интервалы сна в [since, until) по routine_sleep/routine_wake (строки памяти с запасом до since)."""
    out, start = [], None
    for ts, kind, _ in mem_rows:
        if kind == "routine_sleep":
            start = ts if start is None else start
        elif kind == "routine_wake" and start is not None:
            if ts > since and start < until:
                out.append((max(start, since), min(ts, until)))
            start = None
    if start is not None and sleeping_now and start < until:
        out.append((max(start, since), until))
    return out


def awake_hours(sleep, since, until):
    """Начала бодрствующих часов окна: час бодрствующий, если сна в нём меньше 30 мин."""
    hours, t = [], since
    while t < until:
        end = min(t + 3600, until)
        slept = sum(max(0.0, min(b, end) - max(a, t)) for a, b in sleep)
        if end - t >= 1800 and slept < 1800:
            hours.append(t)
        t = end
    return hours


def hunt_ratio(mem_rows, since, until, routine_kv=None, now=None):
    """M1: минут охоты в окне / норма дня. Счёт по hunted_min событий распорядка (сброс в новый день — с нуля)."""
    pts = [(ts, d.get("hunted_min"), d.get("budget_min")) for ts, kind, d in mem_rows
           if kind.startswith("routine_") and isinstance(d.get("hunted_min"), (int, float))]
    rk = routine_kv or {}
    now = now or time.time()
    if until >= now - 60 and isinstance(rk.get("hunted"), (int, float)) and rk.get("budget"):
        pts.append((now, rk["hunted"] / 60, rk["budget"] / 60))
    before = [p for p in pts if p[0] < since]
    inside = [p for p in pts if since <= p[0] < until]
    if not inside:
        return None
    total, prev = 0.0, (before[-1][1] if before else None)
    for _, h, _ in inside:
        if prev is None:
            prev = h
            continue
        total += h - prev if h >= prev else h
        prev = h
    budgets = [b for _, _, b in inside if b]
    norm = sum(budgets) / len(budgets) if budgets else None
    return round(total / norm, 2) if norm else None


def affinity_growth(relation_log, peers, since, until):
    """M17: наибольший суммарный прирост affinity к одному жителю за окно (kv relation_log)."""
    best = None
    for peer, rows in (relation_log or {}).items():
        if peers is not None and peer not in peers:
            continue
        s = sum(int(r.get("delta") or 0) for r in rows or [] if since <= (r.get("ts") or 0) < until)
        best = s if best is None else max(best, s)
    return best


def zeny_flow(mem_rows, cfg):
    """M18: (приток, сток) по событиям памяти; сток None — покупки у NPC не записываются (не измерить)."""
    inc = out = 0
    seen_sink = False
    for _, kind, d in mem_rows:
        if kind in (cfg.get("income_kinds") or {}):
            inc += int(d.get(cfg["income_kinds"][kind]) or 0)
        if kind in (cfg.get("sink_kinds") or {}):
            out += int(d.get(cfg["sink_kinds"][kind]) or 0)
            seen_sink = seen_sink or kind not in ("trade_bought", "mail_sent")
    return inc, (out if seen_sink else None)


def tracebacks(log_path, since, until):
    """M22: Traceback в brain.log (и .1), время — по последней строке журнала с отметкой перед ним (локальное)."""
    n = 0
    for p in (Path(str(log_path) + ".1"), Path(log_path)):
        try:
            with open(p, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 32 * 2 ** 20))
                data = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        last = None
        for line in data.splitlines():
            if len(line) >= 19 and line[4] == "-" and line[10] == "T":
                try:
                    last = time.mktime(time.strptime(line[:19], "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    pass
            elif line.startswith("Traceback") and last is not None and since <= last < until:
                n += 1
    return n


# ---------------- пороги ----------------

_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "==": lambda a, b: a == b}


def _rule(rule, value, context):
    op, ref = rule[0], rule[1]
    if not _OPS[op](value, ref):
        return False
    for name, (op2, ref2) in (rule[2] if len(rule) > 2 else {}).items():
        other = (context or {}).get(name)
        if other is None or not _OPS[op2](other, ref2):
            return False
    return True


def grade(name, value, thresholds, context=None):
    """"dead" | "alive" | "noisy" | None (нет значения или нет порогов). Шумно проверяется раньше мёртвого."""
    if value is None:
        return None
    spec = (thresholds or {}).get("metrics", thresholds or {}).get(name) if isinstance(thresholds, dict) else None
    if not spec:
        return None
    for zone in ("noisy", "dead"):
        if any(_rule(r, value, context) for r in spec.get(zone) or []):
            return zone
    return "alive"


def day_grade(grades, cfg, quiet=False):
    """Итог дня: мёртвый — ≥ dead_min «мертво» из dead_from; шумный — ≥ noisy_min «шумно» из noisy_from; иначе живой.
    Тихий день (Т-39) — без quiet_skip (M2, M16)."""
    dg = cfg.get("day_grade") or {}
    skip = set(dg.get("quiet_skip") or []) if quiet else set()
    dead = [m for m in dg.get("dead_from") or [] if m not in skip and grades.get(m) == "dead"]
    noisy = [m for m in dg.get("noisy_from") or [] if m not in skip and grades.get(m) == "noisy"]
    why = {"dead": dead, "noisy": noisy}
    if len(dead) >= dg.get("dead_min", 2):
        return "dead", why
    if len(noisy) >= dg.get("noisy_min", 3):
        return "noisy", why
    return "alive", why


def why_text(why):
    """«мёртво: M1, M7; шумно: M6» — какие метрики итога дня вне нормы."""
    parts = [f"{GRADE_RU[z]}: {', '.join(why.get(z) or [])}" for z in ("dead", "noisy") if (why or {}).get(z)]
    return "; ".join(parts) or "всё в норме"


# ---------------- сбор по лаборатории ----------------

def read_decisions(state_dir, since, until):
    """Строки decisions.jsonl и ротированных частей с ts в [since, until); старые файлы по mtime пропускаются."""
    files = [Path(state_dir) / f"decisions.jsonl.{i}" for i in (3, 2, 1)] + [Path(state_dir) / "decisions.jsonl"]
    files = [f for f in files if f.exists()]
    if not files:
        return None
    out = []
    for f in files:
        try:
            if f.stat().st_mtime < since:
                continue
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    t = ts_of(r) if isinstance(r, dict) else None
                    if t is not None and since <= t < until:
                        out.append(r)
        except OSError:
            continue
    out.sort(key=ts_of)
    return out


def load_resident(lab_root, bot, since, until, now=None, cfg=None):
    """Всё нужное о жителе за [since, until) (с запасом назад для тела/сна/сходства). Только чтение."""
    cfg = cfg if cfg is not None else load_config()
    now = now or time.time()
    state = Path(lab_root) / "state" / bot
    db_path = state / "memory.sqlite"
    if not db_path.exists():
        return None
    back = max(DAY, cfg.get("similarity_days", 7) * DAY)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        kv = {}
        for key in ("last_state", "routine", "needs", "relation_log"):
            row = db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            if row:
                try:
                    kv[key] = json.loads(row[0])
                except ValueError:
                    pass
        mem_rows = _rows(db, since - back, until)
    finally:
        db.close()
    decisions = read_decisions(state, since - DAY, until)            # запас: владелец тела на начало окна
    st = kv.get("last_state") or {}
    return {"bot": bot, "name": st.get("name") or bot, "kv": kv, "mem_rows": mem_rows, "decisions": decisions,
            "log": Path(lab_root) / "logs" / bot / "brain.log", "now": now,
            "db_path": db_path}


def _deeds_for(res, since, until, cfg):
    db = sqlite3.connect(f"file:{res['db_path']}?mode=ro", uri=True, timeout=10)
    try:
        return deeds(db, since, until, cfg)
    finally:
        db.close()


def resident_metrics(res, since, until, peers, cfg):
    """M1–M14, M17, M18, M20 (участие), M22 одного жителя за окно. Возвращает (метрики, вспомогательные, тело)."""
    kv, now = res["kv"], res["now"]
    mem_all = res["mem_rows"]
    mem = [r for r in mem_all if since <= r[0] < until]
    dec = res["decisions"]
    days = max((until - since) / DAY, 1e-9)
    m, extra = {}, {}
    rk = kv.get("routine") or {}
    m["M1"] = hunt_ratio([r for r in mem_all if r[0] < until], since, until, rk, now)
    sleep = sleep_intervals([r for r in mem_all if r[1] in ("routine_sleep", "routine_wake")], since, until,
                            sleeping_now=rk.get("mode") == "sleep" and until >= now - 60)
    hours = awake_hours(sleep, since, until)
    extra["awake_h"] = len(hours)
    acts = [d.get("name") for _, k, d in mem if k == "activity" and d.get("name")]
    m["M7"] = len(set(acts))
    extra["M7_starts"] = len(acts)
    extra["M7_share"] = round(len(set(acts)) / len(acts), 2) if acts else 0.0
    said = [d for _, k, d in mem if k == "social_said"]
    texts = [d["text"] for d in said if d.get("text")]
    m["M5"] = round(100 * (len(texts) - len(set(texts))) / len(texts)) if texts else None
    m["M6"] = round(sum(1 for d in said if str(d.get("topic") or "").startswith("weather")) / len(said), 2) \
        if said else None
    m["M11"] = len({d.get("point") for _, k, d in mem if k == "social_walk" and d.get("point")})
    m["M17"] = affinity_growth(kv.get("relation_log"), peers - {res["name"]}, since, until)
    if m["M17"] is not None:
        m["M17"] = round(m["M17"] / days, 2)
    inc, out = zeny_flow(mem, cfg)
    extra["income"], extra["sink"] = inc, out
    m["M18"] = round(out / inc, 2) if inc and out is not None else None
    m["M22"] = tracebacks(res["log"], since, until)
    owners = {}
    if dec is None:
        for k in ("M2", "M3", "M4", "M8", "M9", "M10", "M12", "M13", "M14"):
            m[k] = None
    else:
        sp = speech(dec, since, until, None, cfg)
        extra["speech"] = {k: v for k, v in sp.items() if k != "opened_ts"}
        m["M2"] = round(sp["opened"] / days, 1)
        m["M3"] = round(len(whispers(dec, since, until, peers - {res["name"]})) / days, 1)
        n_deeds = _deeds_for(res, since, until, cfg)
        extra["deeds"] = n_deeds
        m["M4"] = round(sp["opened"] / max(n_deeds, 1), 2) if sp["opened"] else 0.0
        switches = [ts_of(r) for r in dec if r.get("type") == "activity" and r.get("chosen")
                    and r.get("chosen") != r.get("current") and since <= ts_of(r) < until]
        m["M8"] = round(len(switches) / len(hours), 2) if hours else None
        cvs = {"talk": regularity(sp["opened_ts"]), "activity": regularity(switches)}
        extra["cv"] = cvs
        vals = [v for v in cvs.values() if v is not None]
        m["M9"] = min(vals) if vals else None
        hs = cfg.get("hotspot") or {"map": "prontera", "x": 156, "y": 185, "cells": 6}
        m["M10"] = hotspot_share(dec, hs, hs.get("cells", 6), since, until, cfg)
        emotes = [ts_of(r) for r in dec if since <= ts_of(r) < until
                  for a in _actions(r) if a.get("action") == "emote"]
        loud = set()
        for t in sp["opened_ts"] + emotes:
            loud.add(int((t - since) // 3600))
        quiet = [h for h in hours if int((h - since) // 3600) not in loud]
        m["M12"] = round(len(quiet) / len(hours), 2) if hours else None
        owners = body_owners(dec, since, until, cfg)
        base = set(cfg.get("base_owners") or ("routine", "rule", "operator", "unknown"))
        side = [v for k, v in owners.items() if k not in base]
        m["M13"] = round(max(side), 2) if side else (0.0 if owners else None)
        town_h = town_seconds(dec, hs.get("map"), since, until, cfg) / 3600
        extra["town_h"] = round(town_h, 1)
        m["M14"] = round(len(emotes) / town_h, 1) if town_h >= 0.5 else None
    return m, extra, owners


def discover_bots(lab_root):
    return sorted(p.name for p in (Path(lab_root) / "state").glob("*") if (p / "memory.sqlite").exists()
                  and p.name != "shared")


def world_report(lab_root, since, until, bots=None, cfg=None, now=None, loaded=None):
    """Все метрики по всем жителям + мир за [since, until). loaded — {bot: load_resident(...)} (повторное
    использование при нескольких окнах)."""
    cfg = cfg if cfg is not None else load_config()
    now = now or time.time()
    lab_root = Path(lab_root)
    if bots is None:
        bots = discover_bots(lab_root)
    loaded = loaded if loaded is not None else {}
    res = {}
    for b in bots:
        if b not in loaded:
            loaded[b] = load_resident(lab_root, b, since, until, now, cfg)
        if loaded[b]:
            res[b] = loaded[b]
    peers = {r["name"] for r in res.values()}
    out = {"since": since, "until": until, "residents": {}, "world": {}, "missing": [b for b in bots if b not in res]}
    all_dec = []
    for b, r in res.items():
        m, extra, owners = resident_metrics(r, since, until, peers, cfg)
        out["residents"][b] = {"bot": b, "name": r["name"], "metrics": m, "extra": extra, "owners": owners}
        for row in r["decisions"] or []:
            if since - 600 <= ts_of(row) < until and _actions(row):
                all_dec.append(dict(row, _who=r["name"]))
    # мир: шина
    bus = _bus_rows(lab_root / "state" / "shared" / "world.sqlite", since - cfg.get("chain_hours", 24) * 3600, until)
    days = max((until - since) / DAY, 1e-9)
    w = {}
    w["M15"] = round(sum(1 for t, _, k, _ in bus if k == "director" and since <= t < until) / days, 1) \
        if bus is not None else None
    w["M16"] = round(chains(bus or [], res, since, until, cfg) / days, 1) if bus is not None else None
    wealth = [((r["kv"].get("needs") or {}).get("wealth")) for r in res.values()]
    wealth = [v for v in wealth if isinstance(v, (int, float))]
    w["M19"] = round(sum(1 for v in wealth if v <= 0.001) / len(wealth), 2) if wealth else None
    pp = cfg.get("pingpong") or {}
    pings = pingpong(sorted(all_dec, key=ts_of), pp.get("window", 600), pp.get("min_count", 3)) \
        if any(r["decisions"] is not None for r in res.values()) else None
    w["M20"] = len(pings) if pings is not None else None
    out["pingpong"] = pings or []
    sim_from = until - cfg.get("similarity_days", 7) * DAY
    vecs = {r["name"]: resident_vector([x for x in r["mem_rows"] if sim_from <= x[0] < until],
                                       [x for x in r["decisions"] or [] if since <= ts_of(x) < until])
            for r in res.values()}
    pairs = {}
    names = sorted(vecs)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            s = similarity(vecs[a], vecs[b])
            if s is not None:
                pairs[f"{a}–{b}"] = s
    out["similarity"] = pairs
    w["M21"] = max(pairs.values()) if pairs else None
    quiet = bool(bus) and any(k in (cfg.get("quiet_day_kinds") or []) and since <= t < until for t, _, k, _ in bus)
    out["quiet_day"] = quiet
    for rr in out["residents"].values():
        rr["metrics"]["M20"] = sum(1 for p in pings if rr["name"] in p["pair"]) if pings is not None else None
    out["world"] = {"metrics": w}
    return regrade(out, cfg)


def _mkey(k):
    try:
        return int(k[1:])
    except ValueError:
        return 999


def _bus_rows(path, since, until):
    if not Path(path).exists():
        return None
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        rows = db.execute("SELECT ts, bot, kind, data FROM world_events WHERE ts >= ? AND ts < ? ORDER BY ts",
                          (since, until)).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        db.close()
    out = []
    for ts, bot, kind, data in rows:
        try:
            d = json.loads(data)
        except ValueError:
            d = {}
        out.append((ts, bot, kind, d if isinstance(d, dict) else {}))
    return out


def _mentions(d, who):
    return any(d.get(k) == who for k in ("peer", "to", "name", "target", "from", "who", "patient"))


def chains(bus, residents, since, until, cfg):
    """M16: событие жителя X в шине (окно) → реакция другого жителя Y за chain_hours: шёпот X с темой реакции
    (decisions social said) или событие памяти Y о X (или о том же субъекте, например карте слуха)."""
    rules = cfg.get("chains") or {}
    horizon = cfg.get("chain_hours", 24) * 3600
    n = 0
    for t, who, kind, d in bus:
        rule = rules.get(kind)
        if not rule or not since <= t < until:
            continue
        hit = False
        for r in residents.values():
            if r["name"] == who:
                continue
            for x in r["decisions"] or []:
                tx = ts_of(x)
                if (t < tx <= t + horizon and x.get("type") == "social" and x.get("event") == "said"
                        and x.get("to") == who and x.get("topic") in (rule.get("topics") or [])):
                    hit = True
                    break
            if not hit:
                for tx, k, dx in r["mem_rows"]:
                    if t < tx <= t + horizon and k in (rule.get("events") or []):
                        same = rule.get("same")
                        if (same and dx.get(same) and dx.get(same) == d.get(same)) or _mentions(dx, who):
                            hit = True
                            break
            if hit:
                break
        n += hit
    return n


def average(reports):
    """Среднее по дням (None пропускаются) — для --days N."""
    if len(reports) == 1:
        return reports[0]
    base = json.loads(json.dumps(reports[-1], default=str))
    base["since"] = reports[0]["since"]
    for b, rr in base["residents"].items():
        for k in rr["metrics"]:
            vals = [r["residents"][b]["metrics"].get(k) for r in reports if b in r["residents"]]
            vals = [v for v in vals if v is not None]
            rr["metrics"][k] = round(sum(vals) / len(vals), 2) if vals else None
        tot, own = 0.0, {}
        for r in reports:
            for o, v in (r["residents"].get(b) or {}).get("owners", {}).items():
                own[o] = own.get(o, 0) + v
                tot += v
        rr["owners"] = {o: round(v / tot, 4) for o, v in sorted(own.items(), key=lambda kv: -kv[1])} if tot else {}
    for k in base["world"]["metrics"]:
        vals = [r["world"]["metrics"].get(k) for r in reports if r["world"]["metrics"].get(k) is not None]
        base["world"]["metrics"][k] = round(sum(vals) / len(vals), 2) if vals else None
    base["days"] = len(reports)
    return base


def regrade(rep, cfg):
    """Оценки после усреднения."""
    w = rep["world"]["metrics"]
    for rr in rep["residents"].values():
        ctx = dict(rr["metrics"], **{k: v for k, v in rr.get("extra", {}).items() if isinstance(v, (int, float))})
        rr["grades"] = {k: grade(k, v, cfg, ctx) for k, v in rr["metrics"].items()}
        merged = dict(rr["grades"], **{k: grade(k, w.get(k), cfg) for k in WORLD_ONLY})
        rr["grade"], rr["why"] = day_grade(merged, cfg, rep.get("quiet_day"))
    wg = {k: grade(k, v, cfg) for k, v in w.items()}
    share = (cfg.get("day_grade") or {}).get("resident_share", 0.5)
    rs = list(rep["residents"].values())
    for k in sorted({k for r in rs for k in r["grades"]} - set(w), key=_mkey):
        gs = [r["grades"].get(k) for r in rs if r["grades"].get(k)]
        wg[k] = None if not gs else next((z for z in ("noisy", "dead")
                                          if sum(g == z for g in gs) >= max(1, math.ceil(share * len(gs)))), "alive")
    rep["world"]["grades"] = wg
    rep["world"]["grade"], rep["world"]["why"] = day_grade(wg, cfg, rep.get("quiet_day"))
    return rep


# ---------------- оповещения (ORG-114) ----------------

def alert_lines(today, yesterday, cfg):
    """Метрики в зоне «шумно»/«мертво» 2 дня подряд (одна и та же зона): [(житель|мир, метрика, зона, значение)]."""
    out = []
    for b, rr in today["residents"].items():
        prev = (yesterday["residents"].get(b) or {}).get("grades") or {}
        for k, g in rr["grades"].items():
            if g in ("dead", "noisy") and prev.get(k) == g and k not in WORLD_ONLY:
                out.append((rr["name"], k, g, rr["metrics"].get(k)))
    for k in WORLD_ONLY:
        g = today["world"]["grades"].get(k)
        if g in ("dead", "noisy") and yesterday["world"]["grades"].get(k) == g:
            out.append(("мир", k, g, today["world"]["metrics"].get(k)))
    return out


def write_alerts(lab_root, items, cfg, now=None):
    """Строки в run/alerts.log (формат AUT-118). Один раз в сутки (дата UTC) на метрику и жителя."""
    now = now or time.time()
    path = Path(lab_root) / "run" / "alerts.log"
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    seen = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if str(r.get("kind", "")).startswith("organic:") and str(r.get("ts", ""))[:10] == day:
                seen.add((r.get("bot"), r.get("kind")))
    except OSError:
        pass
    written = []
    for who, k, g, v in items:
        kind = f"organic:{k}"
        if (who, kind) in seen:
            continue
        spec = (cfg.get("metrics") or {}).get(k) or {}
        alive = spec.get("alive")
        norm = f"; живо {alive[0] if alive[0] is not None else '…'}–{alive[1] if alive[1] is not None else '…'}" \
            if alive else ""
        text = f"{who}: {spec.get('label', k)} {v} — {GRADE_RU[g]} 2 дня подряд ({k}{norm}; пороги — гипотеза)"
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "bot": who, "kind": kind,
               "text": text[:300]}
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        seen.add((who, kind))
        written.append(rec)
    return written


# ---------------- вывод ----------------

def owners_line(owners):
    return ", ".join(f"{OWNER_RU.get(k, k)} {round(v * 100)} %" for k, v in owners.items()) or "нет данных"


def format_table(rep, cfg, only=None):
    metrics = cfg.get("metrics") or {}
    rs = [r for b, r in rep["residents"].items() if not only or only in (b, r["name"])]
    head = ["метрика"] + [r["name"] for r in rs] + ([] if only else ["мир"])
    rows = [head]
    for k in sorted(metrics, key=_mkey):
        line = [f"{k} {metrics[k].get('label', '')}"]
        for r in rs:
            v = None if k in WORLD_ONLY else r["metrics"].get(k)
            g = None if k in WORLD_ONLY else r["grades"].get(k)
            line.append("·" if k in WORLD_ONLY else f"{'—' if v is None else v} {GRADE_RU[g] if g else ''}".strip())
        if not only:
            v, g = rep["world"]["metrics"].get(k), rep["world"]["grades"].get(k)
            line.append(f"{'' if v is None else v} {GRADE_RU[g] if g else ''}".strip() or "—")
        rows.append(line)
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(head))]
    out = ["  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)).rstrip() for r in rows]
    out.append("")
    for r in rs:
        out.append(f"{r['name']}: день {GRADE_RU[r['grade']]} ({why_text(r.get('why'))}); "
                   f"кто вёл тело: {owners_line(r['owners'])}")
    if not only:
        w = rep["world"]
        out.append(f"мир: день {GRADE_RU[w['grade']]} ({why_text(w.get('why'))})"
                   + ("; тихий день — без M2 и M16" if rep.get("quiet_day") else ""))
        for p in rep.get("pingpong") or []:
            out.append(f"пинг-понг [{p['label']}:] {p['pair'][0]}↔{p['pair'][1]} {p['count']} раз за "
                       f"{(cfg.get('pingpong') or {}).get('window', 600) // 60} мин")
    if rep.get("missing"):
        out.append("нет памяти: " + ", ".join(rep["missing"]))
    out.append("пороги — гипотеза до первой недели (brain/world/organic.json); только чтение")
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("who", nargs="?", default="all")
    p.add_argument("--lab-root", required=True)
    p.add_argument("--bots", nargs="+", default=None)
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--json", action="store_true")
    p.add_argument("--alerts", action="store_true")
    p.add_argument("--config")
    p.add_argument("--now", type=float, help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    cfg = load_config(a.config)
    now = a.now or time.time()
    days = max(1, a.days)
    span = max(days, 2 if a.alerts else 1)
    loaded = {}
    first = now - span * DAY
    bots = a.bots or discover_bots(a.lab_root)
    for b in bots:
        loaded[b] = load_resident(a.lab_root, b, first, now, now, cfg)
    reps = [world_report(a.lab_root, now - (i + 1) * DAY, now - i * DAY, bots, cfg, now, loaded)
            for i in reversed(range(span))]
    rep = regrade(average(reps[-days:]), cfg) if days > 1 else reps[-1]
    only = None if a.who in ("all", "", None) else a.who
    if only and not any(only in (b, r["name"]) for b, r in rep["residents"].items()):
        print(f"organic: нет жителя {only}", file=sys.stderr)
        return 2
    written = write_alerts(a.lab_root, alert_lines(reps[-1], reps[-2], cfg), cfg, now) if a.alerts else []
    if a.json:
        if only:
            rep = dict(rep, residents={b: r for b, r in rep["residents"].items() if only in (b, r["name"])})
        print(json.dumps(dict(rep, alerts_written=written), ensure_ascii=False, default=str, indent=1))
    else:
        print(f"Органичность за {days} сут. (до {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}):")
        print(format_table(rep, cfg, only))
        if a.alerts:
            print(f"оповещений записано: {len(written)} (run/alerts.log, scripts/lab alerts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
