import pathlib,subprocess,unittest
ROOT=pathlib.Path(__file__).parents[1]
class ATools(unittest.TestCase):
 def test_events_wait_and_tester_entrypoints_exist(self):
  for p in ('tools/events.py','tools/wait_event.py','tools/tester.sh','residents.yaml'):
   self.assertTrue((ROOT/p).is_file(),p+' missing')
 def test_position_is_5s_and_filtered(self):
  s=(ROOT/'server/witness/residents_witness.txt').read_text()
  self.assertIn('OnTimer5000:',s)
  self.assertIn('.@dx*.@dx + .@dy*.@dy <= 9',s)
  self.assertIn('OnPCBaseLvUpEvent:',s)
  self.assertIn('OnPCJobLvUpEvent:',s)
