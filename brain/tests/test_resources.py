"""ORG-047: разбор /proc на поддельном каталоге, оценка «сколько ещё влезет», история пиков, kv мозга.

Запуск: cd brain && python3 -m unittest -v tests.test_resources
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from live_brain import resources as R
from live_brain.memory import Memory

CLK = os.sysconf("SC_CLK_TCK")
LAB = "/opt/ro-bot-lab"


def fake_proc(root, pid, args, rss_kb, utime, stime, start_ticks, comm="x"):
    d = Path(root) / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes("\0".join(args).encode() + b"\0")
    (d / "status").write_text(f"Name:\t{comm}\nVmPeak:\t{rss_kb * 2} kB\nVmRSS:\t{rss_kb} kB\nThreads:\t1\n")
    rest = ["S"] + ["0"] * 10 + [str(utime), str(stime)] + ["0"] * 6 + [str(start_ticks)] + ["0"] * 10
    (d / "stat").write_text(f"{pid} ({comm} with) spaces) " + " ".join(rest) + "\n")


class ProcTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proc = Path(self.tmp.name) / "proc"
        self.proc.mkdir()
        (self.proc / "uptime").write_text("1000.00 3000.00\n")
        (self.proc / "meminfo").write_text("MemTotal:  2097152 kB\nMemFree:  100000 kB\n"
                                           "MemAvailable:  1048576 kB\n")
        # тело bot01 (100 МБ, 50 с CPU, запущено на 500-й секунде), мозг bot01 (30 МБ)
        fake_proc(self.proc, 101, ["perl", "/opt/ok/openkore.pl", f"--control={LAB}/run/bots/bot01/control"],
                  102400, 40 * CLK, 10 * CLK, 500 * CLK)
        fake_proc(self.proc, 102, ["python3", "-m", "live_brain", "--env", "e", "--bot", "bot01",
                                   "--lab-root", LAB], 30720, 3 * CLK, 2 * CLK, 0)
        # тело bot02 и мозг другого LAB_ROOT (не наш), чужой perl, сервер
        fake_proc(self.proc, 103, ["perl", "openkore.pl", f"--control={LAB}/run/bots/bot02/control"],
                  81920, 0, 0, 0)
        fake_proc(self.proc, 104, ["python3", "-m", "live_brain", "--bot", "bot02", "--lab-root", "/other"],
                  50000, 0, 0, 0)
        fake_proc(self.proc, 105, ["perl", "/usr/bin/other.pl"], 9999, 0, 0, 0)
        fake_proc(self.proc, 106, ["/srv/rathena/map-server"], 307200, 0, 0, 0)
        (self.proc / "107").mkdir()                     # процесс исчез между listdir и чтением

    def tearDown(self):
        self.tmp.cleanup()

    def test_proc_info_parses_stat_with_parens_in_comm(self):
        info = R.proc_info(101, self.proc, CLK, 1000.0)
        self.assertEqual(info["rss_kb"], 102400)
        self.assertEqual(info["cpu_s"], 50.0)
        self.assertEqual(info["age_s"], 500.0)
        self.assertEqual(R.cpu_pct(info), 10.0)
        self.assertIsNone(R.proc_info(107, self.proc))

    def test_scan_classifies_like_lab(self):
        got = {(p["role"], p["name"]): p["pid"] for p in R.scan(LAB, ["bot01", "bot02"], self.proc)}
        self.assertEqual(got, {("body", "bot01"): 101, ("brain", "bot01"): 102, ("body", "bot02"): 103,
                               ("server", "map"): 106})
        only1 = {(p["role"], p["name"]) for p in R.scan(LAB, ["bot01"], self.proc)}
        self.assertNotIn(("body", "bot02"), only1, "бот не из LAB_BOTS не считается")

    def test_capacity_by_mem_available(self):
        procs = R.scan(LAB, ["bot01", "bot02"], self.proc)
        cap = R.capacity(procs, R.meminfo(self.proc), reserve_mb=256)
        # житель = среднее тело (90 МБ) + средний мозг (30 МБ) = 120 МБ; свободно 1024 − 256 = 768 МБ
        self.assertEqual(cap["per_resident_kb"], 92160 + 30720)
        self.assertEqual(cap["more"], 6)
        self.assertEqual(cap["online"], 2)
        self.assertIsNone(R.capacity([], R.meminfo(self.proc)), "без тел оценки нет")

    def test_report_and_history_peaks(self):
        lab = Path(self.tmp.name) / "lab"
        (lab / "state" / "bot01").mkdir(parents=True)
        (lab / "state" / "bot01" / "memory.sqlite").write_bytes(b"x" * 2048)
        (lab / "logs" / "bot01").mkdir(parents=True)
        (lab / "logs" / "bot01" / "console.log").write_bytes(b"y" * 4096)
        hist = lab / "logs" / "resources.jsonl"
        hist.write_text(json.dumps({"ts": time.time() - 7 * 3600, "procs": [
            {"role": "body", "name": "bot01", "rss_kb": 999999}]}) + "\n")      # старше 6 ч — не в пиках
        R.sample(LAB, ["bot01"], hist, self.proc)
        rows = R.read_history(hist, time.time() - R.HISTORY_KEEP)
        self.assertEqual(len(rows), 1)
        self.assertEqual(R.peaks(rows)[("body", "bot01")], 102400)
        text = R.report(LAB, ["bot01", "bot02"], self.proc, hist)
        self.assertIn("bot01: тело pid 101 RSS 100 МБ (пик 6 ч 100 МБ)", text)
        self.assertIn("bot02: тело pid 103", text)
        self.assertIn("мозг не запущен", text)
        self.assertIn("влезет ещё ≈ 6", text)
        self.assertIn("LAB_MAX_ONLINE ≤ 8", text)
        sq, lg = R.disk(lab, "bot01")
        self.assertEqual((sq, lg), (2048, 4096))

    def test_history_rotates(self):
        hist = Path(self.tmp.name) / "r.jsonl"
        hist.write_bytes(b"{}\n" * (R.HISTORY_MAX_BYTES // 3 + 10))
        R.sample(LAB, ["bot01"], hist, self.proc)
        self.assertTrue(Path(f"{hist}.1").exists())
        self.assertEqual(len(hist.read_text().splitlines()), 1)


class BrainSampleTest(unittest.TestCase):
    def test_kv_history_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            mem = Memory(Path(d) / "m.sqlite")
            try:
                for i in range(R.BRAIN_HISTORY + 5):
                    R.brain_sample(mem, now=1000 + i)
                res = mem.get("resources")
                self.assertEqual(len(res["history"]), R.BRAIN_HISTORY)
                self.assertGreater(res["rss_kb"], 1000)
                self.assertIn("ресурсы мозга: RSS", R.brain_line(res))
                self.assertIsNone(R.brain_line(None))
            finally:
                mem.close()


if __name__ == "__main__":
    unittest.main()
