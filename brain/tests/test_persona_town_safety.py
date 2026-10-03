"""Город отдыха персоны должен совпадать с разрешённым городом safety."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind


class PersonaTownSafetyTest(unittest.TestCase):
    def test_persona_town_overrides_world_without_widening_allowlist(self):
        brain = Path(__file__).resolve().parents[1]
        world = json.loads((brain / 'world/goals.json').read_text())
        persona = json.loads((brain / 'personas/bot01.json').read_text())
        persona['routine'] = {'town': {'map': 'payon', 'x': 161, 'y': 58}}
        world['routine']['town'] = {'map': 'prontera', 'x': 150, 'y': 150}
        async def send(action):
            pass
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'BRAIN_DISABLE': 'home'}):
            root = Path(directory)
            memory = Memory(root / 'memory.sqlite')
            mind = Mind(Settings.from_env({}), persona, memory, send, root / 'decisions.jsonl', RuleGate(), world=world)
            self.assertIn('payon', mind.point_maps)
            self.assertIn('payon', mind.safety.point_maps)
            self.assertNotIn('unknown_map', mind.safety.point_maps)
