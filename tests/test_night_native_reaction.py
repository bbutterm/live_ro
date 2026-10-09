import pathlib
import tempfile
import unittest
from packages.resident_demo.state import ControllerState

class NativeReactionTests(unittest.TestCase):
    def test_unsafe_or_late_recovery_is_not_admitted(self):
        import copy
        status = {'age': .1, 'actions': [], 'telemetry': {'dead': False, 'map': 'prt_fild08', 'hp': 795, 'hp_max': 795}}
        cases = [(dict(status, age=4), 1000), (dict(status, actions=['active']), 1000), (status, 214)]
        for field, value in (('dead', True), ('map', 'prontera'), ('hp', 1)):
            changed = copy.deepcopy(status);changed['telemetry'][field] = value;cases.append((changed, 1000))
        for status, deadline in cases:
            with self.subTest(status=status, deadline=deadline), tempfile.TemporaryDirectory() as d:
                mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
                s = ControllerState(pathlib.Path(d)/'state.json', mem, 63)
                s.actors['resident_a']['pending'] = {'action_id': 'x', 'skill': 'travel_to', 'params': {'map': 'absent'}, 'expires_at': 200}
                self.assertFalse(s.finish_native('resident_a', {'action_id': 'x', 'skill': 'travel_to', 'state': 'failed', 'code': 'NO_ROUTE'}, status, 100, deadline))
                self.assertEqual(s.actors['resident_a']['steps'], [])


    def test_all_gateway_terminal_failures_clear_without_recovery(self):
        from packages.body_gateway.journal import TERMINAL
        with tempfile.TemporaryDirectory() as d:
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
            s = ControllerState(pathlib.Path(d)/'state.json', mem, 63)
            for state in TERMINAL - {'confirmed'}:
                s.actors['resident_a']['pending'] = {'action_id': state, 'skill': 'say', 'params': {}, 'expires_at': 200}
                s.actors['resident_a']['steps'] = [('say', {}, 30)]
                self.assertFalse(s.finish_native('resident_a', {'action_id': state, 'skill': 'say', 'state': state, 'code': 'FAIL'}, {}, 100, 1000))
                self.assertEqual(s.actors['resident_a']['steps'], [])


    def test_corrupt_reaction_accounting_is_rejected_unchanged(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
            s = ControllerState(pathlib.Path(d)/'state.json', mem, 63)
            for reaction in ({}, {'action_id': 'x', 'failed_map': 'absent', 'blocked_until': float('nan')}, {'action_id': 'x', 'failed_map': 'absent', 'blocked_until': True}):
                s.actors['resident_a']['reaction'] = reaction
                s.save()
                before = s.path.read_bytes()
                with self.assertRaisesRegex(ValueError, 'CONTROLLER_STATE_SCHEMA'):
                    ControllerState(s.path, mem, 63)
                self.assertEqual(before, s.path.read_bytes())


    def test_quarantined_destination_is_not_dispatched(self):
        with tempfile.TemporaryDirectory() as d:
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
            s = ControllerState(pathlib.Path(d)/'state.json', mem, 63)
            import time
            s.actors['resident_a']['reaction'] = {'action_id': 'failed-1', 'failed_map': 'absent', 'blocked_until': time.time()+7200}
            s.actors['resident_a']['steps'] = [('travel_to', {'map': 'absent'}, 100)]
            s.save()
            sent = []
            with self.assertRaisesRegex(ValueError, 'CONTROLLER_MAP_QUARANTINED'):
                s.dispatch('resident_a', lambda msg: (sent.append(msg) or {'action_id': 'unexpected'}))
            self.assertEqual(sent, [])
            self.assertEqual(len(s.actors['resident_a']['steps']), 1)

    def test_second_failure_after_restart_cannot_fund_another_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
            s = ControllerState(pathlib.Path(d)/'state.json', mem, 63)
            status = {'age': .1, 'actions': [], 'telemetry': {'dead': False, 'map': 'prt_fild08', 'hp': 795, 'hp_max': 795}}
            for action in ('first', 'second'):
                s = ControllerState(s.path, mem, 63)
                s.actors['resident_a']['pending'] = {'action_id': action, 'skill': 'travel_to', 'params': {'map': 'absent'}, 'expires_at': 200}
                done = {'action_id': action, 'skill': 'travel_to', 'state': 'failed', 'code': 'NO_ROUTE'}
                selected = s.finish_native('resident_a', done, status, 100, 1000)
                self.assertEqual(selected, action == 'first')
            self.assertEqual(s.actors['resident_a']['steps'], [])

    def test_no_route_selects_bounded_alternative_activity(self):
        self.assertTrue(hasattr(ControllerState, 'finish_native'), 'Production failure reaction missing')
        with tempfile.TemporaryDirectory() as d:
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
            s = ControllerState(pathlib.Path(d)/'state.json', mem, 63)
            s.actors['resident_a']['pending'] = {'action_id': 'failed-1', 'skill': 'travel_to', 'params': {'map': 'absent'}, 'expires_at': 200}
            s.actors['resident_a']['steps'] = [('say', {'text': 'unexecuted'}, 30)]
            status = {'age': .1, 'actions': [], 'telemetry': {'dead': False, 'map': 'prt_fild08', 'hp': 795, 'hp_max': 795}}
            done = {'action_id': 'failed-1', 'skill': 'travel_to', 'state': 'failed', 'code': 'NO_ROUTE'}
            self.assertTrue(s.finish_native('resident_a', done, status, 100, 1000))
            self.assertEqual(s.actors['resident_a']['steps'], [('hunt', {'map': 'prt_fild08', 'duration': 60, 'min_kills': 1}, 100)])
            restart = ControllerState(s.path, mem, 63)
            self.assertEqual(restart.actors['resident_a']['reaction']['blocked_until'], 7300)
            self.assertEqual(restart.actors['resident_a']['reaction']['failed_map'], 'absent')
            self.assertIsNone(restart.actors['resident_a']['pending'])
            self.assertEqual(restart.actors['resident_a']['mem']['outcomes'][-1]['code'], 'NO_ROUTE')
