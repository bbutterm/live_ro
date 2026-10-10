import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_behavior_adapter import Clock
from tests.test_behavior_motor import Body


class LiveTests(unittest.TestCase):
    def test_lease_bound_llm_opt_in_injects_provider_and_can_be_revoked(self):
        from packages.resident_behavior.live import LiveRunner
        client=object()
        (self.d/'BEHAVIOR_LLM').write_text(self.lease['token'])
        with patch('packages.resident_behavior.provider.from_environment',return_value=client) as factory:
            runner=LiveRunner(dict(name='Mira',resident_id='actor'),self.d,clock=self.clock,request=self.body)
        try:
            factory.assert_called_once()
            self.assertIs(runner.controller.llm,client)
            self.assertTrue(runner.controller.llm_enabled('mira'))
            (self.d/'BEHAVIOR_LLM').unlink()
            self.assertFalse(runner.controller.llm_enabled('mira'))
        finally:
            runner.close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.clock = Clock()
        self.body = Body(self.clock)
        self.body.t.update(behavior_v1=True, running=None)
        self.lease = dict(token='owner', started_at=self.clock().timestamp(),
                          deadline=self.clock().timestamp()+1200, phase='controlled')
        self.save()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        (self.d/'behavior-lease.json').write_text(json.dumps(self.lease))

    def test_real_controller_tick_durable_evaluation_and_owner_gate(self):
        from packages.resident_behavior.live import LiveRunner
        runner = LiveRunner(dict(name='Mira', resident_id='actor'), self.d,
                            clock=self.clock, request=self.body)
        try:
            result = runner.tick()
            self.assertEqual(result['mode'], 'controlled')
            self.assertEqual(result['llm_calls'], 0)
            self.assertTrue(result['ack']['accepted'])
            mutations = [c for c in self.body.calls if c['op'] in ('run','cancel','safe_stop')]
            self.assertTrue(mutations)
            self.assertTrue(all(c['behavior_owner']=='owner' for c in mutations))
            self.assertTrue(all(c.get('behavior_owner')=='owner' for c in self.body.calls if c['op']=='status'))
            self.assertTrue((self.d/'behavior-adapter.sqlite').exists())
            self.assertTrue((self.d/'behavior-state.sqlite').exists())
            self.assertEqual(json.loads((self.d/'brain.json').read_text())['source'], 'behavior_v1')
            self.assertEqual(json.loads((self.d/'behavior-live.jsonl').read_text().splitlines()[-1])['kind'], 'evaluation')
        finally:
            runner.close()

    def test_gate_rechecks_loaded_flag_lease_and_compound_stage(self):
        from packages.resident_behavior.live import LiveRunner
        from tests.test_behavior_contracts import execute
        runner = LiveRunner(dict(name='Mira', resident_id='actor'), self.d,
                            clock=self.clock, request=self.body)
        try:
            self.body.t.update(map='prt_fild08')
            p = execute('hunt', {'map':'prt_fild08','minutes':15})
            runner.motor.execute(p)
            self.body.complete()
            self.lease['phase'] = 'shadow'; self.save()
            runner.motor.action(p['action_id'])
            self.assertEqual(len([c for c in self.body.calls if c['op']=='run']), 1)
            self.body.t['behavior_v1'] = False
            with self.assertRaises(ConnectionError):
                runner.request(runner.socket, {'op':'safe_stop'})
            self.lease['phase']='controlled'; self.save()
            self.clock.advance(1200);self.body.fresh()
            with self.assertRaises(ConnectionError):
                runner.request(runner.socket, {'op':'run','skill':'rest'})
        finally:
            runner.close()

    def test_drain_waits_economics_and_requires_fresh_empty_body(self):
        from packages.resident_behavior.live import LiveRunner
        runner = LiveRunner(dict(name='Mira', resident_id='actor'), self.d,
                            clock=self.clock, request=self.body)
        try:
            self.body.rows.append(dict(action_id='buy',state='running',skill='buy_potions',
                                       behavior_owner='owner',idem_key='v1-buy'))
            self.body.t['running']='buy'
            self.clock.advance(1200); self.body.fresh()
            result=runner.tick()
            self.assertFalse(result['released'])
            self.assertEqual(json.loads((self.d/'behavior-lease.json').read_text())['phase'],'blocked')
            self.assertFalse([c for c in self.body.calls if c['op'] in ('cancel','safe_stop','run')])
            self.body.rows.clear();self.body.t['running']=None
            (self.d/'BEHAVIOR_V1').touch()
            self.assertTrue(runner.tick()['released'])
            self.assertFalse((self.d/'BEHAVIOR_V1').exists())
        finally:
            runner.close()

    def test_drain_owner_cancels_only_non_economic_and_never_releases_running_body(self):
        from packages.resident_behavior.live import LiveRunner
        runner=LiveRunner(dict(name='Mira',resident_id='actor'),self.d,clock=self.clock,request=self.body)
        try:
            self.body.rows.append(dict(action_id='walk',state='running',skill='travel_to',
                                       behavior_owner='owner',idem_key='v1-walk'))
            self.body.t['running']='walk'
            runner.stopping=True
            self.assertFalse(runner.tick()['released'])
            cancel=[c for c in self.body.calls if c['op']=='cancel']
            self.assertEqual(cancel,[dict(op='cancel',action_id='walk',behavior_owner='owner')])
            self.body.rows.clear()
            self.assertFalse(runner.tick()['released'])
            self.body.t['running']=None
            self.clock.advance(4)
            self.assertFalse(runner.tick()['released'])
            self.body.fresh()
            self.assertTrue(runner.tick()['released'])
        finally:
            runner.close()

    def test_missing_claim_cannot_release_despite_empty_native(self):
        from packages.resident_behavior.live import LiveRunner
        from tests.test_behavior_contracts import execute
        runner=LiveRunner(dict(name='Mira',resident_id='actor'),self.d,clock=self.clock,request=self.body)
        try:
            runner.tick()
            self.body.rows.clear();self.body.t['running']=None
            p=execute('walk_to',{'place':'square'})
            runner.state.save_state('mira',runner.state.get_state('mira'),action=p)
            runner.state.claim_action(p['action_id'])
            runner.stopping=True
            self.assertFalse(runner.tick()['released'])
        finally:
            runner.close()

    def test_initial_draining_is_read_only_not_automatic_release(self):
        from packages.resident_behavior.live import LiveRunner
        self.lease['phase']='draining';self.save()
        runner=LiveRunner(dict(name='Mira',resident_id='actor'),self.d,clock=self.clock,request=self.body)
        try:
            self.assertEqual(runner.tick()['mode'],'read_only')
            self.assertEqual(json.loads((self.d/'behavior-lease.json').read_text())['phase'],'draining')
            self.assertFalse([c for c in self.body.calls if c['op'] in ('run','cancel','safe_stop')])
        finally:
            runner.close()

    def test_no_lease_never_constructs_runner(self):
        from packages.resident_behavior.live import LiveRunner
        (self.d/'behavior-lease.json').unlink()
        with self.assertRaises(RuntimeError):
            LiveRunner(dict(name='Mira',resident_id='actor'),self.d,request=self.body)
        self.assertFalse(self.body.calls)

    def test_seam_lease_precedes_legacy_exec(self):
        from packages.resident_world import supervisor
        resident=dict(name='Mira',resident_id='actor')
        actor=self.d/'actor';actor.mkdir()
        (actor/'behavior-lease.json').write_text(json.dumps(self.lease))
        config=dict(residents=[resident],brain_python='/legacy/python',chat_service='standalone')
        with patch.object(supervisor,'STATE',self.d), patch.object(supervisor,'config',return_value=config), \
             patch('sys.argv',['supervisor','--brain','actor']), \
             patch.object(supervisor.os,'execv') as execv, \
             patch('packages.resident_behavior.live.run') as run:
            supervisor.main()
            self.assertFalse(any(call.args[0]=='/legacy/python' for call in execv.call_args_list))
            self.assertTrue(run.called or any(call.args[0]=='/usr/bin/python3' for call in execv.call_args_list))

    def test_standalone_chat_reaches_controller_without_legacy_cancel_or_promise(self):
        from packages.resident_behavior.live import LiveRunner
        from packages.resident_world import chat,supervisor
        r=dict(name='Mira',resident_id='actor')
        event=dict(id=31,name='Tester',text='Мира, иди к фонтану',type='W',ts=self.clock().timestamp())
        c={'places':{'fountain':{'map':'prontera','x':156,'y':191}},'residents':[r]}
        store=chat.ChatStore(self.d/'chat.sqlite')
        store.enqueue(event,'Mira',False,self.clock().timestamp());store.claim('Mira',self.clock().timestamp())
        with patch.object(supervisor,'STATE',self.d.parent),patch('packages.body_gateway.cli.request',side_effect=self.body),\
             patch('packages.resident_world.skills.manual') as manual,patch('time.time',side_effect=lambda:self.clock().timestamp()),\
             patch('packages.resident_world.chat.STATE',self.d.parent,create=True):
            # Map the test resident to the fixture directory without touching live STATE.
            with patch.object(supervisor,'STATE',self.d):
                actor=self.d/'actor';actor.mkdir();(actor/'behavior-lease.json').write_text(json.dumps(self.lease))
                chat.handle(event,r,c,store,model=lambda *args:self.fail('legacy model bypassed Controller'))
                manual.assert_not_called()
                self.assertFalse([m for m in self.body.calls if m['op']!='status'])
                runner=LiveRunner(r,actor,clock=self.clock,request=self.body)
                try:
                    result=runner.consume_chat_goal()
                    self.assertFalse(result['movement_accepted'])  # initial sleep/break priority is preserved
                    self.assertTrue(result['speech_ack']['accepted'])
                    self.assertEqual(result['speech']['action'],'whisper')
                    self.assertNotIn('Иду',result['speech']['args']['text'])
                    runs=[m for m in self.body.calls if m['op']=='run']
                    self.assertEqual([m['skill'] for m in runs],['whisper'])
                    self.assertTrue(all(m['behavior_owner']=='owner' for m in runs))
                finally:runner.close()
        store.db.close()

    def test_chat_goal_rejected_without_legacy_skill(self):
        from packages.resident_behavior.live import LiveRunner
        (self.d/'chat-goal.json').write_text(json.dumps(dict(id='chat-1',decision={'action':'meet'})))
        runner=LiveRunner(dict(name='Mira',resident_id='actor'),self.d,clock=self.clock,request=self.body)
        try:
            runner.consume_chat_goal()
            self.assertFalse((self.d/'chat-goal.json').exists())
            entry=json.loads((self.d/'behavior-live.jsonl').read_text())
            self.assertFalse(entry['accepted'])
            self.assertFalse(self.body.calls)
        finally:
            runner.close()
