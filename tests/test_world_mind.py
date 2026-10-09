import unittest,tempfile,pathlib,json

class WorldMindTests(unittest.TestCase):
    def test_scoped_expiring_429_enters_persisted_pause_without_provider_call(self):
        from packages.resident_world import mind
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            d=pathlib.Path(tmp);fault=d/'llm-fault.json'
            fault.write_text(json.dumps({'resident':'Mira','kind':'429','expires':1200}))
            m=mind.Mind.__new__(mind.Mind);m.r={'name':'Mira','char_id':2};m.d=d;m.c={'places':{},'residents':[],'llm':{}}
            m.path=d/'model-intent.json';m.history=[];m.history_path=d/'goal-history.json';m.errors=0;m.next_call=0;m.persona='Житель.';m.key='fake-test-key';m.cfg={}
            with patch.object(mind,'STATE',d),patch.object(mind.time,'time',return_value=1000) as clock,patch('packages.resident_world.memory.refresh'),patch.object(mind,'world_snapshot',side_effect=RuntimeError('NETWORK_BOUNDARY')) as provider:
                self.assertIsNone(m.choose({'telemetry':{}}))
                pause=json.loads((d/'llm-pause.json').read_text());self.assertEqual(pause['status'],429);self.assertEqual(pause['until'],1030)
                self.assertFalse(m.path.exists());provider.assert_not_called()
                clock.return_value=1029;self.assertIsNone(m.choose({'telemetry':{}}));provider.assert_not_called()
                m.r['name']='Borin'
                with self.assertRaisesRegex(RuntimeError,'NETWORK_BOUNDARY'):m._complete([])
                m.r['name']='Mira';clock.return_value=1201
                with self.assertRaisesRegex(RuntimeError,'NETWORK_BOUNDARY'):m._complete([])
                self.assertEqual(provider.call_count,2)

    def test_model_json_must_be_one_safe_menu_goal_with_linked_promise(self):
        from packages.resident_world.mind import validate_decision
        c={'places':{'field':{'map':'prt_fild08','x':170,'y':240}},'residents':[]}
        s={'telemetry':{'hp':100,'hp_max':100,'inventory':{'501':10},'nearby_players':[]}}
        self.assertEqual(validate_decision({'action':'walk_to','args':{'place':'field'},'say':'Иду на поле.','reason':'Прогуляться'},c,s)['action'],'walk_to')
        for d in [
          {'action':'shell','args':{},'reason':'x'},
          {'action':'idle','args':{'minutes':1},'say':'Я иду к тебе','reason':'x'},
          {'action':'hunt','args':{'map':'prt_fild08','minutes':5},'reason':'x','command':'rm'},
          {'action':'say','args':{'text':'Hello'},'reason':'x'}]:
            with self.assertRaises(ValueError):validate_decision(d,c,s)
    def test_model_hunt_obeys_configured_goal_duration_cap(self):
        from packages.resident_world.mind import validate_decision
        c={'places':{},'residents':[],'llm':{'max_hunt_minutes':5}};s={'telemetry':{'hp':100,'hp_max':100,'inventory':{'501':20}}}
        with self.assertRaises(ValueError):validate_decision({'action':'hunt','args':{'map':'prt_fild08','minutes':15},'reason':'Охота'},c,s)
    def test_hunt_is_rejected_without_health_or_potions(self):
        from packages.resident_world.mind import validate_decision
        c={'places':{},'residents':[]};d={'action':'hunt','args':{'map':'prt_fild08','minutes':5},'reason':'Охота'}
        with self.assertRaises(ValueError):validate_decision(d,c,{'telemetry':{'hp':30,'hp_max':100,'inventory':{'501':10}}})
        with self.assertRaises(ValueError):validate_decision(d,c,{'telemetry':{'hp':100,'hp_max':100,'inventory':{}}})
    def test_state_is_utf8_bounded_and_does_not_include_unconfirmed_claims(self):
        from packages.resident_world.mind import compact_state
        r={'name':'Mira','char_id':150002};s={'telemetry':{'hp':100,'hp_max':100,'nearby_players':[{'name':'x'*100,'pos':{'x':1,'y':1}}]*50},'projection':{'server_pos':{'value':{'x':1,'y':1}},'server_map':{'value':'prontera'}}}
        with tempfile.TemporaryDirectory() as tmp:
            text=compact_state(r,s,pathlib.Path(tmp),[{'action':'hunt','outcome':'failed(NO_EVIDENCE)'}]*10,'Я '*1000)
            self.assertLessEqual(len(text.encode('utf8')),1536);self.assertIn('failed(NO_EVIDENCE)',text)
