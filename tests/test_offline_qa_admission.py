import json
import pathlib
import tempfile
import unittest


class OfflineQAAdmissionTests(unittest.TestCase):
    def test_healthy_runtime_denied_before_any_scenario_callback(self):
        from packages.resident_demo import replacement
        self.assertTrue(hasattr(replacement, 'require_offline'),
                        'authoritative offline launch gate missing')
        calls = []
        def query(_defaults, sql):
            calls.append(sql)
            return '150000\tTester\t0\n150001\tResidentA\t1\n150002\tMira\t1\n150003\tBorin\t1\n'
        with self.assertRaisesRegex(RuntimeError, 'AUTHORITATIVE_ONLINE'):
            replacement.require_offline(query)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith('SELECT '))


    def test_missing_duplicate_wrong_identity_and_malformed_rows_fail_closed(self):
        from packages.resident_demo.replacement import require_offline
        valid = '150000\tTester\t0\n150001\tResidentA\t0\n150002\tMira\t0\n150003\tBorin\t0\n'
        self.assertEqual(len(require_offline(lambda *_: valid)), 4)
        for rows in ('', valid.replace('150003\tBorin\t0\n', ''),
                     valid + '150001\tResidentA\t0\n',
                     valid.replace('ResidentA', 'Other'),
                     valid.replace('150003', '150004'),
                     valid.replace('150003\tBorin\t0', '0')):
            with self.subTest(rows=rows):
                with self.assertRaisesRegex(RuntimeError, 'AUTHORITATIVE_IDENTITY'):
                    require_offline(lambda *_: rows)


    def test_launcher_rechecks_offline_under_both_canonical_locks_before_commands(self):
        import fcntl
        import sqlite3
        import time
        from unittest.mock import patch
        from packages.resident_demo import replacement as mod
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root/'private').mkdir()
            old = root/'autonomy-20261008'
            old.mkdir()
            (old/'identity.json').write_text(json.dumps(dict(stopped=time.time()-100, children=[])))
            state = old/'life-state.json'
            state.write_text(json.dumps(dict(pending=None, blocked=None, version=1,
                resident='resident_a', hunt_seconds=60, cycles=39, phase='town')))
            (root/'CONTINUOUS_REGISTER.json').write_text(json.dumps(dict(authorized=True, max_native_lease_seconds=28800)))
            for actor in ('resident_a', 'resident_b', 'resident_c'):
                p = root/'Demo'/actor
                p.mkdir(parents=True)
                with sqlite3.connect(p/'journal.sqlite') as db:
                    db.execute('CREATE TABLE actions (state TEXT)')
            before = state.read_bytes()
            reached = []
            def authoritative_query(*_):
                for path in (old/'supervisor.lock', root/'private/night-budget.json.lock'):
                    with path.open('r+') as lock:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                reached.append(True)
                return '150000\tTester\t0\n150001\tResidentA\t1\n150002\tMira\t0\n150003\tBorin\t0'
            with patch('tools.events.query', side_effect=authoritative_query), \
                    patch.object(mod, 'build_commands', return_value=[]) as commands, \
                    patch.object(mod.supervisor, 'supervise') as supervise:
                with self.assertRaisesRegex(RuntimeError, 'AUTHORITATIVE_ONLINE'):
                    mod.launch(root/'new-qa', time.time()+600, root=root)
                commands.assert_not_called()
                supervise.assert_not_called()
            self.assertEqual(reached, [True])
            self.assertEqual(state.read_bytes(), before)
            self.assertFalse((root/'new-qa').exists())
            self.assertFalse((root/'NATIVE_CURRENT.json').exists())


if __name__ == '__main__':
    unittest.main()
