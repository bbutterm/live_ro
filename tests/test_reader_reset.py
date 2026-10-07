import importlib.util,pathlib,subprocess,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).parents[1]
s=importlib.util.spec_from_file_location('reader',ROOT/'packages/witness_reader/reader.py');assert s and s.loader
r=importlib.util.module_from_spec(s);s.loader.exec_module(r)
class ResetGuard(unittest.TestCase):
 def test_source_sequence_behind_cursor_fails_closed(self):
  with patch.object(r.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0,'',''),subprocess.CompletedProcess([],0,'10\n','')]):
   with self.assertRaisesRegex(ValueError,'generation'):r.read_events('/private/test',50)
