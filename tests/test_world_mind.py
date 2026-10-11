import unittest,tempfile,pathlib,json

class WorldMindTests(unittest.TestCase):
    def test_budget_pause_refreshes_witness_memory_without_model_call(self):
        from packages.resident_world import mind
        from unittest.mock import Mock,patch
        with tempfile.TemporaryDirectory() as tmp:
            m=mind.Mind.__new__(mind.Mind);m.d=pathlib.Path(tmp);m.path=m.d/'model-intent.json'
            m.r={'name':'Mira','char_id':2};m.next_call=200;m._complete=Mock()
            with patch.object(mind.time,'time',return_value=100),patch('packages.resident_world.memory.refresh') as refresh:
                self.assertIsNone(m.choose({}))
                refresh.assert_called_once_with(m.r,m.d)
            m._complete.assert_not_called()
            with patch.object(mind.time,'time',return_value=100),patch('packages.resident_world.memory.refresh',side_effect=RuntimeError('witness unavailable')):
                self.assertIsNone(m.choose({}))
            m._complete.assert_not_called()

    def test_meeting_activity_uses_only_verified_recent_meetings(self):
        from packages.resident_world.mind import meeting_activity
        import tempfile,json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'manual_task.json').write_text(json.dumps({'action':'hunt','started':90,'expires':140}))
            meeting={'self':{'source':'witness','ts':95},'peer':{'source':'witness','ts':94}}
            m={'source':'witness','evidence':{'meetings':[meeting,{'self':{'source':'telemetry','ts':96},'peer':{'source':'witness','ts':96}}]}}
            self.assertEqual(meeting_activity(p,m,100),{'current_action':'hunt','recent_meetings':1,'last_meeting_seconds':6})
            self.assertEqual(meeting_activity(p,{'source':'model','evidence':m['evidence']},100)['recent_meetings'],0)
    def test_negotiation_receives_current_activity_context(self):
        from packages.resident_world import mind
        from unittest.mock import Mock,patch
        with tempfile.TemporaryDirectory() as tmp:
            m=mind.Mind.__new__(mind.Mind);m.d=pathlib.Path(tmp);m.next_call=0;m.r={'name':'Mira','char_id':2};m.c={'places':{'field':{}}};m.persona='Житель';m.history=[]
            (m.d/'manual_task.json').write_text(json.dumps({'action':'hunt','started':90,'expires':140}))
            m._complete=Mock(return_value=json.dumps({'choice':'decline','place':'field','reason':'Заканчиваю охоту'}))
            with patch.object(mind.time,'time',return_value=100),patch.object(mind,'record'):
                self.assertEqual(m.negotiate({'resident':'Borin','place':'field'}, {},True)['choice'],'decline')
            # Read the exact untrusted context passed to the separate resident vote.
            context=json.loads(m._complete.call_args.args[0][1]['content'])
            self.assertEqual(context['activity']['current_action'],'hunt')
            self.assertLessEqual(len(m._complete.call_args.args[0][1]['content'].encode()),1536)

    def test_meeting_schema_is_bounded_and_counter_requires_another_place(self):
        from packages.resident_world.meeting import validate_vote
        c={'places':{'fountain':{},'field':{}}}
        for choice,place in [('accept','fountain'),('decline','fountain'),('counter','field')]:
            vote={'choice':choice,'place':place,'reason':'Хочу поговорить'}
            self.assertEqual(validate_vote(vote,c,'fountain',True),vote)
        for vote in [dict(choice='counter',place='fountain',reason='Нет'),dict(choice='accept',place='field',reason='Да'),dict(choice='accept',place='fountain',reason='Да',command='shell')]:
            with self.assertRaises(ValueError):validate_vote(vote,c,'fountain',True)
        with self.assertRaises(ValueError):validate_vote(dict(choice='counter',place='field',reason='Нет'),c,'fountain',False)

    def test_meeting_budget_pause_never_becomes_scripted_acceptance(self):
        from packages.resident_world.mind import Mind
        from unittest.mock import Mock,patch
        with tempfile.TemporaryDirectory() as tmp:
            m=Mind.__new__(Mind);m.d=pathlib.Path(tmp);m.next_call=200;m._complete=Mock()
            with patch('packages.resident_world.mind.time.time',return_value=100):
                self.assertIsNone(m.negotiate({'resident':'ResidentA','place':'fountain'}, {},True))
            m._complete.assert_not_called()

    def test_counter_reply_honors_own_current_chat_goal_but_not_another(self):
        from packages.resident_world import mind
        from unittest.mock import Mock,patch
        with tempfile.TemporaryDirectory() as tmp:
            m=mind.Mind.__new__(mind.Mind);m.d=pathlib.Path(tmp);m.next_call=0;m.r={'name':'Mira','char_id':2};m.c={'places':{'field':{}}};m.persona='Житель';m.history=[]
            m._complete=Mock(return_value=json.dumps({'choice':'accept','place':'field','reason':'Хочу поговорить'}))
            (m.d/'chat-goal.json').write_text(json.dumps({'id':'own-meeting'}))
            offer={'resident':'Borin','place':'field','meeting_id':'own-meeting'}
            with patch.object(mind,'record'):
                self.assertEqual(m.negotiate(offer,{},False)['choice'],'accept')
                self.assertEqual(m._complete.call_args.kwargs['timeout'],20)
                offer['meeting_id']='another-meeting'
                self.assertIsNone(m.negotiate(offer,{},False))
                self.assertEqual(m._complete.call_count,1)

    def test_scoped_expiring_429_enters_persisted_pause_without_provider_call(self):
        from packages.resident_world import mind
        from unittest.mock import Mock,patch
        with tempfile.TemporaryDirectory() as tmp:
            d=pathlib.Path(tmp);fault=d/'llm-fault.json'
            fault.write_text(json.dumps({'resident':'Mira','kind':'429','expires':1200}))
            m=mind.Mind.__new__(mind.Mind);m.r={'name':'Mira','char_id':2};m.d=d;m.c={'places':{},'residents':[],'llm':{}}
            m.path=d/'model-intent.json';m.history=[];m.history_path=d/'goal-history.json';m.persona='Житель.'
            m.client=Mock();m.client.complete.side_effect=RuntimeError('NETWORK_BOUNDARY')
            with patch.object(mind,'STATE',d),patch.object(mind.time,'time',return_value=1000) as clock,patch('packages.resident_world.memory.refresh'):
                self.assertIsNone(m.choose({'telemetry':{}}))
                pause=json.loads((d/'llm-pause.json').read_text());self.assertEqual(pause['status'],429);self.assertTrue(1030<=pause['until']<=1036)
                self.assertFalse(m.path.exists());m.client.complete.assert_not_called()
                self.assertTrue(m.waiting(1001))  # a short pause is waited out, not handed to a native habit
                clock.return_value=1029;self.assertIsNone(m.choose({'telemetry':{}}));m.client.complete.assert_not_called()
                m.r['name']='Borin'
                with self.assertRaisesRegex(RuntimeError,'NETWORK_BOUNDARY'):m._complete([])
                m.r['name']='Mira';clock.return_value=1201
                with self.assertRaisesRegex(RuntimeError,'NETWORK_BOUNDARY'):m._complete([])
                self.assertEqual(m.client.complete.call_count,2)

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
        with self.assertRaises(ValueError):validate_decision(d,c,{'telemetry':{'hp':100,'hp_max':100,'inventory':{},'zeny':100}})
        with self.assertRaises(ValueError):validate_decision(d,c,{'telemetry':{'hp':100,'hp_max':100,'inventory':{'501':10},'weight':75,'weight_max':100}})
        # Broke and nothing to sell: hunting is the only way to earn potions (fallback does the same).
        self.assertEqual(validate_decision(d,c,{'telemetry':{'hp':100,'hp_max':100,'inventory':{},'zeny':0,'sellable':{}}}),d)
    def test_rest_rejects_regeneration_disabled_at_half_weight(self):
        from packages.resident_world.mind import validate_decision
        c={'places':{},'residents':[]};d={'action':'rest','args':{'until_hp':85},'reason':'Восстановить здоровье'}
        for weight in (50,81):
            with self.subTest(weight=weight),self.assertRaisesRegex(ValueError,'OVERWEIGHT'):
                validate_decision(d,c,{'telemetry':{'weight':weight,'weight_max':100}})
        for t in ({'weight':49,'weight_max':100},{'weight':50,'weight_max':0},{}):
            self.assertEqual(validate_decision(d,c,{'telemetry':t}),d)

    def test_state_is_utf8_bounded_and_does_not_include_unconfirmed_claims(self):
        from packages.resident_world.mind import compact_state
        r={'name':'Mira','char_id':150002};s={'telemetry':{'hp':100,'hp_max':100,'nearby_players':[{'name':'x'*100,'pos':{'x':1,'y':1}}]*50},'projection':{'server_pos':{'value':{'x':1,'y':1}},'server_map':{'value':'prontera'}}}
        with tempfile.TemporaryDirectory() as tmp:
            text=compact_state(r,s,pathlib.Path(tmp),[{'action':'hunt','outcome':'failed(NO_EVIDENCE)'}]*10,'Я '*1000)
            self.assertLessEqual(len(text.encode('utf8')),1536);self.assertIn('failed(NO_EVIDENCE)',text)
