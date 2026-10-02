"""ORG-047: замер ресурсов лаборатории по /proc — без сторонних утилит и без сети.

    python3 -m live_brain.resources --lab-root DIR --bots bot01 bot02 [--sample --history FILE]

Что считается (только чтение):
    тело  — процесс OpenKore (perl … openkore.pl, профиль botNN в аргументах), как find_procs в scripts/lab;
    мозг  — python3 -m live_brain --bot botNN --lab-root DIR;
    сервер — login-server/char-server/map-server (для итога RAM);
    RSS — VmRSS из /proc/<pid>/status, CPU — utime+stime из /proc/<pid>/stat (секунды и средний % с запуска);
    память SQLite и журналы — размеры файлов state/<bot>/*.sqlite*, decisions.jsonl*, logs/<bot>/*;
    «сколько ещё влезет» — (MemAvailable − резерв LAB_RAM_RESERVE_MB) / (средний RSS тела + мозга).
--sample дописывает одну строку JSON в историю (сторож раз в 10 мин): отчёт показывает пики за 6 ч.
Мозг сам пишет свой RSS/CPU в kv "resources" (brain_sample, __main__) — это видно в report бота.
Оценка грубая: RSS включает разделяемые страницы (libc, perl), реальная экономия при росте меньше суммы.
"""
import argparse
import json
import os
import re
import resource
import sys
import time
from pathlib import Path

BOT_IN_ARGS = re.compile(r"/(bot\d+)(?:/|\s|$)")
SERVERS = ("login-server", "char-server", "map-server")
HISTORY_KEEP = 6 * 3600
HISTORY_MAX_BYTES = 5 * 1024 * 1024
BRAIN_HISTORY = 36            # kv "resources": 36 замеров раз в 10 мин = 6 ч


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def proc_info(pid, proc="/proc", clk_tck=None, uptime=None):
    """{pid, args, rss_kb, cpu_s, age_s} процесса из /proc или None (процесс исчез/нет доступа)."""
    base = Path(proc) / str(pid)
    raw_args = _read(base / "cmdline")
    status = _read(base / "status")
    stat = _read(base / "stat")
    if not raw_args or status is None or stat is None:
        return None
    args = raw_args.replace(b"\0", b" ").decode("utf-8", "replace").strip()
    rss = 0
    for line in status.decode("utf-8", "replace").splitlines():
        if line.startswith("VmRSS:"):
            rss = int(line.split()[1])
            break
    # comm в скобках может содержать пробелы — поля считаются после последней ')'
    fields = stat.decode("utf-8", "replace").rsplit(")", 1)[-1].split()
    clk = clk_tck or os.sysconf("SC_CLK_TCK")
    try:
        cpu = (int(fields[11]) + int(fields[12])) / clk          # utime, stime (поля 14, 15)
        start = int(fields[19]) / clk                            # starttime (поле 22)
    except (IndexError, ValueError):
        return None
    up = uptime if uptime is not None else read_uptime(proc)
    return {"pid": int(pid), "args": args, "rss_kb": rss, "cpu_s": round(cpu, 2),
            "age_s": max(0.0, round(up - start, 1)) if up else None}


def read_uptime(proc="/proc"):
    raw = _read(Path(proc) / "uptime")
    try:
        return float(raw.split()[0]) if raw else None
    except ValueError:
        return None


def meminfo(proc="/proc"):
    """{MemTotal, MemAvailable, ...} в кБ."""
    out = {}
    raw = _read(Path(proc) / "meminfo") or b""
    for line in raw.decode("utf-8", "replace").splitlines():
        k, _, v = line.partition(":")
        if v.split():
            try:
                out[k.strip()] = int(v.split()[0])
            except ValueError:
                pass
    return out


def classify(args, lab_root, bots):
    """('body'|'brain'|'server', имя) или None. Правила — как find_procs в scripts/lab."""
    if not args:
        return None
    argv0 = os.path.basename(args.split()[0])
    if argv0 in SERVERS:
        return "server", argv0.replace("-server", "")
    if argv0.startswith("python") and "-m live_brain" in args and f"--lab-root {lab_root} " in args + " ":
        m = re.search(r"--bot (bot\d+)(?:\s|$)", args)
        if m and m.group(1) in bots:
            return "brain", m.group(1)
        return None
    if argv0.startswith("perl") and "openkore.pl" in args:
        m = BOT_IN_ARGS.search(args)
        if m and m.group(1) in bots:
            return "body", m.group(1)
        if not m and "bot01" in bots:
            return "body", "bot01"          # ручной запуск без метки профиля — как в scripts/lab
    return None


def scan(lab_root, bots, proc="/proc"):
    """Список процессов лаборатории: [{role, name, pid, rss_kb, cpu_s, age_s}]."""
    out = []
    clk = os.sysconf("SC_CLK_TCK")
    up = read_uptime(proc)
    try:
        pids = sorted(int(p) for p in os.listdir(proc) if p.isdigit())
    except OSError:
        return out
    me = os.getpid()
    for pid in pids:
        if pid == me:
            continue
        info = proc_info(pid, proc, clk, up)
        if not info:
            continue
        kind = classify(info["args"], str(lab_root), set(bots))
        if kind:
            info.pop("args")
            out.append(dict(info, role=kind[0], name=kind[1]))
    return out


def du(paths):
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def disk(lab_root, bot):
    """(байт SQLite, байт журналов) жителя."""
    st, lg = Path(lab_root) / "state" / bot, Path(lab_root) / "logs" / bot
    sqlite = du(st.glob("*.sqlite*")) if st.is_dir() else 0
    logs = (du(st.glob("decisions.jsonl*")) + du(st.glob("replay.jsonl*"))) if st.is_dir() else 0
    logs += du(p for p in lg.rglob("*") if p.is_file()) if lg.is_dir() else 0
    return sqlite, logs


def capacity(procs, mem, reserve_mb=256):
    """Оценка: сколько ещё жителей (тело + мозг) влезет в MemAvailable − резерв. None — нечем мерить."""
    bodies = [p["rss_kb"] for p in procs if p["role"] == "body"]
    brains = [p["rss_kb"] for p in procs if p["role"] == "brain"]
    if not bodies or "MemAvailable" not in mem:
        return None
    per = sum(bodies) / len(bodies) + (sum(brains) / len(brains) if brains else 0)
    free = mem["MemAvailable"] - reserve_mb * 1024
    return {"per_resident_kb": int(per), "free_kb": max(0, free),
            "more": max(0, int(free // per)) if per > 0 else 0, "online": len(bodies)}


def cpu_pct(p):
    return round(100 * p["cpu_s"] / p["age_s"], 1) if p.get("age_s") else None


def mb(kb):
    return f"{kb / 1024:.0f} МБ" if kb >= 10240 else f"{kb / 1024:.1f} МБ"


def read_history(path, since):
    rows = []
    for name in (f"{path}.1", str(path)):
        raw = _read(name)
        if not raw:
            continue
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("ts", 0) >= since:
                rows.append(rec)
    return rows


def peaks(rows):
    """{(role, name): максимальный RSS кБ} по истории."""
    out = {}
    for rec in rows:
        for p in rec.get("procs") or []:
            k = (p.get("role"), p.get("name"))
            out[k] = max(out.get(k, 0), int(p.get("rss_kb") or 0))
    return out


def sample(lab_root, bots, history, proc="/proc", now=None):
    """Одна строка истории (сторож): процессы и MemAvailable. Файл не растёт больше HISTORY_MAX_BYTES."""
    rec = {"ts": round(now or time.time()), "mem_available_kb": meminfo(proc).get("MemAvailable"),
           "procs": [{k: p[k] for k in ("role", "name", "rss_kb", "cpu_s")} for p in scan(lab_root, bots, proc)]}
    path = Path(history)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > HISTORY_MAX_BYTES:
            os.replace(path, f"{path}.1")
    except OSError:
        pass
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def report(lab_root, bots, proc="/proc", history=None, reserve_mb=256, now=None):
    now = now or time.time()
    procs = scan(lab_root, bots, proc)
    mem = meminfo(proc)
    pk = peaks(read_history(history, now - HISTORY_KEEP)) if history else {}
    lines = ["== ресурсы (ORG-047, /proc)"]
    by = {(p["role"], p["name"]): p for p in procs}
    total_sqlite = total_logs = 0
    for b in bots:
        parts = []
        for role, label in (("body", "тело"), ("brain", "мозг")):
            p = by.get((role, b))
            if p:
                pct = cpu_pct(p)
                peak = pk.get((role, b))
                parts.append(f"{label} pid {p['pid']} RSS {mb(p['rss_kb'])}"
                             + (f" (пик 6 ч {mb(peak)})" if peak else "")
                             + f", CPU {p['cpu_s']:.0f} с" + (f" ({pct}% с запуска)" if pct is not None else ""))
            else:
                parts.append(f"{label} не запущен")
        sq, lg = disk(lab_root, b)
        total_sqlite += sq
        total_logs += lg
        parts.append(f"SQLite {mb(sq // 1024)}, журналы {mb(lg // 1024)}")
        lines.append(f"   {b}: " + "; ".join(parts))
    servers = [p for p in procs if p["role"] == "server"]
    if servers:
        lines.append("   серверы rAthena: " + ", ".join(f"{p['name']} {mb(p['rss_kb'])}" for p in servers))
    shared = Path(lab_root) / "state" / "shared"
    shared_sq = du(shared.glob("*.sqlite*")) if shared.is_dir() else 0
    body_kb = sum(p["rss_kb"] for p in procs if p["role"] == "body")
    brain_kb = sum(p["rss_kb"] for p in procs if p["role"] == "brain")
    srv_kb = sum(p["rss_kb"] for p in servers)
    lines.append(f"   итого RSS: тела {mb(body_kb)}, мозги {mb(brain_kb)}, серверы {mb(srv_kb)}; "
                 f"SQLite жителей {mb(total_sqlite // 1024)} + общие {mb(shared_sq // 1024)}, "
                 f"журналы {mb(total_logs // 1024)}")
    cap = capacity(procs, mem, reserve_mb)
    if mem:
        head = f"   RAM: всего {mb(mem.get('MemTotal', 0))}, доступно {mb(mem.get('MemAvailable', 0))}"
        if cap:
            lines.append(head + f", резерв {reserve_mb} МБ; житель ≈ {mb(cap['per_resident_kb'])} (тело + мозг) "
                                f"→ влезет ещё ≈ {cap['more']}; разумный LAB_MAX_ONLINE ≤ {cap['online'] + cap['more']}")
        else:
            lines.append(head + "; оценки на жителя нет — ни одно тело не запущено")
    return "\n".join(lines)


def brain_sample(memory, now=None):
    """Мозг пишет свой RSS/CPU в kv "resources" (история BRAIN_HISTORY замеров) — для report без /proc чужих."""
    ru = resource.getrusage(resource.RUSAGE_SELF)
    info = proc_info(os.getpid()) or {}
    rec = {"ts": round(now or time.time()), "rss_kb": info.get("rss_kb") or ru.ru_maxrss,
           "cpu_s": round(ru.ru_utime + ru.ru_stime, 2)}
    old = memory.get("resources") or {}
    hist = (old.get("history") or [])[-(BRAIN_HISTORY - 1):] + [[rec["ts"], rec["rss_kb"], rec["cpu_s"]]]
    memory.set("resources", dict(rec, maxrss_kb=ru.ru_maxrss, history=hist))
    return rec


def brain_line(res):
    """Строка report бота из kv "resources" или None."""
    if not res or not res.get("rss_kb"):
        return None
    hist = res.get("history") or []
    peak = max((h[1] for h in hist), default=res["rss_kb"])
    age = int((time.time() - res.get("ts", 0)) / 60)
    return (f"   ресурсы мозга: RSS {mb(res['rss_kb'])} (пик за {len(hist) * 10} мин {mb(peak)}), "
            f"CPU {res.get('cpu_s', 0):.0f} с; замер {age} мин назад")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--lab-root", required=True)
    p.add_argument("--bots", nargs="+", required=True)
    p.add_argument("--proc", default="/proc")
    p.add_argument("--history", help="история замеров (по умолчанию LAB_ROOT/logs/resources.jsonl)")
    p.add_argument("--sample", action="store_true", help="дописать один замер в историю и выйти")
    p.add_argument("--reserve-mb", type=int, default=int(os.environ.get("LAB_RAM_RESERVE_MB") or 256))
    a = p.parse_args(argv)
    history = a.history or str(Path(a.lab_root) / "logs" / "resources.jsonl")
    if a.sample:
        sample(a.lab_root, a.bots, history, a.proc)
        return 0
    print(report(a.lab_root, a.bots, a.proc, history, a.reserve_mb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
