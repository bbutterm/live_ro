import unittest
from packages.resident_world.supervisor import fallback

class FallbackLifeTests(unittest.TestCase):
    def test_unserviceable_weight_waits_safely_in_city_instead_of_shop_field_loop(self):
        from packages.resident_world.fallback_life import city_goal,completed
        c={'places':{'fountain':{'map':'prontera','x':156,'y':191}}}
        t={'hp':100,'hp_max':100,'map':'prt_in','weight':2879,'weight_max':3550,'inventory':{'501':20,'1202':26}}
        s={'telemetry':t,'age':1,'projection':{}}
        state={'next_city_at':1000}
        self.assertEqual(city_goal(s,state,100,c)['action'],'walk_to')
        t['map']='prontera'
        s['projection']={'server_map':{'value':'prontera'},'server_pos':{'ts':101,'value':{'x':156,'y':191}}}
        goal=city_goal(s,state,101,c)
        self.assertEqual(goal['action'],'idle')
        completed(state,goal,'done',401)
        s['projection']['server_pos']['ts']=402
        self.assertEqual(city_goal(s,state,402,c)['action'],'idle')
        t['hp']=10
        self.assertEqual(city_goal(s,state,402,c)['action'],'idle')
        t['weight']=1775
        self.assertEqual(city_goal(s,state,402,c)['action'],'idle')
        t['inventory']['909']=1
        self.assertIsNone(city_goal(s,state,402,c))
        del t['inventory']['909'];t['dead']=True
        self.assertIsNone(city_goal(s,state,402,c))

    def test_chat_interrupt_resumes_rest_without_repeating_greeting(self):
        from packages.resident_world.fallback_life import city_goal,completed
        c={'places':{'fountain':{'map':'prontera','x':156,'y':191}}}
        s={'telemetry':{'hp':100,'hp_max':100,'map':'prontera','inventory':{'501':20}},'age':1,'projection':{'server_map':{'value':'prontera'},'server_pos':{'ts':106,'value':{'x':156,'y':191}}}}
        state={'stage':'rest'}
        completed(state,{'action':'idle'},'failed(CHAT_INTERRUPT)',100)
        self.assertIsNone(city_goal(s,state,104,c))
        self.assertEqual(city_goal(s,state,106,c)['action'],'idle')

    def test_health_and_supply_take_priority_over_city_visit(self):
        from packages.resident_world.fallback_life import city_goal
        c={'places':{'fountain':{'map':'prontera','x':156,'y':191}}}
        for t in ({'dead':True}, {'hp':10,'hp_max':100},
                  {'hp':100,'hp_max':100,'map':'prt_fild08','inventory':{},'zeny':100},
                  {'hp':100,'hp_max':100,'map':'prt_in','inventory':{'501':20},'zeny':100}):
            with self.subTest(telemetry=t):
                self.assertIsNone(city_goal({'telemetry':t,'age':1,'projection':{}},{},100,c))

    def test_real_brain_dispatches_city_goal_when_model_is_unavailable(self):
        import tempfile,json
        from pathlib import Path
        from unittest.mock import patch
        from packages.resident_world import supervisor,skills
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);(state/'fixture').mkdir()
            r={'resident_id':'fixture','name':'Fixture','char_id':99}
            c={'llm':{'enabled':False},'chat_service':'standalone','places':{'fountain':{'map':'prontera','x':156,'y':191}}}
            s={'telemetry':{'hp':100,'hp_max':100,'map':'prt_fild08','inventory':{'501':20},'zeny':100},'age':1,'projection':{},'actions':[]}
            def rpc(sock,command):
                if command['op']=='run':(state/'STOP').touch();return {'action_id':'native'}
                return s
            def execute(*args,**kwargs):
                (state/'STOP').touch();return {'outcome':'done'}
            with patch.object(supervisor,'STATE',state),patch.object(supervisor,'config',return_value=c),patch.object(supervisor,'request',side_effect=rpc),patch.object(skills,'execute',side_effect=execute) as action:
                supervisor.brain(r)
            action.assert_called_once_with('Fixture','walk_to',{'place':'fountain'},origin='brain')
            self.assertEqual(json.loads((state/'fixture/fallback-life.json').read_text())['stage'],'say')

    def test_city_cycle_requires_witness_and_done_before_progress(self):
        from packages.resident_world.fallback_life import city_goal, completed
        from packages.resident_world.skills import witness_at
        c={'places':{'fountain':{'map':'prontera','x':156,'y':191}}}
        t={'hp':100,'hp_max':100,'map':'prt_fild08','inventory':{'501':20},'zeny':100}
        s={'telemetry':t,'age':1,'projection':{}}
        state={};goal=city_goal(s,state,100,c)
        self.assertEqual(goal['action'],'walk_to')
        completed(state,goal,'done',110)
        self.assertEqual(city_goal(s,state,111,c)['action'],'walk_to')
        t['map']='prontera'
        s['projection']={'server_map':{'value':'prontera'},'server_pos':{'ts':112,'value':{'x':156,'y':191}}}
        goal=city_goal(s,state,113,c)
        self.assertEqual(goal['action'],'say')
        completed(state,goal,'done',114)
        goal=city_goal(s,state,115,c)
        self.assertEqual(goal,{'action':'idle','args':{'minutes':5},'reason':'Отдыхаю в городе без модели'})
        completed(state,goal,'done',415)
        self.assertIsNone(city_goal(s,state,416,c))
        s['projection']['server_pos']['ts']=1315
        self.assertEqual(city_goal(s,state,1315,c)['action'],'say')
