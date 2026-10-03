"""Реестр жителей, шаблоны и генератор нового жителя (ORG-040..044).

Запуск: cd brain && python3 -m unittest -v tests.test_roster
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_brain.__main__ import peer_names

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import roster  # noqa: E402

PHRASE_KEYS = ("hello", "weather", "hunt", "loot", "tired", "death", "level", "congrats", "condolence", "thanks", "bye")
TRAITS = ("bravery", "sociability", "greed", "curiosity", "diligence", "generosity", "patience", "whimsy")
SECRET_LINE = re.compile(r"^(username|password|storageAuto_password|adminPassword)[ \t]+\S", re.M)
FIXTURE_RESIDENTS = {
    "bot01": {"name": "Arkady", "job": "Swordsman", "template": "swordsman", "persona": "bot01",
              "home_town": "prontera", "active": True, "born": None},
    "bot02": {"name": "Vera", "job": "Acolyte", "template": "acolyte", "persona": "bot02",
              "home_town": "prontera", "active": True, "born": None},
}


def copy_repo(dst):
    """Двухжительный сценарий, независимый от населения deployment."""
    for rel in ("bots/bot01", "bots/bot02", "bots/templates"):
        shutil.copytree(ROOT / rel, dst / rel)
    shutil.copytree(ROOT / "brain/world", dst / "brain/world", ignore=shutil.ignore_patterns("roster.json"))
    (dst / "brain/personas").mkdir(parents=True)
    for bot in FIXTURE_RESIDENTS:
        shutil.copy2(ROOT / "brain/personas" / f"{bot}.json", dst / "brain/personas" / f"{bot}.json")
    residents = {bot: dict(entry) for bot, entry in FIXTURE_RESIDENTS.items()}
    roster.save(dst, {"residents": residents})
    for bot in residents:
        config = dst / "bots" / bot / "control/config.txt"
        text, missing = roster.set_values(config.read_text(), roster.static_values(residents, bot))
        if missing:
            raise AssertionError(f"{bot}: отсутствуют fixture-поля {missing}")
        config.write_text(text)
    return dst


class RosterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    # ---------- реестр в Git ----------

    def test_repo_roster_consistent(self):
        doc = roster.load(ROOT)
        personas = {p.stem: json.loads(p.read_text())["name"]
                    for p in (ROOT / "brain/personas").glob("bot*.json")}
        self.assertTrue(personas, "в checkout должны быть персоны жителей")
        self.assertEqual({b: r["name"] for b, r in doc["residents"].items()}, personas)
        problems, _ = roster.check(ROOT, list(doc["residents"]))
        self.assertEqual(problems, [])

    def test_runtime_values_follow_lab_bots(self):
        res = roster.load(copy_repo(self.root / "r"))["residents"]
        self.assertEqual(roster.runtime_values(res, "bot01", ["bot01", "bot02"]),
                         {"residents": "Vera", "dealAuto_names": "Vera"})
        # Vera не запущена: в группу её не ждём, но сделки по-прежнему только с жителями (не пустой список)
        self.assertEqual(roster.runtime_values(res, "bot01", ["bot01"]),
                         {"residents": "", "dealAuto_names": "Vera"})
        res = dict(res, bot02=dict(res["bot02"], active=False))
        with self.assertRaises(roster.RosterError):
            roster.runtime_values(res, "bot01", ["bot01"])     # пустой dealAuto_names = сделки от всех

    def test_set_values_top_level_only(self):
        text = "attackSkillSlot SM_BASH {\n\tresidents nope\n}\nresidents Vera\ndealAuto_names Vera\n"
        out, missing = roster.set_values(text, {"residents": "Vera,Bram", "autoCreate": "1"})
        self.assertIn("\tresidents nope\n", out)
        self.assertIn("\nresidents Vera,Bram\n", out)
        self.assertEqual(missing, ["autoCreate"])
        out, missing = roster.set_values(text, {"autoCreate": "1"}, append=True)
        self.assertTrue(out.endswith("autoCreate 1\n"))
        self.assertEqual(missing, [])

    # ---------- мозг: peer_names из реестра ∩ LAB_BOTS ----------

    def test_peer_names_from_roster(self):
        repo = copy_repo(self.root / "r")
        persona = repo / "brain" / "personas" / "bot01.json"
        self.assertEqual(peer_names(persona, ["bot01"]), {"Arkady"})
        self.assertEqual(peer_names(persona, ["bot01", "bot02"]), {"Arkady", "Vera"})
        self.assertEqual(peer_names(persona), {"Arkady", "Vera"})

    def test_peer_names_inactive_and_persona_field(self):
        repo = copy_repo(self.root / "r")
        doc = roster.load(repo)
        doc["residents"]["bot03"] = {"name": "Bram", "job": "Merchant", "template": "merchant", "persona": "bram",
                                     "home_town": "prontera", "active": True, "born": None}
        doc["residents"]["bot02"]["active"] = False
        roster.save(repo, doc)
        bram = json.loads((ROOT / "bots/templates/merchant/persona.json").read_text())
        (repo / "brain/personas/bram.json").write_text(json.dumps(bram))
        persona = repo / "brain/personas/bot01.json"
        self.assertEqual(peer_names(persona, ["bot01", "bot02", "bot03"]), {"Arkady", "Bram"})
        self.assertEqual(peer_names(persona, ["bot01", "bot02"]), {"Arkady"})

    def test_peer_names_without_roster_as_before(self):
        repo = copy_repo(self.root / "r")
        (repo / "brain/world/roster.json").unlink()
        persona = repo / "brain/personas/bot01.json"
        self.assertEqual(peer_names(persona, ["bot01"]), {"Arkady"})
        self.assertEqual(peer_names(persona), {"Arkady", "Vera"})

    # ---------- шаблоны (ORG-041) ----------

    def test_templates_and_personas(self):
        goals = json.loads((ROOT / "brain/world/goals.json").read_text())
        points = set(goals["social"]["points"])
        classes = json.loads((ROOT / "bots/combat/classes.json").read_text())["classes"]
        names = set()
        for t in ("swordsman", "acolyte", "merchant", "archer", "thief", "mage"):
            tpl = roster.load_template(ROOT, t)
            self.assertIn(tpl["job"], classes, t)
            self.assertTrue((ROOT / "bots" / tpl["base"] / "control/config.txt").is_file())
            if not tpl.get("persona"):
                continue
            p = json.loads((ROOT / "bots/templates" / t / tpl["persona"]).read_text())
            self.assertRegex(p["name"], roster.NAME_RE, t)
            self.assertEqual(p["name"], tpl["default_name"])
            names.add(p["name"])
            for key in ("character", "speech", "goals", "hunt_maps", "greeting", "routine", "social", "phrases",
                        "traits", "sleep"):
                self.assertIn(key, p, f"{t}: {key}")
            for key in PHRASE_KEYS:
                self.assertGreaterEqual(len(p["phrases"][key]), 5, f"{t}: phrases.{key}")
                self.assertEqual(len(set(p["phrases"][key])), len(p["phrases"][key]), f"{t}: повторы в {key}")
            for tr in TRAITS:
                self.assertTrue(0 <= p["traits"][tr] <= 1, f"{t}: {tr}")
            self.assertLessEqual(set(p["social"]["point_weights"]), points, t)
            self.assertRegex(p["sleep"]["start"], r"^\d\d:\d\d$")
            self.assertEqual(len(p["sleep"]["hours"]), 2)
        self.assertEqual(len(names), 4, "четыре разных новых жителя")
        self.assertFalse(names & {"Arkady", "Vera"})

    # ---------- генератор нового жителя ----------

    def test_new_resident(self):
        repo = copy_repo(self.root / "r")
        created = roster.new_resident(repo, "bot04", "merchant", "Bram")
        self.assertIn("bots/bot04/control", created)
        cfg = (repo / "bots/bot04/control/config.txt").read_text()
        self.assertIsNone(SECRET_LINE.search(cfg), "в профиле не должно быть логина/пароля")
        v = roster.read_values(cfg, ("residents", "dealAuto_names", "autoCreate", "autoCreate_name", "autoCreate_sex",
                                     "economy_storeIds", "char"))
        self.assertEqual(v["residents"], "Arkady,Vera")
        self.assertEqual(v["dealAuto_names"], "Arkady,Vera")
        self.assertEqual((v["autoCreate"], v["autoCreate_name"], v["autoCreate_sex"], v["char"]), ("1", "Bram", "M", "0"))
        self.assertEqual(v["economy_storeIds"], "984,985,756,757,1002,998")
        self.assertIn(",autoCreate\n", (repo / "bots/bot04/control/sys.txt").read_text())
        self.assertTrue((repo / "bots/bot04/tables/servers.txt").is_file())
        persona = json.loads((repo / "brain/personas/bot04.json").read_text())
        self.assertEqual(persona["name"], "Bram")
        entry = roster.load(repo)["residents"]["bot04"]
        self.assertEqual(entry, {"name": "Bram", "job": "Merchant", "template": "merchant", "persona": "bot04",
                                 "home_town": "prontera", "active": False, "born": None})
        problems, _ = roster.check(repo)
        self.assertEqual(problems, [], "новый неактивный житель не меняет residents других")
        # квестовые вещи Knight у не-мечника продаются, как у bot02
        ic = (repo / "bots/bot04/control/items_control.txt").read_text()
        self.assertEqual(ic, (ROOT / "bots/bot02/control/items_control.txt").read_text())
        # повтор, занятое имя, неверное имя, шаблон без персоны
        with self.assertRaises(roster.RosterError):
            roster.new_resident(repo, "bot04", "thief", "Rook")
        with self.assertRaises(roster.RosterError):
            roster.new_resident(repo, "bot05", "thief", "vera")
        with self.assertRaises(roster.RosterError):
            roster.new_resident(repo, "bot05", "thief", "Ro k")
        with self.assertRaises(roster.RosterError):
            roster.new_resident(repo, "bot05", "acolyte", "Vesna")
        self.assertFalse((repo / "bots/bot05").exists(), "неудача не оставляет полупрофиль")

    def test_new_swordsman_keeps_knight_items_and_persona_file(self):
        repo = copy_repo(self.root / "r")
        roster.new_resident(repo, "bot03", "swordsman", "Gerold", persona_file=ROOT / "brain/personas/bot01.json")
        ic = (repo / "bots/bot03/control/items_control.txt").read_text()
        self.assertEqual(ic, (ROOT / "bots/bot01/control/items_control.txt").read_text())
        p = json.loads((repo / "brain/personas/bot03.json").read_text())
        self.assertEqual(p["name"], "Gerold")
        self.assertIn("Gerold снова на поле", p["greeting"])

    def test_activation_and_sync(self):
        repo = copy_repo(self.root / "r")
        roster.new_resident(repo, "bot06", "mage", "Odette")
        doc = roster.load(repo)
        doc["residents"]["bot06"]["active"] = True
        roster.save(repo, doc)
        problems, _ = roster.check(repo)
        self.assertEqual(len([p for p in problems if "roster sync" in p]), 4)
        changes = roster.sync(repo, write=False)
        self.assertEqual(len(changes), 4)
        self.assertIn("residents Vera\n", (repo / "bots/bot01/control/config.txt").read_text(), "без --write не пишет")
        roster.sync(repo, write=True)
        self.assertEqual(roster.check(repo)[0], [])
        cfg = (repo / "bots/bot01/control/config.txt").read_text()
        self.assertIn("\nresidents Vera,Odette\n", cfg)
        self.assertIn("\ndealAuto_names Vera,Odette\n", cfg)
        self.assertEqual(roster.sync(repo), [])

    def test_render_uses_roster(self):
        repo = copy_repo(self.root / "r")
        roster.new_resident(repo, "bot03", "archer", "Ilsa", sex="F")
        env = self.root / "env"
        env.write_text("BOT03_USER=ilsa03\nBOT03_PASS=pw\nBOT03_SEX=F\nLAB_BOTS=\"bot01 bot03\"\n")
        out = self.root / "out"
        subprocess.run([sys.executable, str(ROOT / "scripts/lib/render.py"), "bot", str(repo / "bots/bot03/control"),
                        str(out), str(env), "bot03"], check=True, capture_output=True)
        v = roster.read_values((out / "config.txt").read_text(),
                               ("residents", "dealAuto_names", "autoCreate_name", "autoCreate_sex", "username"))
        self.assertEqual(v, {"residents": "Arkady", "dealAuto_names": "Arkady", "autoCreate_name": "Ilsa",
                             "autoCreate_sex": "F", "username": "ilsa03"})
        # реестра нет — config.txt как в профиле (прежнее поведение)
        (repo / "brain/world/roster.json").unlink()
        shutil.rmtree(out)
        subprocess.run([sys.executable, str(ROOT / "scripts/lib/render.py"), "bot", str(repo / "bots/bot03/control"),
                        str(out), str(env), "bot03"], check=True, capture_output=True)
        v = roster.read_values((out / "config.txt").read_text(), ("residents",))
        self.assertEqual(v["residents"], "Arkady,Vera")

    def test_birth_check(self):
        repo = copy_repo(self.root / "r")
        roster.new_resident(repo, "bot04", "thief", "Rook")
        problems, notes = roster.birth_check(repo, "bot04")
        self.assertEqual(problems, [])
        doc = roster.load(repo)
        doc["residents"]["bot02"]["born"] = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
        roster.save(repo, doc)
        _, notes = roster.birth_check(repo, "bot04")
        self.assertTrue(any("не чаще одного жителя в неделю" in n for n in notes))
        problems, _ = roster.birth_check(repo, "bot09")
        self.assertTrue(problems)

    # ---------- диспетчер смен (ORG-044) ----------

    def test_shift_awake_first_and_limit(self):
        repo = copy_repo(self.root / "r")
        for bot, tpl, name in (("bot03", "merchant", "Bram"), ("bot04", "thief", "Rook")):
            roster.new_resident(repo, bot, tpl, name)
        tz = timezone(timedelta(hours=json.loads((ROOT / "brain/world/goals.json").read_text())["timezone_offset_hours"]))
        bots = ["bot01", "bot02", "bot03", "bot04"]
        # 03:30 по миру: Arkady (02:30) и Vera (23:30), Bram (22:30) спят; Rook (04:00) бодрствует
        night = datetime(2026, 10, 2, 3, 30, tzinfo=tz).timestamp()
        allowed, info = roster.shift(repo, bots, 4, night)
        self.assertEqual(allowed, ["bot04"])
        self.assertEqual(info["bot01"], "asleep")
        # 15:00: все бодрствуют, лимит 2 — первые по LAB_BOTS
        day = datetime(2026, 10, 2, 15, 0, tzinfo=tz).timestamp()
        allowed, info = roster.shift(repo, bots, 2, day)
        self.assertEqual(allowed, ["bot01", "bot02"])
        self.assertEqual(info["bot04"], "limit")
        # 23:45: Bram (22:30) и Vera (23:30) уже спят
        late = datetime(2026, 10, 2, 23, 45, tzinfo=tz).timestamp()
        allowed, _ = roster.shift(repo, bots, 4, late)
        self.assertEqual(allowed, ["bot01", "bot04"])


if __name__ == "__main__":
    unittest.main()
