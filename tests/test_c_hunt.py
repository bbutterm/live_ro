import tempfile,time,unittest,json
from packages.body_gateway.journal import Journal
from packages.body_gateway.executor import Executor
class HuntTest(unittest.TestCase):
 def test_hunt_done_requires_real_kills_not_ack(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/w.sqlite');e=Executor(j,'resident_a',150001,lambda m:None);now=time.time();e.body({'type':'hello','body_epoch':'ep','ts':now});e.body({'type':'telemetry','body_epoch':'ep','ts':now,'map':'prt_fild08','pos':{'x':170,'y':200},'dead':False,'hp':40,'hp_max':40})
   a=e.run('hunt',{'map':'prt_fild08','duration':900,'min_kills':1},930,'hunt');aid=a['action_id'];e.body({'type':'skill_accepted','action_id':aid});e.body({'type':'skill_progress','action_id':aid});e.body({'type':'skill_result','action_id':aid,'outcome':'done','code':'HUNTED'});e.evaluate();self.assertNotEqual(j.action(aid)['state'],'confirmed')
   e.witness({'id':1,'kind':'kill','char_id':150001,'map':'prt_fild08','x':170,'y':200,'a1':1002,'event_ts':time.time()});e.evaluate();self.assertEqual(j.action(aid)['state'],'confirmed')
