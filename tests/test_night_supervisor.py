import importlib.util
import pathlib
import tempfile
import time
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / 'packages/resident_demo/supervisor.py'

class SupervisorTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self.clock_window = patch('packages.resident_demo.supervisor.DEADLINE', time.time() + 30)
        self.clock_window.start()
        self.addCleanup(self.clock_window.stop)
    def test_expired_deadline_never_spawns(self):
        self.assertTrue(SOURCE.exists(), 'Night supervisor missing')
        mod = __import__('packages.resident_demo.supervisor', fromlist=['supervise'])
        with tempfile.TemporaryDirectory(dir='/root/ragnarok/run', prefix='supervisor-test-') as d:
            marker = pathlib.Path(d) / 'spawned'
            commands = [{'name': 'probe', 'cwd': d, 'argv': ['/usr/bin/touch', str(marker)]}]
            with self.assertRaisesRegex(RuntimeError, 'DEADLINE'):
                mod.supervise(commands, d, time.time() - 1)
            self.assertFalse(marker.exists())

    def test_native_child_is_registered_and_stopped_at_deadline(self):
        import json
        import os
        import sys
        mod = __import__('packages.resident_demo.supervisor', fromlist=['supervise'])
        with tempfile.TemporaryDirectory(dir='/root/ragnarok/run', prefix='supervisor-test-') as d:
            commands = [{'name': 'probe', 'cwd': d, 'argv': [sys.executable, '-c', 'import time; time.sleep(30)']}]
            mod.supervise(commands, d, time.time() + 0.3)
            state = json.loads((pathlib.Path(d) / 'identity.json').read_text())
            self.assertEqual(state['reason'], 'DEADLINE')
            self.assertEqual(len(state['children']), 1)
            self.assertGreater(state['children'][0]['pid'], 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(state['children'][0]['pid'], 0)
            self.assertEqual((pathlib.Path(d) / 'identity.json').stat().st_mode & 0o077, 0)

    def test_exited_parent_does_not_leave_running_descendant(self):
        import json
        import os
        import sys
        mod = __import__('packages.resident_demo.supervisor', fromlist=['supervise'])
        with tempfile.TemporaryDirectory(dir='/root/ragnarok/run', prefix='supervisor-test-') as d:
            pidfile = pathlib.Path(d) / 'descendant'
            code = "import subprocess,time,pathlib; p=subprocess.Popen(['sleep','30']); pathlib.Path(%r).write_text(str(p.pid)); time.sleep(.2)" % str(pidfile)
            commands = [{'name': 'probe', 'cwd': d, 'argv': [sys.executable, '-c', code]}]
            try:
                mod.supervise(commands, d, time.time() + 2)
                pid = int(pidfile.read_text())
                stat = pathlib.Path('/proc/%s/stat' % pid)
                self.assertTrue(not stat.exists() or stat.read_text().split()[2] == 'Z', 'Running orphan remains')
            finally:
                if pidfile.exists():
                    try: os.kill(int(pidfile.read_text()), 9)
                    except ProcessLookupError: pass

    def test_new_native_authorization_does_not_reopen_expired_night(self):
        from unittest.mock import patch
        import json,sys
        from packages.resident_demo import supervisor as mod
        with tempfile.TemporaryDirectory(dir='/root/ragnarok/run') as d, patch.object(mod,'DEADLINE',time.time()-60):
            commands=[{'name':'probe','cwd':d,'argv':[sys.executable,'-c','import time;time.sleep(30)']}]
            with self.assertRaisesRegex(RuntimeError,'DEADLINE'):
                mod.supervise(commands,d,time.time()+.3)
            mod.supervise(commands,d,time.time()+.3,authorized_native=True)
            self.assertEqual(json.loads((pathlib.Path(d)/'identity.json').read_text())['reason'],'DEADLINE')

    def test_native_factory_accepts_new_finite_native_window(self):
        import inspect
        from packages.resident_demo import supervisor as mod
        self.assertIn('deadline',inspect.signature(mod.native_commands).parameters,'Native body factory is tied to expired night')
        deadline=time.time()+900
        from unittest.mock import patch
        # Isolate socket admission from the live service; production check stays intact.
        with patch('pathlib.Path.exists',return_value=False):
            commands=mod.native_commands(deadline=deadline)
        gateways=[c for c in commands if '--duration' in c['argv']]
        self.assertEqual(len(gateways),3)
        self.assertTrue(all(850<float(c['argv'][-1])<=900 for c in gateways))

if __name__ == '__main__': unittest.main()
