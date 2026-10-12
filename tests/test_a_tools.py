import pathlib,subprocess,unittest
ROOT=pathlib.Path(__file__).parents[1]
class ATools(unittest.TestCase):
 def test_events_wait_and_tester_entrypoints_exist(self):
  for p in ('tools/events.py','tools/wait_event.py','tools/tester.sh','residents.yaml'):
   self.assertTrue((ROOT/p).is_file(),p+' missing')
 def test_position_is_5s_and_filtered(self):
  s=(ROOT/'server/witness/residents_witness.txt').read_text()
  self.assertIn('OnTimer5000:',s)
  self.assertIn('FROM residents.registry WHERE enabled=1 AND char_id=',s)
  self.assertNotIn('.@dx*.@dx + .@dy*.@dy <= 9) return;',s) # B needs fresh stationary observations
  self.assertIn('OnPCBaseLvUpEvent:',s)
  self.assertIn('OnPCJobLvUpEvent:',s)

 def test_witness_time_comes_from_the_db_epoch_not_a_hardcoded_zone(self):
  # 25.10.2026 02:30 happens twice in Europe/Amsterdam; the DB epoch is unambiguous.
  from tools.events import row_epoch,db_zone
  self.assertEqual(db_zone(),'UTC')  # the new world keeps MariaDB in UTC
  first=1792888200.0  # 2026-10-25T00:30Z = 02:30 CEST
  self.assertEqual(row_epoch({'ts':'2026-10-25 02:30:00','epoch':first}),first)
  self.assertEqual(row_epoch({'ts':'2026-10-25 01:30:00.000','epoch':None}),1792891800.0)  # no epoch: parsed as UTC
