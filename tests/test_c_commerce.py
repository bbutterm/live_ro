import tempfile,time,unittest
from packages.body_gateway.journal import Journal
from packages.body_gateway.executor import Executor
class CommerceTest(unittest.TestCase):
 def test_buy_needs_server_picklog_and_inventory_not_local_ack(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/w');e=Executor(j,'resident_a',150001,lambda m:None);now=time.time();e.body({'type':'hello','body_epoch':'ep','ts':now});e.body({'type':'telemetry','body_epoch':'ep','ts':now,'map':'prt_in','pos':{'x':126,'y':76},'dead':False,'hp':40,'hp_max':40,'inventory':{}});a=e.run('buy_potions',{'item_id':501,'stock_goal':5,'max_zeny':100},60,'buy');aid=a['action_id'];e.body({'type':'skill_accepted','action_id':aid});e.body({'type':'skill_progress','action_id':aid});e.body({'type':'skill_result','action_id':aid,'outcome':'done','code':'BOUGHT'});e.evaluate();self.assertNotEqual(j.action(aid)['state'],'confirmed');j.event('picklog',1,time.time(),{'id':1,'char_id':150001,'type':'S','item_id':501,'amount':5,'map':'prt_in'},1);e.evaluate();self.assertNotEqual(j.action(aid)['state'],'confirmed');e.body({'type':'telemetry','body_epoch':'ep','ts':time.time(),'map':'prt_in','pos':{'x':126,'y':76},'dead':False,'hp':40,'hp_max':40,'inventory':{'501':5}});e.evaluate();self.assertEqual(j.action(aid)['state'],'confirmed')
