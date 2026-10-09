import pathlib,tempfile,time,unittest
class ExecutorTest(unittest.TestCase):
 def test_unknown_arrival_does_not_confirm_while_body_task_is_active(self):
  from packages.body_gateway.journal import Journal
  from packages.body_gateway.executor import Executor
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/world.sqlite');e=Executor(j,'resident_a',150001,lambda m:None);now=time.time();e.body({'type':'hello','body_epoch':'ep','ts':now});e.body({'type':'telemetry','body_epoch':'ep','ts':now,'map':'prontera','pos':{'x':10,'y':10},'dead':False})
   a=e.run('travel_to',{'map':'prt_fild08','r':0},60,'race');j.transition(a['action_id'],'unknown','DIVERGENCE')
   e.body({'type':'telemetry','body_epoch':'ep','ts':time.time(),'map':'prt_fild08','pos':{'x':170,'y':375},'dead':False,'running':{'action_id':a['action_id']}});e.witness({'id':1,'kind':'loadmap','char_id':150001,'map':'prt_fild08','x':170,'y':375,'event_ts':time.time()});e.evaluate();self.assertNotEqual(j.action(a['action_id'])['state'],'confirmed')

 def test_recovery_does_not_dispatch_uncommitted_plan(self):
  from packages.body_gateway.journal import Journal
  from packages.body_gateway.executor import Executor
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/world.sqlite');a=j.plan('resident_a','travel_to',{'map':'prontera'},'pending',time.time()+60,'epoch1',0)
   try:Executor(j,'resident_a',150001,lambda m:self.fail('must not send'))
   except ValueError as err:self.fail('recovery crashed: '+str(err))
   self.assertEqual(j.action(a['action_id'])['state'],'rejected_local')

 def test_witness_records_actual_receipt_time(self):
  import json
  from packages.body_gateway.journal import Journal
  from packages.body_gateway.executor import Executor
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/world.sqlite');e=Executor(j,'resident_a',150001,lambda m:None);before=time.time()
   e.witness({'id':1,'event_ts':before-1,'kind':'pos','map':'prontera','x':10,'y':10,'char_id':150001})
   row=json.loads(j.db.execute("SELECT payload FROM events WHERE source='witness'").fetchone()[0]);self.assertGreaterEqual(row.get('received_ts',0),before)

 def test_recovery_restores_server_authority_without_replaying(self):
  from packages.body_gateway.journal import Journal
  from packages.body_gateway.executor import Executor
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/world.sqlite');j.event('witness',8,time.time(),{'id':8,'event_ts':time.time(),'kind':'die','map':'prontera','x':10,'y':10,'char_id':150001},8)
   e=Executor(j,'resident_a',150001,lambda m:self.fail('must not replay'))
   self.assertTrue(e.projection.get('server_dead',{}).get('value'))
   self.assertEqual(j.cursor('witness'),8)

 def test_ack_and_body_arrival_are_not_server_confirmation(self):
  p=pathlib.Path(__file__).parents[1]/'packages/body_gateway/executor.py';self.assertTrue(p.exists(),'Executor missing')
  from packages.body_gateway.journal import Journal
  from packages.body_gateway.executor import Executor
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/world.sqlite');sent=[];e=Executor(j,'resident_a',150001,sent.append);now=time.time()
   e.body({'type':'hello','body_epoch':'epoch1','ts':now});e.body({'type':'telemetry','body_epoch':'epoch1','ts':now,'map':'prontera','pos':{'x':10,'y':10},'dead':False})
   a=e.run('travel_to',{'map':'prontera','x':150,'y':150,'r':3},60,'once');aid=a['action_id'];e.body({'type':'skill_accepted','action_id':aid});e.body({'type':'skill_progress','action_id':aid});e.body({'type':'skill_result','action_id':aid,'outcome':'done','code':'ARRIVED'})
   e.body({'type':'telemetry','body_epoch':'epoch1','ts':time.time(),'map':'prontera','pos':{'x':150,'y':150},'dead':False});e.evaluate();self.assertNotEqual(j.action(aid)['state'],'confirmed')
   e.witness({'id':43,'kind':'pos','map':'prontera','x':150,'y':150,'char_id':150001,'event_ts':time.time()});e.evaluate();self.assertEqual(j.action(aid)['state'],'confirmed');self.assertEqual(j.action(aid)['code'],'ARRIVED')
   self.assertEqual(e.run('travel_to',{},60,'once')['action_id'],aid);self.assertEqual(len(sent),1)

 def test_duplicate_rejection_does_not_fail_original_running_action(self):
  from packages.body_gateway.journal import Journal
  from packages.body_gateway.executor import Executor
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/world.sqlite');e=Executor(j,'resident_a',150001,lambda m:None);now=time.time();e.body({'type':'hello','body_epoch':'epoch1','ts':now});e.body({'type':'telemetry','body_epoch':'epoch1','ts':now,'map':'prontera','pos':{'x':10,'y':10},'dead':False})
   a=e.run('travel_to',{'map':'prontera','r':3},60,'same');e.body({'type':'skill_accepted','action_id':a['action_id']});e.body({'type':'skill_progress','action_id':a['action_id']});e.body({'type':'skill_rejected','action_id':a['action_id'],'code':'DUPLICATE'})
   self.assertEqual(j.action(a['action_id'])['state'],'running')

 def test_server_death_prevents_confirmation_from_old_arrival(self):
  from packages.body_gateway.journal import Journal
  from packages.body_gateway.executor import Executor
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/world.sqlite');e=Executor(j,'resident_a',150001,lambda m:None);now=time.time();e.body({'type':'hello','body_epoch':'epoch1','ts':now});e.body({'type':'telemetry','body_epoch':'epoch1','ts':now,'map':'prontera','pos':{'x':10,'y':10},'dead':False})
   a=e.run('travel_to',{'map':'prontera','x':150,'y':150,'r':3},60,'death');aid=a['action_id'];e.body({'type':'skill_accepted','action_id':aid});e.body({'type':'skill_result','action_id':aid,'outcome':'done','code':'ARRIVED'});e.body({'type':'telemetry','body_epoch':'epoch1','ts':time.time(),'map':'prontera','pos':{'x':150,'y':150},'dead':False});e.witness({'id':1,'kind':'pos','map':'prontera','x':150,'y':150,'char_id':150001,'event_ts':time.time()});e.witness({'id':2,'kind':'die','map':'prontera','x':150,'y':150,'char_id':150001,'event_ts':time.time()});e.evaluate();self.assertNotEqual(j.action(aid)['state'],'confirmed')
