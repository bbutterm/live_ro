"""Хроника мира: что произошло за сутки у всех жителей вместе (только чтение памяти).

Владелец видит мир целиком, а не по одному боту: кто где охотился, кто с кем встречался,
кто кого вылечил, кто кому помог, кто погиб и почему (по разбору смерти), что сказали
дневники и какие были оповещения. Источник — только события в памяти жителей (факты игры)
и общая шина мира state/shared/world.sqlite (раздел «События мира», ORG-045), ничего не придумывается. Запуск: scripts/lab chronicle [YYYY-MM-DD] (по умолчанию — сегодня
по часовому поясу мира из goals.json).

    python3 -m live_brain.chronicle --lab-root /opt/ro-bot-lab --bots bot01 bot02 [--day 2026-10-02]
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import world_bus
from .economy import CHRONICLE_LINES, metrics_from_rows
from .society import CHRONICLE_LINES as SOCIETY_LINES   # society: ссоры, примирения, вывески

LINES = {
    "level_up": lambda d: f"достиг {d.get('level')} уровня",
    "party_confirmed": lambda d: f"группа {d.get('party')} подтверждена сервером ({d.get('members')})",
    "heal_confirmed": lambda d: f"Heal: {d.get('from')} → {d.get('to')} +{d.get('amount')} HP",
    "gift_given": lambda d: f"отдал {d.get('peer')} {d.get('amount')} × {d.get('item')}",
    "gift_received": lambda d: f"получил от {d.get('peer')} {d.get('got') or d.get('amount')} × {d.get('item')}",
    "meeting_confirmed": lambda d: f"встретился с {d.get('partner')} на {d.get('map')}",
    "death_report": lambda d: (f"погиб на {d.get('map')}" + (f" (бил {d.get('cause')})" if d.get("cause") else "")
                               + (f"; неизвестно: {', '.join(d['unknown'])}" if d.get("unknown") else "")),
    "map_banned": lambda d: f"исключил карту {d.get('map')} после смертей",
    "routine_blocked": lambda d: "застрял в тупике — нужен владелец",
    "recover_blocked": lambda d: "не может восстановиться (вес/зелья)",
    "diary": lambda d: d.get("text"),
    **CHRONICLE_LINES,                                    # ORG-037: сделки и письма жителей
    **SOCIETY_LINES,                                      # society: ORG-026/027
}


def day_bounds(day, tz_hours):
    tz = timezone(timedelta(hours=tz_hours))
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=tz).timestamp()
    return start, start + 86400, tz


def read_bot(db_path, start, end):
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT ts, kind, data FROM events WHERE ts >= ? AND ts < ? ORDER BY ts",
                          (start, end)).fetchall()
        kv = dict(db.execute("SELECT key, value FROM kv WHERE key IN ('last_state', 'status')").fetchall())
    finally:
        db.close()
    return rows, {k: json.loads(v) for k, v in kv.items()}


def chronicle(lab_root, bots, day=None, tz_hours=3):
    tz = timezone(timedelta(hours=tz_hours))
    day = day or datetime.now(tz).strftime("%Y-%m-%d")
    start, end, tz = day_bounds(day, tz_hours)
    out = [f"# Хроника мира за {day} (UTC{tz_hours:+d})", ""]
    timeline = []
    for bot in bots:
        db = Path(lab_root) / "state" / bot / "memory.sqlite"
        if not db.exists():
            out.append(f"- {bot}: памяти нет")
            continue
        rows, kv = read_bot(db, start, end)
        st = kv.get("last_state") or {}
        name = st.get("name") or bot
        counts = {}
        for ts, kind, data in rows:
            counts[kind] = counts.get(kind, 0) + 1
            if kind in LINES:
                try:
                    text = LINES[kind](json.loads(data))
                except (ValueError, TypeError):
                    continue
                if text:
                    timeline.append((ts, name, text))
        status = (kv.get("status") or {}).get("state", "?")
        out.append(f"## {name} — {st.get('job', '?')} {st.get('lv', '?')}/{st.get('job_lv', '?')}, сейчас {status}, "
                   f"{st.get('map', '?')}")
        out.append(f"побед {counts.get('kill', 0)}, смертей {counts.get('died', 0)}, уровней {counts.get('level_up', 0)}, "
                   f"лечений в группе {counts.get('heal_confirmed', 0)}, передач {counts.get('gift_given', 0)}/"
                   f"{counts.get('gift_received', 0)}, встреч {counts.get('meeting_confirmed', 0)}")
        econ = {k: v for k, v in metrics_from_rows([(k, d) for _, k, d in rows], st).items() if v}
        if econ:
            out.append("экономика: " + ", ".join(f"{k} {v}" for k, v in econ.items()))
        out.append("")
    if timeline:
        out.append("## События")
        for ts, name, text in sorted(timeline):
            out.append(f"- {datetime.fromtimestamp(ts, tz).strftime('%H:%M')} {name}: {text}")
    else:
        out.append("Событий за день нет.")
    out += world_section(lab_root, start, end, tz)
    alerts = Path(lab_root) / "run" / "alerts.log"
    if alerts.exists():
        day_alerts = [json.loads(l) for l in alerts.read_text(encoding="utf-8").splitlines() if l.strip()]
        day_alerts = [a for a in day_alerts if a.get("ts", "").startswith(day)]
        if day_alerts:
            out += ["", "## Оповещения владельцу"] + [f"- {a['ts'][11:16]}Z {a['bot']}: {a['kind']} — {a['text']}"
                                                       for a in day_alerts]
    return "\n".join(out)


def world_section(lab_root, start, end, tz):
    """ORG-045: раздел «События мира» — общая шина state/shared/world.sqlite (значимые факты всех жителей,
    объявления сервера, слухи, цели недели). Нет шины или событий — раздела нет."""
    events = world_bus.read_period(Path(lab_root) / "state" / "shared" / "world.sqlite", start, end)
    if not events:
        return []
    lines = ["", "## События мира"]
    for e in events:
        mark = "!" * max(0, e["importance"] - 3)
        lines.append(f"- {datetime.fromtimestamp(e['ts'], tz).strftime('%H:%M')} {e['bot']}: "
                     f"{world_bus.describe(e['kind'], e['data'])}{(' ' + mark) if mark else ''}")
    return lines


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--lab-root", required=True)
    p.add_argument("--bots", nargs="+", required=True)
    p.add_argument("--day")
    p.add_argument("--world", default=str(Path(__file__).resolve().parents[1] / "world" / "goals.json"))
    a = p.parse_args(argv)
    tz_hours = json.loads(Path(a.world).read_text(encoding="utf-8")).get("timezone_offset_hours", 0) \
        if Path(a.world).exists() else 0
    print(chronicle(a.lab_root, a.bots, a.day, tz_hours))
    return 0


if __name__ == "__main__":
    sys.exit(main())
