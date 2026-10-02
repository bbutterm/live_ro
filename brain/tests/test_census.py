"""Перепись мира и «кого родить следующим» (ORG-088, ТЗ Т-26): только чтение, только совет.

Запуск: cd brain && python3 -m unittest -v tests.test_census
"""
import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain import census
from live_brain.memory import Memory
from tests.test_resources import CLK, fake_proc

REPO = Path(__file__).resolve().parents[2]


def roster(extra=None):
    res = {"bot01": {"name": "Arkady", "job": "Swordsman", "template": "swordsman", "persona": "bot01",
                     "home_town": "prontera", "active": True, "born": None},
           "bot02": {"name": "Vera", "job": "Acolyte", "template": "acolyte", "persona": "bot02",
                     "home_town": "prontera", "active": True, "born": None}}
    res.update(extra or {})
    return {"residents": res}


class CensusTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        (self.repo / "brain" / "world").mkdir(parents=True)
        shutil.copytree(REPO / "bots" / "templates", self.repo / "bots" / "templates")
        for f in ("progression.json", "goals.json"):
            shutil.copy(REPO / "brain" / "world" / f, self.repo / "brain" / "world" / f)
        self.write_roster(roster())
        self.lab = self.root / "lab"
        self.proc = self.root / "proc"
        self.proc.mkdir()
        (self.proc / "uptime").write_text("1000.00 3000.00\n")
        self.now = time.time()
        for bot, name, job, lv, peer, aff in (("bot01", "Arkady", "Swordman", 42, "Vera", 3),
                                             ("bot02", "Vera", "Acolyte", 30, "Arkady", -1)):
            (self.lab / "state" / bot).mkdir(parents=True)
            mem = Memory(self.lab / "state" / bot / "memory.sqlite")
            mem.set("last_state", {"name": name, "job": job, "lv": lv, "job_lv": 20, "map": "prt_fild08",
                                   "party": "Fountain"})
            mem.update_relation(peer, 0)
            mem.db.execute("UPDATE relations SET affinity = ? WHERE name = ?", (aff, peer))
            mem.update_relation("Stranger", 1)                       # человек — не житель, в связи не попадает
            mem.db.commit()
            for _ in range(3):
                mem.add_event("activity", {"name": "hunt_early"})
            mem.add_event("activity", {"name": "stroll"})
            mem.close()

    def write_roster(self, doc):
        (self.repo / "brain" / "world" / "roster.json").write_text(json.dumps(doc), encoding="utf-8")

    def collect(self, bots=("bot01", "bot02"), max_online=None):
        return census.collect(self.repo, self.lab, list(bots), max_online, now=self.now, proc=self.proc)

    def test_roles(self):
        self.assertEqual(census.role_of("Acolyte"), "healer")
        self.assertEqual(census.role_of("Priest"), "healer")
        self.assertEqual(census.role_of("Merchant"), "merchant")
        self.assertEqual(census.role_of("Blacksmith"), "merchant")
        self.assertEqual(census.role_of("Swordman"), "tank")
        self.assertEqual(census.role_of("Swordsman"), "tank")
        self.assertEqual(census.role_of("Hunter"), "ranged")
        self.assertEqual(census.role_of("Wizard"), "magic")
        self.assertEqual(census.role_of("Rogue"), "thief")
        self.assertEqual(census.role_of("Novice"), "novice")

    def test_composition_and_relations(self):
        data = self.collect()
        a, v = data["residents"]
        self.assertEqual((a["name"], a["job"], a["lv"], a["role"], a["map"]), ("Arkady", "Swordman", 42, "tank",
                                                                                "prt_fild08"))
        self.assertEqual(v["role"], "healer")
        self.assertEqual(a["relations"], {"Vera": 3})
        self.assertEqual(v["relations"], {"Arkady": -1})
        self.assertEqual(a["activities"][0], ("hunt_early", 3))
        text = census.render(data)
        self.assertIn("bot01 Arkady: Swordman, ур. 42/20, роль танк, prt_fild08, группа «Fountain»", text)
        self.assertIn("связи: Vera +3", text)
        self.assertIn("hunt_early ×3", text)
        self.assertNotIn("Stranger", text)

    def test_merchant_missing_recommends_merchant(self):
        data = self.collect()
        roles = [g["role"] for g in data["gaps"]]
        self.assertIn("merchant", roles)
        self.assertNotIn("healer", roles)                 # Vera — лекарь и в той же группе
        best = data["recommend"][0]
        self.assertEqual(best["job"], "Merchant")
        self.assertTrue(any("offer_shop" in w for w in best["why"]))
        text = census.render(data)
        self.assertIn("следующий — Merchant (Bram): scripts/lab new-resident bot03 merchant <Имя>", text)
        self.assertIn("почему: нет торговца", text)

    def test_with_merchant_other_advice(self):
        self.write_roster(roster({"bot03": {"name": "Bram", "job": "Merchant", "template": "merchant",
                                            "persona": "bot03", "home_town": "prontera", "active": True,
                                            "born": "2026-01-01"}}))
        data = self.collect(("bot01", "bot02", "bot03"))
        self.assertNotIn("merchant", [g["role"] for g in data["gaps"]])
        self.assertNotEqual(data["recommend"][0]["job"], "Merchant")
        self.assertEqual(data["residents"][2]["memory"], False)       # памяти нет — не падение
        self.assertEqual(data["residents"][2]["role"], "merchant")    # роль по профессии из реестра
        self.assertIn("памяти нет", census.render(data))
        self.assertEqual(data["next_bot"], "bot04")

    def test_group_without_healer(self):
        self.write_roster(roster({"bot02": {"name": "Vera", "job": "Mage", "template": "mage", "persona": "bot02",
                                            "home_town": "prontera", "active": True, "born": None}}))
        mem = Memory(self.lab / "state" / "bot02" / "memory.sqlite")
        mem.set("last_state", {"name": "Vera", "job": "Mage", "lv": 30, "job_lv": 20, "map": "prt_fild08",
                               "party": "Fountain"})
        mem.close()
        data = self.collect()
        texts = [g["text"] for g in data["gaps"]]
        self.assertTrue(any(t.startswith("нет лекаря") for t in texts), texts)
        self.assertIn("в группе «Fountain» (Arkady, Vera) нет лекаря", texts)
        self.assertEqual(data["recommend"][0]["job"], "Acolyte")
        self.assertIn("у шаблона нет своей персоны", " ".join(data["recommend"][0]["blockers"]))

    def test_novice_counts_by_target(self):
        mem = Memory(self.lab / "state" / "bot02" / "memory.sqlite")
        mem.set("last_state", {"name": "Vera", "job": "Novice", "lv": 5, "job_lv": 4, "map": "iz_int01"})
        mem.close()
        data = self.collect()
        v = data["residents"][1]
        self.assertEqual(v["role"], "novice")
        self.assertNotIn("healer", [g["role"] for g in data["gaps"]])        # идёт к Acolyte
        self.assertIn("Novice → Acolyte", census.render(data))

    def test_birth_blockers(self):
        today = datetime.fromtimestamp(self.now, timezone.utc).date()
        self.write_roster(roster({"bot03": {"name": "Rook", "job": "Thief", "template": "thief", "persona": "bot03",
                                            "home_town": "prontera", "active": False,
                                            "born": str(today - timedelta(days=2))}}))
        data = self.collect()
        text = " ".join(data["blockers"])
        self.assertIn("start_point в Пронтере не включён", text)            # progression.json override.enabled false
        self.assertIn("auto_job_change выключен", text)
        self.assertIn("темп ORG-043", text)
        self.assertIn("bot03 Rook (Thief)", text)
        self.assertNotIn("bot03", [r["bot"] for r in census.world(data["residents"])])   # не активен — не в мире
        prog = json.loads((self.repo / "brain" / "world" / "progression.json").read_text(encoding="utf-8"))
        prog["start"]["override"]["enabled"] = True
        (self.repo / "brain" / "world" / "progression.json").write_text(json.dumps(prog), encoding="utf-8")
        self.assertNotIn("start_point", " ".join(self.collect()["blockers"]))

    def test_thief_penalty(self):
        rec = {t["job"]: t for t in self.collect()["recommend"]}
        self.assertIn("1200 z", " ".join(rec["Thief"]["blockers"]))
        self.assertLess(rec["Thief"]["score"], rec["Mage"]["score"])

    def test_capacity_live_proc_and_max_online(self):
        (self.proc / "meminfo").write_text("MemTotal: 2097152 kB\nMemAvailable: 1048576 kB\n")
        fake_proc(self.proc, 101, ["perl", "/ok/openkore.pl", f"--control={self.lab}/run/bots/bot01/control"],
                  102400, 40 * CLK, 10 * CLK, 500 * CLK)
        fake_proc(self.proc, 102, ["python3", "-m", "live_brain", "--bot", "bot01", "--lab-root", str(self.lab)],
                  30720, 0, 0, 0)
        cap = self.collect(max_online=2)["capacity"]
        # (1024 − 256 резерв) / (100 + 30) МБ = 5
        self.assertEqual((cap["more"], cap["online"], cap["source"]), (5, 1, "/proc сейчас"))
        self.assertTrue(any("LAB_MAX_ONLINE=2" in n and "ждать смены" in n for n in cap["notes"]))
        self.assertFalse(any("LAB_MAX_ONLINE" in n for n in self.collect(max_online=5)["capacity"]["notes"]))

    def test_capacity_from_history_and_none(self):
        cap = self.collect()["capacity"]
        self.assertIsNone(cap["more"])
        self.assertIn("оценки ресурсов нет", " ".join(cap["notes"]))
        (self.lab / "logs").mkdir()
        rec = {"ts": self.now - 600, "mem_available_kb": 300 * 1024,
               "procs": [{"role": "body", "name": "bot01", "rss_kb": 102400, "cpu_s": 1},
                         {"role": "brain", "name": "bot01", "rss_kb": 30720, "cpu_s": 1}]}
        (self.lab / "logs" / "resources.jsonl").write_text(json.dumps(rec) + "\n")
        cap = self.collect()["capacity"]
        self.assertEqual(cap["more"], 0)                  # (300 − 256) МБ < 130 МБ на жителя
        self.assertIn("история сторожа", cap["source"])
        self.assertIn("ресурсов на ещё одного жителя", " ".join(cap["notes"]))

    def test_read_only_and_cli_json(self):
        paths = list((self.lab / "state").glob("*/memory.sqlite"))      # -wal/-shm SQLite создаёт и при чтении
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        roster_before = (self.repo / "brain" / "world" / "roster.json").read_bytes()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = census.main(["--repo", str(self.repo), "--lab-root", str(self.lab), "--bots", "bot01", "bot02",
                              "--proc", str(self.proc), "--max-online", "4", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["recommend"][0]["job"], "Merchant")
        self.assertEqual(data["capacity"]["max_online"], 4)
        self.assertEqual({p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}, before)
        self.assertEqual((self.repo / "brain" / "world" / "roster.json").read_bytes(), roster_before)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            census.main(["--repo", str(self.repo), "--lab-root", str(self.root / "nolab"), "--bots", "bot01",
                         "--proc", str(self.proc)])
        self.assertIn("== перепись мира", out.getvalue())

    def test_real_repo_templates(self):
        jobs = {t["job"] for t in census.templates(REPO)}
        self.assertEqual(jobs, {"Acolyte", "Archer", "Mage", "Merchant", "Swordsman", "Thief"})


if __name__ == "__main__":
    unittest.main()
