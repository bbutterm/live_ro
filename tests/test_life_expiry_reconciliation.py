"""Deterministic expiry-boundary fixtures, not native acceptance evidence."""
import pathlib
import tempfile
import unittest
from packages.resident_demo.life_cycle import LifeCycle


class ExpiryReconciliationTests(unittest.TestCase):
    def test_confirmed_original_action_is_read_before_expiry_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'state.json'
            life = LifeCycle(path, 1000, 60)
            life.state['phase'] = 'hunt'
            life.state['pending'] = dict(skill='hunt', params=dict(map='prt_fild08', duration=60, min_kills=1),
                timeout=100, next_phase='town', reason='HUNT_INTERVAL', idem='fixture-idem',
                expires_at=200, action_id='fixture-original', from_phase='hunt')
            life.save()
            restarted = LifeCycle(path, 2000, 60)
            calls = []
            def rpc(message):
                calls.append(message['op'])
                if message['op'] == 'status':
                    return dict(age=.1, actions=[], telemetry=dict(map='prt_fild08', hp=795,
                        hp_max=795, dead=False, inventory={'501':20}, zeny=628))
                if message['op'] == 'action':
                    self.assertEqual(message['action_id'], 'fixture-original')
                    return dict(action_id='fixture-original', resident='resident_a', skill='hunt',
                        idem_key='fixture-idem', state='confirmed', code='HUNTED', created=100, updated=199)
                self.fail('Expiry reconciliation dispatched side effect: '+message['op'])
            result = restarted.tick(rpc, 201)
            self.assertEqual(calls, ['status', 'action'])
            self.assertEqual(result['action_id'], 'fixture-original')
            self.assertIsNone(restarted.state['pending'])
            self.assertIsNone(restarted.state['blocked'])
            self.assertEqual(restarted.state['phase'], 'town')
            self.assertEqual(restarted.state['cycles'], 0)
            self.assertTrue(restarted.state['has_hunted'])


    def test_malformed_pending_expiry_is_rejected_before_rpc_without_repair(self):
        for expiry in (float('nan'), float('inf'), -1, True, None, '200'):
            with self.subTest(expiry=expiry), tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / 'state.json'
                life = LifeCycle(path, 1000, 60)
                life.state['pending'] = dict(skill='hunt', params={}, timeout=100,
                    next_phase='town', reason='HUNT_INTERVAL', idem='fixture-idem',
                    expires_at=expiry, action_id='fixture-original', from_phase='hunt')
                life.save()
                before = path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'LIFE_PENDING_EXPIRY'):
                    LifeCycle(path, 2000, 60)
                self.assertEqual(before, path.read_bytes())


    def test_expired_unconfirmed_or_mismatched_action_preserves_original_intent(self):
        valid = dict(action_id='fixture-original', resident='resident_a', skill='hunt',
            idem_key='fixture-idem', state='confirmed', code='HUNTED', created=100, updated=199)
        cases = [dict(valid, state=state) for state in ('unknown', 'verifying', 'failed', 'escalated')]
        cases += [dict(valid, **change) for change in (
            {'action_id':'wrong'}, {'resident':'resident_b'}, {'skill':'buy_potions'},
            {'idem_key':'wrong'}, {'updated':201}, {'updated':float('nan')},
            {'updated':True}, {'created':-1}, {'created':200})]
        for action in cases:
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / 'state.json'
                life = LifeCycle(path, 1000, 60)
                pending = dict(skill='hunt', params=dict(map='prt_fild08', duration=60, min_kills=1),
                    timeout=100, next_phase='town', reason='HUNT_INTERVAL', idem='fixture-idem',
                    expires_at=200, action_id='fixture-original', from_phase='hunt')
                life.state['pending'] = pending.copy()
                life.save()
                calls = []
                def rpc(message):
                    calls.append(message['op'])
                    if message['op'] == 'status':
                        return dict(age=.1, actions=[], telemetry={'dead':False})
                    self.assertEqual(message, {'op':'action', 'action_id':'fixture-original'})
                    return action
                with self.assertRaisesRegex(RuntimeError, 'NATIVE_INTENT_EXPIRED'):
                    life.tick(rpc, 201)
                self.assertEqual(calls, ['status', 'action'])
                self.assertEqual(life.state['pending'], pending)
                self.assertEqual(life.state['cycles'], 0)
                self.assertEqual(life.state['history'], [])
                restart = LifeCycle(path, 2000, 60)
                before = path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'NATIVE_INTENT_EXPIRED'):
                    restart.tick(lambda m: self.fail('Blocked state issued RPC'), 202)
                self.assertEqual(before, path.read_bytes())

    def test_real_journal_confirmation_reconciles_without_native_dispatch(self):
        from packages.body_gateway.journal import Journal
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'state.json'
            journal = Journal(str(pathlib.Path(directory) / 'journal.sqlite'))
            try:
                with patch('packages.body_gateway.journal.time.time', return_value=100):
                    action = journal.plan('resident_a', 'hunt',
                        dict(map='prt_fild08', duration=60, min_kills=1), 'fixture-idem', 200, 'fixture-epoch', 0)
                    journal.transition(action['action_id'], 'dispatched')
                    journal.transition(action['action_id'], 'verifying', 'HUNTED')
                with patch('packages.body_gateway.journal.time.time', return_value=199):
                    journal.transition(action['action_id'], 'confirmed', 'HUNTED', {'server_kills':[{'id':1}]})
                life = LifeCycle(path, 1000, 60)
                life.state['pending'] = dict(skill='hunt', params={}, timeout=100,
                    next_phase='town', reason='HUNT_INTERVAL', idem='fixture-idem', expires_at=200,
                    action_id=action['action_id'], from_phase='hunt')
                life.save()
                before = journal.trace(action['action_id'])
                def rpc(message):
                    if message['op']=='status':return dict(age=.1, actions=[], telemetry={'dead':False})
                    self.assertEqual(message, {'op':'action', 'action_id':action['action_id']})
                    return journal.action(message['action_id'])
                result = life.tick(rpc, 201)
                self.assertEqual(result['action_id'], action['action_id'])
                self.assertEqual(journal.trace(action['action_id']), before)
                self.assertIsNone(life.state['pending'])
                self.assertEqual(life.state['cycles'], 0)
            finally:
                journal.close()


if __name__ == '__main__':
    unittest.main()
