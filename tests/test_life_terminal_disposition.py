"""Offline terminal disposition fixtures; not live recovery acceptance."""
import copy
import json
import pathlib
import tempfile
import unittest
from packages.resident_demo.life_cycle import LifeCycle


class TerminalDispositionTests(unittest.TestCase):
    def fixture(self, directory):
        life = LifeCycle(pathlib.Path(directory)/'state.json', 2000, 60)
        pending = dict(skill='hunt', params=dict(map='prt_fild08', duration=60, min_kills=1),
            timeout=100, next_phase='town', reason='HUNT_INTERVAL', idem='fixture-key',
            expires_at=200, action_id='fixture-id', from_phase='hunt')
        life.state.update(phase='hunt', cycles=30, pending=pending,
            blocked='NATIVE_INTENT_EXPIRED', has_hunted=True,
            history=[dict(action_id='earlier', code='HUNTED')])
        life.save()
        action = dict(action_id='fixture-id', resident='resident_a', skill='hunt',
            idem_key='fixture-key', params=json.dumps(pending['params']),
            created=100, deadline=200.01, updated=270, state='escalated', code='NO_EVIDENCE')
        return life, action

    def test_terminal_unconfirmed_hunt_is_archived_without_success_or_replay(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('packages.resident_demo.disposition'),
            'Missing explicit terminal disposition policy')
        from packages.resident_demo import disposition
        with tempfile.TemporaryDirectory() as directory:
            life, action = self.fixture(directory)
            original = copy.deepcopy(life.state)
            result = disposition.park_unconfirmed_hunt(life, action, 300)
            restarted = LifeCycle(life.path, 3000, 60)
            self.assertEqual(result, 'PARKED_UNCONFIRMED_HUNT')
            self.assertEqual(restarted.state['cycles'], 30)
            self.assertFalse(restarted.state['has_hunted'])
            self.assertEqual(restarted.state['phase'], 'town')
            self.assertIsNone(restarted.state['pending'])
            self.assertIsNone(restarted.state['blocked'])
            self.assertEqual(restarted.state['history'][:-1], original['history'])
            entry = restarted.state['history'][-1]
            self.assertEqual(entry['pending'], original['pending'])
            self.assertEqual(entry['action'], action)
            self.assertEqual(entry['code'], 'NO_EVIDENCE')
            self.assertEqual(entry['state'], 'escalated')
            self.assertEqual(restarted.state['terminal_disposition']['action_id'], action['action_id'])
            before = life.path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, 'TERMINAL_DISPOSITION_REJECTED'):
                disposition.park_unconfirmed_hunt(restarted, action, 400)
            self.assertEqual(before, life.path.read_bytes())

    def test_real_journal_disposition_selects_new_town_intent_not_old_hunt(self):
        from packages.resident_demo import disposition
        from packages.body_gateway.journal import Journal
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            life, action = self.fixture(directory)
            journal = Journal(str(pathlib.Path(directory)/'journal.sqlite'))
            try:
                with patch('packages.body_gateway.journal.time.time', return_value=100):
                    actual = journal.plan('resident_a','hunt',life.state['pending']['params'],
                        'fixture-key',200.01,'fixture-epoch',0)
                    journal.transition(actual['action_id'],'dispatched')
                    journal.transition(actual['action_id'],'unknown','VERIFY_TIMEOUT')
                with patch('packages.body_gateway.journal.time.time', return_value=270):
                    journal.transition(actual['action_id'],'escalated','NO_EVIDENCE')
                action=journal.action(actual['action_id'])
                life.state['pending']['action_id']=actual['action_id'];life.save()
                trace=journal.trace(actual['action_id'])
                disposition.park_unconfirmed_hunt(life,action,300)
                restarted=LifeCycle(life.path,3000,60); calls=[]
                def rpc(message):
                    calls.append(message)
                    if message['op']=='status':
                        return dict(age=.1, actions=[], telemetry=dict(map='prt_fild08',
                            hp=795,hp_max=795,dead=False,inventory={'501':20},zeny=600))
                    self.assertEqual(message['op'],'run')
                    self.assertEqual(message['skill'],'travel_to')
                    self.assertEqual(message['params']['map'],'prt_in')
                    self.assertNotEqual(message['idem'],'fixture-key')
                    return dict(action_id='new-town-id',state='running')
                restarted.tick(rpc,301)
                self.assertEqual(restarted.state['cycles'],30)
                self.assertEqual(restarted.state['pending']['action_id'],'new-town-id')
                self.assertEqual(journal.trace(actual['action_id']),trace)
            finally:
                journal.close()

    def test_unsafe_identity_economic_or_malformed_disposition_preserves_bytes(self):
        from packages.resident_demo import disposition
        changes = [
            ('action', {'action_id':'other'}), ('action', {'resident':'resident_b'}),
            ('action', {'skill':'buy_potions'}), ('action', {'idem_key':'other'}),
            ('action', {'state':'unknown'}), ('action', {'state':'confirmed'}),
            ('action', {'code':'HUNTED'}), ('action', {'params':'{}'}),
            ('action', {'params':'broken'}), ('action', {'deadline':202}),
            ('action', {'deadline':float('inf')}), ('action', {'created':True}),
            ('action', {'updated':float('nan')}), ('action', {'updated':99}),
            ('state', {'phase':'buy'}), ('state', {'cycles':True}),
            ('state', {'history':None}), ('state', {'has_hunted':1}),
            ('state', {'reaction':{}}), ('pending', {'skill':'sell_loot'}),
            ('pending', {'reason':'OTHER'}), ('pending', {'next_phase':'hunt'}),
            ('pending', {'expires_at':True}), ('pending', {'timeout':True}),
            ('pending', {'idem':''}), ('pending', {'from_phase':'buy'}),
            ('pending', {'params':{'map':'prt_fild08','duration':True,'min_kills':1}}),
        ]
        for target, change in changes:
            with self.subTest(target=target, change=change), tempfile.TemporaryDirectory() as directory:
                life, action = self.fixture(directory)
                {'action':action,'state':life.state,'pending':life.state['pending']}[target].update(change)
                life.save(); before=life.path.read_bytes(); memory=copy.deepcopy(life.state)
                with self.assertRaisesRegex(RuntimeError, 'TERMINAL_DISPOSITION_REJECTED'):
                    disposition.park_unconfirmed_hunt(life, action, 300)
                self.assertEqual(before, life.path.read_bytes())
                self.assertEqual(memory, life.state)
        for now in (True, float('nan'), float('inf'), -1, 199, 260):
            with self.subTest(now=now), tempfile.TemporaryDirectory() as directory:
                life, action = self.fixture(directory); before=life.path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'TERMINAL_DISPOSITION_REJECTED'):
                    disposition.park_unconfirmed_hunt(life, action, now)
                self.assertEqual(before, life.path.read_bytes())


if __name__ == '__main__':
    unittest.main()
