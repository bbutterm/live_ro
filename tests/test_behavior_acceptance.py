import unittest
from packages.resident_behavior.acceptance import replay_gate
from packages.resident_behavior.config import load_behavior

class BehaviorAcceptanceTests(unittest.TestCase):
    def test_replay_gate_is_explicit_scenario_not_claimed_live_proof(self):
        report=replay_gate(load_behavior('config/behavior.yaml'),hours=1)
        self.assertEqual(report['evidence_kind'],'generated_replay_scenario')
        self.assertTrue(report['deterministic'])
        self.assertEqual(report['llm_calls'],0)
        self.assertEqual(report['live_motor_commands'],0)
        self.assertEqual(set(report['decisions']),{'resident_a','mira','borin'})
