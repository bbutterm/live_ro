"""Support completion uses own server-earned job levels, never invented kills."""
import unittest,tempfile,json,time
from packages.body_gateway.journal import Journal
from packages.body_gateway.executor import Executor

class SupportProofTests(unittest.TestCase):
    def outcome(self,progress=True,observed=True):
        with tempfile.TemporaryDirectory() as d:
            j=Journal(d+'/j.sqlite');e=Executor(j,'resident_b',150002,lambda m:None);now=time.time()
            party={'name':'MiraBorin','share':1,'members':[{'name':'Borin'}]}
            e.body({'type':'hello','body_epoch':'body'})
            frame={'type':'telemetry','body_epoch':'body','ts':now,'map':'prt_fild08','pos':{'x':170,'y':240},'dead':False,'hp':200,'job_level':1,'party':party,'running':None,'cooperative_support':False}
            e.body(frame)
            a=e.run('hunt',{'map':'prt_fild08','duration':30,'min_kills':1,'support_target':'Borin'},60,'support')
            e.body({**frame,'ts':now+0.01,'cooperative_support':observed,'running':{'action_id':a['action_id']}})
            if progress:e.witness({'id':1,'event_ts':now+0.02,'char_id':150002,'kind':'job_level','map':'prt_fild08','a1':2})
            e.body({'type':'skill_result','action_id':a['action_id'],'outcome':'done','code':'HUNTED'})
            e.body({**frame,'ts':now+0.03,'job_level':2 if progress else 1})
            e.evaluate(); result=j.action(a['action_id'])
            return result,j.trace(a['action_id'])

    def test_real_support_progress_confirms_without_self_kills(self):
        result,trace=self.outcome()
        self.assertEqual(result['state'],'confirmed')
        self.assertEqual(result['code'],'SUPPORT_PROGRESS')
        self.assertNotIn('server_kills',json.dumps(trace))

    def test_no_progress_cannot_confirm_support(self):
        result,_=self.outcome(progress=False)
        self.assertEqual(result['state'],'verifying')

    def test_no_native_support_observation_cannot_confirm(self):
        result,_=self.outcome(observed=False)
        self.assertEqual(result['state'],'verifying')
