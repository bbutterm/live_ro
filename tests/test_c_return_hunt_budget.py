import unittest
from typing import Any
from checks.c_live_cycle import Driver
class ReturnHuntBudgetTest(unittest.TestCase):
 def test_return_hunt_preserves_real_kill_requirement_and_has_search_budget(self):
  calls=[];d:Any=Driver.__new__(Driver);d.cycles=0;d.steps={}
  d.status=lambda:{'telemetry':{'map':'prt_fild08','hp':100,'hp_max':100,'inventory':{'501':20},'zeny':100}}
  d.recover=lambda:None;d.travel=lambda *a:None;d.note=lambda *a,**k:None
  d.kills=lambda t:[{'kind':'kill'}]
  def run(skill,params,deadline,**kwargs):calls.append((skill,params,deadline));return {'state':'confirmed'}
  d.run=run;d.cycle(180)
  skill,params,deadline=calls[-1];self.assertEqual(skill,'hunt');self.assertEqual(params['min_kills'],1);self.assertGreaterEqual(params['duration'],180);self.assertGreater(deadline,params['duration'])
 def test_no_server_kill_after_return_still_fails(self):
  d:Any=Driver.__new__(Driver);d.cycles=0;d.steps={};d.status=lambda:{'telemetry':{'map':'prt_fild08','hp':100,'hp_max':100,'inventory':{'501':20},'zeny':100}}
  d.recover=lambda:None;d.travel=lambda *a:None;d.note=lambda *a,**k:None;d.run=lambda *a,**k:{'state':'confirmed'}
  kills=iter([[{'kind':'kill'}],[]]);d.kills=lambda t:next(kills)
  with self.assertRaisesRegex(RuntimeError,'NO_KILL_AFTER_FIELD_RETURN'):d.cycle(180)
  self.assertEqual(d.cycles,0)
