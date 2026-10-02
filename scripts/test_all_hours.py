#!/usr/bin/env python3
"""Прогон тестов мозга (brain/tests) с подменой «текущего» времени — для разработчика, не для CI.

Часть логики жителей зависит от времени мира (сон 23:30–08:00, ночь, вечерний круг, день недели,
праздники календаря). Тесты должны быть зелёными в любое время запуска; этот скрипт проверяет это,
сдвигая часы процесса через временный sitecustomize.py:
  - time.time()/time_ns();
  - time.localtime()/gmtime()/ctime()/strftime() без аргумента времени;
  - datetime.now()/utcnow()/today(), date.today().
Монотонные часы (time.monotonic, asyncio) не трогаются.

Usage:
  python3 scripts/test_all_hours.py                     # сдвиги 0,3,…,21 ч + 1..6 суток
  python3 scripts/test_all_hours.py --hours 0-23 --days 0
  python3 scripts/test_all_hours.py --hours 1,13 --days 0,2,5 -j 4 -k test_party
  python3 scripts/test_all_hours.py --hours 0.5,2.75 --days 0       # дробные часы — проверить границы окон
Переменные LIVE_RO_RATHENA/LIVE_RO_OPENKORE берутся из окружения, иначе — upstream/ в репозитории.
Код выхода 0 — все сдвиги зелёные.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SITECUSTOMIZE = r'''
import os, time, datetime as _dt
_off = float(os.environ.get("FAKE_OFFSET", "0"))
if _off:
    _real, _real_ns = time.time, time.time_ns
    time.time = lambda: _real() + _off
    time.time_ns = lambda: _real_ns() + int(_off * 1e9)
    class _NoArgNow:
        # Не функция, а объект: иначе как атрибут класса (logging.Formatter.converter = time.localtime)
        # он стал бы методом и получил self вместо секунд.
        def __init__(self, f):
            self.f = f
        def __call__(self, secs=None):
            return self.f(time.time() if secs is None else secs)
    for _n in ("localtime", "gmtime", "ctime"):
        setattr(time, _n, _NoArgNow(getattr(time, _n)))
    def _strftime(fmt, t=None, _f=time.strftime):
        return _f(fmt, time.localtime() if t is None else t)
    time.strftime = _strftime
    _RD = _dt.datetime
    class _FakeDatetime(_RD):
        @classmethod
        def now(cls, tz=None):
            return _RD.fromtimestamp(time.time(), tz)
        @classmethod
        def utcnow(cls):
            return _RD.fromtimestamp(time.time(), _dt.timezone.utc).replace(tzinfo=None)
        @classmethod
        def today(cls):
            return _RD.fromtimestamp(time.time())
    class _FakeDate(_dt.date):
        @classmethod
        def today(cls):
            return _RD.fromtimestamp(time.time()).date()
    _dt.datetime, _dt.date = _FakeDatetime, _FakeDate
'''


def parse_list(text):
    out = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(float(part) if "." in part else int(part))
    return out


def run_one(offset_h, offset_d, fake_dir, pattern):
    env = dict(os.environ)
    env["PYTHONPATH"] = fake_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["FAKE_OFFSET"] = str(int(offset_h * 3600) + offset_d * 86400)
    env.setdefault("LIVE_RO_RATHENA", str(ROOT / "upstream" / "rathena"))
    env.setdefault("LIVE_RO_OPENKORE", str(ROOT / "upstream" / "openkore"))
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
    if pattern:
        cmd += ["-k", pattern]
    p = subprocess.run(cmd, cwd=ROOT / "brain", env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\S+ \([^)]*\))", p.stdout, re.M)))
    summary = (re.findall(r"^(Ran \d+ tests.*|OK.*|FAILED.*)$", p.stdout, re.M) or ["?"])[-1]
    return offset_h, offset_d, p.returncode, summary, failed, p.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hours", default="0,3,6,9,12,15,18,21", help="сдвиги в часах: 0,3,6 или 0-23")
    ap.add_argument("--days", default="0-6", help="сдвиги в сутках (дни недели), сочетаются с --hours")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 2, help="параллельных прогонов")
    ap.add_argument("-k", dest="pattern", help="фильтр тестов (unittest -k)")
    ap.add_argument("-v", "--verbose", action="store_true", help="печатать вывод упавших прогонов")
    a = ap.parse_args()
    hours, days = parse_list(a.hours), parse_list(a.days)
    # Часы на нулевом дне, плюс по одному сдвигу часа на каждый другой день (полное произведение долго).
    combos = [(h, 0) for h in hours] if 0 in days else []
    combos += [(hours[i % len(hours)], d) for i, d in enumerate(x for x in days if x)]
    with tempfile.TemporaryDirectory(prefix="faketime_") as tmp:
        Path(tmp, "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
        with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
            results = list(pool.map(lambda c: run_one(c[0], c[1], tmp, a.pattern), combos))
    bad = 0
    for h, d, rc, summary, failed, out in sorted(results, key=lambda r: (r[1], r[0])):
        mark = "ok  " if rc == 0 else "FAIL"
        print(f"{mark} +{d}д {h:+5g}ч  {summary}")
        for name in failed:
            print(f"       {name}")
        if rc:
            bad += 1
            if a.verbose:
                print(out)
    print(f"\nсдвигов: {len(results)}, упало: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
