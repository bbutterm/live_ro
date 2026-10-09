import unittest
from packages.resident_world.skills import validate, outcome, retry_allowed, native_plan

class WorldSkillsTests(unittest.TestCase):
    def test_cli_do_routes_validated_json_to_menu(self):
        import io,sys
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from packages.resident_world.cli import main
        with patch.object(sys,'argv',['ro','do','ResidentA','say','{"text":"Привет!"}']),patch('packages.resident_world.skills.execute',return_value={'outcome':'done'}) as run,redirect_stdout(io.StringIO()) as output:
            main()
        run.assert_called_once_with('ResidentA','say',{'text':'Привет!'})
        self.assertIn('done',output.getvalue())
    def test_meeting_confirmation_requires_both_fresh_server_positions(self):
        from packages.resident_world.skills import witness_at
        p={'map':'prontera','x':156,'y':191}
        s={'age':1,'projection':{'server_map':{'value':'prontera'},'server_pos':{'value':{'x':158,'y':190},'ts':99}}}
        self.assertTrue(witness_at(s,p,100))
        self.assertFalse(witness_at(s,p,130))
        s['projection']['server_pos']['value']['x']=200
        self.assertFalse(witness_at(s,p,100))
    def test_meet_obtains_peer_agreement_and_moves_both_residents(self):
        import tempfile,pathlib,time
        from unittest.mock import patch
        from packages.resident_world import skills
        c={'residents':[{'name':'Mira','resident_id':'resident_b'}],'places':{'fountain':{'map':'prontera','x':156,'y':191}}}
        s={'actions':[],'age':1,'projection':{'server_map':{'value':'prontera'},'server_pos':{'value':{'x':156,'y':191},'ts':time.time()}}}
        plans=[]
        def native(sock,plan,key,task,d):plans.append((sock,plan));return 'done'
        with tempfile.TemporaryDirectory() as tmp:
            d=pathlib.Path(tmp);(d/'resident_b').mkdir()
            with patch.object(skills,'STATE',d),patch.object(skills,'request',return_value=s),patch.object(skills,'run_native',side_effect=native),patch.object(skills.time,'sleep'):
                self.assertEqual(skills.execute_native_menu('meet',{'resident':'Mira','place':'fountain'},c,'self-sock','key',{'native_results':[]},d),'done')
        self.assertEqual(sum(p['skill']=='travel_to' for _,p in plans),2)
        self.assertTrue(any('Договорились' in p.get('params',{}).get('text','') for _,p in plans))
    def test_follow_requires_measured_proximity_not_just_seen_target(self):
        from packages.resident_world.skills import follow_verified
        self.assertTrue(follow_verified([{'near':True}]*7+[{'near':False}]*3))
        self.assertFalse(follow_verified([{'near':True}]*6+[{'near':False}]*4))
        self.assertFalse(follow_verified([]))
    def test_walk_has_one_global_minute_including_retries(self):
        from packages.resident_world.skills import execution_deadline
        self.assertEqual(execution_deadline('walk_to',100),160)
        self.assertEqual(execution_deadline('follow',100),1300)
    def test_town_service_allows_cross_map_route_without_short_walk_budget(self):
        c={'places':{},'residents':[]}
        p=native_plan('town_service',{},c)
        self.assertEqual(p['skill'],'travel_to')
        self.assertEqual(p['params']['map'],'prt_in')
        self.assertEqual(p['timeout'],240)
    def test_status_displays_live_manual_goal_instead_of_old_brain_goal(self):
        from packages.resident_world.cli import status_intent
        self.assertEqual(status_intent({'activity':'hunt','reason':'старое'},{'action':'idle'},True),('idle','Ручная проверка навыка'))
        self.assertEqual(status_intent({'activity':'hunt','reason':'старое'},{'action':'idle','ended':100},False),('hunt','старое'))
    def test_brain_does_not_dispatch_while_another_writer_holds_resident_lock(self):
        import tempfile,pathlib,fcntl
        from unittest.mock import patch
        from packages.resident_world import supervisor
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);d=root/'r';d.mkdir();lock=(d/'manual.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            dispatched=[]
            def rpc(sock,m):
                if m['op']=='status':
                    (root/'STOP').touch();return {'age':0,'actions':[],'telemetry':{'map':'prt_fild08','hp':100,'hp_max':100,'zeny':100,'inventory':{'501':10}}}
                dispatched.append(m);return {'action_id':'unexpected'}
            with patch.object(supervisor,'STATE',root),patch.object(supervisor,'request',side_effect=rpc),patch.object(supervisor.time,'sleep'):
                supervisor.brain({'resident_id':'r'})
            lock.close();self.assertEqual(dispatched,[])
    def test_idle_rejects_fresh_position_when_body_is_still_hunting(self):
        import tempfile,pathlib
        from unittest.mock import patch
        from packages.resident_world import skills
        s={'age':0,'telemetry':{'mode':'HUNT'},'projection':{'server_pos':{'ts':100,'value':{'x':10,'y':10}}}}
        with tempfile.TemporaryDirectory() as tmp,patch.object(skills,'request',return_value=s),patch.object(skills.time,'time',return_value=100),patch.object(skills.time,'monotonic',side_effect=[0,61]):
            result=skills.execute_native_menu('idle',{'minutes':1},{},'sock','key',{},pathlib.Path(tmp))
        self.assertEqual(result,'failed(IDLE_NOT_SAFE)')
    def test_menu_rejects_unknown_actions_and_extra_args(self):
        for action,args in [('shell',{}),('say',{'text':'Привет','command':'rm'}),('hunt',{'map':'bad','minutes':5}),('idle',{'minutes':True})]:
            with self.assertRaises(ValueError):validate(action,args,{'places':{},'residents':[]})
    def test_speech_is_russian_bounded_and_no_empty_promises(self):
        c={'places':{},'residents':[]}
        for text in ['x'*81,'/kick','Привет\nпока','Hello','Я иду к тебе']:
            with self.assertRaises(ValueError):validate('say',{'text':text},c)
        self.assertEqual(validate('say',{'text':'Привет!'},c),{'text':'Привет!'})
    def test_only_server_confirmation_is_done(self):
        self.assertEqual(outcome({'state':'confirmed','code':'ARRIVED'}),'done')
        self.assertIsNone(outcome({'state':'accepted','code':'ACK'}))
        self.assertIsNone(outcome({'state':'unknown','code':'VERIFY_TIMEOUT'}))
        self.assertEqual(outcome({'state':'timed_out','code':'TIMEOUT'}),'timeout')
        self.assertEqual(outcome({'state':'failed','code':'NO_ROUTE'}),'failed(NO_ROUTE)')
    def test_economic_uncertainty_never_gets_a_blind_retry(self):
        self.assertFalse(retry_allowed('town_service',1,'failed(NO_EVIDENCE)'))
        self.assertTrue(retry_allowed('walk_to',1,'failed(NO_ROUTE)'))
        self.assertFalse(retry_allowed('walk_to',3,'failed(NO_ROUTE)'))
    def test_walk_uses_allowlisted_named_place_and_bounded_native_timeout(self):
        c={'places':{'fountain':{'map':'prontera','x':156,'y':191}},'residents':[]}
        p=native_plan('walk_to',validate('walk_to',{'place':'fountain'},c),c)
        self.assertEqual(p['skill'],'travel_to');self.assertEqual(p['params']['x'],156)
        self.assertLessEqual(p['timeout'],60)
    def test_hunt_has_real_minimum_duration_and_server_kill_requirement(self):
        c={'places':{},'residents':[]}
        p=native_plan('hunt',validate('hunt',{'map':'prt_fild08','minutes':10},c),c)
        self.assertEqual(p['params']['duration'],600);self.assertEqual(p['params']['min_kills'],1)
