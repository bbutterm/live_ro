"""Real gateway/journal proof paths, entirely offline."""
import json,tempfile,time,unittest
from packages.body_gateway.executor import Executor
from packages.body_gateway.journal import Journal

class ActionProofTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.j=Journal(self.tmp.name+'/journal.sqlite')
        self.sent=[];self.e=Executor(self.j,'resident_a',1,self.sent.append);self.now=time.time()
        self.frame={'type':'telemetry','body_epoch':'body','ts':self.now,'map':'prt_in','pos':{'x':126,'y':76},'dead':False,'running':None,'inventory':{'909':3,'2301':1},'sellable_by_category':{'loot':{'909':3},'equipment':{'2301':1}}}
        self.e.body({'type':'hello','body_epoch':'body'});self.e.body(self.frame)
    def tearDown(self):
        self.j.db.close();self.tmp.cleanup()
    def finish(self,a,frame):
        self.e.body({'type':'skill_result','action_id':a['action_id'],'outcome':'done','code':'DONE'})
        self.e.body({**frame,'ts':time.time()});self.e.evaluate()
        return self.j.action(a['action_id'])
    def trade(self,item,amount,id):
        self.j.event('picklog',id,self.now+.01,{'id':id,'char_id':1,'map':'prt_in','type':'S','item_id':item,'amount':amount})
    def test_category_sale_requires_server_receipt_and_rejects_other_category(self):
        a=self.e.run('sell_loot',{'category':'equipment'},40,'sale')
        self.assertEqual(self.finish(a,{**self.frame,'inventory':{'909':3}})['state'],'verifying')
        self.trade(2301,-1,1);self.trade(909,-3,2)
        result=self.finish(a,{**self.frame,'inventory':{}})
        self.assertNotEqual(result['state'],'confirmed','sale outside selected category must not confirm')
    def test_legacy_sale_witness_preserves_operator_kept_loot(self):
        self.e.body({**self.frame,'sellable':{'2301':1}})
        a=self.e.run('sell_loot',{},40,'legacy')
        self.trade(2301,-1,1)
        self.assertEqual(self.finish(a,{**self.frame,'inventory':{'909':3}})['state'],'confirmed')

    def test_party_hunt_rejects_absent_native_party(self):
        with self.assertRaisesRegex(ValueError,'INVALID_PARTY'):
            self.e.run('hunt',{'map':'prt_fild08','duration':30,'min_kills':1,'solo':True,'party_target':'Borin'},60,'party')
    def test_party_loss_does_not_suppress_deadline_expiration(self):
        from unittest.mock import patch
        party={'source':'native_client','share':1,'members':[{'name':'Borin','online':True}]}
        frame={**self.frame,'map':'prt_fild08','party':party};self.e.body(frame)
        a=self.e.run('hunt',{'map':'prt_fild08','duration':1,'min_kills':1,'solo':True,'party_target':'Borin'},2,'deadline')
        self.e.body({'type':'skill_result','action_id':a['action_id'],'outcome':'done','code':'DONE'})
        with patch('packages.body_gateway.executor.time.time',return_value=a['deadline']+.1):
            self.e.body({**frame,'ts':a['deadline']+.1,'party':None});self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'],'unknown')

    def test_party_hunt_needs_kill_witness_and_still_joined_party(self):
        party={'source':'native_client','share':1,'members':[{'name':'Borin','online':True}]}
        frame={**self.frame,'map':'prt_fild08','party':party}
        self.e.body(frame)
        a=self.e.run('hunt',{'map':'prt_fild08','duration':30,'min_kills':1,'solo':True,'party_target':'Borin'},60,'party')
        self.assertEqual(self.finish(a,frame)['state'],'verifying')
        self.e.witness({'id':1,'event_ts':self.now+.01,'char_id':1,'kind':'kill','map':'prt_fild08','a1':1002})
        self.assertNotEqual(self.finish(a,{**frame,'party':None})['state'],'confirmed')
        self.assertEqual(self.finish(a,frame)['state'],'confirmed')
