"""Callable algorithms: offline tests, no provider or live gameplay."""
import pathlib,tempfile,unittest
from unittest.mock import patch
from packages.resident_world import skills,mind
from tests import test_behavior_native as native_fixture
from tests.test_world_mind_brain import C,HEALTHY as BASE_HEALTHY

# Successful action fixtures model the gateway's explicitly fresh status.
HEALTHY={**BASE_HEALTHY,'age':0}

class ActionInterfaceTests(unittest.TestCase):
    def test_solo_is_discoverable_validated_model_callable_and_native(self):
        self.assertIn('hunt_solo',skills.MENU)
        args={'map':'prt_fild08','minutes':5}
        self.assertEqual(skills.validate('hunt_solo',args,C),args)
        with self.assertRaises(ValueError):skills.validate('hunt_solo',dict(args,minutes=True),C)
        self.assertIn('hunt_solo',mind.DECISION_SCHEMA['properties']['action']['enum'])
        decision,_=mind.normalize_decision({'action':'hunt_solo',**args,'reason':'Опыт'},C,HEALTHY)
        self.assertEqual(decision['args'],args)
        with tempfile.TemporaryDirectory() as tmp:
            d=pathlib.Path(tmp)/'resident_b'
            c={**C,'residents':[{'resident_id':d.name,'support_target':'Borin'}]}
            with patch.object(skills,'request',return_value={'age':0,'telemetry':{'map':'prt_fild08'}}),patch.object(skills,'run_native',return_value='done') as run:
                self.assertEqual(skills.execute_native_menu('hunt_solo',args,c,'sock','key',{},d),'done')
                self.assertEqual(run.call_args.args[1]['params']['solo'],True)
                self.assertNotIn('support_target',run.call_args.args[1]['params'])

    def test_solo_hunt_rechecks_eligibility_after_confirmed_field_route(self):
        args={'map':'prt_fild08','minutes':5}
        c={**C,'residents':[{'resident_id':'resident_b','name':'Mira','support_target':'Borin'}]}
        initial={**HEALTHY,'telemetry':{**HEALTHY['telemetry'],'map':'prontera'}}
        arrived={**initial,'telemetry':{**initial['telemetry'],'map':'prt_fild08'}}
        cases=[(arrived,'done'),
               ({**arrived,'age':None},'failed(STALE_BODY)'),
               ({**arrived,'telemetry':{**arrived['telemetry'],'dead':True}},'failed(DEAD)'),
               ({**arrived,'telemetry':{**arrived['telemetry'],'weight':70,'weight_max':100}},'failed(OVERWEIGHT_HUNT)'),
               ({**arrived,'telemetry':{**arrived['telemetry'],'hp':59,'hp_max':100}},'failed(INSUFFICIENT_HUNT_RESOURCES)')]
        for final,expected in cases:
            with self.subTest(expected=expected),tempfile.TemporaryDirectory() as tmp:
                with patch.object(skills,'request',side_effect=[initial,initial,final]) as rpc,patch.object(skills,'run_native',return_value='done') as run:
                    self.assertEqual(skills.execute_native_menu('hunt_solo',args,c,'sock','key',{},pathlib.Path(tmp)/'resident_b'),expected)
                    self.assertEqual(rpc.call_count,3)
                    self.assertEqual(run.call_count,2 if expected=='done' else 1)
                    self.assertEqual(run.call_args_list[0].args[1]['skill'],'travel_to')
                    if expected=='done':
                        self.assertTrue(run.call_args.args[1]['params']['solo'])
                        self.assertNotIn('support_target',run.call_args.args[1]['params'])
        # Failed travel never starts combat or requests post-route telemetry.
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(skills,'request',return_value=initial) as rpc,patch.object(skills,'run_native',return_value='timeout') as run:
                self.assertEqual(skills.execute_native_menu('hunt_solo',args,c,'sock','key',{},pathlib.Path(tmp)/'resident_b'),'timeout')
                self.assertEqual(rpc.call_count,2);self.assertEqual(run.call_count,1)

    def test_native_confirmation_after_task_deadline_is_not_goal_success(self):
        # Slow gateway reads can return a terminal ACK after the caller's lease.
        # Keep the actual terminal record, particularly for economics, but never
        # claim that the bounded goal finished within its execution window.
        for skill,params in (('travel_to',{'map':'prontera','x':156,'y':191,'r':3}),
                             ('sell_loot',{'category':'loot'})):
            for completed_at in (109,110,111):
                with self.subTest(skill=skill,completed_at=completed_at),tempfile.TemporaryDirectory() as tmp:
                    d=pathlib.Path(tmp);task={'id':'bounded-native','expires':110}
                    clock={'now':100}
                    calls=[]
                    def rpc(sock,payload):
                        calls.append(payload)
                        if payload['op']=='run':return {'action_id':'native-one','state':'running'}
                        clock['now']=completed_at
                        return {'action_id':'native-one','state':'confirmed','code':'OK'}
                    with patch.object(skills,'request',side_effect=rpc),patch.object(skills.time,'time',side_effect=lambda:clock['now']),patch.object(skills.time,'monotonic',return_value=0):
                        result=skills.run_native('sock',{'skill':skill,'params':params,'timeout':40},'single-dispatch',task,d)
                    self.assertEqual(result,'done' if completed_at<110 else 'timeout')
                    self.assertEqual([p['op'] for p in calls],['run','action'])
                    self.assertEqual(task['native_results'][0]['outcome'],'done')
                    self.assertEqual(task['native_results'][0]['at'],completed_at)
                    self.assertNotIn('unresolved',task)  # Native terminal is known, not a blind retry.
                    if completed_at>=110:
                        self.assertFalse(skills.retry_allowed('native_control',1,result))
                        self.assertFalse(skills.retry_allowed('sell_loot',1,result))

    def test_new_actions_reject_unknown_or_invalid_body_age_before_dispatch(self):
        from packages.resident_world import actions
        args={'skill':'travel_to','params':{'map':'prontera','x':156,'y':191,'r':3}}
        for age in (None,True,'0',-1,float('nan'),float('inf'),4.01):
            with self.subTest(age=age):
                state={**HEALTHY,'age':age}
                self.assertEqual(actions.unavailable('native_control',args,C,state),'STALE_BODY')
                self.assertFalse(actions.discover(C,state)['actions']['travel_to']['available'])
                with tempfile.TemporaryDirectory() as tmp:
                    with patch.object(skills,'request',return_value=state),patch.object(skills,'run_native') as run:
                        self.assertEqual(skills.execute_native_menu('native_control',args,C,'sock','key',{},pathlib.Path(tmp)),'failed(STALE_BODY)')
                        run.assert_not_called()
        for age in (0,0.5,4):
            self.assertIsNone(actions.unavailable('native_control',args,C,{**HEALTHY,'age':age}))

    def test_native_whisper_runtime_and_model_restrict_targets_to_own_recent_contacts(self):
        import json,sqlite3,time
        c={**C,'residents':[{'resident_id':'resident_b','name':'Mira'},
                          {'resident_id':'resident_a','name':'Borin'},
                          {'resident_id':'disabled','name':'Disabled','enabled':False}]}
        now=time.time()
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);d=root/'resident_b';d.mkdir()
            with sqlite3.connect(root/'chat.sqlite') as db:
                db.execute('CREATE TABLE inbox(id INTEGER PRIMARY KEY,payload TEXT,resident TEXT,status TEXT,result TEXT)')
                # Sixteen more recent turns exclude an older contact even within 30min.
                db.execute('INSERT INTO inbox(payload,resident,status) VALUES(?,?,?)',
                           (json.dumps({'name':'Evicted','ts':now-60}),'Mira','done'))
                for _ in range(16):
                    db.execute('INSERT INTO inbox(payload,resident,status) VALUES(?,?,?)',
                               (json.dumps({'name':'Recent','ts':now-30}),'Mira','done'))
                # Insert invalid contacts last so each predicate, not just the limit, excludes them.
                for target,resident,status,ts in (
                    ('Old','Mira','done',now-1801),('Ignored','Mira','ignored',now-60),
                    ('Expired','Mira','expired',now-60),('Foreign','Borin','done',now-60),
                    ('Future','Mira','done',now+60)):
                    db.execute('INSERT INTO inbox(payload,resident,status) VALUES(?,?,?)',
                               (json.dumps({'name':target,'ts':ts}),resident,status))
            for target in ('Mira','Stranger','Disabled','Old','Ignored','Expired','Foreign','Future','Evicted','Tester','Borin','Recent'):
                args={'skill':'whisper','params':{'to_name':target,'text':'Привет!'}}
                permitted=target in ('Tester','Borin','Recent')
                with self.subTest(target=target),patch.object(skills,'STATE',root),patch.object(skills,'request',return_value={**HEALTHY,'_known':['Stranger']}),patch.object(skills,'run_native',return_value='done') as run:
                    actual=skills.execute_native_menu('native_control',args,c,'sock','key',{},d)
                    self.assertEqual(actual,'done' if permitted else 'failed(WHISPER_TARGET)')
                    self.assertEqual(run.call_count,int(permitted))
                if permitted:
                    decision,_=mind.normalize_decision({'action':'native_control',**args,'reason':'Привет'},c,HEALTHY,known=('Recent',),me='Mira')
                    self.assertEqual(decision['args'],args)
                else:
                    with self.assertRaisesRegex(ValueError,'WHISPER_TARGET'):
                        mind.normalize_decision({'action':'native_control',**args,'reason':'Привет'},c,HEALTHY,known=('Recent',),me='Mira')
            # No durable inbox: nearby and enabled residents remain usable, recent stranger fails closed.
            (root/'chat.sqlite').unlink()
            with patch.object(skills,'STATE',root),patch.object(skills,'request',return_value=HEALTHY),patch.object(skills,'run_native') as run:
                self.assertEqual(skills.execute_native_menu('native_control',{'skill':'whisper','params':{'to_name':'Recent','text':'Привет!'}},c,'sock','key',{},d),'failed(WHISPER_TARGET)')
                run.assert_not_called()

    def test_party_search_never_offers_to_self_even_with_operator_consent(self):
        from packages.resident_world import actions
        c={**C,'residents':[{'resident_id':'resident_b','name':'Mira',
                           'party_search_consent':['Mira','Tester']}]}
        self_player={'name':'Mira','pos':{'x':100,'y':100}}
        other={'name':'Tester','pos':{'x':102,'y':100}}
        t={**HEALTHY['telemetry'],'pos':{'x':100,'y':100},
           'nearby_players':[self_player,other]}
        for identity in ({'_resident_id':'resident_b'},{'_me':'Mira'}):
            with self.subTest(identity=identity):
                s={**HEALTHY,**identity,'telemetry':t}
                self.assertEqual([p['name'] for p in actions.party_candidates(c,s)],['Tester'])
                self.assertIsNone(actions.unavailable('find_party',{'minutes':1},c,s))
        self_only={**HEALTHY,'_resident_id':'resident_b',
                   'telemetry':{**t,'nearby_players':[self_player]}}
        self.assertEqual(actions.unavailable('find_party',{'minutes':1},c,self_only),'NO_CONSENTING_CANDIDATES')
        self.assertFalse(actions.discover(c,self_only)['actions']['find_party']['available'])
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(skills,'request',return_value=self_only),patch.object(skills,'run_native') as run:
                self.assertEqual(skills.execute_native_menu('find_party',{'minutes':1},c,'sock','key',{},pathlib.Path(tmp)/'resident_b'),'failed(NO_CONSENTING_CANDIDATES)')
                run.assert_not_called()

    def test_catalog_support_guard_and_explicit_unavailable_heal(self):
        self.assertTrue(hasattr(skills,'discover'),'catalog missing')
        from packages.resident_world import actions
        catalog=actions.discover(C,HEALTHY)
        self.assertEqual(catalog['version'],1)
        self.assertEqual(set(catalog['actions']),set(skills.MENU))
        self.assertFalse(catalog['actions']['heal']['available'])
        self.assertIn('CAST_RESULT',catalog['actions']['heal']['reason'])
        args={'player':'Borin','map':'prt_fild08','minutes':5}
        c={**C,'residents':[{'resident_id':'resident_b','name':'Mira','support_target':'Borin'}]}
        s={'age':0,'_resident_id':'resident_b','telemetry':{'map':'prt_fild08','job_level':10,'learned_skills':{'AL_HEAL':3},'party':{'source':'native_client','share':1,'members':[{'name':'Borin','online':True}]}}}
        self.assertIsNone(actions.unavailable('support',args,c,s))
        with tempfile.TemporaryDirectory() as tmp:
            d=pathlib.Path(tmp)/'resident_b'
            with patch.object(skills,'request',return_value=s),patch.object(skills,'run_native',return_value='done') as run:
                self.assertEqual(skills.execute_native_menu('support',args,c,'sock','key',{},d),'done')
                self.assertEqual(run.call_args.args[1]['params']['support_target'],'Borin')
            with patch.object(skills,'request',return_value={**s,'telemetry':{**s['telemetry'],'party':None}}),patch.object(skills,'run_native') as run:
                self.assertEqual(skills.execute_native_menu('support',args,c,'sock','key',{},d),'failed(INVALID_SUPPORT_PARTY)')
                run.assert_not_called()
        for change in ({'behavior_v1':True},{'learned_skills':{'AL_HEAL':0}}):
            self.assertIsNotNone(actions.unavailable('support',args,c,{**s,'telemetry':{**s['telemetry'],**change}}))

    def test_sales_and_supplies_are_separate_bounded_no_retry(self):
        self.assertIn('sell_equipment',skills.MENU)
        from packages.resident_world import actions
        for action in ('sell_loot','sell_equipment','buy_potions'):
            self.assertFalse(skills.retry_allowed(action,1,'timeout'))
        with self.assertRaises(ValueError):skills.validate('buy_potions',{'stock_goal':21,'max_zeny':300},C)
        for action,category in (('sell_loot','loot'),('sell_equipment','equipment')):
            with tempfile.TemporaryDirectory() as tmp:
                d=pathlib.Path(tmp)
                s={'age':0,'telemetry':{'map':'prt_in','inventory':{'909':3},'sellable_by_category':{category:{'909':3}}}}
                with patch.object(skills,'request',return_value=s),patch.object(skills,'run_native',return_value='done') as run:
                    self.assertEqual(skills.execute_native_menu(action,{},C,'sock','key',{},d),'done')
                    self.assertEqual(run.call_args.args[1]['params'],{'category':category})
        s={'age':0,'telemetry':{'inventory':{},'sellable_by_category':{'equipment':{}}}}
        self.assertEqual(actions.unavailable('sell_equipment',{},C,s),'NO_SELLABLE_ITEMS')

    def test_economic_actions_recheck_eligibility_after_confirmed_shop_route(self):
        initial={'age':0,'telemetry':{'map':'prontera','zeny':300,'inventory':{},
                 'sellable_by_category':{'loot':{'909':3},'equipment':{'1201':1}}}}
        arrived={**initial,'telemetry':{**initial['telemetry'],'map':'prt_in'}}
        for action in ('sell_loot','sell_equipment','buy_potions'):
            args={'stock_goal':20,'max_zeny':300} if action=='buy_potions' else {}
            changed=({'inventory':{'501':20}} if action=='buy_potions' else
                     {'sellable_by_category':{'loot':{},'equipment':{}}})
            depleted={**arrived,'telemetry':{**arrived['telemetry'],**changed}}
            denied='STOCK_ALREADY_MET' if action=='buy_potions' else 'NO_SELLABLE_ITEMS'
            cases=[(arrived,'done'),({**arrived,'age':None},'failed(STALE_BODY)'),
                   (depleted,'failed('+denied+')'),
                   ({**arrived,'telemetry':{**arrived['telemetry'],'dead':True}},'failed(DEAD)')]
            for final,expected in cases:
                with self.subTest(action=action,expected=expected),tempfile.TemporaryDirectory() as tmp:
                    with patch.object(skills,'request',side_effect=[initial,final]) as rpc,patch.object(skills,'run_native',return_value='done') as run:
                        self.assertEqual(skills.execute_native_menu(action,args,C,'sock','key',{},pathlib.Path(tmp)),expected)
                        self.assertEqual(rpc.call_count,2)
                        self.assertEqual(run.call_count,2 if expected=='done' else 1)
                        self.assertEqual(run.call_args_list[0].args[1]['skill'],'travel_to')
            # An unconfirmed route never attempts an economic operation or refresh.
            with tempfile.TemporaryDirectory() as tmp:
                with patch.object(skills,'request',return_value=initial) as rpc,patch.object(skills,'run_native',return_value='timeout') as run:
                    self.assertEqual(skills.execute_native_menu(action,args,C,'sock','key',{},pathlib.Path(tmp)),'timeout')
                    self.assertEqual(rpc.call_count,1);self.assertEqual(run.call_count,1)

    def test_free_native_control_has_schema_allowlist_and_no_console(self):
        self.assertIn('native_control',skills.MENU)
        from packages.resident_world import actions
        args={'skill':'travel_to','params':{'map':'prontera','x':156,'y':191,'r':3}}
        self.assertEqual(skills.validate('native_control',args,C),args)
        for bad in ({'skill':'console','params':{'text':'quit'}}, {'skill':'travel_to','params':{**args['params'],'x':True}}, {'skill':'travel_to','params':{**args['params'],'console':'quit'}}, {'skill':'travel_to','params':{**args['params'],'map':'unknown'}}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):skills.validate('native_control',bad,C)
        self.assertIn('skill',mind.ARG_KEYS)
        self.assertIn('params',mind.DECISION_SCHEMA['properties'])
        decision,_=mind.normalize_decision({'action':'native_control',**args,'reason':'Прогулка'},C,HEALTHY)
        self.assertEqual(decision['args'],args)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(skills,'request',return_value=HEALTHY),patch.object(skills,'run_native',return_value='timeout') as run:
                self.assertEqual(skills.execute_native_menu('native_control',args,C,'sock','key',{},pathlib.Path(tmp)),'timeout')
                self.assertEqual(run.call_args.args[1]['skill'],'travel_to')
        self.assertFalse(skills.retry_allowed('native_control',1,'timeout'))
        # v1: the catalog is offered to the model only when the host lists it in llm.menu.
        self.assertNotIn('native_control',mind.Mind({'name':'Mira'},pathlib.Path('/unused'),C,client=object()).system_prompt())
        wide={**C,'llm':{**C.get('llm',{}),'menu':[*mind.CORE_MENU,'native_control']}}
        self.assertIn('native_control',mind.Mind({'name':'Mira'},pathlib.Path('/unused'),wide,client=object()).system_prompt())
        self.assertTrue(mind.availability(C,HEALTHY)['heal'].startswith('нельзя'))
        with self.assertRaisesRegex(ValueError,'CAST_RESULT'):
            mind.normalize_decision({'action':'heal','player':'Tester','reason':'Помочь'},C,HEALTHY)

    def test_respawn_and_coordinate_travel_are_callable(self):
        self.assertIn('respawn',skills.MENU)
        self.assertIn('travel_to',skills.MENU)
        args={'map':'prontera','x':156,'y':191,'r':3}
        self.assertEqual(skills.validate('travel_to',args,C),args)
        from packages.resident_world import actions
        dead={'age':0,'telemetry':{'dead':True,'behavior_v1':False}}
        self.assertIsNone(actions.unavailable('respawn',{},C,dead))
        self.assertEqual(actions.unavailable('respawn',{},C,HEALTHY),'NOT_DEAD')
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(skills,'request',return_value=dead),patch.object(skills,'run_native',return_value='done') as run:
                self.assertEqual(skills.execute_native_menu('respawn',{},C,'sock','key',{},pathlib.Path(tmp)),'done')
                self.assertEqual(run.call_args.args[1]['skill'],'respawn')

    def test_party_hunt_requires_native_party_and_dispatches_guarded_plan(self):
        self.assertIn('hunt_party',skills.MENU)
        args={'player':'Borin','map':'prt_fild08','minutes':5}
        s={'age':0,'telemetry':{**HEALTHY['telemetry'],'map':'prt_fild08','party':{'source':'native_client','share':1,'members':[{'name':'Borin','online':True}]}}}
        from packages.resident_world import actions
        self.assertIsNone(actions.unavailable('hunt_party',args,C,s))
        self.assertEqual(actions.unavailable('hunt_party',args,C,HEALTHY),'INVALID_PARTY')
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(skills,'request',return_value=s),patch.object(skills,'run_native',return_value='done') as run:
                self.assertEqual(skills.execute_native_menu('hunt_party',args,C,'sock','key',{},pathlib.Path(tmp)),'done')
                self.assertEqual(run.call_args.args[1]['params']['party_target'],'Borin')
                self.assertTrue(run.call_args.args[1]['params']['solo'])

    def test_cooperative_hunt_rechecks_eligibility_after_confirmed_travel(self):
        args={'player':'Borin','map':'prt_fild08','minutes':5}
        c={**C,'residents':[{'resident_id':'resident_b','name':'Mira','support_target':'Borin'}]}
        initial={'age':0,'telemetry':{**HEALTHY['telemetry'],'map':'prontera','job_level':10,'learned_skills':{'AL_HEAL':3},'party':{'source':'native_client','share':1,'members':[{'name':'Borin','online':True}]}}}
        arrived={**initial,'telemetry':{**initial['telemetry'],'map':'prt_fild08'}}
        cases=[(arrived,'done'),
               ({**arrived,'age':None},'failed(STALE_BODY)'),
               ({**arrived,'telemetry':{**arrived['telemetry'],'party':None}},'failed(INVALID_SUPPORT_PARTY)'),
               ({**arrived,'telemetry':{**arrived['telemetry'],'learned_skills':{'AL_HEAL':0}}},'failed(NO_LEARNED_HEAL)')]
        for action in ('support','hunt_party'):
            for final,expected in cases:
                if action=='hunt_party' and expected=='failed(INVALID_SUPPORT_PARTY)':expected='failed(INVALID_PARTY)'
                with self.subTest(action=action,expected=expected),tempfile.TemporaryDirectory() as tmp:
                    d=pathlib.Path(tmp)/'resident_b'
                    with patch.object(skills,'request',side_effect=[initial,final]) as rpc,patch.object(skills,'run_native',return_value='done') as run:
                        self.assertEqual(skills.execute_native_menu(action,args,c,'sock','key',{},d),expected)
                        self.assertEqual(rpc.call_count,2)
                        self.assertEqual(run.call_count,2 if expected=='done' else 1)
                        self.assertEqual(run.call_args_list[0].args[1]['skill'],'travel_to')
        # Frontline party hunt must also reject membership lost during the route.
        frontline={**C,'residents':[{'resident_id':'resident_b','name':'Mira'}]}
        final={**arrived,'telemetry':{**arrived['telemetry'],'party':None}}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(skills,'request',side_effect=[initial,final]),patch.object(skills,'run_native',return_value='done') as run:
                self.assertEqual(skills.execute_native_menu('hunt_party',args,frontline,'sock','key',{},pathlib.Path(tmp)/'resident_b'),'failed(INVALID_PARTY)')
                self.assertEqual(run.call_count,1)
        # A failed route must never dispatch hunt or request a second status.
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(skills,'request',return_value=initial) as rpc,patch.object(skills,'run_native',return_value='timeout') as run:
                self.assertEqual(skills.execute_native_menu('support',args,c,'sock','key',{},pathlib.Path(tmp)/'resident_b'),'timeout')
                self.assertEqual(rpc.call_count,1);self.assertEqual(run.call_count,1)

    def test_find_party_contacts_only_consenting_candidates_once_and_waits_for_native_party(self):
        self.assertIn('find_party',skills.MENU)
        from packages.resident_world import actions
        c={**C,'residents':[{'resident_id':'resident_b','name':'Mira','party_search_consent':['Tester']}]}
        s={**HEALTHY,'age':0,'_resident_id':'resident_b'}
        self.assertEqual([p['name'] for p in actions.party_candidates(c,s)],['Tester'])
        import time
        joined={**s,'projection':{'server_map':{'value':s['telemetry']['map']},'server_pos':{'value':s['telemetry']['pos'],'ts':time.time()}},'telemetry':{**s['telemetry'],'party':{'source':'native_client','share':1,'members':[{'name':'Tester','online':True}]}}}
        for final,expected in ((s,'failed(PARTY_NOT_CONFIRMED)'),(joined,'done')):
            with tempfile.TemporaryDirectory() as tmp:
                d=pathlib.Path(tmp)/'resident_b';d.mkdir()
                with patch.object(skills,'request',side_effect=[s,final]),patch.object(skills,'run_native',return_value='done') as run,patch.object(skills.time,'monotonic',side_effect=[0,0,61]),patch.object(skills.time,'sleep'):
                    self.assertEqual(skills.execute_native_menu('find_party',{'minutes':1},c,'sock','key',{},d),expected)
                    self.assertEqual(run.call_count,1)
                    self.assertEqual(run.call_args.args[1]['skill'],'whisper')
                    self.assertEqual(run.call_args.args[1]['params']['to_name'],'Tester')
        self.assertEqual(actions.party_candidates(C,s),[])
        self.assertIn('NO_NATIVE_PARTY_INVITE',actions.unavailable('party_invite',{'player':'Tester'},C,s))

    def test_party_search_never_confirms_membership_after_task_deadline(self):
        c={**C,'residents':[{'resident_id':'resident_b','name':'Mira','party_search_consent':['Tester']}]}
        initial={**HEALTHY,'age':0}
        joined={**initial,'projection':{'server_map':{'value':initial['telemetry']['map']},'server_pos':{'value':initial['telemetry']['pos'],'ts':1000}},'telemetry':{**initial['telemetry'],'party':{'source':'native_client','share':1,'members':[{'name':'Tester','online':True}]}}}
        for stage in ('offer','poll','within_deadline'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as tmp:
                d=pathlib.Path(tmp)/'resident_b';d.mkdir()
                clock=[1000]
                def status(sock,payload):
                    if rpc.call_count==1:return initial
                    if stage=='poll':clock[0]=1001
                    return joined
                def offer(*args):
                    if stage=='offer':clock[0]=1001
                    return 'done'
                task={'id':'party-deadline','expires':1001}
                with patch.object(skills,'request',side_effect=status) as rpc,patch.object(skills,'run_native',side_effect=offer) as run,patch.object(skills.time,'time',side_effect=lambda:clock[0]),patch.object(skills.time,'monotonic',side_effect=[0,0,61]),patch.object(skills.time,'sleep'):
                    self.assertEqual(skills.execute_native_menu('find_party',{'minutes':1},c,'sock','key',task,d),'done' if stage=='within_deadline' else 'timeout')
                    self.assertEqual(rpc.call_count,1 if stage=='offer' else 2)
                    self.assertEqual(run.call_count,1)
                    self.assertEqual('native_party_confirmation' in task,stage=='within_deadline')

    def test_status_cancel_and_invoke_replay_are_task_id_scoped(self):
        self.assertTrue(hasattr(skills,'action_status'),'callable task status missing')
        import json
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);d=root/'resident_b';d.mkdir()
            with patch.object(skills,'STATE',root),patch.object(skills,'config',return_value=C),patch.object(skills,'manual',return_value=nullcontext((d,'sock'))),patch.object(skills,'execute_native_menu',return_value='done') as run:
                first=skills.execute('Mira','sell_equipment',{},task_id='sale-one')
                again=skills.execute('Mira','sell_equipment',{},task_id='sale-one')
                self.assertEqual(first,again);self.assertEqual(run.call_count,1)
                with self.assertRaisesRegex(ValueError,'ACTION_ID_CONFLICT'):skills.execute('Mira','sell_loot',{},task_id='sale-one')
                self.assertEqual(skills.action_status('Mira','sale-one')['state'],'done')
                for bad in ('../evil','/tmp/x',''):
                    with self.assertRaises(ValueError):skills.action_status('Mira',bad)
                task={'id':'walking','action':'travel_to','args':{},'expires':9999999999,'outcome':None,'native_action':'native-1'}
                skills.persist_task(task,d)
                with patch.object(skills,'request',return_value={'state':'running','skill':'travel_to','action_id':'native-1'}) as rpc:
                    result=skills.cancel_action('Mira','walking')
                    self.assertEqual(result['state'],'cancel_requested')
                    self.assertTrue(skills.interrupted(task,'travel_to',d))
                    self.assertTrue(any(call.args[1].get('op')=='cancel' for call in rpc.call_args_list))
                self.assertFalse(skills.interrupted({'id':'other','origin':'chat'},'travel_to',d))
                with patch.object(skills,'request') as rpc:
                    self.assertEqual(skills.run_native('sock',{'skill':'travel_to','params':{},'timeout':5},'key',task,d),'failed(CANCELLED)')
                    rpc.assert_not_called()
                task={'id':'selling','action':'sell_equipment','args':{},'outcome':None,'native_action':'trade'};skills.persist_task(task,d)
                with patch.object(skills,'request',return_value={'state':'unknown','skill':'sell_loot'}):
                    with self.assertRaisesRegex(BlockingIOError,'ECONOMIC_ACTION_IN_FLIGHT'):skills.cancel_action('Mira','selling')
                self.assertFalse(skills.cancelled(task,d))

    def test_new_model_arguments_and_availability_share_runtime_guards(self):
        from packages.resident_world import actions
        heavy={'age':0,'telemetry':{**HEALTHY['telemetry'],'weight':800,'weight_max':1000}}
        self.assertTrue(mind.availability(C,heavy)['hunt_solo'].startswith('нельзя'))
        args={'skill':'rest','params':{'hp_pct':90}}
        self.assertEqual(actions.unavailable('native_control',args,C,heavy),'OVERWEIGHT')
        with self.assertRaises(ValueError):skills.validate('hunt_solo',{'map':'prt_fild08','minutes':16},C)
        args={'skill':'sell_loot','params':{'category':'equipment'}}
        s={'age':0,'telemetry':{'sellable_by_category':{'loot':{},'equipment':{'2301':1}}}}
        self.assertIsNone(actions.unavailable('native_control',args,C,s))
        c={**C,'residents':[{'name':'Mira','resident_id':'resident_b','support_target':'Borin'}]}
        s={**HEALTHY,'age':0,'telemetry':{**HEALTHY['telemetry'],'job_level':10,'learned_skills':{'AL_HEAL':3},'party':{'source':'native_client','share':1,'members':[{'name':'Borin','online':True}]}}}
        decision,_=mind.normalize_decision({'action':'support','player':'Borin','map':'prt_fild08','minutes':5,'reason':'Поддержать'},c,s,me='Mira')
        self.assertEqual(decision['action'],'support')
        with self.assertRaises(ValueError):mind.normalize_decision({'action':'travel_to','map':'unknown','x':1,'y':2,'r':3,'reason':'Идти'},C,HEALTHY)
        with self.assertRaises(ValueError):mind.normalize_decision({'action':'buy_potions','stock_goal':10,'max_zeny':30,'args':{'console':'quit'},'reason':'Зелья'},C,HEALTHY)

    def test_cli_discover_invoke_status_cancel_json_contract(self):
        import contextlib,io,json,sys
        from packages.resident_world import cli
        def call(argv):
            out=io.StringIO()
            with patch.object(sys,'argv',['ro','actions',*argv]),patch.object(cli,'config',return_value=C),contextlib.redirect_stdout(out):cli.main()
            return json.loads(out.getvalue())
        try:catalog=call(['discover'])
        except SystemExit as error:self.fail('callable actions CLI missing: '+str(error))
        self.assertIn('native_control',catalog['actions'])
        self.assertFalse(catalog['actions']['hunt_solo']['available'])
        with patch.object(skills,'execute',return_value={'id':'call-one','outcome':'done'}) as run:
            self.assertEqual(call(['invoke','Mira','buy_potions','{"stock_goal":10,"max_zeny":200}','call-one'])['outcome'],'done')
            self.assertEqual(run.call_args.kwargs['task_id'],'call-one')
        with patch.object(skills,'action_status',return_value={'state':'active'}) as read:
            self.assertEqual(call(['status','Mira','call-one'])['state'],'active')
            read.assert_called_once_with('Mira','call-one')
        with patch.object(skills,'cancel_action',return_value={'state':'cancel_requested'}) as cancel:
            self.assertEqual(call(['cancel','Mira','call-one'])['state'],'cancel_requested')
            cancel.assert_called_once_with('Mira','call-one')

    def test_invoke_rechecks_id_under_writer_lock_and_preserves_uncertain_status(self):
        self.assertTrue(hasattr(skills,'action_status'))
        import contextlib
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);d=root/'resident_b';d.mkdir()
            saved={'id':'race-sale','resident':'Mira','action':'sell_equipment','args':{},'outcome':'done','ended':1}
            @contextlib.contextmanager
            def writer(r):
                skills.persist_task(saved,d)
                yield d,'sock'
            with patch.object(skills,'STATE',root),patch.object(skills,'config',return_value=C),patch.object(skills,'manual',side_effect=writer),patch.object(skills,'execute_native_menu',return_value='done') as run:
                result=skills.execute('Mira','sell_equipment',{},task_id='race-sale')
                self.assertEqual(result,saved)
                run.assert_not_called()
            saved={**saved,'id':'uncertain','outcome':'timeout','unresolved':True,'native_action':'sale'};skills.persist_task(saved,d)
            with patch.object(skills,'STATE',root),patch.object(skills,'config',return_value=C),patch.object(skills,'request',return_value={'state':'unknown','skill':'sell_loot'}) as rpc:
                status=skills.action_status('Mira','uncertain')
                self.assertEqual(status['state'],'unconfirmed')
                self.assertEqual(status['native']['state'],'unknown')
                with self.assertRaisesRegex(BlockingIOError,'ECONOMIC_ACTION_IN_FLIGHT'):skills.cancel_action('Mira','uncertain')

    def test_native_speech_never_leaks_optional_prelude_and_cancel_waits_for_native_terminal(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);d=root/'resident_b';d.mkdir()
            args={'skill':'whisper','params':{'to_name':'Tester','text':'Привет!'}}
            with patch.object(skills,'STATE',root),patch.object(skills,'config',return_value=C),patch.object(skills,'manual',return_value=nullcontext((d,'sock'))),patch.object(skills,'execute_native_menu',return_value='done'),patch.object(skills,'run_native',return_value='done') as prelude:
                skills.execute('Mira','native_control',args,say='Привет всем!',task_id='private')
                prelude.assert_not_called()
            decision,_=mind.normalize_decision({'action':'native_control',**args,'say':'Привет всем!','reason':'Привет'},C,HEALTHY)
            self.assertNotIn('say',decision)
            task={'id':'cancel-end','resident':'Mira','action':'travel_to','outcome':'failed(CANCELLED)','ended':1,'native_action':'walk'};skills.persist_task(task,d)
            with patch.object(skills,'STATE',root),patch.object(skills,'config',return_value=C):
                with patch.object(skills,'request',return_value={'state':'running','skill':'travel_to'}):self.assertEqual(skills.action_status('Mira','cancel-end')['state'],'cancel_requested')
                with patch.object(skills,'request',return_value={'state':'cancelled','skill':'travel_to'}):self.assertEqual(skills.action_status('Mira','cancel-end')['state'],'cancelled')

    def test_find_party_requires_fresh_self_witness_not_only_native_membership(self):
        c={**C,'residents':[{'resident_id':'resident_b','name':'Mira','party_search_consent':['Tester']}]}
        initial={**HEALTHY,'age':0}
        joined={**initial,'projection':{},'telemetry':{**initial['telemetry'],'party':{'source':'native_client','share':1,'members':[{'name':'Tester','online':True}]}}}
        with tempfile.TemporaryDirectory() as tmp:
            d=pathlib.Path(tmp)/'resident_b';d.mkdir()
            with patch.object(skills,'request',side_effect=[initial,joined]),patch.object(skills,'run_native',return_value='done'),patch.object(skills.time,'monotonic',side_effect=[0,0]):
                self.assertEqual(skills.execute_native_menu('find_party',{'minutes':1},c,'sock','key',{},d),'failed(STALE_WITNESS)')

    def test_find_party_rejects_invalid_poll_age_before_membership_confirmation(self):
        import time
        c={**C,'residents':[{'resident_id':'resident_b','name':'Mira','party_search_consent':['Tester']}]}
        initial={**HEALTHY,'age':0}
        joined={**initial,'projection':{'server_map':{'value':initial['telemetry']['map']},'server_pos':{'value':initial['telemetry']['pos'],'ts':time.time()}},'telemetry':{**initial['telemetry'],'party':{'source':'native_client','share':1,'members':[{'name':'Tester','online':True}]}}}
        for age in (None,True,'0',-1,float('nan'),float('inf'),4.01):
            with self.subTest(age=age),tempfile.TemporaryDirectory() as tmp:
                d=pathlib.Path(tmp)/'resident_b';d.mkdir()
                with patch.object(skills,'request',side_effect=[initial,{**joined,'age':age}]),patch.object(skills,'run_native',return_value='done') as run,patch.object(skills.time,'monotonic',side_effect=[0,0]):
                    task={}
                    self.assertEqual(skills.execute_native_menu('find_party',{'minutes':1},c,'sock','key',task,d),'failed(STALE_BODY)')
                    self.assertNotIn('native_party_confirmation',task)
                    self.assertEqual(run.call_count,1)

    def test_party_hunt_never_silently_changes_a_support_profile_partner(self):
        from packages.resident_world import actions
        c={**C,'residents':[{'resident_id':'resident_b','name':'Mira','support_target':'Borin'}]}
        args={'player':'Other','map':'prt_fild08','minutes':5}
        s={'age':0,'_resident_id':'resident_b','telemetry':{**HEALTHY['telemetry'],'party':{'source':'native_client','share':1,'members':[{'name':'Other','online':True}]}}}
        self.assertEqual(actions.unavailable('hunt_party',args,c,s),'SUPPORT_PROFILE_REQUIRED')

    def test_unsupported_invocation_never_takes_over_a_body(self):
        with patch.object(skills,'config',return_value=C),patch.object(skills,'manual') as writer:
            for action,code in (('heal','NO_NATIVE_CAST_RESULT'),('party_invite','NO_NATIVE_PARTY_INVITE')):
                with self.subTest(action=action),self.assertRaisesRegex(ValueError,code):skills.execute('Mira',action,{'player':'Tester'})
            writer.assert_not_called()

class ActionNativeTests(unittest.TestCase):
    native=native_fixture.BehaviorNativeTests.native
    def test_party_hunt_stops_when_member_leaves_native_party(self):
        result=self.native("$Globals::char->{party}={joined=>1,share=>1,users=>{b=>{name=>'Borin',online=>1}}};start('hunt',{map=>'prt_fild08',duration=>30,party_target=>'Borin',solo=>JSON::PP::true});$Globals::char->{party}{joined}=0;residentBody::pollSkill();print JSON::PP::encode_json(\\@frames);",enabled=False)
        self.assertEqual(result[-1][1].get('code'),'INVALID_PARTY')

    def test_explicit_support_rejects_wrong_profile_target(self):
        result=self.native("$Globals::config{residentSupportTarget}='Borin';$Globals::config{followTarget}='Borin';$Globals::char->{party}={joined=>1,share=>1,users=>{b=>{name=>'Borin',online=>1},c=>{name=>'Other',online=>1}}};start('hunt',{map=>'prt_fild08',duration=>30,support_target=>'Other'});print JSON::PP::encode_json(\\@frames);",enabled=False)
        self.assertEqual(result[-1][1].get('code'),'INVALID_SUPPORT_PARTY')

    def test_sales_preserve_operator_kept_and_valuable_equipment(self):
        result=self.native("$Globals::config{residentKeepItems}='2303, 909';$Globals::char->{items}=[{ID=>'plain',nameID=>2301,amount=>1,type=>4,type_equip=>1,identified=>1},{ID=>'kept',nameID=>2303,amount=>1,type=>4,type_equip=>1},{ID=>'refined',nameID=>2304,amount=>1,type=>4,type_equip=>1,upgrade=>1},{ID=>'carded',nameID=>2305,amount=>1,type=>4,type_equip=>1,cards=>pack('v',4001)},{ID=>'unknown',nameID=>2306,amount=>1,type=>4,type_equip=>1,identified=>0},{ID=>'loot',nameID=>909,amount=>3,type=>3}];print JSON::PP::encode_json(residentBody::telemetryFrame()->{sellable_by_category});",enabled=False)
        self.assertEqual(result,{'equipment':{'2301':1},'loot':{}})

    def test_category_sales_keep_equipment_and_protected_items(self):
        items="[{ID=>'loot',nameID=>909,amount=>3,type=>3},{ID=>'gear',nameID=>2301,amount=>1,type=>4,type_equip=>1},{ID=>'worn',nameID=>2302,amount=>1,type=>4,type_equip=>1,equipped=>1},{ID=>'potion',nameID=>501,amount=>5,type=>0},{ID=>'card',nameID=>4001,amount=>1,type=>6}]"
        for category,expected in (('loot',['loot']),('equipment',['gear'])):
            result=self.native("$Globals::field->{baseName}='prt_in';$Globals::char->{pos_to}={x=>126,y=>76};$Globals::npcsList=bless{items=>[{ID=>'npc',name=>'Tool Dealer#test',pos=>{x=>126,y=>76}}]},'List';$Globals::char->{items}="+items+";start('sell_loot',{category=>'"+category+"'});$Globals::ai_v{npc_talk}{talk}='buy_or_sell';residentBody::pollSkill();$Globals::ai_v{npc_talk}{talk}='sell';residentBody::pollSkill();print JSON::PP::encode_json({sold=>\\@Misc::sold,t=>residentBody::telemetryFrame()});",enabled=False)
            self.assertEqual([i['ID'] for i in result['sold']],expected)
            self.assertEqual(result['t']['sellable_by_category'],{'loot':{'909':3},'equipment':{'2301':1}})

    def test_explicit_solo_disables_profile_support_in_actual_perl(self):
        result=self.native("$Globals::config{residentSupportTarget}='Borin';$Globals::config{followTarget}='Borin';$Globals::char->{party}={joined=>1,users=>{b=>{name=>'Borin'}}};start('hunt',{map=>'prt_fild08',duration=>30,solo=>JSON::PP::true});print JSON::PP::encode_json(\\%Globals::config);",enabled=False)
        self.assertEqual(result['follow'],0)
        self.assertEqual(result['attackAuto'],1)
        self.assertEqual(result['route_randomWalk'],2)
