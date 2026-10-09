import tempfile,time,unittest
from packages.body_gateway.journal import Journal
from packages.body_gateway.executor import Executor
class RecoveryTest(unittest.TestCase):
 def test_rest_confirmation_needs_hp_and_fresh_server_position(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/w');e=Executor(j,'resident_a',150001,lambda m:None);now=time.time();e.body({'type':'hello','body_epoch':'ep','ts':now});e.body({'type':'telemetry','body_epoch':'ep','ts':now,'map':'prontera','pos':{'x':150,'y':150},'dead':False,'hp':10,'hp_max':40});a=e.run('rest',{'hp_pct':80},120,'rest');aid=a['action_id'];e.body({'type':'skill_accepted','action_id':aid});e.body({'type':'skill_progress','action_id':aid});e.body({'type':'skill_result','action_id':aid,'outcome':'done','code':'RESTED'});e.witness({'id':1,'kind':'pos','char_id':150001,'map':'prontera','x':150,'y':150,'event_ts':time.time()});e.evaluate();self.assertNotEqual(j.action(aid)['state'],'confirmed');e.body({'type':'telemetry','body_epoch':'ep','ts':time.time(),'map':'prontera','pos':{'x':150,'y':150},'dead':False,'hp':35,'hp_max':40});e.evaluate();self.assertEqual(j.action(aid)['state'],'confirmed')
 def test_dead_character_can_request_native_respawn_not_travel(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/w');sent=[];e=Executor(j,'resident_a',150001,sent.append);now=time.time();e.body({'type':'hello','body_epoch':'ep','ts':now});e.body({'type':'telemetry','body_epoch':'ep','ts':now,'map':'prt_fild08','pos':{'x':150,'y':150},'dead':True,'hp':0});a=e.run('respawn',{},60,'respawn');self.assertEqual(a['state'],'dispatched');self.assertEqual(sent[0]['skill'],'respawn')
