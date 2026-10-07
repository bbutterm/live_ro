import importlib.util,pathlib,subprocess,sys,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).parents[1];sys.path.insert(0,str(ROOT/'tools'))
s=importlib.util.spec_from_file_location('waiter',ROOT/'tools/wait_event.py');assert s and s.loader
w=importlib.util.module_from_spec(s);s.loader.exec_module(w)
class WaitErrors(unittest.TestCase):
 def test_database_timeout_is_failure_not_absent_event(self):
  with patch.object(sys,'argv',['wait_event','--char','Tester','--kind','kill','--timeout','0']),patch.object(w,'get_events',side_effect=subprocess.TimeoutExpired('reader',8)):
   self.assertEqual(w.main(),2)
