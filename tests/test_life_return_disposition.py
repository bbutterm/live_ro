"""Explicit offline return disposition fixtures, not automatic/live recovery."""
import copy
import json
import pathlib
import tempfile
import unittest
from packages.resident_demo.life_cycle import LifeCycle
from packages.resident_demo import disposition


class ReturnDispositionTests(unittest.TestCase):
    def fixture(self, directory):
        life = LifeCycle(pathlib.Path(directory)/'state.json', 2000, 60)
        pending = dict(skill='travel_to', params=dict(map='prt_fild08', x=170, y=240, r=3),
            timeout=240, next_phase='hunt', reason='LIFE_RETURN', idem='return-key',
            expires_at=400, action_id='return-id', from_phase='return')
        life.state.update(phase='return', cycles=31, pending=pending,
            blocked='NATIVE_INTENT_EXPIRED', has_hunted=True,
            terminal_disposition=dict(action_id='old-hunt', at=50),
            history=[dict(action_id='old-hunt', code='NO_EVIDENCE')])
        life.save()
        action = dict(action_id='return-id', resident='resident_a', skill='travel_to',
            idem_key='return-key', params=json.dumps(pending['params']), created=160,
            deadline=400.01, updated=470, state='escalated', code='NO_EVIDENCE')
        return life, action

    def test_terminal_return_archives_original_without_credit_or_old_marker_reset(self):
        self.assertTrue(hasattr(disposition, 'park_unconfirmed_return'), 'Missing explicit return policy')
        with tempfile.TemporaryDirectory() as directory:
            life, action = self.fixture(directory)
            before = copy.deepcopy(life.state)
            result = disposition.park_unconfirmed_return(life, action, 500)
            loaded = LifeCycle(life.path, 2500, 60)
            self.assertEqual(result, 'PARKED_UNCONFIRMED_RETURN')
            self.assertEqual(loaded.state['cycles'], 31)
            self.assertEqual(loaded.state['terminal_disposition'], before['terminal_disposition'])
            self.assertEqual(loaded.state['history'][:-1], before['history'])
            self.assertEqual(loaded.state['history'][-1]['pending'], before['pending'])
            self.assertEqual(loaded.state['history'][-1]['action'], action)
            self.assertEqual(loaded.state['phase'], 'town')
            self.assertFalse(loaded.state['has_hunted'])
            self.assertIsNone(loaded.state['pending'])
            self.assertIsNone(loaded.state['blocked'])
            self.assertEqual(loaded.state['return_disposition']['action_id'], 'return-id')

    def test_unsafe_or_repeated_return_disposition_preserves_checkpoint_bytes(self):
        changes = [('action', {'action_id':'other'}), ('action', {'resident':'resident_b'}),
            ('action', {'skill':'buy_potions'}), ('action', {'idem_key':'other'}),
            ('action', {'state':'unknown'}), ('action', {'state':'confirmed'}),
            ('action', {'code':'ARRIVED'}), ('action', {'params':'{}'}),
            ('action', {'deadline':402}), ('action', {'updated':450}),
            ('action', {'created':True}), ('action', {'updated':float('nan')}),
            ('state', {'phase':'buy'}), ('state', {'cycles':True}),
            ('state', {'history':None}), ('state', {'has_hunted':1}),
            ('state', {'return_disposition':{}}), ('state', {'reaction':{}}),
            ('state', {'stuck_recovery':{}}), ('state', {'death_recovery':{}}),
            ('state', {'terminal_disposition':{'action_id':'old','at':True}}),
            ('pending', {'skill':'sell_loot'}), ('pending', {'reason':'LIFE_TOWN'}),
            ('pending', {'from_phase':'rest'}), ('pending', {'next_phase':'buy'}),
            ('pending', {'timeout':True}), ('pending', {'expires_at':float('inf')}),
            ('pending', {'params':dict(map='prt_fild08',x=170,y=240,r=4)}),
            ('pending', {'params':dict(map='prt_fild08',x=170,y=240,r=True)})]
        for target, change in changes:
            with self.subTest(target=target,change=change), tempfile.TemporaryDirectory() as directory:
                life, action = self.fixture(directory)
                {'action':action,'state':life.state,'pending':life.state['pending']}[target].update(change)
                life.save(); before=life.path.read_bytes()
                with self.assertRaisesRegex(RuntimeError,'RETURN_DISPOSITION_REJECTED'):
                    disposition.park_unconfirmed_return(life,action,500)
                self.assertEqual(life.path.read_bytes(),before)
        for now in (True,-1,float('nan'),float('inf'),469,2000):
            with self.subTest(now=now),tempfile.TemporaryDirectory() as directory:
                life,action=self.fixture(directory);before=life.path.read_bytes()
                with self.assertRaisesRegex(RuntimeError,'RETURN_DISPOSITION_REJECTED'):
                    disposition.park_unconfirmed_return(life,action,now)
                self.assertEqual(life.path.read_bytes(),before)
        with tempfile.TemporaryDirectory() as directory:
            life,action=self.fixture(directory)
            disposition.park_unconfirmed_return(life,action,500)
            life.state.update(pending=copy.deepcopy(life.state['history'][-1]['pending']),
                blocked='NATIVE_INTENT_EXPIRED',phase='return');life.save()
            loaded=LifeCycle(life.path,2500,60);before=life.path.read_bytes()
            with self.assertRaisesRegex(RuntimeError,'RETURN_DISPOSITION_REJECTED'):
                disposition.park_unconfirmed_return(loaded,action,600)
            self.assertEqual(life.path.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
