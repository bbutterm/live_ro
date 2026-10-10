import tempfile
import unittest
from pathlib import Path
from packages.resident_behavior.controller import Controller
from packages.resident_behavior.adapter import Adapter, SQLiteAdapterStore
from packages.resident_behavior.config import load_behavior
from packages.resident_behavior.store import BehaviorStore
from packages.resident_behavior.reducer import initial_state
from unittest.mock import patch
from tests.test_behavior_motor import Body
from tests.test_behavior_adapter import Clock
from packages.resident_behavior.motor import OpenKoreMotor
from tests.test_behavior_contracts import execute

class BehaviorControllerTests(unittest.TestCase):
    def test_injected_model_choice_reaches_durable_motor_action(self):
        import inspect, json
        from packages.resident_behavior.llm import Client
        self.assertIn('llm', inspect.signature(Controller).parameters, 'controller has no LLM seam')
        self.clock.now=self.clock.now.replace(hour=12,minute=0)
        self.body.fresh()
        observation=self.motor.snapshot('mira')
        self.adapter.telemetry(observation)
        mental=initial_state(self.config,'mira',observation,self.clock().timestamp())
        mental['schedule']['break_after_at']=self.clock().timestamp()+3600
        self.state.save_state('mira',mental)
        calls=[]
        def choose(request, timeout):
            data=json.loads(json.loads(request.data)['messages'][1]['content'])
            calls.append(data)
            return {'choices':[{'message':{'content':json.dumps(dict(data['primary'],reason='Выбираю занятие.'))}}]}
        client=Client(transport=choose)
        controller=Controller(self.config,self.state,self.adapter,clock=self.clock,enabled=lambda rid:True,llm=client)
        result=controller.step('mira',dispatch=True)
        self.assertEqual(len(calls),1,result)
        self.assertEqual(result['decision']['source'],'llm')
        self.assertEqual(result['llm_calls'],1)
        self.assertTrue(result['ack']['accepted'])
        self.assertIsNotNone(self.state.get_action(result['decision']['action_id']))

    def test_death_during_model_wait_overrides_the_model_choice(self):
        import json
        from packages.resident_behavior.llm import Client
        self.clock.now=self.clock.now.replace(hour=12,minute=0)
        self.body.fresh()
        observation=self.motor.snapshot('mira');self.adapter.telemetry(observation)
        mental=initial_state(self.config,'mira',observation,self.clock().timestamp())
        mental['schedule']['break_after_at']=self.clock().timestamp()+3600
        self.state.save_state('mira',mental)
        def choose(request, timeout):
            data=json.loads(json.loads(request.data)['messages'][1]['content'])
            self.clock.advance(8);self.body.t['dead']=True;self.body.fresh()
            return {'choices':[{'message':{'content':json.dumps(dict(data['primary'],reason='Выбираю занятие.'))}}]}
        controller=Controller(self.config,self.state,self.adapter,clock=self.clock,enabled=lambda rid:True,llm=Client(transport=choose))
        result=controller.step('mira',dispatch=True)
        self.assertEqual(result['decision']['source'],'safety')
        self.assertEqual(result['decision']['trigger'],'dead')

    def test_client_exception_keeps_fallback_dispatch_alive(self):
        self.clock.now=self.clock.now.replace(hour=12,minute=0);self.body.fresh()
        observation=self.motor.snapshot('mira');self.adapter.telemetry(observation)
        mental=initial_state(self.config,'mira',observation,self.clock().timestamp())
        mental['schedule']['break_after_at']=self.clock().timestamp()+3600
        self.state.save_state('mira',mental)
        class Broken:
            def complete(self,*args,**kwargs):
                raise RuntimeError('provider unavailable')
        controller=Controller(self.config,self.state,self.adapter,clock=self.clock,enabled=lambda rid:True,llm=Broken())
        result=controller.step('mira',dispatch=True)
        self.assertEqual(result['decision']['source'],'fallback')
        self.assertEqual(result['llm']['fallback_reason'],'client_error')
        self.assertTrue(result['ack']['accepted'])

    def test_chat_whisper_runs_in_parallel_without_replacing_motor(self):
        p=execute('walk_to',{'place':'square'})
        self.adapter.telemetry(self.motor.snapshot('mira'));self.adapter.execute(p)
        controller=self.controller(enabled=True)
        intent={'id':'chat-17','event':{'id':17,'name':'Tester','text':'кто ты?','type':'W','ts':self.clock().timestamp()},
                'decision':{'action':'whisper','args':{'to':'Tester','text':'Иду!'}}}
        result=controller.chat('mira',intent)
        self.assertTrue(result['speech_ack']['accepted'])
        speech=[c for c in self.body.calls if c['op']=='run' and c['skill']=='whisper']
        self.assertEqual(len(speech),1)
        self.assertEqual(speech[0]['params']['to_name'],'Tester')
        self.assertNotEqual(speech[0]['params']['text'],'Иду!')
        self.assertFalse([c for c in self.body.calls if c['op']=='cancel'])
        self.assertEqual(self.motor.snapshot('mira')['current_action']['action_id'],'a1')
        before=len(self.body.calls)
        self.assertTrue(controller.chat('mira',intent)['duplicate'])
        self.assertEqual(len(self.body.calls),before)

    def test_chat_movement_is_accepted_before_its_promise(self):
        self.clock.now=self.clock.now.replace(hour=12,minute=0)
        self.body.t['pos']={'x':156,'y':160};self.body.fresh()
        snapshot=self.motor.snapshot('mira');self.adapter.telemetry(snapshot)
        mental=initial_state(self.config,'mira',snapshot,self.clock().timestamp())
        mental['schedule']['break_after_at']=self.clock().timestamp()+3600
        self.state.save_state('mira',mental)
        intent={'id':'chat-18','event':{'id':18,'name':'Tester','text':'Мира, иди к фонтану','type':'O','ts':self.clock().timestamp()},
                'decision':{'action':'walk_to','args':{'place':'square'},'say':'Иду к фонтану.'}}
        result=self.controller(enabled=True).chat('mira',intent)
        self.assertTrue(result['movement_accepted'],result)
        self.assertTrue(result['movement_ack']['accepted'])
        runs=[c for c in self.body.calls if c['op']=='run']
        self.assertEqual([c['skill'] for c in runs],['travel_to','say'])
        self.assertEqual(runs[-1]['params']['text'],'Иду к фонтану.')
        self.assertIsNotNone(self.state.get_action(result['movement_ack']['action_id']))

    def test_chat_model_selects_only_approved_private_reply_through_shared_client(self):
        from packages.resident_behavior.llm import Client
        import json
        self.clock.now=self.clock.now.replace(hour=12,minute=0);self.body.fresh()
        snapshot=self.motor.snapshot('mira');self.adapter.telemetry(snapshot)
        mental=initial_state(self.config,'mira',snapshot,self.clock().timestamp())
        mental['schedule']['break_after_at']=self.clock().timestamp()+3600
        self.state.save_state('mira',mental)
        calls=[]
        def transport(request,timeout):
            payload=json.loads(request.data);context=json.loads(payload['messages'][1]['content'])
            calls.append(context)
            self.assertEqual(context['kind'],'chat')
            self.assertEqual(payload['max_tokens'],128)
            choice=dict(context['candidates'][1],reason='Ответ игроку')
            return {'choices':[{'message':{'content':json.dumps(choice,ensure_ascii=False)}}]}
        controller=Controller(self.config,self.state,self.adapter,clock=self.clock,enabled=lambda rid:True,
                              llm=Client(transport=transport))
        intent={'id':'chat-19','event':{'id':19,'name':'Tester','text':'Ты бот?','type':'W','ts':self.clock().timestamp()},
                'decision':{'action':'whisper','args':{'to':'Tester','text':'Я настоящий человек.'}}}
        result=controller.chat('mira',intent)
        self.assertEqual(len(calls),1)
        self.assertIsNone(result['llm']['fallback_reason'])
        self.assertEqual(result['speech']['args'],calls[0]['candidates'][1]['args'])
        self.assertEqual(result['speech']['action'],'whisper')
        self.assertTrue(result['speech_ack']['accepted'])
        self.assertNotIn('человек.',result['speech']['args']['text'])

    def chat_fixture(self, eid=20):
        self.clock.now=self.clock.now.replace(hour=12,minute=0);self.body.fresh()
        snapshot=self.motor.snapshot('mira');self.adapter.telemetry(snapshot)
        mental=initial_state(self.config,'mira',snapshot,self.clock().timestamp())
        mental['schedule']['break_after_at']=self.clock().timestamp()+3600
        self.state.save_state('mira',mental)
        return {'id':'chat-'+str(eid),'event':{'id':eid,'name':'Tester','text':'Ты бот?','type':'W','ts':self.clock().timestamp()},
                'decision':{'action':'whisper','args':{'to':'Tester','text':'Иду к фонтану.'}}}

    def test_chat_preserves_planning_cadence_and_rng(self):
        intent=self.chat_fixture(23)
        mental=self.state.get_state('mira')
        mental.update(next_plan_at=self.clock().timestamp()+300,rng_index=7,decision_index=9)
        self.state.save_state('mira',mental)
        self.controller(enabled=True).chat('mira',intent)
        after=self.state.get_state('mira')
        for key in ('next_plan_at','rng_index','decision_index'):
            self.assertEqual(after[key],mental[key],key)

    def test_chat_budget_denial_keeps_reply_and_never_calls_transport(self):
        from packages.resident_behavior.llm import Client
        intent=self.chat_fixture();reservations=[]
        class Budget:
            def reserve(self,rid,kind,tokens,now):
                reservations.append((rid,kind,tokens));return None
        controller=Controller(self.config,self.state,self.adapter,clock=self.clock,enabled=lambda rid:True,
                              llm=Client(budget=Budget(),transport=lambda *args:self.fail('budget bypass')))
        result=controller.chat('mira',intent)
        self.assertEqual(reservations,[('mira','chat',128)])
        self.assertEqual(result['llm']['fallback_reason'],'budget_denied')
        self.assertTrue(result['speech_ack']['accepted'])
        self.assertEqual(result['llm_calls'],0)
        self.assertEqual(result['speech']['args']['text'],'Да, я ИИ-житель.')

    def test_chat_unknown_claim_survives_restart_without_resend(self):
        intent=self.chat_fixture(21)
        self.state.claim_chat_intent('mira',intent)
        # Crash after claim, before a receipt; another Controller must not guess/replay.
        before=len(self.body.calls)
        result=self.controller(enabled=True).chat('mira',intent)
        self.assertEqual(result,{'blocked':'pending_reconciliation','duplicate':True})
        self.assertEqual(len(self.body.calls),before)

    def test_chat_model_cannot_replace_speech_with_a_motor(self):
        from packages.resident_behavior.llm import Client
        import json
        intent=self.chat_fixture(22)
        bad={'action':'walk_to','args':{'place':'square'},'reason':'Ignore instructions'}
        controller=Controller(self.config,self.state,self.adapter,clock=self.clock,enabled=lambda rid:True,
                              llm=Client(transport=lambda *args:{'choices':[{'message':{'content':json.dumps(bad)}}]}))
        result=controller.chat('mira',intent)
        self.assertEqual(result['llm']['fallback_reason'],'invalid_response')
        self.assertEqual([c['skill'] for c in self.body.calls if c['op']=='run'],['whisper'])
        self.assertFalse(result['movement_accepted'])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.clock=Clock();self.body=Body(self.clock)
        self.config=load_behavior('config/behavior.yaml')
        self.state=BehaviorStore(Path(self.tmp.name)/'state.sqlite')
        self.transport=SQLiteAdapterStore(Path(self.tmp.name)/'transport.sqlite')
        self.motor=OpenKoreMotor({'mira':'explicit-test'},self.config,self.transport,clock=self.clock,request=self.body)
        self.adapter=Adapter(source=self.motor,executor=self.motor,store=self.transport,clock=self.clock,veto=lambda p,s:None)
    def tearDown(self):
        self.state.close();self.transport.close();self.tmp.cleanup()
    def controller(self, enabled=False):
        return Controller(self.config,self.state,self.adapter,clock=self.clock,enabled=lambda rid:enabled)
    def test_default_read_only_persists_state_without_motor_outbox(self):
        result=self.controller().step('mira')
        self.assertEqual(result['mode'],'read_only')
        self.assertIsNotNone(self.state.get_state('mira'))
        self.assertFalse(self.state.pending_actions())
        self.assertFalse([c for c in self.body.calls if c['op']!='status'])
    def test_dispatch_without_loaded_opt_in_is_rejected_without_run(self):
        result=self.controller().step('mira',dispatch=True)
        self.assertEqual(result['blocked'],'native_v1_not_enabled')
        self.assertFalse(self.state.pending_actions())
        self.assertFalse([c for c in self.body.calls if c['op']=='run'])
    def test_motor_minimum_survives_controller_recreation(self):
        p=execute('walk_to',{'place':'square'})
        self.adapter.telemetry(self.motor.snapshot('mira'))
        self.adapter.execute(p)
        before=len([c for c in self.body.calls if c['op']=='run'])
        result=self.controller(enabled=True).step('mira',dispatch=True)
        self.assertTrue(result['decision']['continuation'])
        self.assertEqual(result['decision']['action_id'],'a1')
        self.assertEqual(len([c for c in self.body.calls if c['op']=='run']),before)
    def test_claimed_running_root_keeps_safety_and_state_ticks_live(self):
        controller=self.controller(enabled=True)
        first=controller.step('mira',dispatch=True)
        self.assertTrue(first['ack']['accepted'])
        root=first['decision']['action_id']
        sends=len([c for c in self.body.calls if c['op']=='run'])
        self.clock.advance(5)
        second=self.controller(enabled=True).step('mira',dispatch=True)
        self.assertNotIn('blocked',second)
        self.assertTrue(second['decision']['continuation'])
        self.assertEqual(second['decision']['action_id'],root)
        self.assertEqual(len([c for c in self.body.calls if c['op']=='run']),sends)
        self.assertEqual(self.state.get_action(root)['status'],'reconcile')
    def test_in_flight_town_service_is_not_replaced_by_idle_between_planning_ticks(self):
        self.body.t['inventory']['501']=20
        self.adapter.telemetry(self.motor.snapshot('mira'))
        p=execute('town_service',{})
        self.assertTrue(self.adapter.execute(p)['accepted'])
        before=len([c for c in self.body.calls if c['op']=='run'])
        self.clock.advance(1);self.body.fresh()
        result=self.controller(enabled=True).step('mira',dispatch=True)
        self.assertTrue(result['decision']['continuation'])
        self.assertEqual(result['decision']['action_id'],'a1')
        self.assertEqual(len([c for c in self.body.calls if c['op']=='run']),before)
        self.assertFalse([c for c in self.body.calls if c['op'] in ('cancel','safe_stop')])

    def test_fresh_observation_received_during_rpc_does_not_interrupt_as_future(self):
        original=self.motor.request
        def receive(path,message):
            if message['op']=='status':
                self.clock.advance(.01);self.body.fresh()
            return original(path,message)
        self.adapter.telemetry(self.motor.snapshot('mira'))
        self.assertTrue(self.adapter.execute(execute('walk_to',{'place':'square'}))['accepted'])
        self.motor.request=receive
        self.clock.advance(5)
        result=self.controller(enabled=True).step('mira',dispatch=True)
        self.assertTrue(result['decision']['continuation'])
        self.assertEqual(result['decision']['action_id'],'a1')
        self.assertNotEqual(result['decision']['trigger'],'stale_state')
        self.assertFalse([c for c in self.body.calls if c['op'] in ('cancel','safe_stop')])

    def test_running_outbox_does_not_delay_low_hp_interrupt_until_completion(self):
        self.body.t.update(map='prt_fild08',hp=100,hp_max=100)
        self.adapter.telemetry(self.motor.snapshot('mira'))
        p=execute('hunt',{'map':'prt_fild08','minutes':15})
        self.assertTrue(self.adapter.execute(p)['accepted'])
        self.body.t['hp']=20;self.clock.advance(5)
        result=self.controller(enabled=True).step('mira',dispatch=True)
        self.assertEqual(result['decision']['action'],'rest')
        self.assertEqual(result['blocked'],'interrupt_unconfirmed')
        self.assertIsNone(self.adapter.action('a1')['result'])
        self.assertTrue(any(c['op']=='safe_stop' for c in self.body.calls))
        self.assertFalse(result['decision']['continuation'])
        self.body.complete(state='cancelled',code='CANCELLED');self.body.fresh()
        resumed=self.controller(enabled=True).step('mira',dispatch=True)
        self.assertEqual(self.adapter.action('a1')['result'],'failed')
        self.assertTrue(resumed['ack']['accepted'])
    def test_restart_consumes_cached_hp_crossing_before_new_full_snapshot(self):
        self.controller().step('mira')
        self.clock.advance(1);self.body.t['hp']=20;self.body.fresh()
        self.adapter.refresh('mira')  # received, crash before mental application
        captured=self.motor.snapshot('mira')['seq']
        self.state.close()
        self.state=BehaviorStore(Path(self.tmp.name)/'state.sqlite')
        self.clock.advance(1);self.body.t['hp']=100;self.body.fresh()
        self.controller().step('mira')
        mental=self.state.get_state('mira')
        self.assertTrue(any(row['kind']=='low_hp' for row in mental['audit']))
        self.assertGreater(mental['consumed_snapshot_seq'],captured)
        self.assertFalse([c for c in self.body.calls if c['op']=='run'])

    def test_fractional_clock_keeps_exact_envelope_ttl(self):
        from datetime import datetime
        self.clock.advance(.431997);self.body.fresh()
        result=self.controller(enabled=True).step('mira',dispatch=True)
        self.assertTrue(result['ack']['accepted'])
        request=self.state.pending_actions()[0]
        parse=lambda s:datetime.fromisoformat(s.replace('Z','+00:00'))
        self.assertEqual((parse(request['expires_at'])-parse(request['server_time'])).total_seconds(),30)

    def test_dispatch_does_not_reconcile_other_resident_slots(self):
        with self.transport.transaction() as state:
            state.setdefault('slots',{})['resident_a']='other-root'
        controller=self.controller(enabled=True)
        controller.enabled=lambda rid:rid=='mira'
        with patch.object(self.motor,'snapshot',wraps=self.motor.snapshot) as snapshots:
            controller.step('mira',dispatch=True)
        self.assertFalse(any(c.args[0]=='resident_a' for c in snapshots.call_args_list))

    def test_transport_failure_on_interruption_preserves_mental_tick(self):
        p=execute('hunt',{'map':'prt_fild08','minutes':15})
        self.adapter.telemetry(self.motor.snapshot('mira'));self.adapter.execute(p)
        controller=self.controller(enabled=True)
        controller.step('mira',dispatch=True)
        before=self.state.get_state('mira')['last_update']
        self.clock.advance(5);self.body.fresh()
        self.body.t['near_monsters']=[{'id':'999','x':150,'y':139}]
        with patch.object(self.motor,'interrupt',side_effect=TimeoutError):
            result=controller.step('mira',dispatch=True)
        self.assertEqual(result['blocked'],'interrupt_unconfirmed')
        self.assertGreater(self.state.get_state('mira')['last_update'],before)

    def test_restart_does_not_resend_claimed_but_uncertain_outbox(self):
        p=execute('walk_to',{'place':'square'})
        mental=initial_state(self.config,'mira',self.motor.snapshot('mira'),self.clock())
        self.state.save_state('mira',mental,action=p);self.state.claim_action('a1')
        self.clock.advance(5);self.body.fresh()
        result=self.controller(enabled=True).step('mira',dispatch=True)
        self.assertEqual(result['blocked'],'pending_reconciliation')
        self.assertGreater(self.state.get_state('mira')['last_update'],mental['last_update'])
        self.assertFalse([c for c in self.body.calls if c['op']=='run'])
