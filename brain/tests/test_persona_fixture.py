"""Тестовая география не наследует deployment и не меняет боевые persona."""
import json
import tempfile
import unittest
from pathlib import Path
from tests.persona_fixture import BRAIN_DIR, PRONTERA_HUNT_MAPS, prontera_persona, write_prontera_personas


class PersonaFixtureTest(unittest.TestCase):
    def test_scenario_geography_is_explicit_and_returns_fresh_objects(self):
        for bot in ("bot01", "bot02"):
            with self.subTest(bot=bot):
                path = BRAIN_DIR / "personas" / f"{bot}.json"
                before = path.read_bytes()
                actual = json.loads(before)
                first = prontera_persona(bot)
                self.assertEqual(first["hunt_maps"], list(PRONTERA_HUNT_MAPS))
                self.assertNotIn("town", first["routine"])
                self.assertEqual(first["traits"], actual["traits"])
                self.assertEqual(first["phrases"], actual["phrases"])
                first["hunt_maps"].clear()
                first["traits"]["bravery"] = -1
                self.assertEqual(prontera_persona(bot)["hunt_maps"], list(PRONTERA_HUNT_MAPS))
                self.assertEqual(prontera_persona(bot)["traits"], actual["traits"])
                self.assertEqual(path.read_bytes(), before)

    def test_subprocess_fixture_contains_both_peers(self):
        with tempfile.TemporaryDirectory() as root:
            path = write_prontera_personas(root)
            self.assertEqual(path.parent, Path(root) / "personas")
            for bot, name in (("bot01", "Arkady"), ("bot02", "Vera")):
                data = json.loads((path.parent / f"{bot}.json").read_text())
                self.assertEqual(data["name"], name)
                self.assertEqual(data["hunt_maps"], list(PRONTERA_HUNT_MAPS))
