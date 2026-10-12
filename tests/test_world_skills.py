import unittest
from packages.resident_world.skills import validate, outcome, retry_allowed, native_plan

class WorldSkillsTests(unittest.TestCase):
    def test_speech_action_does_not_repeat_optional_say_or_leak_whisper(self):
        import tempfile,pathlib
        from contextlib import nullcontext
        from unittest.mock import patch
        from packages.resident_world import skills
        c={'residents':[{'name':'Mira','resident_id':'b'}],'places':{'field':{}}}
        for action,args in [('say',{'text':'Привет, сосед!'}),('whisper',{'to':'Borin','text':'Привет, сосед!'}),('walk_to',{'place':'field'})]:
            with self.subTest(action=action),tempfile.TemporaryDirectory() as tmp:
                root=pathlib.Path(tmp);d=root/'b';d.mkdir()
                with patch.object(skills,'STATE',root),patch.object(skills,'config',return_value=c),patch.object(skills,'manual',return_value=nullcontext((d,'sock'))),patch.object(skills,'run_native',return_value='done') as prelude,patch.object(skills,'execute_native_menu',return_value='done') as run:
                    self.assertEqual(skills.execute('Mira',action,args,say='Привет, сосед!')['outcome'],'done')
                    self.assertEqual(prelude.call_count,1 if action=='walk_to' else 0)
                    run.assert_called_once()

    def test_retry_reuses_confirmed_prelude_but_retries_failed_speech(self):
        import tempfile,pathlib
        from contextlib import nullcontext
        from unittest.mock import patch
        from packages.resident_world import skills
        c={'residents':[{'name':'Mira','resident_id':'b'}],'places':{}}
        for speech_results,menu_results,spoken_count in [(['done'],['failed(NO_ROUTE)','done'],1),(['failed(NO_EVIDENCE)','done'],['done'],2)]:
            with self.subTest(speech_results=speech_results),tempfile.TemporaryDirectory() as tmp:
                root=pathlib.Path(tmp);d=root/'b';d.mkdir()
                with patch.object(skills,'STATE',root),patch.object(skills,'config',return_value=c),patch.object(skills,'manual',return_value=nullcontext((d,'sock'))),patch.object(skills.time,'sleep'),patch.object(skills,'run_native',side_effect=speech_results) as prelude,patch.object(skills,'execute_native_menu',side_effect=menu_results) as run:
                    result=skills.execute('Mira','follow',{'player':'Borin','minutes':1},say='Пойдём вместе!')
                    self.assertEqual(result['outcome'],'done')
                    self.assertEqual(prelude.call_count,spoken_count)
                    self.assertEqual(run.call_count,len(menu_results))
                    self.assertEqual(result['prelude_outcome'],'done')

    def test_follow_registers_an_unwitnessed_player_only_for_its_duration(self):
        import tempfile,pathlib
        from unittest.mock import patch
        from packages.resident_world import skills
        calls=[]
        def query(cnf,sql):
            calls.append((pathlib.Path(cnf).name,sql.split()[0]))
            if 'SELECT char_id' in sql:return '150007\n'
            if 'COUNT(*)' in sql:return '0\n'
            return ''
        with tempfile.TemporaryDirectory() as tmp,patch('tools.events.query',side_effect=query),patch('tools.events.DEFAULTS',str(pathlib.Path(tmp)/'reader.cnf')):
            (pathlib.Path(tmp)/'registry.cnf').write_text('[client]\n')
            d=pathlib.Path(tmp)/'b';d.mkdir()
            with patch.object(skills,'follow_player',return_value='done') as walk:
                self.assertEqual(skills.execute_native_menu('follow',{'player':'Owner','minutes':3},{'residents':[]},'sock','k',{'id':'t'},d),'done')
            walk.assert_called_once()
            self.assertEqual(calls,[('reader.cnf','SELECT'),('registry.cnf','SELECT'),('registry.cnf','INSERT'),('registry.cnf','DELETE')])
            calls.clear()
            with patch.object(skills,'follow_player',side_effect=RuntimeError('crash')):
                with self.assertRaises(RuntimeError):skills.execute_native_menu('follow',{'player':'Owner','minutes':3},{'residents':[]},'sock','k',{'id':'t'},d)
            self.assertEqual(calls[-1],('registry.cnf','DELETE'))                 # unregistered even when following crashed
        from packages.resident_world.chat import fallback_reply
        s={'telemetry':{'nearby_players':[{'name':'Vanya'}]}}
        self.assertEqual(fallback_reply('Mira',{'name':'Vanya','text':'пойдём со мной','type':'O'},s)['action'],'follow')
        for text in ('не иди за мной','пожалуйста, не надо идти к фонтану','не пойдём со мной'):
            self.assertEqual(fallback_reply('Mira',{'name':'Vanya','text':text,'type':'O'},s)['action'],'say',text)

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
    def test_meet_moves_both_only_after_model_agreement(self):
        import tempfile,pathlib,time
        from unittest.mock import patch
        from packages.resident_world import skills
        c={'residents':[{'name':'Mira','resident_id':'resident_b'}],'places':{'fountain':{'map':'prontera','x':156,'y':191}}}
        s={'actions':[],'age':1,'projection':{'server_map':{'value':'prontera'},'server_pos':{'value':{'x':156,'y':191},'ts':time.time()}}}
        plans=[]
        def native(sock,plan,key,task,d):plans.append((sock,plan));return 'done'
        with tempfile.TemporaryDirectory() as tmp:
            d=pathlib.Path(tmp);(d/'resident_b').mkdir()
            with patch.object(skills,'STATE',d),patch.object(skills,'request',return_value=s),patch.object(skills,'run_native',side_effect=native),patch.object(skills.time,'sleep'),patch('packages.resident_world.meeting.decide',return_value={'choice':'accept','place':'fountain','reason':'Хочу поговорить'}):
                self.assertEqual(skills.execute_native_menu('meet',{'resident':'Mira','place':'fountain'},c,'self-sock','key',{'id':'key','resident':'ResidentA','native_results':[]},d),'done')
                self.assertEqual(sum(p['skill']=='travel_to' for _,p in plans),2)
            plans.clear()
            with patch.object(skills,'STATE',d),patch.object(skills,'request',return_value=s),patch.object(skills,'run_native',side_effect=native),patch.object(skills.time,'sleep'),patch('packages.resident_world.meeting.decide',return_value={'choice':'decline','place':'fountain','reason':'Хочу отдыхать'}):
                self.assertEqual(skills.execute_native_menu('meet',{'resident':'Mira','place':'fountain'},c,'self-sock','key',{'id':'key','resident':'ResidentA','native_results':[]},d),'failed(MEETING_DECLINED)')
                self.assertFalse(any(p['skill']=='travel_to' for _,p in plans))
    def test_follow_from_afar_walks_to_the_witnessed_player_then_counts_from_the_meeting(self):
        import tempfile,pathlib,time
        from unittest.mock import patch
        from packages.resident_world import skills
        clock={'t':1000.0}
        def body(sock,msg):
            met=clock['t']>=1060
            return {'age':1,'telemetry':{'map':'prontera','pos':{'x':150,'y':150} if met else {'x':100,'y':100},'nearby_players':[{'name':'Owner','pos':{'x':151,'y':151}}] if met else []},
                    'projection':{'server_map':{'value':'prontera'},'server_pos':{'value':{'x':150,'y':150} if met else {'x':100,'y':100},'ts':clock['t']}}}
        def query(defaults,sql):return f"prontera\t151\t151\t{clock['t']}\n"
        def native(sock,plan,key,task,d):clock['t']+=30;return 'timeout'
        def sleep(s):clock['t']+=s
        with tempfile.TemporaryDirectory() as tmp,patch.object(skills,'request',side_effect=body),patch('tools.events.query',side_effect=query),\
             patch.object(skills,'run_native',side_effect=native) as walk,patch.object(skills.time,'sleep',side_effect=sleep),\
             patch.object(skills.time,'time',side_effect=lambda:clock['t']),patch.object(skills.time,'monotonic',side_effect=lambda:clock['t']),\
             patch.object(skills,'interrupted',return_value=False),patch.object(skills,'witness_at',return_value=True):
            task={'id':'t','origin':'brain','action':'follow'}
            self.assertEqual(skills.follow_player({'player':'Owner','minutes':1},{},'sock','k',task,pathlib.Path(tmp),None),'done')
            self.assertEqual(walk.call_args_list[0].args[1]['params'],{'map':'prontera','x':151,'y':151,'r':3})  # by the witness, not by sight
            self.assertTrue(task['met']);self.assertGreaterEqual(task['met_after_s'],60)
            self.assertTrue(all(s['at']>=1060 for s in task['follow_samples']))  # the way there is not proof of following
            clock['t']=-10**6  # never met: give up after APPROACH_SECONDS with an honest code
            task={'id':'t2','origin':'brain','action':'follow'}
            self.assertEqual(skills.follow_player({'player':'Owner','minutes':1},{},'sock','k',task,pathlib.Path(tmp),None),'failed(TARGET_NOT_REACHED)')
            self.assertNotIn('met',task)

    def test_follow_requires_measured_proximity_not_just_seen_target(self):
        from packages.resident_world.skills import follow_verified
        self.assertTrue(follow_verified([{'near':True}]*7+[{'near':False}]*3))
        self.assertFalse(follow_verified([{'near':True}]*6+[{'near':False}]*4))
        self.assertFalse(follow_verified([]))
    def test_meeting_counter_and_unavailable_vote_never_imply_acceptance(self):
        import tempfile,pathlib,time
        from unittest.mock import patch
        from packages.resident_world import skills,meeting
        c={'residents':[{'name':'Mira','resident_id':'b'},{'name':'ResidentA','resident_id':'a'}],'places':{'fountain':{'map':'prontera','x':156,'y':191},'field':{'map':'prt_fild08','x':170,'y':240}}}
        s={'actions':[],'age':1,'projection':{'server_map':{'value':'prt_fild08'},'server_pos':{'value':{'x':170,'y':240},'ts':time.time()}}}
        for answer,expected in [(dict(choice='accept',place='field',reason='Хочу поговорить'),'done'),(dict(choice='decline',place='field',reason='Хочу отдыхать'),'failed(MEETING_DECLINED)'),(None,'failed(MEETING_MODEL_UNAVAILABLE)')]:
            with self.subTest(answer=answer),tempfile.TemporaryDirectory() as tmp:
                root=pathlib.Path(tmp);d=root/'a';d.mkdir();(root/'b').mkdir();plans=[]
                def native(sock,plan,key,task,path):plans.append(plan);return 'done'
                task={'id':'key','resident':'ResidentA','action':'meet','expires':time.time()+300}
                with patch.object(skills,'STATE',root),patch.object(skills,'request',return_value=s),patch.object(skills,'run_native',side_effect=native),patch.object(skills.time,'sleep'),patch.object(meeting,'decide',side_effect=[dict(choice='counter',place='field',reason='На поле ближе'),answer]) as model:
                    self.assertEqual(meeting.execute({'resident':'Mira','place':'fountain'},c,'sock','key',task,d),expected)
                    self.assertEqual(model.call_count,2)
                    routes=[p for p in plans if p['skill']=='travel_to']
                    self.assertEqual(len(routes),2 if expected=='done' else 0)
                    self.assertTrue(all(p['params']['map']=='prt_fild08' for p in routes))
                    # Повтор той же сохранённой договорённости не вызывает новые голоса.
                    self.assertEqual(meeting.execute({'resident':'Mira','place':'fountain'},c,'sock','key',task,d),expected)
                    self.assertEqual(model.call_count,2)

    def test_meeting_with_a_peer_busy_past_our_window_fails_before_any_vote(self):
        # A 15-minute model hunt holds the peer's writer longer than a 5-minute meet window.
        import tempfile,pathlib,json,fcntl
        from unittest.mock import patch
        from packages.resident_world import skills,meeting
        c={'residents':[{'name':'Mira','resident_id':'b'}],'places':{'field':{'map':'prt_fild08','x':170,'y':240}}}
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);d=root/'a';d.mkdir();peer=root/'b';peer.mkdir()
            (peer/'manual_task.json').write_text(json.dumps({'id':'hunt','resident':'Mira','action':'hunt','args':{'minutes':15},'started':990,'expires':1890}))
            s={'actions':[],'age':1,'projection':{}}
            task={'id':'key','resident':'ResidentA','action':'meet','expires':1300}
            with (peer/'manual.lock').open('a') as held:
                fcntl.flock(held,fcntl.LOCK_EX)  # the peer's brain is inside its hunt
                with patch.object(skills,'STATE',root),patch.object(skills,'request',return_value=s),patch.object(skills,'run_native') as native,patch.object(meeting,'decide') as model:
                    self.assertEqual(meeting.execute({'resident':'Mira','place':'field'},c,'sock','key',task,d),'failed(TARGET_BUSY)')
                model.assert_not_called();native.assert_not_called()
                # A short goal that leaves time to answer and walk is worth waiting for;
                # a brain hunt's own lease includes its route to the field, rest/town_service retries.
                for goal,busy in (({'action':'idle','args':{'minutes':3},'expires':2190},False),({'action':'say','args':{'text':'Привет'},'expires':2190},False),
                                  ({'action':'hunt','args':{'minutes':5},'expires':1290},True),({'action':'rest','args':{},'expires':2190},True),
                                  ({'action':'walk_to','args':{'place':'field'},'expires':1050},False),({'action':'town_service','args':{},'expires':2190},True)):
                    with self.subTest(goal=goal):
                        (peer/'manual_task.json').write_text(json.dumps({'id':'g','resident':'Mira','started':990,**goal}))
                        self.assertEqual(meeting.peer_long_goal(peer,1300),busy)
            # A task file left by a crashed brain (nobody holds the writer) never blocks a meeting.
            (peer/'manual_task.json').write_text(json.dumps({'id':'hunt','resident':'Mira','action':'hunt','args':{'minutes':15},'started':990,'expires':1890}))
            self.assertFalse(meeting.peer_long_goal(peer,1300))

    def test_meeting_waits_for_peer_writer_without_repeating_vote(self):
        import tempfile,pathlib
        from contextlib import contextmanager
        from unittest.mock import patch
        from packages.resident_world import skills,meeting
        c={'residents':[{'name':'Mira','resident_id':'b'}],'places':{'field':{'map':'prt_fild08','x':170,'y':240}}}
        for mode,expected in [('release','done'),('chat','failed(CHAT_INTERRUPT)'),('deadline','timeout'),('collision','failed(MEETING_COLLISION)'),('expired_collision','done'),('collision_winner','done')]:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=pathlib.Path(tmp);d=root/'a';d.mkdir();peer=root/'b';peer.mkdir()
                clock=[1000];calls=[];plans=[]
                if 'collision' in mode:
                    import json
                    (peer/'manual_task.json').write_text(json.dumps({'id':'other','resident':'Mira','action':'meet','args':{'resident':'Borin' if mode=='collision_winner' else 'ResidentA','place':'field'},'started':990,'expires':999 if mode=='expired_collision' else 1300}))
                s={'actions':[],'age':1,'projection':{'server_map':{'value':'prt_fild08'},'server_pos':{'value':{'x':170,'y':240},'ts':1000}}}
                @contextmanager
                def writer(*args,**kwargs):
                    calls.append(1)
                    if len(calls)==1 or mode not in ('release','expired_collision','collision_winner'):raise BlockingIOError('WRITER_BUSY')
                    yield peer,'peer-sock'
                def sleep(seconds):
                    if mode=='collision':raise AssertionError('Взаимная встреча не должна ждать свой timeout')
                    if mode=='chat':(peer/'CHAT_PENDING').touch()
                    if mode=='deadline':clock[0]=1296
                def native(sock,plan,*args):plans.append(plan);return 'done'
                task={'id':'key','resident':'Borin' if mode=='collision_winner' else 'ResidentA','action':'meet','expires':1300}
                with patch.object(skills,'STATE',root),patch.object(skills,'request',return_value=s),patch.object(skills,'run_native',side_effect=native),patch.object(skills,'manual',side_effect=writer),patch.object(meeting.time,'time',side_effect=lambda:clock[0]),patch.object(meeting.time,'sleep',side_effect=sleep),patch.object(meeting,'decide',return_value=dict(choice='accept',place='field',reason='Хочу поговорить')) as model:
                    self.assertEqual(meeting.execute({'resident':'Mira','place':'field'},c,'sock','key',task,d),expected)
                    model.assert_called_once()
                self.assertEqual(len([p for p in plans if p['skill']=='travel_to']),2 if mode in ('release','expired_collision','collision_winner') else 0)
                self.assertEqual(task['expires'],1300)

    def test_meeting_decline_is_terminal_even_when_peer_cannot_speak(self):
        import tempfile,pathlib,time
        from contextlib import contextmanager
        from unittest.mock import patch
        from packages.resident_world import skills,meeting
        c={'residents':[{'name':'Mira','resident_id':'b'}],'places':{'field':{}}}
        for mode in ('busy','speech_failed','speech_done'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=pathlib.Path(tmp);d=root/'a';d.mkdir();peer=root/'b';peer.mkdir();plans=[]
                @contextmanager
                def writer(*args,**kwargs):
                    if mode=='busy':raise BlockingIOError('WRITER_BUSY')
                    yield peer,'peer-sock'
                def native(sock,plan,*args):
                    plans.append(plan)
                    return 'failed(NO_EVIDENCE)' if sock=='peer-sock' and mode=='speech_failed' else 'done'
                task={'id':'key','resident':'ResidentA','expires':time.time()+300}
                with patch.object(skills,'STATE',root),patch.object(skills,'request',return_value={'age':1,'actions':[]}),patch.object(skills,'run_native',side_effect=native),patch.object(skills,'manual',side_effect=writer) as arbiter,patch.object(meeting.time,'sleep',side_effect=AssertionError('Отказ не должен ждать writer')) as sleep,patch.object(meeting,'decide',return_value=dict(choice='decline',place='field',reason='Хочу отдыхать')) as model:
                    self.assertEqual(meeting.execute({'resident':'Mira','place':'field'},c,'sock','key',task,d),'failed(MEETING_DECLINED)')
                    arbiter.assert_called_once();model.assert_called_once();sleep.assert_not_called()
                self.assertFalse(any(p['skill']=='travel_to' for p in plans))
                self.assertEqual(task['meeting_reply'],{'choice':'decline','outcome':'not_sent(TARGET_BUSY)' if mode=='busy' else 'failed(NO_EVIDENCE)' if mode=='speech_failed' else 'done'})

    def test_meeting_chat_on_either_side_interrupts_both_routes(self):
        import tempfile
        from packages.resident_world.skills import interrupted
        with tempfile.TemporaryDirectory() as tmp:
            import pathlib
            root=pathlib.Path(tmp);a=root/'a';b=root/'b';a.mkdir();b.mkdir();(b/'CHAT_PENDING').touch()
            task={'origin':'brain','meeting_chat_dirs':[str(a),str(b)]}
            self.assertTrue(interrupted(task,'travel_to',a))
            self.assertTrue(interrupted(task,'travel_to',b))

    def test_replayed_meeting_keeps_persisted_votes_and_finished_task_has_no_side_effects(self):
        import tempfile,pathlib,json,time
        from contextlib import nullcontext
        from unittest.mock import patch
        from packages.resident_world import skills
        c={'residents':[{'name':'Mira','resident_id':'b'},{'name':'Borin','resident_id':'c'}],'places':{'field':{}}}
        args={'resident':'Borin','place':'field'}
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);d=root/'b';d.mkdir()
            old={'id':'same','resident':'Mira','action':'meet','args':args,'origin':'manual','started':time.time()-10,'expires':time.time()+290,'outcome':'done','ended':time.time(),'meeting_votes':{'peer':{'status':'claimed'}}}
            (d/'manual_task.json').write_text(json.dumps(old))
            with patch.object(skills,'config',return_value=c),patch.object(skills,'STATE',root),patch.object(skills,'manual',return_value=nullcontext((d,'sock'))) as writer,patch.object(skills,'execute_native_menu',return_value='failed(MEETING_MODEL_UNAVAILABLE)') as run:
                self.assertEqual(skills.execute('Mira','meet',args,task_id='same'),old)
                writer.assert_not_called();run.assert_not_called()
                del old['ended'];old['outcome']=None
                (d/'manual_task.json').write_text(json.dumps(old))
                recovered=skills.execute('Mira','meet',args,task_id='same')
                self.assertEqual(recovered['meeting_votes'],old['meeting_votes'])
                self.assertEqual(recovered['started'],old['started'])
                self.assertEqual(recovered['expires'],old['expires'])

    def test_walk_has_one_global_minute_including_retries(self):
        from packages.resident_world.skills import execution_deadline
        self.assertEqual(execution_deadline('walk_to',100),160)
        self.assertEqual(execution_deadline('follow',100),1300)
    def test_llm_walk_allows_reachable_cross_map_route_longer_than_one_minute(self):
        import pathlib
        from unittest.mock import patch
        from packages.resident_world import skills
        c={'places':{'fountain':{'map':'prontera','x':156,'y':191}}}
        with patch.object(skills,'run_native',return_value='done') as run:
            self.assertEqual(skills.execute_native_menu('walk_to',{'place':'fountain'},c,'sock','key',{'origin':'brain'},pathlib.Path('/unused')),'done')
        self.assertEqual(run.call_args.args[1]['timeout'],240)
    def test_llm_walk_task_does_not_clip_native_route_to_manual_minute(self):
        import tempfile,pathlib,json,time
        from contextlib import nullcontext
        from unittest.mock import patch
        from packages.resident_world import skills
        c={'residents':[{'name':'Mira','resident_id':'resident_b'}],'places':{'fountain':{'map':'prontera','x':156,'y':191}}}
        with tempfile.TemporaryDirectory() as tmp:
            d=pathlib.Path(tmp);(d/'MANUAL').write_text(json.dumps({'expires':time.time()+7200}))
            with patch.object(skills,'config',return_value=c),patch.object(skills,'STATE',d),patch.object(skills,'manual',return_value=nullcontext((d,'sock'))),patch.object(skills,'execute_native_menu',return_value='done'):
                task=skills.execute('Mira','walk_to',{'place':'fountain'},origin='brain')
        self.assertEqual(task['expires']-task['started'],300)
    def test_llm_hunt_uses_cross_map_route_budget_before_starting_hunt(self):
        import pathlib
        from unittest.mock import patch
        from packages.resident_world import skills
        with patch.object(skills,'request',return_value={'telemetry':{'map':'prt_in'}}),patch.object(skills,'run_native',return_value='done') as run:
            self.assertEqual(skills.execute_native_menu('hunt',{'map':'prt_fild08','minutes':5},{},'sock','key',{'origin':'brain'},pathlib.Path('/unused')),'done')
        self.assertEqual(run.call_args_list[0].args[1]['timeout'],240)
        self.assertEqual(run.call_args_list[1].args[1]['params']['duration'],300)
    def test_town_service_allows_cross_map_route_without_short_walk_budget(self):
        c={'places':{},'residents':[]}
        p=native_plan('town_service',{},c)
        self.assertEqual(p['skill'],'travel_to')
        self.assertEqual(p['params']['map'],'prt_in')
        self.assertEqual(p['timeout'],240)
    def test_status_uses_llm_reason_during_brain_owned_menu_goal(self):
        from packages.resident_world.cli import status_intent
        self.assertEqual(status_intent({'activity':'walk_to','reason':'Хочу прогуляться'},{'action':'walk_to','origin':'brain'},True),('walk_to','Хочу прогуляться'))
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
    def test_explicit_cancellation_never_restarts_the_cancelled_goal(self):
        self.assertFalse(retry_allowed('hunt',1,'failed(CANCELLED)'))
        self.assertFalse(retry_allowed('walk_to',1,'failed(CANCELLED)'))
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
