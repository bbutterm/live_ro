import ast
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class SourceSnapshot(unittest.TestCase):
    def test_python_syntax_without_running_provisioning(self):
        files = list((ROOT / 'tools').glob('*.py')) + list((ROOT / 'checks').glob('*.py'))
        self.assertTrue(files)
        for f in files:
            ast.parse(f.read_text(), filename=str(f))

    def test_upstreams_pinned(self):
        expected = {'vendor/rathena': 'e985006171d2eb320ee512a653f4c83aea3d81b6', 'vendor/openkore': '51de1ddfc4449ae5217f6886de702f87ca934030'}
        for path, sha in expected.items():
            line = subprocess.check_output(['git', 'ls-files', '--stage', path], cwd=ROOT, text=True)
            self.assertIn('160000 ' + sha, line)

    def test_no_runtime_in_index(self):
        names = subprocess.check_output(['git', 'ls-files'], cwd=ROOT, text=True).splitlines()
        for name in names:
            self.assertFalse(name.startswith(('run/', 'logs/', 'evidence/', 'artifacts/')), name)
            self.assertNotIn('credentials.json', name)
            self.assertFalse(name.endswith(('.db', '.sqlite', '.o', '.so', '.log')), name)

if __name__ == '__main__':
    unittest.main()
