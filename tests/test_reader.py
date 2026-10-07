import importlib.util
import pathlib
import subprocess
import unittest
from unittest.mock import patch

P = pathlib.Path(__file__).resolve().parents[1] / 'packages/witness_reader/reader.py'
spec = importlib.util.spec_from_file_location('reader', P)
assert spec is not None and spec.loader is not None
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

class ReaderTests(unittest.TestCase):
    def test_negative_cursor_rejected_before_connection(self):
        with patch.object(reader.subprocess, 'run') as call:
            with self.assertRaises(ValueError): reader.read_events('/private', -1)
            call.assert_not_called()

    def test_select_only_and_parse(self):
        result = subprocess.CompletedProcess([], 0, '{"id":7,"kind":"login"}\n', '')
        with patch.object(reader.subprocess, 'run', return_value=result) as call:
            self.assertEqual(reader.read_events('/private', 6)[0]['id'], 7)
            sql = call.call_args.kwargs['input']
            self.assertTrue(sql.startswith('SELECT '))
            self.assertIn('id > 6', sql)
            self.assertEqual(call.call_args.kwargs['timeout'], 8)

    def test_error_does_not_echo_private_details(self):
        result = subprocess.CompletedProcess([], 1, '', 'password=SECRET')
        with patch.object(reader.subprocess, 'run', return_value=result):
            with self.assertRaises(RuntimeError) as ctx: reader.read_events('/private', 0)
            self.assertNotIn('SECRET', str(ctx.exception))
