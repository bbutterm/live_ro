"""Synthetic C26 fixtures; no live native actions or server mutation."""
import json
import pathlib
import tempfile
import unittest

from typing import Any
from packages.resident_demo.life_cycle import LifeCycle, FIELD


def healthy_status() -> dict[str,Any]:
    return dict(age=.1, actions=[], telemetry=dict(map=FIELD, pos=dict(x=170,y=240),
        hp=795,hp_max=795,dead=False,inventory={'501':20},zeny=600),
        projection=dict(server_map=dict(value=FIELD,source='witness',ts=99),
            server_pos=dict(value=dict(x=170,y=240),source='witness',ts=99),
            server_dead=dict(value=False,source='witness',ts=50)))


def failed_route(life):
    life.state['phase']='town'
    life.state['has_hunted']=True
    life.state['pending']=dict(skill='travel_to',params=dict(map='prt_in',x=126,y=76,r=3),
        timeout=240,next_phase='sell',reason='LIFE_TOWN',idem='fixture-original',
        expires_at=300,action_id='fixture-stuck',from_phase='town')
    life.save()


class StuckRecoveryTests(unittest.TestCase):
    def test_stuck_consumption_queues_one_durable_local_step_after_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path=pathlib.Path(d)/'state.json';life=LifeCycle(path,1000,60)
            failed_route(life)
            def rpc(msg):
                if msg['op']=='status':return healthy_status()
                self.assertEqual(msg['op'],'action','Original stuck route was replayed')
                return dict(action_id='fixture-stuck',state='failed',code='STUCK')
            try:life.tick(rpc,100)
            except RuntimeError as exc:self.fail('STUCK lacks production local recovery: '+str(exc))
            saved=json.loads(path.read_text())
            self.assertIsNone(saved['blocked']);self.assertIsNone(saved['pending'])
            self.assertEqual(saved['cycles'],0);self.assertFalse(saved['has_hunted'])
            self.assertEqual(saved['history'][-1]['code'],'STUCK')
            self.assertEqual(saved['stuck_recovery'],dict(action_id='fixture-stuck',
                map=FIELD,x=174,y=240,expires_at=130,recovered=False))
            restart=LifeCycle(path,2000,60);sent=[]
            def resumed(msg):
                if msg['op']=='status':return healthy_status()
                sent.append(msg)
                self.assertEqual(json.loads(path.read_text())['pending']['idem'],msg['idem'])
                return dict(action_id='fixture-unstuck',state='running')
            restart.tick(resumed,101)
            self.assertEqual(len(sent),1)
            self.assertEqual(sent[0]['skill'],'travel_to')
            self.assertEqual(sent[0]['params'],dict(map=FIELD,x=174,y=240,r=0))
            self.assertEqual(sent[0]['deadline'],29)
            self.assertEqual(restart.state['pending']['expires_at'],130)


    def test_corrupt_stuck_marker_is_rejected_without_repair(self):
        valid=dict(action_id='fixture-stuck',map=FIELD,x=174,y=240,expires_at=130,recovered=False)
        for marker in ({},dict(valid,x=True),dict(valid,y=-1),dict(valid,map='prontera'),
                       dict(valid,expires_at=float('nan')),dict(valid,expires_at=True),
                       dict(valid,recovered='false'),dict(valid,extra=1),dict(valid,action_id='')):
            with self.subTest(marker=marker),tempfile.TemporaryDirectory() as d:
                path=pathlib.Path(d)/'state.json';life=LifeCycle(path,1000,60)
                life.state['stuck_recovery']=marker;life.save();before=path.read_bytes()
                with self.assertRaisesRegex(RuntimeError,'LIFE_STUCK_RECOVERY_SCHEMA'):
                    LifeCycle(path,1000,60)
                self.assertEqual(path.read_bytes(),before)


    def test_queued_local_step_fails_closed_without_renewal_or_unsafe_dispatch(self):
        for case in ('expired','dead','injured','low-stock','changed-map','already-moved','active','missing-position'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as d:
                path=pathlib.Path(d)/'state.json';life=LifeCycle(path,1000,60)
                life.state['phase']='town'
                life.state['stuck_recovery']=dict(action_id='fixture-stuck',map=FIELD,x=174,y=240,
                    expires_at=130,recovered=False)
                life.save();restart=LifeCycle(path,2000,60);before=path.read_bytes()
                s=healthy_status()
                if case=='dead':s['telemetry'].update(dead=True,hp=0)
                if case=='injured':s['telemetry']['hp']=10
                if case=='low-stock':s['telemetry']['inventory']['501']=2
                if case=='changed-map':s['telemetry']['map']='prontera'
                if case=='already-moved':s['telemetry']['pos']['x']=174
                if case=='active':s['actions']=['fixture-other']
                if case=='missing-position':s['telemetry'].pop('pos')
                def rpc(msg):
                    self.assertEqual(msg['op'],'status','Unsafe local step was dispatched')
                    return s
                expected='LIFE_STUCK_RECOVERY_EXPIRED' if case=='expired' else ('OTHER_ACTIVE_ACTION' if case=='active' else 'LIFE_STUCK_DIVERGENCE')
                with self.assertRaisesRegex(RuntimeError,expected):
                    restart.tick(rpc,131 if case=='expired' else 101)
                self.assertEqual(path.read_bytes(),before)


    def test_local_step_needs_authoritative_arrival_before_selecting_different_goal(self):
        from unittest.mock import patch
        from packages.body_gateway.executor import Executor
        from packages.body_gateway.journal import Journal
        with tempfile.TemporaryDirectory() as d,patch('time.time',return_value=100) as clock:
            root=pathlib.Path(d);journal=Journal(str(root/'journal.sqlite'))
            self.addCleanup(journal.db.close);sent=[]
            executor=Executor(journal,'resident_a',150001,sent.append)
            executor.body(dict(type='hello',body_epoch='fixture-epoch',ts=100))
            def body(x):
                t=healthy_status()['telemetry']
                executor.body(dict(t,type='telemetry',ts=clock.return_value,
                    body_epoch='fixture-epoch',pos=dict(x=x,y=240),running=None))
            def witness(event_id,x):
                executor.witness(dict(id=event_id,kind='pos',char_id=150001,map=FIELD,
                    x=x,y=240,event_ts=clock.return_value))
            def rpc(msg):
                if msg['op']=='status':return dict(age=clock.return_value-executor.received,
                    telemetry=executor.telemetry,projection=executor.projection,actions=executor.active())
                if msg['op']=='run':return executor.run(msg['skill'],msg['params'],msg['deadline'],msg['idem'])
                if msg['op']=='action':return journal.action(msg['action_id'])
                self.fail('Unexpected RPC '+msg['op'])
            body(170);witness(1,170)
            executor.projection['server_dead']=dict(value=False,source='witness',ts=100)
            life=LifeCycle(root/'life.json',1000,60);life.state['phase']='town'
            route=life.tick(rpc,100)
            executor.body(dict(type='skill_result',action_id=route['action_id'],outcome='error',code='STUCK'))
            life.tick(rpc,100)
            restart=LifeCycle(root/'life.json',1000,60);clock.return_value=101
            step=restart.tick(rpc,101)
            executor.body(dict(type='skill_result',action_id=step['action_id'],outcome='done',code='ARRIVED'))
            clock.return_value=102;body(174);executor.evaluate()
            self.assertEqual(restart.tick(rpc,102)['state'],'verifying')
            self.assertFalse(restart.state['stuck_recovery']['recovered'],'ACK counted as arrival')
            witness(2,174);executor.evaluate()
            self.assertEqual(restart.tick(rpc,102)['state'],'confirmed')
            self.assertTrue(restart.state['stuck_recovery']['recovered'])
            self.assertEqual(restart.state['cycles'],0)
            again=LifeCycle(root/'life.json',1000,60);clock.return_value=103
            again.tick(rpc,103)
            self.assertEqual(sent[-1]['params']['map'],'prt_in')
            self.assertEqual(len(sent),3,'Local step was repeated instead of changing goal')


    def test_queued_local_step_requires_fresh_matching_server_position(self):
        for case in ('missing','stale','different','wrong-map','server-dead'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as d:
                path=pathlib.Path(d)/'state.json';life=LifeCycle(path,1000,60)
                life.state['stuck_recovery']=dict(action_id='fixture-stuck',map=FIELD,x=174,y=240,
                    expires_at=130,recovered=False)
                life.save();s=healthy_status();p=s['projection']
                if case=='missing':p.pop('server_pos')
                if case=='stale':p['server_pos']['ts']=90
                if case=='different':p['server_pos']['value']['x']=160
                if case=='wrong-map':p['server_map']['value']='prontera'
                if case=='server-dead':p['server_dead']['value']=True
                before=path.read_bytes()
                def rpc(msg):
                    self.assertEqual(msg['op'],'status','Unwitnessed local recovery dispatched')
                    return s
                with self.assertRaisesRegex(RuntimeError,'LIFE_STUCK_DIVERGENCE'):life.tick(rpc,101)
                self.assertEqual(path.read_bytes(),before)


    def test_unsafe_or_repeated_stuck_preserves_original_failure(self):
        for case in ('repeat','no-witness','stale','divergence','dead','injured','stock','active','unknown','other-skill','short-lease'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as d:
                path=pathlib.Path(d)/'state.json';life=LifeCycle(path,135 if case=='short-lease' else 1000,60)
                failed_route(life);s=healthy_status()
                if case=='repeat':life.state['stuck_recovery']=dict(action_id='fixture-prior',map=FIELD,x=174,y=240,expires_at=80,recovered=True)
                if case=='no-witness':s['projection']={}
                if case=='stale':s['projection']['server_pos']['ts']=90
                if case=='divergence':s['projection']['server_pos']['value']['x']=160
                if case=='dead':s['telemetry'].update(dead=True,hp=0)
                if case=='injured':s['telemetry']['hp']=10
                if case=='stock':s['telemetry']['inventory']['501']=2
                if case=='active':s['actions']=['fixture-other']
                if case=='other-skill':life.state['pending']['skill']='hunt'
                life.save()
                def rpc(msg):
                    if msg['op']=='status':return s
                    self.assertEqual(msg['op'],'action','STUCK original intent replayed')
                    return dict(action_id='fixture-stuck',state='unknown' if case=='unknown' else 'failed',code='STUCK')
                if case=='unknown':
                    before=path.read_bytes();self.assertEqual(life.tick(rpc,100)['state'],'unknown')
                    self.assertEqual(path.read_bytes(),before)
                else:
                    with self.assertRaisesRegex(RuntimeError,'NATIVE_FAILURE:STUCK'):life.tick(rpc,100)
                self.assertEqual(life.state['pending']['idem'],'fixture-original')
                self.assertEqual(life.state['pending']['expires_at'],300)
                self.assertEqual(life.state['cycles'],0)
                if case!='repeat':self.assertNotIn('stuck_recovery',life.state)

    def test_failed_local_step_is_not_retried(self):
        for code in ('STUCK','UNWALKABLE','NO_ROUTE'):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as d:
                life=LifeCycle(pathlib.Path(d)/'life.json',1000,60)
                life.state['stuck_recovery']=dict(action_id='fixture-prior',map=FIELD,x=174,y=240,expires_at=130,recovered=False)
                life.state['pending']=dict(skill='travel_to',params=dict(map=FIELD,x=174,y=240,r=0),
                    timeout=30,next_phase='town',reason='STUCK_LOCAL_STEP',idem='fixture-local',
                    expires_at=130,action_id='fixture-local-step',from_phase='town')
                life.save()
                def rpc(msg):
                    if msg['op']=='status':return healthy_status()
                    self.assertEqual(msg['op'],'action')
                    return dict(action_id='fixture-local-step',state='failed',code=code)
                with self.assertRaisesRegex(RuntimeError,'NATIVE_FAILURE:'+code):life.tick(rpc,102)
                self.assertFalse(life.state['stuck_recovery']['recovered'])
                self.assertEqual(life.state['pending']['idem'],'fixture-local')
                before=life.path.read_bytes()
                with self.assertRaisesRegex(RuntimeError,'NATIVE_FAILURE:'+code):life.tick(rpc,103)
                self.assertEqual(before,life.path.read_bytes())


if __name__=='__main__':unittest.main()
