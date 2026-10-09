import unittest,tempfile,time
from packages.body_gateway.journal import Journal
from packages.body_gateway.executor import Executor
class SpeechTest(unittest.TestCase):
 def test_public_speech_needs_matching_native_chat_not_local_ack(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/w');e=Executor(j,'resident_a',150001,lambda m:None);n=time.time();e.body({'type':'hello','body_epoch':'ep','ts':n});e.body({'type':'telemetry','body_epoch':'ep','ts':n,'map':'prontera','pos':{'x':150,'y':150},'dead':False,'hp':100})
   try:a=e.run('say',{'text':'Привет, Mira!'},30,'say')
   except ValueError as err:self.fail('Public speech missing: '+str(err))
   aid=a['action_id'];e.body({'type':'skill_accepted','action_id':aid});e.body({'type':'skill_progress','action_id':aid});e.body({'type':'skill_result','action_id':aid,'outcome':'done','code':'SPOKEN'});self.assertNotEqual(j.action(aid)['state'],'confirmed')
   j.event('chat','1',time.time(),{'char_id':150001,'type':'O','text':'Привет, Mira!'},1);e.evaluate();self.assertEqual(j.action(aid)['state'],'confirmed');j.close()
 def test_result_without_intermediate_ack_waits_for_server_evidence(self):
  # Recovered native ledger may contain the result but not intermediate ACKs.
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/w');self.addCleanup(j.close)
   e=Executor(j,'resident_a',150001,lambda m:None);n=time.time()
   e.body({'type':'hello','body_epoch':'ep','ts':n})
   e.body({'type':'telemetry','body_epoch':'ep','ts':n,'map':'prontera','pos':{'x':150,'y':150},'dead':False,'hp':100})
   a=e.run('say',{'text':'Hello Mira!'},30,'recovered-result');aid=a['action_id']
   try:e.body({'type':'skill_result','action_id':aid,'outcome':'done','code':'SPOKEN'})
   except ValueError as err:self.fail('Completed native result must enter verification: '+str(err))
   e.evaluate();self.assertEqual(j.action(aid)['state'],'verifying')
   j.event('chat','wrong-speaker',time.time(),{'char_id':150002,'type':'O','text':'Hello Mira!'})
   e.evaluate();self.assertEqual(j.action(aid)['state'],'verifying')
   j.event('chat','correct-speaker',time.time(),{'char_id':150001,'type':'O','text':'Hello Mira!'})
   e.evaluate();self.assertEqual(j.action(aid)['state'],'confirmed')
   self.assertEqual([r['state'] for r in j.trace(aid)],['planned','dispatched','verifying','confirmed'])
 def test_wrapped_speech_requires_every_part(self):
  with tempfile.TemporaryDirectory()as d:
   j=Journal(d+'/w');e=Executor(j,'resident_a',150001,lambda m:None);n=time.time();e.body({'type':'hello','body_epoch':'ep','ts':n});e.body({'type':'telemetry','body_epoch':'ep','ts':n,'map':'prontera','pos':{'x':150,'y':150},'dead':False,'hp':100});a=e.run('say',{'text':'Hello Mira! How are you?'},30,'wrap');aid=a['action_id'];e.body({'type':'skill_result','action_id':aid,'outcome':'done','code':'SPOKEN'});j.event('chat','p1',time.time(),{'char_id':150001,'type':'O','text':'Hello Mira! How'});e.evaluate();self.assertEqual(j.action(aid)['state'],'verifying');j.event('chat','p2',time.time(),{'char_id':150001,'type':'O','text':'are you?'});e.evaluate();self.assertEqual(j.action(aid)['state'],'confirmed');j.close()
