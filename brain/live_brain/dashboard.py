"""Дашборд мира (ORG-090): одна самодостаточная HTML-страница для владельца (только чтение памяти).

Кто где и чем занят (карточки жителей), кто с кем как (граф отношений, ссоры пунктиром), что было за день
(летопись памяти + шина мира), метрики органичности (ORG-046), экономики (ORG-037) и ресурсы мозгов (ORG-047).
Источники — только state/<bot>/memory.sqlite и state/shared/world.sqlite в режиме mode=ro; ничего не пишется,
кроме самого HTML-файла. Без внешних скриптов, шрифтов и стилей: файл открывается без сервера и без сети.
Весь текст из памяти экранируется (там есть реплики людей). Секретов нет: ни env, ни паролей, ни адресов.

    python3 -m live_brain.dashboard --lab-root /opt/ro-bot-lab --bots bot01 bot02 [--day 2026-10-02] [--out FILE]
    scripts/lab dashboard [ДАТА]     # по умолчанию $LAB_ROOT/run/dashboard.html
"""
import argparse
import html
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import world_bus
from . import episode as episode_mod                    # serial: серия недели (ORG-091)
from .chronicle import LINES, day_bounds
from .economy import metrics_from_rows

WORLD = Path(__file__).resolve().parents[1] / "world"
KV_KEYS = ("last_state", "status", "needs", "aims", "activity", "mood", "pets", "party", "crew", "society",
           "career", "resources", "routine")
MAX_EVENTS = 400            # лента дня: последние N строк (файл остаётся < 1 МБ)
MAX_CARD_TEXT = 160
STATUS_RU = {"SLEEPING": "спит", "OFFLINE": "не в сети", "DEAD": "погиб", "ESCAPING": "спасается",
             "BLOCKED": "тупик", "RECOVERING": "восстанавливается", "QUEST": "квест", "SOCIAL": "встреча",
             "SERVICING": "дела в городе", "FIGHTING": "бой", "TRAVELING": "в пути", "RESTING": "отдыхает",
             "HUNTING": "охотится"}
NEEDS_RU = {"safety": "безопасность", "supply": "припасы", "progress": "рост", "social": "общение",
            "curiosity": "любопытство", "rest": "отдых", "wealth": "достаток", "care": "забота"}


class _RoMemory:
    """Минимальная обёртка над ro-соединением: organic_metrics/economy_metrics ждут .db и .get()."""

    def __init__(self, db, kv):
        self.db = db
        self.kv = kv

    def get(self, key, default=None):
        return self.kv.get(key, default)


def _loads(text, default=None):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def activity_labels():
    data = _loads((WORLD / "activities.json").read_text(encoding="utf-8"), {}) \
        if (WORLD / "activities.json").exists() else {}
    return {k: v.get("label") or k for k, v in (data.get("activities") or {}).items() if isinstance(v, dict)}


def _organic(mem, since, now):
    try:
        from .__main__ import organic_metrics          # ORG-046; нет — блок пропускается
    except Exception:
        return None
    try:
        return organic_metrics(mem, since, now)
    except Exception:
        return None


def read_resident(db_path, start, end, now, labels):
    """Один житель: kv, события дня, отношения. Только mode=ro."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        kv = {k: _loads(v) for k, v in db.execute(
            f"SELECT key, value FROM kv WHERE key IN ({', '.join('?' * len(KV_KEYS))})", KV_KEYS).fetchall()}
        rows = db.execute("SELECT ts, kind, data FROM events WHERE ts >= ? AND ts < ? ORDER BY ts",
                          (start, end)).fetchall()
        try:
            rels = db.execute("SELECT name, affinity, note FROM relations").fetchall()
        except sqlite3.OperationalError:
            rels = []
        mem = _RoMemory(db, kv)
        organic = _organic(mem, start, min(now, end))
    finally:
        db.close()
    st = kv.get("last_state") or {}
    needs = kv.get("needs") or {}
    top = sorted(((k, v) for k, v in needs.items() if isinstance(v, (int, float))), key=lambda kv_: -kv_[1])[:3]
    aims = [{"text": a.get("text"), "done": bool(a.get("done")), "progress": a.get("progress"),
             "target": a.get("target")} for a in ((kv.get("aims") or {}).get("items") or []) if isinstance(a, dict)]
    act = kv.get("activity") or {}
    pet = (kv.get("pets") or {}).get("pet") or {}
    quarrels = {p: (q or {}).get("cause") for p, q in ((kv.get("society") or {}).get("quarrel") or {}).items()}
    status = kv.get("status") or {}
    res = kv.get("resources") or {}
    counts = {}
    for _, kind, _ in rows:
        counts[kind] = counts.get(kind, 0) + 1
    econ = {k: v for k, v in metrics_from_rows([(k, d) for _, k, d in rows], st).items() if v}
    return {
        "name": st.get("name"), "job": st.get("job"), "lv": st.get("lv"), "job_lv": st.get("job_lv"),
        "map": st.get("map"), "zeny": st.get("zeny"), "hp": st.get("hp_pct"),
        "status": status.get("state"), "why": status.get("why"),
        "activity": labels.get(act.get("name"), act.get("name")) if act.get("name") else None,
        "mood": kv.get("mood") if isinstance(kv.get("mood"), str) else None,
        "needs": top, "aims": aims, "pet": pet.get("name") or pet.get("type"),
        "career": (kv.get("career") or {}).get("text"),
        "party": bool((kv.get("party") or {}).get("confirmed")),
        "crew_map": ((kv.get("crew") or {}).get("decided") or {}).get("map"),
        "quarrels": quarrels,
        "relations": [{"peer": n, "affinity": a, "note": note} for n, a, note in rels],
        "rows": rows, "counts": counts, "economy": econ, "organic": organic,
        "resources": {"rss_mb": round(res["rss_kb"] / 1024, 1), "cpu_s": round(res.get("cpu_s") or 0),
                      "age_min": int((now - res.get("ts", now)) / 60)} if res.get("rss_kb") else None,
        "db_mb": round(sum(p.stat().st_size for p in db_path.parent.glob("memory.sqlite*")) / 2 ** 20, 2),
    }


def relations(residents):
    """Рёбра графа между жителями: пара — одно ребро, affinity — среднее двух сторон, ссора — если хоть у одного."""
    names = {r["name"] for r in residents}
    pairs = {}
    for r in residents:
        for rel in r["relations"]:
            if rel["peer"] not in names or rel["peer"] == r["name"]:
                continue
            key = tuple(sorted((r["name"], rel["peer"])))
            e = pairs.setdefault(key, {"a": key[0], "b": key[1], "aff": [], "quarrel": None})
            e["aff"].append(int(rel["affinity"] or 0))
        for peer, cause in r["quarrels"].items():
            if peer in names and peer != r["name"]:
                key = tuple(sorted((r["name"], peer)))
                e = pairs.setdefault(key, {"a": key[0], "b": key[1], "aff": [], "quarrel": None})
                e["quarrel"] = cause or "ссора"
    return [{"a": e["a"], "b": e["b"], "affinity": round(sum(e["aff"]) / len(e["aff"]), 1) if e["aff"] else 0,
             "quarrel": e["quarrel"]} for _, e in sorted(pairs.items())]


def collect(lab_root, bots, day=None, tz_hours=0, now=None):
    now = now or time.time()
    tz = timezone(timedelta(hours=tz_hours))
    day = day or datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d")
    start, end, tz = day_bounds(day, tz_hours)
    labels = activity_labels()
    residents, missing, events = [], [], []
    for bot in bots:
        db = Path(lab_root) / "state" / bot / "memory.sqlite"
        if not db.exists():
            missing.append(bot)
            continue
        try:
            r = read_resident(db, start, end, now, labels)
        except sqlite3.Error as e:
            missing.append(f"{bot} ({type(e).__name__})")
            continue
        r["bot"] = bot
        r["name"] = r["name"] or bot
        residents.append(r)
        for ts, kind, data in r.pop("rows"):
            if kind in LINES:
                try:
                    text = LINES[kind](json.loads(data))
                except (ValueError, TypeError, AttributeError, KeyError):
                    continue
                if text:
                    events.append({"ts": ts, "who": r["name"], "text": str(text), "src": "летопись", "imp": 0})
    seen = {(datetime.fromtimestamp(e["ts"], tz).strftime("%H:%M"), e["who"], e["text"]) for e in events}
    for e in world_bus.read_period(Path(lab_root) / "state" / "shared" / "world.sqlite", start, end):
        text = world_bus.describe(e["kind"], e["data"])
        key = (datetime.fromtimestamp(e["ts"], tz).strftime("%H:%M"), e["bot"], text)
        if key in seen:
            continue
        seen.add(key)
        events.append({"ts": e["ts"], "who": e["bot"], "text": text, "src": "шина", "imp": e["importance"]})
    events.sort(key=lambda e: e["ts"])
    cut = max(0, len(events) - MAX_EVENTS)
    for e in events:
        e["time"] = datetime.fromtimestamp(e["ts"], tz).strftime("%H:%M")
    try:                                                                  # serial: серия недели с этим днём
        ep = episode_mod.build(lab_root, bots, day, tz_hours, now=now)    # serial: только правила, без LLM
    except (sqlite3.Error, ValueError, OSError):                          # serial:
        ep = None                                                         # serial:
    return {"day": day, "tz_hours": tz_hours, "episode": ep, "generated": datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d %H:%M"),
            "today": day == datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d"),
            "residents": residents, "missing": missing, "relations": relations(residents),
            "events": events[cut:], "events_cut": cut}


# ---------------- HTML ----------------

def esc(v, limit=None):
    s = "—" if v is None or v == "" else str(v)
    if limit and len(s) > limit:
        s = s[:limit - 1] + "…"
    return html.escape(s, quote=True)


CSS = """
:root{--bg:#f6f5f1;--card:#fff;--fg:#1d1d1b;--muted:#6b6a65;--line:#dedbd2;--accent:#3a5fa8;
--pos:#2f8a4c;--neg:#c0392b;--neu:#9a988f;--chip:#eeece5}
@media (prefers-color-scheme:dark){:root{--bg:#16171a;--card:#202227;--fg:#e9e7e1;--muted:#9c9a93;--line:#34363c;
--accent:#86a8ef;--pos:#5cc27b;--neg:#ee6b5c;--neu:#77756f;--chip:#2b2d33}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}
.sub{color:var(--muted);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.card h3{margin:0;font-size:16px}.row{margin-top:6px;font-size:14px;overflow-wrap:anywhere}
.k{color:var(--muted)}.chip{display:inline-block;background:var(--chip);border-radius:6px;padding:1px 7px;margin:2px 4px 0 0;font-size:13px}
.st{float:right;font-size:12px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--muted)}
ul.aims{margin:4px 0 0;padding-left:18px}ul.aims li{font-size:13px}.done{color:var(--pos)}
.q{color:var(--neg)}
.graph{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px}
.graph svg{display:block;width:100%;max-width:560px;height:auto;margin:0 auto}
svg text{fill:var(--fg);font-size:13px}svg .node{fill:var(--chip);stroke:var(--accent);stroke-width:1.5}
svg .pos{stroke:var(--pos)}svg .neg{stroke:var(--neg)}svg .neu{stroke:var(--neu)}svg .dash{stroke-dasharray:6 5}
.legend{font-size:13px;color:var(--muted);margin-top:6px}
.feed{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px 0;max-height:520px;overflow:auto}
.ev{display:grid;grid-template-columns:48px minmax(0,1fr);gap:8px;padding:5px 14px;border-top:1px solid var(--line);font-size:14px}
.ev:first-child{border-top:0}.ev .t{color:var(--muted);font-variant-numeric:tabular-nums}
.ev .who{font-weight:600}.ev .src{color:var(--muted);font-size:12px}.imp{color:var(--neg)}
.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:6px 10px;border-top:1px solid var(--line);text-align:left;white-space:nowrap}
thead th{border-top:0;color:var(--muted);font-weight:600}td.n{text-align:right;font-variant-numeric:tabular-nums}
.empty{color:var(--muted);font-style:italic;padding:8px 14px}
@media (max-width:600px){.grid{grid-template-columns:1fr}main{padding:14px 12px 40px}h1{font-size:19px}}
"""


def _card(r):
    st = STATUS_RU.get(r["status"], r["status"])
    needs = "".join(f'<span class="chip">{esc(NEEDS_RU.get(k, k))} {v:.2f}</span>' for k, v in r["needs"]) or "—"
    aims = ""
    for a in r["aims"]:
        mark = ' <span class="done">✓</span>' if a["done"] else (
            f' <span class="k">{esc(a["progress"])}/{esc(a["target"])}</span>' if a["progress"] is not None else "")
        aims += f"<li>{esc(a['text'], MAX_CARD_TEXT)}{mark}</li>"
    rows = [
        ("уровень", f"{esc(r['lv'])}/{esc(r['job_lv'])}"),
        ("где", esc(r["map"])),
        ("занятие", esc(r["activity"])),
    ]
    if r["mood"]:
        rows.append(("настроение", esc(r["mood"], 60)))
    rows.append(("мотивы", needs))
    if r["pet"]:
        rows.append(("питомец", esc(r["pet"])))
    if r["party"]:
        rows.append(("группа", "подтверждена сервером" + (f", карта {esc(r['crew_map'])}" if r["crew_map"] else "")))
    if r["quarrels"]:
        rows.append(("ссоры", '<span class="q">' + ", ".join(f"{esc(p)} ({esc(c, 60)})"
                                                             for p, c in sorted(r["quarrels"].items())) + "</span>"))
    if r["career"]:
        rows.append(("путь", esc(r["career"], MAX_CARD_TEXT)))
    body = "".join(f'<div class="row"><span class="k">{k}:</span> {v}</div>' for k, v in rows)
    c = r["counts"]
    day = (f'<div class="row k">за день: побед {c.get("kill", 0)}, смертей {c.get("died", 0)}, '
           f'уровней {c.get("level_up", 0)}, встреч {c.get("meeting_confirmed", 0)}</div>')
    return (f'<article class="card"><span class="st" title="{esc(r["why"])}">{esc(st)}</span>'
            f'<h3>{esc(r["name"])}</h3><div class="sub">{esc(r["job"])} · {esc(r["bot"])}</div>{body}'
            f'<div class="row"><span class="k">цели недели:</span>'
            f'{"<ul class=aims>" + aims + "</ul>" if aims else " —"}</div>{day}</article>')


def _graph(residents, edges):
    names = [r["name"] for r in residents]
    if len(names) < 2:
        return '<div class="empty">Для графа нужно хотя бы два жителя с памятью.</div>'
    size, cx = 520, 260
    rad = 190 if len(names) > 2 else 150
    pos = {n: (cx + rad * math.cos(2 * math.pi * i / len(names) - math.pi / 2),
               cx + rad * math.sin(2 * math.pi * i / len(names) - math.pi / 2)) for i, n in enumerate(names)}
    lines = []
    for e in edges:
        (x1, y1), (x2, y2) = pos[e["a"]], pos[e["b"]]
        aff = e["affinity"]
        cls = "pos" if aff > 0 else "neg" if aff < 0 else "neu"
        width = 1.5 + min(10, abs(aff)) * 0.5
        dash = " dash" if e["quarrel"] else ""
        tip = f'{e["a"]} — {e["b"]}: {aff:+g}' + (f'; ссора: {e["quarrel"]}' if e["quarrel"] else "")
        lines.append(f'<line class="{cls}{dash}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke-width="{width:.1f}" stroke-linecap="round"><title>{esc(tip)}</title></line>')
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        lines.append(f'<text x="{mx:.1f}" y="{my - 4:.1f}" text-anchor="middle" font-size="12">{aff:+g}</text>')
    nodes = []
    for n, (x, y) in pos.items():
        nodes.append(f'<circle class="node" cx="{x:.1f}" cy="{y:.1f}" r="7"><title>{esc(n)}</title></circle>')
        ty = y - 14 if y < cx else y + 24
        nodes.append(f'<text x="{x:.1f}" y="{ty:.1f}" text-anchor="middle" font-weight="600">{esc(n, 24)}</text>')
    empty = "" if edges else '<div class="empty">Отношений между жителями пока нет.</div>'
    return (f'<div class="graph"><svg viewBox="-20 -10 {size + 40} {size + 20}" role="img" '
            f'aria-label="Граф отношений жителей">{"".join(lines)}{"".join(nodes)}</svg>{empty}'
            f'<div class="legend"><span style="color:var(--pos)">━</span> симпатия · '
            f'<span style="color:var(--neg)">━</span> неприязнь · <span style="color:var(--neu)">━</span> нейтрально · '
            f'╌ ссора · толщина — сила, число — среднее affinity двух сторон</div></div>')


def _feed(events, cut):
    if not events:
        return '<div class="feed"><div class="empty">Событий за день нет.</div></div>'
    out = []
    for e in reversed(events):
        imp = f' <span class="imp">{"!" * (e["imp"] - 3)}</span>' if e["imp"] > 3 else ""
        out.append(f'<div class="ev"><span class="t">{esc(e["time"])}</span><span><span class="who">{esc(e["who"])}</span>'
                   f' {esc(e["text"], 300)}{imp} <span class="src">· {esc(e["src"])}</span></span></div>')
    more = f'<div class="empty">ещё {cut} более ранних событий не показано</div>' if cut else ""
    return f'<div class="feed">{"".join(out)}{more}</div>'


def _table(residents, key, title):
    have = [r for r in residents if r.get(key)]
    if not have:
        return f'<div class="tbl"><div class="empty">{esc(title)}: нет данных.</div></div>'
    metrics = []
    for r in have:
        for k in r[key]:
            if k not in metrics:
                metrics.append(k)
    head = "".join(f"<th>{esc(r['name'])}</th>" for r in have)
    body = "".join(f"<tr><th>{esc(m)}</th>" + "".join(f'<td class="n">{esc(r[key].get(m))}</td>' for r in have)
                   + "</tr>" for m in metrics)
    return f'<div class="tbl"><table><thead><tr><th>{esc(title)}</th>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _resources(residents):
    rows = []
    for r in residents:
        res = r["resources"]
        brain = f"{res['rss_mb']} МБ, CPU {res['cpu_s']} с, замер {res['age_min']} мин назад" if res else "—"
        rows.append(f"<tr><th>{esc(r['name'])}</th><td>{esc(brain)}</td><td class=n>{esc(r['db_mb'])} МБ</td></tr>")
    if not rows:
        return '<div class="tbl"><div class="empty">Ресурсы: нет данных.</div></div>'
    return ('<div class="tbl"><table><thead><tr><th>житель</th><th>мозг (RSS, CPU)</th><th>память SQLite</th></tr>'
            f'</thead><tbody>{"".join(rows)}</tbody></table></div>')


def _episode(ep):
    """ORG-091: «Серия недели» — заголовок, сцены со ссылками на факты, незавершённые линии."""
    if not ep:
        return '<div class="feed"><div class="empty">Серия недели недоступна.</div></div>'
    if not ep["scenes"]:
        return f'<div class="feed"><div class="empty">Серия {ep["number"]}: тихая неделя — сюжетных линий нет.</div></div>'
    scenes = "".join(
        f'<div class="ev"><span class="t">{i}</span><span><span class="who">{esc(s["title"])}</span> '
        f'{esc(s["text"], 600)} <span class="src">· '
        + esc("; ".join(f"{r['when'][5:]} {r['who']} {r['kind']}" for r in s["refs"]), 300)
        + '</span></span></div>' for i, s in enumerate(ep["scenes"], 1))
    nxt = (f'<div class="ev"><span class="t">→</span><span class="k">в следующей серии: '
           f'{esc("; ".join(ep["next"]), 400)}</span></div>' if ep["next"] else "")
    return (f'<div class="sub">Серия {ep["number"]}. {esc(ep["title"])} · неделя {esc(ep["week"])}</div>'
            f'<div class="feed">{scenes}{nxt}</div>')


def render(data):
    res = data["residents"]
    cards = "".join(_card(r) for r in res) or '<div class="empty">Нет ни одной памяти жителя.</div>'
    missing = (f'<p class="sub">Нет памяти: {esc(", ".join(data["missing"]))}</p>' if data["missing"] else "")
    since = "с начала дня" if data["today"] else f"с {data['day']} по сейчас"
    tz = f"UTC{data['tz_hours']:+d}"
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Дашборд мира</title>
<style>{CSS}</style></head>
<body><main>
<h1>Мир за {esc(data['day'])}</h1>
<div class="sub">Сформировано {esc(data['generated'])} ({esc(tz)}) · жителей {len(res)} · только чтение памяти и шины мира</div>
{missing}
<h2>Жители</h2>
<section class="grid">{cards}</section>
<h2>Отношения</h2>
{_graph(res, data['relations'])}
<h2>События дня</h2>
{_feed(data['events'], data['events_cut'])}
<h2>Серия недели</h2>
{_episode(data.get('episode'))}
<h2>Органичность <span class="sub">({esc(since)})</span></h2>
{_table(res, 'organic', 'метрика')}
<h2>Экономика <span class="sub">(за день)</span></h2>
{_table(res, 'economy', 'метрика')}
<h2>Ресурсы</h2>
{_resources(res)}
</main></body></html>
"""


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--lab-root", required=True)
    p.add_argument("--bots", nargs="+", required=True)
    p.add_argument("--day")
    p.add_argument("--out")
    p.add_argument("--world", default=str(WORLD / "goals.json"))
    a = p.parse_args(argv)
    tz_hours = _loads(Path(a.world).read_text(encoding="utf-8"), {}).get("timezone_offset_hours", 0) \
        if Path(a.world).exists() else 0
    out = Path(a.out) if a.out else Path(a.lab_root) / "run" / "dashboard.html"
    text = render(collect(a.lab_root, a.bots, a.day, tz_hours))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
