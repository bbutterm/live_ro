import json
import pathlib
import tempfile
import unittest

class ReplacementTests(unittest.TestCase):
    def test_launch_plan_preserves_absolute_deadline_and_canonical_state(self):
        from packages.resident_demo import supervisor
        source = pathlib.Path(__file__).resolve().parents[1] / 'packages/resident_demo/replacement.py'
        self.assertTrue(source.exists(), 'safe replacement launcher missing')
        from packages.resident_demo.replacement import build_commands
        from unittest.mock import patch
        with patch.object(supervisor, 'native_commands', return_value=[{'name':'body','argv':[], 'cwd':'/tmp'}]):
            commands = build_commands(2000, 1000)
        life = commands[-1]['argv']
        self.assertEqual(float(life[life.index('--deadline')+1]), 2000)
        self.assertEqual(life[life.index('--state')+1], '/root/ragnarok/run/autonomy-20261008/life-state.json')
        self.assertEqual(life[life.index('--hunt-seconds')+1], '60')
        self.assertEqual(commands[-1]['name'], 'resident_a-life')

    def test_admission_fails_closed_for_revocation_estop_deadline_and_backoff(self):
        from packages.resident_demo import replacement as mod
        self.assertTrue(hasattr(mod, 'admit'), 'replacement admission missing')
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            auth = root/'register.json'
            auth.write_text(json.dumps({'authorized': True, 'max_native_lease_seconds': 28800}))
            stops = [root/'ESTOP']
            self.assertIsNone(mod.admit(auth, stops, 2000, 1000, None))
            for deadline in [999, float('nan'), float('inf'), 30000]:
                with self.assertRaisesRegex(RuntimeError, 'DEADLINE'):
                    mod.admit(auth, stops, deadline, 1000, None)
            with self.assertRaisesRegex(RuntimeError, 'BACKOFF'):
                mod.admit(auth, stops, 2000, 1000, 990)
            stops[0].touch()
            with self.assertRaisesRegex(RuntimeError, 'ESTOP'):
                mod.admit(auth, stops, 2000, 1000, None)
            stops[0].unlink()
            auth.write_text(json.dumps({'authorized': False}))
            with self.assertRaisesRegex(RuntimeError, 'AUTHORIZATION'):
                mod.admit(auth, stops, 2000, 1000, None)

    def test_revoked_guard_stops_owned_process_group(self):
        import json, sys
        from packages.resident_demo import supervisor as mod
        import inspect
        self.assertIn('guard', inspect.signature(mod.supervise).parameters, 'runtime revocation guard missing')
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d)
            marker=root/'revoked'
            code="import pathlib,time; pathlib.Path(%r).touch(); time.sleep(30)" % str(marker)
            def guard():
                if marker.exists(): raise RuntimeError('AUTHORIZATION')
            with self.assertRaisesRegex(RuntimeError, 'AUTHORIZATION'):
                mod.supervise([dict(name='probe',cwd=d,argv=[sys.executable,'-c',code])],d,
                    __import__('time').time()+5,authorized_native=True,guard=guard)
            identity=json.loads((root/'identity.json').read_text())
            self.assertEqual(identity['reason'], 'CONTROLLER_FAILURE')
            self.assertIsNotNone(identity['children'][0]['exit'])

    def test_replacement_rejects_unresolved_checkpoint_without_mutation(self):
        from packages.resident_demo import replacement as mod
        self.assertTrue(hasattr(mod, 'check_state'), 'checkpoint preflight missing')
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'life.json'
            p.write_text(json.dumps(dict(pending={'action_id':'old'},blocked=None)))
            before=p.read_bytes()
            with self.assertRaisesRegex(RuntimeError,'UNRESOLVED_STATE'): mod.check_state(p)
            self.assertEqual(p.read_bytes(),before)
            p.write_text(json.dumps(dict(pending=None,blocked=None,version=1,resident='resident_a',hunt_seconds=60,cycles=17,phase='buy')))
            self.assertEqual(mod.check_state(p)['cycles'],17)

    def test_launch_records_provenance_before_supervision_and_preserves_state(self):
        from packages.resident_demo import replacement as mod
        self.assertTrue(hasattr(mod, 'launch'), 'supervised launch missing')
        from unittest.mock import patch
        import time, sqlite3
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d)
            (root/'private').mkdir()
            old=root/'autonomy-20261008';old.mkdir()
            (old/'identity.json').write_text(json.dumps(dict(stopped=time.time()-100,children=[])))
            state=old/'life-state.json'
            state.write_text(json.dumps(dict(pending=None,blocked=None,version=1,resident='resident_a',hunt_seconds=60,cycles=17,phase='buy')))
            (root/'CONTINUOUS_REGISTER.json').write_text(json.dumps(dict(authorized=True,max_native_lease_seconds=28800)))
            for actor in ['resident_a','resident_b','resident_c']:
                p=root/'Demo'/actor;p.mkdir(parents=True)
                db=sqlite3.connect(p/'journal.sqlite');db.execute('CREATE TABLE actions (state TEXT)');db.close()
            before=state.read_bytes()
            out=root/'replacement';deadline=time.time()+1000
            def supervised(commands, directory, end, **kw):
                record=json.loads((out/'launch.json').read_text())
                self.assertEqual(record['deadline'],deadline)
                self.assertEqual(record['state_sha256'],__import__('hashlib').sha256(before).hexdigest())
                self.assertTrue(record['source_sha256'])
                kw['guard']()
                return dict(reason='TEST_ONLY')
            offline = '150000\tTester\t0\n150001\tResidentA\t0\n150002\tMira\t0\n150003\tBorin\t0'
            with patch('tools.events.query', return_value=offline), patch.object(mod,'build_commands',return_value=[]), patch.object(mod.supervisor,'supervise',side_effect=supervised):
                mod.launch(out,deadline,root=root)
            self.assertEqual(state.read_bytes(),before)
            self.assertEqual(json.loads((root/'NATIVE_CURRENT.json').read_text())['directory'],str(out))

if __name__ == '__main__': unittest.main()
