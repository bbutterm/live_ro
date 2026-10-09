import importlib.util,pathlib,tempfile,unittest
class JournalTest(unittest.TestCase):
 def test_transition_chain_is_durable_and_duplicate_does_not_create_action(self):
  module=pathlib.Path(__file__).parents[1]/'packages/body_gateway/journal.py'
  self.assertTrue(module.exists(),'Action journal missing')
  from packages.body_gateway.journal import Journal
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/world.sqlite');a=j.plan('resident_a','travel_to',{'map':'prontera','x':150,'y':150,'r':3},'key1',123,'epoch1',42)
   self.assertEqual(j.plan('resident_a','travel_to',{},'key1',123,'epoch1',42)['action_id'],a['action_id'])
   for s in ('dispatched','accepted','running','verifying','confirmed'):j.transition(a['action_id'],s,'ARRIVED' if s=='confirmed' else '',{})
   self.assertEqual([r['state'] for r in j.trace(a['action_id'])],['planned','dispatched','accepted','running','verifying','confirmed']);j.close()
   j=Journal(d+'/world.sqlite');self.assertEqual(j.action(a['action_id'])['state'],'confirmed');self.assertEqual(len(j.actions()),1);j.close()
