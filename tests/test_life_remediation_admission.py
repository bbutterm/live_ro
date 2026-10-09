"""Synthetic admission fixtures; not native recovery/endurance evidence."""
import copy
import tempfile
import unittest
from tests import test_life_terminal_disposition as fixtures
from packages.resident_demo import disposition

class RemediationAdmissionTests(unittest.TestCase):
    def fixture(self, directory):
        life, action = fixtures.TerminalDispositionTests().fixture(directory)
        action['action_id'] = life.state['pending']['action_id'] = '702b4620-95c4-4a4d-90ae-fbf7ef82082c'
        action['idem_key'] = life.state['pending']['idem'] = 'e0d36ad2-c687-4ffd-aa70-47f9405faf2e'
        life.state['terminal_disposition'] = dict(action_id='old-hunt', at=10)
        life.state['return_disposition'] = dict(action_id='old-return', at=20)
        life.save()
        evidence = dict(old_plugin='b105e3fdd3b7d76ce73bb58b7ccf798f42fb8e69311706dc02edcbedce0c7fe4',
            fixed_plugin='1ec429f869e2292d678bc33600accaac98c5b95d81040d87f919745d18af6c96',
            qa_status='PASS_TWO_AUTHORITATIVE_HUNTS',
            hunt_ids=['60dc65bf-62d5-45ab-993e-de828f228ca8','2e058263-3516-4de4-b233-07d0a5a0f7ec'],
            kill_ids=[1,2,3,4,5,6], offline=True, checkpoint_preserved=True)
        return life, action, evidence

    def test_corrected_native_source_admission_archives_failed_intent_preserving_consumption(self):
        self.assertTrue(hasattr(disposition, 'admit_corrected_native_hunt'), 'missing remediation-bound admission')
        with tempfile.TemporaryDirectory() as d:
            life, action, evidence = self.fixture(d); original=copy.deepcopy(life.state)
            disposition.admit_corrected_native_hunt(life, action, evidence, 300)
            self.assertEqual(life.state['terminal_disposition'], original['terminal_disposition'])
            self.assertEqual(life.state['return_disposition'], original['return_disposition'])
            self.assertEqual(life.state['cycles'], original['cycles'])
            self.assertEqual(life.state['history'][:-1], original['history'])
            self.assertEqual(life.state['history'][-1]['pending'], original['pending'])
            self.assertEqual(life.state['history'][-1]['action'], action)
            self.assertEqual(life.state['history'][-1]['evidence'], evidence)
            self.assertIsNone(life.state['pending']); self.assertIsNone(life.state['blocked'])
            self.assertFalse(life.state['has_hunted']); self.assertEqual(life.state['phase'], 'town')

    def test_missing_mismatched_or_repeated_remediation_rejected_unchanged(self):
        for change in ({'offline':False}, {'checkpoint_preserved':False},
                       {'fixed_plugin':'wrong'}, {'old_plugin':'wrong'},
                       {'hunt_ids':['same','same']}, {'kill_ids':[True,2]},
                       {'kill_ids':[1,1,2,3,4,5]}, {'qa_status':'UNKNOWN'}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as d:
                life, action, evidence=self.fixture(d); evidence.update(change)
                before=life.path.read_bytes(); memory=copy.deepcopy(life.state)
                with self.assertRaisesRegex(RuntimeError, 'REMEDIATION_ADMISSION_REJECTED'):
                    disposition.admit_corrected_native_hunt(life,action,evidence,300)
                self.assertEqual(before,life.path.read_bytes()); self.assertEqual(memory,life.state)
        with tempfile.TemporaryDirectory() as d:
            life,action,evidence=self.fixture(d)
            disposition.admit_corrected_native_hunt(life,action,evidence,300)
            from packages.resident_demo.life_cycle import LifeCycle
            restarted=LifeCycle(life.path,3000,60); before=life.path.read_bytes()
            with self.assertRaisesRegex(RuntimeError,'REMEDIATION_ADMISSION_REJECTED'):
                disposition.admit_corrected_native_hunt(restarted,action,evidence,400)
            self.assertEqual(before,life.path.read_bytes())
