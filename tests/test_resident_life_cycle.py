import importlib.util
import unittest

FIELD = 'prt_fild08'

def frame(**changes):
    value = dict(map=FIELD, hp=795, hp_max=795, dead=False,
                 inventory={'501':20}, zeny=628)
    value.update(changes)
    return value

class LifeCycleTests(unittest.TestCase):
    def test_without_player_input_resident_selects_hunt(self):
        self.assertIsNotNone(importlib.util.find_spec('packages.resident_demo.life_cycle'),
                             'Persistent autonomous life cycle is missing')
        from packages.resident_demo.life_cycle import choose
        action = choose('hunt', frame(), hunt_seconds=60)
        self.assertEqual(action['skill'], 'hunt')
        self.assertEqual(action['params']['duration'], 60)
        self.assertEqual(action['next_phase'], 'town')

    def test_complete_cycle_services_real_needs_then_returns_to_hunt(self):
        from packages.resident_demo.life_cycle import choose
        a = choose('town', frame())
        self.assertEqual((a['skill'],a['params']['map'],a['next_phase']), ('travel_to','prt_in','sell'))
        t = frame(map='prt_in',inventory={'501':2,'909':3},zeny=200)
        a = choose('sell',t)
        self.assertEqual((a['skill'],a['next_phase']),('sell_loot','buy'))
        t['inventory']={'501':2}
        a = choose('buy',t)
        self.assertEqual(a['skill'],'buy_potions')
        self.assertEqual(a['params']['stock_goal'],20)
        t['inventory']={'501':20}
        a = choose('rest',t)
        self.assertEqual((a['skill'],a['params']['map'],a['next_phase']), ('travel_to',FIELD,'hunt'))
        self.assertEqual(choose(a['next_phase'],frame())['skill'],'hunt')

    def test_hunting_never_ignores_death_injury_or_wrong_map(self):
        from packages.resident_demo.life_cycle import choose
        a=choose('hunt',frame(dead=True,hp=0))
        self.assertEqual((a['skill'],a['next_phase']),('respawn','town'))
        a=choose('hunt',frame(hp=200))
        self.assertEqual((a['skill'],a['params']['map']),('travel_to','prt_in'))
        a=choose('rest',frame(map='prt_in',hp=200))
        self.assertEqual((a['skill'],a['params']['hp_pct']),('rest',80))
        a=choose('hunt',frame(map='prontera'))
        self.assertEqual(a['skill'],'travel_to')
        with self.assertRaisesRegex(ValueError,'TELEMETRY'):
            choose('hunt',frame(hp_max=0))

    def test_restart_reuses_intent_written_before_lost_socket_reply(self):
        import json,tempfile,pathlib,time
        from packages.resident_demo import life_cycle as module
        self.assertTrue(hasattr(module,'LifeCycle'),'Durable native activity controller missing')
        now=time.time()
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'state.json'
            life=module.LifeCycle(p,now+900,hunt_seconds=60)
            ids=[]
            def rpc(msg):
                if msg['op']=='status':return {'age':.1,'actions':[],'telemetry':frame()}
                ids.append(msg['idem'])
                self.assertEqual(json.loads(p.read_text())['pending']['idem'],msg['idem'])
                raise TimeoutError('accepted request but socket reply lost')
            with self.assertRaises(TimeoutError):life.tick(rpc,now)
            restart=module.LifeCycle(p,now+900,hunt_seconds=60)
            with self.assertRaises(TimeoutError):restart.tick(rpc,now+1)
            self.assertEqual(len(ids),2)
            self.assertEqual(ids[0],ids[1])

    def test_expired_pending_intent_is_never_dispatched_after_restart(self):
        import tempfile,pathlib,time
        from packages.resident_demo.life_cycle import LifeCycle
        now=time.time()
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'state.json';life=LifeCycle(p,now+900,60)
            def rpc(msg):
                if msg['op']=='status':return {'age':.1,'actions':[],'telemetry':frame()}
                raise TimeoutError('lost reply')
            with self.assertRaises(TimeoutError):life.tick(rpc,now)
            # Runtime renewal must not renew the old command's expiry.
            restart=LifeCycle(p,now+2000,60)
            with self.assertRaisesRegex(RuntimeError,'NATIVE_INTENT_EXPIRED'):
                restart.tick(rpc,now+1000)

    def test_failed_hunt_cannot_be_counted_as_completed_cycle(self):
        import tempfile,pathlib,time
        from packages.resident_demo.life_cycle import LifeCycle
        now=time.time()
        with tempfile.TemporaryDirectory() as d:
            life=LifeCycle(pathlib.Path(d)/'state.json',now+900,60)
            life.state['phase']='rest'
            def rpc(msg):
                if msg['op']=='status':return {'age':.1,'actions':[],'telemetry':frame(map='prt_in')}
                return {'action_id':'fixture-confirmed-return','state':'confirmed','code':'ARRIVED'}
            life.tick(rpc,now)
            self.assertEqual(life.state['cycles'],0,'Return without witnessed hunt was counted as life cycle')

    def test_no_route_consumption_durably_selects_one_alternative_after_restart(self):
        import tempfile,pathlib,json
        from packages.resident_demo.life_cycle import LifeCycle
        with tempfile.TemporaryDirectory() as d:
            path=pathlib.Path(d)/'state.json'
            life=LifeCycle(path,1000,60)
            life.state['phase']='town'
            def rpc(msg):
                if msg['op']=='status':return {'age':.1,'actions':[],'telemetry':frame()}
                self.assertEqual(msg['op'],'run')
                return {'action_id':'fixture-failed-route','state':'failed','code':'NO_ROUTE'}
            try:result=life.tick(rpc,100)
            except RuntimeError as exc:self.fail('NO_ROUTE has no production alternative: '+str(exc))
            self.assertEqual(result['state'],'failed')
            saved=json.loads(path.read_text())
            self.assertIsNone(saved['pending'])
            self.assertIsNone(saved['blocked'])
            self.assertEqual(saved['phase'],'hunt')
            self.assertEqual(saved['cycles'],0)
            self.assertEqual(saved['reaction'],dict(action_id='fixture-failed-route',failed_map='prt_in',blocked_until=7300,expires_at=200))
            self.assertEqual(saved['history'][-1]['code'],'NO_ROUTE')
            restart=LifeCycle(path,1000,60)
            sent=[]
            def resumed(msg):
                if msg['op']=='status':return {'age':.1,'actions':[],'telemetry':frame()}
                sent.append(msg)
                self.assertEqual(json.loads(path.read_text())['pending']['idem'],msg['idem'])
                return {'action_id':'fixture-alternative-hunt','state':'running','code':''}
            restart.tick(resumed,101)
            self.assertEqual(len(sent),1)
            self.assertEqual(sent[0]['skill'],'hunt')
            self.assertEqual(sent[0]['deadline'],99)
            self.assertEqual(restart.state['pending']['expires_at'],200)

    def test_quarantined_map_is_not_retried_after_alternative_hunt(self):
        import tempfile,pathlib
        from packages.resident_demo.life_cycle import LifeCycle
        with tempfile.TemporaryDirectory() as d:
            path=pathlib.Path(d)/'state.json'
            life=LifeCycle(path,1000,60)
            life.state['phase']='town'
            life.state['reaction']=dict(action_id='fixture-route',failed_map='prt_in',blocked_until=7300,expires_at=200)
            life.save()
            restart=LifeCycle(path,1000,60)
            before=path.read_bytes();calls=[]
            def rpc(msg):
                calls.append(msg['op'])
                if msg['op']=='status':return {'age':.1,'actions':[],'telemetry':frame()}
                self.fail('Quarantined map received another run')
            with self.assertRaisesRegex(RuntimeError,'LIFE_MAP_QUARANTINED'):
                restart.tick(rpc,102)
            self.assertEqual(calls,['status'])
            self.assertEqual(before,path.read_bytes())

    def test_corrupt_reaction_is_rejected_without_checkpoint_repair(self):
        import tempfile,pathlib,json
        from packages.resident_demo.life_cycle import LifeCycle
        for reaction in ({},dict(action_id='x',failed_map='prt_in',blocked_until=float('nan'),expires_at=200),
                         dict(action_id='x',failed_map='prt_in',blocked_until=True,expires_at=200),
                         dict(action_id='',failed_map='prt_in',blocked_until=1,expires_at=200),
                         dict(action_id='x',failed_map='prt_in',blocked_until=7300,expires_at=float('nan')),
                         dict(action_id='x',failed_map='prt_in',blocked_until=7300,expires_at=True)):
            with self.subTest(reaction=reaction),tempfile.TemporaryDirectory() as d:
                path=pathlib.Path(d)/'state.json'
                life=LifeCycle(path,1000,60);life.state['reaction']=reaction;life.save()
                before=path.read_bytes()
                with self.assertRaisesRegex(RuntimeError,'LIFE_REACTION_SCHEMA'):
                    LifeCycle(path,1000,60)
                self.assertEqual(before,path.read_bytes())

    def test_no_route_with_low_potions_fails_closed_instead_of_hunting(self):
        import tempfile,pathlib
        from packages.resident_demo.life_cycle import LifeCycle
        with tempfile.TemporaryDirectory() as d:
            life=LifeCycle(pathlib.Path(d)/'state.json',1000,60)
            life.state['phase']='town'
            def rpc(msg):
                if msg['op']=='status':return {'age':.1,'actions':[],'telemetry':frame(inventory={'501':2})}
                return {'action_id':'fixture-low-stock','state':'failed','code':'NO_ROUTE'}
            with self.assertRaisesRegex(RuntimeError,'NATIVE_FAILURE:NO_ROUTE'):
                life.tick(rpc,100)
            self.assertNotIn('reaction',life.state)
            self.assertEqual(life.state['pending']['action_id'],'fixture-low-stock')
            self.assertEqual(life.state['cycles'],0)

    def test_repeated_or_unsafe_no_route_preserves_failure_without_alternative(self):
        import tempfile,pathlib
        from packages.resident_demo.life_cycle import LifeCycle
        cases=[('already-consumed',frame(),9000),('injured',frame(hp=20),100),
               ('dead',frame(hp=0,dead=True),100),('wrong-map',frame(map='prontera'),100),
               ('short-window',frame(),895)]
        for case,telemetry,now in cases:
            with self.subTest(case=case),tempfile.TemporaryDirectory() as d:
                path=pathlib.Path(d)/'state.json';life=LifeCycle(path,now+900,60)
                life.state['phase']='town'
                life.state['pending']=dict(skill='travel_to',params=dict(map='prt_in',x=126,y=76,r=3),
                    timeout=240,next_phase='sell',reason='LIFE_TOWN',idem='fixture-idem',
                    expires_at=now+200,action_id='fixture-failure',from_phase='town')
                if case=='already-consumed':
                    life.state['reaction']=dict(action_id='fixture-prior',failed_map='prt_in',blocked_until=7300,expires_at=200)
                life.save();restart=LifeCycle(path,1000 if case=='short-window' else now+900,60)
                def rpc(msg):
                    if msg['op']=='status':return {'age':.1,'actions':[],'telemetry':telemetry}
                    self.assertEqual(msg['op'],'action','Recovery must not reissue failed command')
                    return {'action_id':'fixture-failure','state':'failed','code':'NO_ROUTE'}
                with self.assertRaisesRegex(RuntimeError,'NATIVE_FAILURE:NO_ROUTE'):
                    restart.tick(rpc,now)
                self.assertEqual(restart.state['pending']['idem'],'fixture-idem')
                self.assertEqual(restart.state['pending']['expires_at'],now+200)
                self.assertEqual(restart.state['cycles'],0)
                self.assertEqual(restart.state['phase'],'town')

    def test_delayed_alternative_cannot_renew_its_original_window(self):
        import tempfile,pathlib
        from packages.resident_demo.life_cycle import LifeCycle
        with tempfile.TemporaryDirectory() as d:
            path=pathlib.Path(d)/'state.json';life=LifeCycle(path,1000,60)
            life.state['phase']='town'
            def rpc(msg):
                if msg['op']=='status':return {'age':.1,'actions':[],'telemetry':frame()}
                return {'action_id':'fixture-failure','state':'failed','code':'NO_ROUTE'}
            life.tick(rpc,100)
            restart=LifeCycle(path,2000,60);before=path.read_bytes()
            def delayed(msg):
                self.assertEqual(msg['op'],'status','Delayed recovery renewed its native window')
                return {'age':.1,'actions':[],'telemetry':frame()}
            with self.assertRaisesRegex(RuntimeError,'LIFE_RECOVERY_EXPIRED'):
                restart.tick(delayed,201)
            self.assertEqual(before,path.read_bytes())

    def test_witnessed_death_consumption_survives_restart_without_replaying_hunt(self):
        import tempfile,pathlib,json
        from packages.resident_demo.life_cycle import LifeCycle
        with tempfile.TemporaryDirectory() as d:
            path=pathlib.Path(d)/'state.json';life=LifeCycle(path,1000,60)
            life.state['phase']='hunt'
            life.state['has_hunted']=True
            life.state['pending']=dict(skill='hunt',params=dict(map=FIELD,duration=60,min_kills=1),
                timeout=100,next_phase='town',reason='HUNT_INTERVAL',idem='fixture-original',
                expires_at=200,action_id='fixture-dead-hunt',from_phase='hunt')
            life.save()
            def rpc(msg):
                if msg['op']=='status':return {'age':.1,'actions':[],
                    'telemetry':frame(dead=True,hp=0),
                    'projection':{'server_dead':dict(value=True,source='witness',ts=99)}}
                self.assertEqual(msg['op'],'action','Failed hunt was replayed')
                return dict(action_id='fixture-dead-hunt',state='failed',code='DIED',created=50)
            try:life.tick(rpc,100)
            except RuntimeError as exc:self.fail('Witnessed death has no durable recovery: '+str(exc))
            saved=json.loads(path.read_text())
            self.assertIsNone(saved['pending']);self.assertIsNone(saved['blocked'])
            self.assertFalse(saved['has_hunted']);self.assertEqual(saved['cycles'],0)
            self.assertEqual(saved['death_recovery'],dict(action_id='fixture-dead-hunt',expires_at=160,recovered=False))
            restart=LifeCycle(path,2000,60);sent=[]
            def resumed(msg):
                if msg['op']=='status':return {'age':.1,'actions':[],
                    'telemetry':frame(dead=True,hp=0),
                    'projection':{'server_dead':dict(value=True,source='witness',ts=99)}}
                sent.append(msg)
                self.assertEqual(json.loads(path.read_text())['pending']['idem'],msg['idem'])
                return dict(action_id='fixture-respawn',state='running')
            restart.tick(resumed,101)
            self.assertEqual(len(sent),1);self.assertEqual(sent[0]['skill'],'respawn')
            self.assertEqual(sent[0]['deadline'],59)
            self.assertEqual(restart.state['pending']['expires_at'],160)

    def test_death_recovery_requires_original_window_and_dead_body_before_dispatch(self):
        import tempfile,pathlib
        from packages.resident_demo.life_cycle import LifeCycle
        for label,now,telemetry,code in (
                ('expired',161,frame(dead=True,hp=0),'LIFE_DEATH_RECOVERY_EXPIRED'),
                ('alive-without-respawn-proof',101,frame(),'LIFE_DEATH_DIVERGENCE')):
            with self.subTest(label=label),tempfile.TemporaryDirectory() as d:
                path=pathlib.Path(d)/'state.json';life=LifeCycle(path,1000,60)
                life.state['phase']='town'
                life.state['death_recovery']=dict(action_id='fixture-death',expires_at=160,recovered=False)
                life.save();restart=LifeCycle(path,2000,60);before=path.read_bytes()
                def rpc(msg):
                    self.assertEqual(msg['op'],'status','Unsafe recovery issued another intent')
                    return {'age':.1,'actions':[],'telemetry':telemetry}
                with self.assertRaisesRegex(RuntimeError,code):restart.tick(rpc,now)
                self.assertEqual(path.read_bytes(),before)

    def test_corrupt_death_recovery_is_rejected_without_checkpoint_repair(self):
        import tempfile,pathlib
        from packages.resident_demo.life_cycle import LifeCycle
        valid=dict(action_id='fixture-death',expires_at=160,recovered=False)
        for recovery in ({},dict(valid,expires_at=float('nan')),dict(valid,expires_at=True),
                         dict(valid,action_id=''),dict(valid,recovered='false'),
                         dict(valid,extra='unexpected'),dict(valid,expires_at=-1)):
            with self.subTest(recovery=recovery),tempfile.TemporaryDirectory() as d:
                path=pathlib.Path(d)/'state.json';life=LifeCycle(path,1000,60)
                life.state['death_recovery']=recovery;life.save();before=path.read_bytes()
                with self.assertRaisesRegex(RuntimeError,'LIFE_DEATH_RECOVERY_SCHEMA'):
                    LifeCycle(path,1000,60)
                self.assertEqual(path.read_bytes(),before)

    def test_unsafe_death_and_stuck_never_consume_failure_or_retry(self):
        import tempfile,pathlib
        from packages.resident_demo.life_cycle import LifeCycle
        cases=('no-witness','old-witness','future-witness','alive','invalid-hp','other-active',
               'economic','failed-respawn','repeated-death','short-window','stuck','unknown')
        for case in cases:
            with self.subTest(case=case),tempfile.TemporaryDirectory() as d:
                path=pathlib.Path(d)/'state.json';life=LifeCycle(path,150 if case=='short-window' else 1000,60)
                life.state['phase']='hunt'
                skill={'economic':'buy_potions','failed-respawn':'respawn'}.get(case,'hunt')
                life.state['pending']=dict(skill=skill,params={},timeout=100,next_phase='town',
                    reason='fixture',idem='fixture-original',expires_at=140,action_id='fixture-failed',from_phase='hunt')
                if case=='repeated-death':life.state['death_recovery']=dict(action_id='fixture-prior',expires_at=80,recovered=True)
                life.save();t=frame(dead=True,hp=0)
                if case=='alive':t=frame()
                if case=='invalid-hp':t['hp']=True
                death=dict(value=True,source='witness',ts=99)
                if case=='no-witness':death={}
                if case=='old-witness':death['ts']=49
                if case=='future-witness':death['ts']=103
                code='STUCK' if case=='stuck' else 'DIED'
                state='unknown' if case=='unknown' else 'failed'
                def rpc(msg):
                    if msg['op']=='status':return dict(age=.1,telemetry=t,
                        actions=['fixture-other'] if case=='other-active' else [],projection={'server_dead':death})
                    self.assertEqual(msg['op'],'action','Failure was retried')
                    return dict(action_id='fixture-failed',state=state,code=code,created=50)
                if case=='unknown':
                    before=path.read_bytes();self.assertEqual(life.tick(rpc,100)['state'],'unknown')
                    self.assertEqual(path.read_bytes(),before)
                else:
                    with self.assertRaisesRegex(RuntimeError,'NATIVE_FAILURE:'+code):life.tick(rpc,100)
                self.assertEqual(life.state['pending']['idem'],'fixture-original')
                self.assertEqual(life.state['pending']['expires_at'],140)
                self.assertEqual(life.state['cycles'],0)
                if case!='repeated-death':self.assertNotIn('death_recovery',life.state)

    def test_cli_requires_bounded_positive_duration(self):
        import subprocess,sys
        r=subprocess.run([sys.executable,'-m','packages.resident_demo.life_cycle','--execute',
                          '--state','/tmp/unit-invalid-state','--socket','/tmp/unit-invalid-socket',
                          '--duration','0'],capture_output=True,text=True)
        self.assertNotEqual(r.returncode,0,'Module has no bounded CLI')
        self.assertIn('bounded duration/hunt required',r.stderr)

    def test_short_remaining_lease_drains_without_creating_intent(self):
        import tempfile,pathlib,json
        from packages.resident_demo.life_cycle import LifeCycle
        with tempfile.TemporaryDirectory() as d:
            path=pathlib.Path(d)/'state.json'
            life=LifeCycle(path,1000,60)
            before=path.read_bytes()
            calls=[]
            def rpc(msg):
                calls.append(msg['op'])
                self.assertEqual(msg['op'],'status','Drain dispatched a new native command')
                return {'age':.1,'actions':[],'telemetry':frame()}
            try:
                result=life.tick(rpc,900)
            except RuntimeError as exc:
                self.fail('Normal lease drain raised instead of returning an outcome: '+str(exc))
            self.assertEqual(result['state'],'drained')
            self.assertEqual(result['code'],'LIFE_WINDOW_EXHAUSTED')
            self.assertIsNone(result['action_id'])
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(calls,['status'])

    def test_cli_exits_normally_after_draining_and_safe_stops(self):
        import tempfile,pathlib,io,contextlib
        from unittest.mock import patch
        from packages.resident_demo.life_cycle import main
        with tempfile.TemporaryDirectory() as d:
            calls=[]
            def request(socket,msg):
                calls.append(msg['op'])
                if msg['op']=='status':
                    return {'age':.1,'actions':[],'telemetry':frame()}
                self.assertEqual(msg['op'],'safe_stop')
                return {}
            argv=['life_cycle','--execute','--state',str(pathlib.Path(d)/'state.json'),
                  '--socket',str(pathlib.Path(d)/'fixture.sock'),'--duration','900',
                  '--hunt-seconds','60','--writer-lock',str(pathlib.Path(d)/'writer.lock')]
            output=io.StringIO()
            with patch('sys.argv',argv), patch('packages.body_gateway.cli.request',request), \
                 patch('time.time',return_value=100) as clock, \
                 patch('time.sleep',side_effect=AssertionError('Drained CLI kept polling')), \
                 contextlib.redirect_stdout(output):
                clock.side_effect=[100,950,950,950]
                main()
            self.assertEqual(calls,['status','status','safe_stop'])
            self.assertIn('LIFE_WINDOW_EXHAUSTED',output.getvalue())

    def test_cli_restart_preserves_absolute_lease_deadline(self):
        import tempfile,pathlib,io,contextlib
        from unittest.mock import patch
        from packages.resident_demo.life_cycle import main
        with tempfile.TemporaryDirectory() as d:
            calls=[]
            def request(socket,msg):
                calls.append(msg['op'])
                if msg['op']=='status':
                    return {'age':.1,'actions':[],'telemetry':frame()}
                self.assertEqual(msg['op'],'safe_stop','Restart renewed lease and dispatched work')
                return {}
            argv=['life_cycle','--execute','--state',str(pathlib.Path(d)/'state.json'),
                  '--socket',str(pathlib.Path(d)/'fixture.sock'),'--duration','900',
                  '--deadline','1000','--hunt-seconds','60',
                  '--writer-lock',str(pathlib.Path(d)/'writer.lock')]
            output=io.StringIO()
            with patch('sys.argv',argv), patch('packages.body_gateway.cli.request',request), \
                 patch('time.time',return_value=950), \
                 patch('time.sleep',side_effect=AssertionError('Restart renewed lease')), \
                 contextlib.redirect_stdout(output):
                try:main()
                except SystemExit as exc:self.fail('Absolute lease CLI is unavailable: '+str(exc))
            self.assertEqual(calls,['status','status','safe_stop'])
            self.assertIn('LIFE_WINDOW_EXHAUSTED',output.getvalue())

    def test_cli_rejects_invalid_absolute_lease_before_any_side_effect(self):
        import tempfile,pathlib,io,contextlib
        from unittest.mock import patch
        from packages.resident_demo.life_cycle import main
        for deadline in ('949','950','nan','inf','-inf','1851'):
            with self.subTest(deadline=deadline), tempfile.TemporaryDirectory() as d:
                root=pathlib.Path(d)
                argv=['life_cycle','--execute','--state',str(root/'state.json'),
                      '--socket',str(root/'fixture.sock'),'--duration','900',
                      '--deadline',deadline,'--writer-lock',str(root/'writer.lock')]
                with patch('sys.argv',argv), patch('time.time',return_value=950), \
                     patch('packages.body_gateway.cli.request',side_effect=AssertionError('Invalid lease reached IPC')), \
                     contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as rejected:main()
                self.assertEqual(rejected.exception.code,2)
                self.assertEqual(list(root.iterdir()),[],'Invalid lease mutated filesystem')

    def test_stale_or_invalid_age_never_dispatches(self):
        import tempfile,pathlib,time
        from packages.resident_demo.life_cycle import LifeCycle
        for age in (4,float('nan'),-1):
            with tempfile.TemporaryDirectory() as d:
                life=LifeCycle(pathlib.Path(d)/'state.json',time.time()+900,60)
                calls=[]
                def rpc(msg):
                    calls.append(msg)
                    if msg['op']!='status':self.fail('Invalid telemetry dispatched native action')
                    return {'age':age,'actions':[],'telemetry':frame()}
                with self.assertRaisesRegex(RuntimeError,'STALE_BODY'):life.tick(rpc,time.time())
                self.assertEqual([m['op'] for m in calls],['status'])

if __name__ == '__main__': unittest.main()
