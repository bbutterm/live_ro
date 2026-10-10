"""Motor contract exercised against a deterministic body control transport."""
import copy
import tempfile
import unittest
from pathlib import Path
from packages.resident_behavior.adapter import Adapter, SQLiteAdapterStore
from packages.resident_behavior.config import load_behavior
from packages.resident_behavior.contracts import validate_result, validate_telemetry
from tests.test_behavior_adapter import Clock
from tests.test_behavior_contracts import execute


class Body:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.rows = []
        self.timeout = False
        self.t = dict(ts=clock().timestamp(), map='prontera', pos={'x': 156, 'y': 191},
                      hp=100, hp_max=100, sp=20, sp_max=20, dead=False, sitting=False,
                      weight=10, weight_max=100, zeny=100, inventory={'501': 1, '909': 20},
                      near_players=[], near_monsters=[], mode='IDLE_SAFE')
    def fresh(self):
        self.t['ts'] = self.clock().timestamp()
    def __call__(self, socket, message):
        self.calls.append(copy.deepcopy(message))
        op = message['op']
        if op == 'status': return {'telemetry': copy.deepcopy(self.t), 'age': 0, 'actions': self.rows}
        if op == 'actions': return copy.deepcopy(self.rows)
        if op == 'action': return copy.deepcopy(next(r for r in self.rows if r['action_id'] == message['action_id']))
        if op == 'run':
            row = dict(action_id='native-'+str(len(self.rows)), idem_key=message['idem'],
                       state='dispatched', code='', skill=message['skill'], params=message['params'])
            self.rows.append(row)
            if self.timeout: raise TimeoutError()
            return copy.deepcopy(row)
        if op in ('cancel', 'safe_stop'): return {'queued': True}
        raise AssertionError(message)
    def complete(self, state='confirmed', code=''):
        self.rows[-1].update(state=state, code=code)


class MotorTests(unittest.TestCase):
    def setUp(self):
        from packages.resident_behavior.motor import OpenKoreMotor
        self.cls = OpenKoreMotor
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.body = Body(self.clock)
        self.config = load_behavior('config/behavior.yaml')
        self.store = SQLiteAdapterStore(Path(self.tmp.name)/'motor.sqlite')
        self.motor = self.make()
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def make(self):
        return self.cls({'mira': '/explicit/test.sock'}, self.config, self.store,
                        clock=self.clock, request=self.body)
    def runs(self): return [c for c in self.body.calls if c['op'] == 'run']
    def test_walk_adapter_result_requires_confirmed_arrival_and_restart_dedup(self):
        source = self.motor.snapshot('mira')
        validate_telemetry(source)
        adapter = Adapter(source=self.motor, executor=self.motor, clock=self.clock, veto=lambda p,s: None)
        adapter.telemetry(source)
        p = execute('walk_to', {'place': 'square'})
        p['state_seq'] = source['seq']
        self.assertTrue(adapter.execute(p)['accepted'])
        self.assertIsNone(adapter.action(p['action_id'])['result'])
        self.assertRegex(self.runs()[0]['idem'], r'^v1-[0-9a-f]{64}$')
        self.assertEqual(self.runs()[0]['skill'], 'travel_to')
        self.assertEqual(self.runs()[0]['params'], dict(map='prontera', x=156, y=191, r=3))
        self.motor = self.make()
        self.assertTrue(self.motor.execute(p)['accepted'])
        self.assertEqual(len(self.runs()), 1)
        self.body.complete()
        self.assertEqual(validate_result(self.motor.action(p['action_id']))['result'], 'done')
        q = copy.deepcopy(p); q['args']['place'] = 'kafra'
        self.assertEqual(self.motor.execute(q)['error_code'], 'conflict')

    def test_snapshot_latest_terminal_root_uses_creation_order_not_id(self):
        place=self.config['places']['square']
        self.body.t.update(map=place['map'],pos={'x':place['x'],'y':place['y']})
        for aid in ('z-first','a-second'):
            p=execute('walk_to',{'place':'square'});p['action_id']=aid
            self.motor.execute(p);self.body.complete();self.motor.action(aid)
        self.assertEqual(self.motor.snapshot('mira')['current_action']['action_id'],'a-second')

    def test_fractional_minutes_remain_in_root_but_native_seconds_are_integer(self):
        import math
        p=execute('hunt',{'map':'prt_fild08','minutes':12.345})
        field=self.motor._plan(p)[0][2]
        self.body.t.update(map=field['map'],pos={'x':field['x'],'y':field['y']})
        self.motor.execute(p)
        self.body.complete();self.motor.action(p['action_id'])
        hunts=[c for c in self.body.calls if c.get('op')=='run' and c.get('skill')=='hunt']
        self.assertTrue(hunts)
        duration=hunts[-1]['params']['duration']
        self.assertIs(type(duration),int)
        self.assertEqual(duration,math.ceil(12.345*60))
        self.assertEqual(self.motor.snapshot('mira')['current_action']['args']['minutes'],12.345)

    def test_snapshot_tracks_durable_motor_not_parallel_speech(self):
        self.motor.execute(execute('walk_to', {'place':'square'}))
        before = self.motor.snapshot('mira')
        self.assertEqual(before['current_action']['action_id'], 'a1')
        self.assertIsNone(before['current_action']['result'])
        speech = execute('say', {'text':'Привет'})
        speech.update(action_id='speech1', decision_id='speech-decision')
        self.motor.execute(speech)
        self.motor = self.make()
        self.assertEqual(self.motor.snapshot('mira')['current_action']['action'], 'walk_to')
        self.body.rows[0].update(state='confirmed')
        self.assertEqual(self.motor.action('a1')['result'], 'done')
        completed = self.motor.snapshot('mira')
        self.assertEqual(completed['current_action']['result'], 'done')
        self.assertGreater(completed['seq'], before['seq'])
        validate_telemetry(completed)

    def test_hunt_timer_starts_after_route_and_survives_restart(self):
        p = execute('hunt', {'map': 'prt_fild08', 'minutes': 5})
        self.motor.execute(p)
        self.assertRegex(self.runs()[0]['idem'], r'^v1-[0-9a-f]{64}$')
        self.assertEqual(self.runs()[0]['skill'], 'travel_to')
        self.clock.advance(120); self.body.fresh()
        self.body.t['map'] = 'prt_fild08'; self.body.complete()
        self.motor.action('a1')
        self.assertEqual(self.runs()[-1]['skill'], 'hunt')
        self.assertEqual(self.runs()[-1]['params']['duration'], 300)
        self.body.complete()
        self.assertIsNone(self.motor.action('a1')['result'])
        self.clock.advance(300); self.body.fresh()
        self.motor = self.make()
        self.assertEqual(self.motor.action('a1')['result'], 'done')
        self.assertEqual(len(self.runs()), 2)

    def test_service_sells_only_goal_surplus_then_buys_affordable_target(self):
        self.body.t['inventory'] = {'501': 0, '909': 20, '713': 4, '938': 2, '914': 99}
        self.motor.execute(execute('town_service', {}))
        self.body.complete(); self.body.t['map'] = 'prt_in'
        self.motor.action('a1')
        self.assertEqual(self.runs()[-1]['skill'], 'sell_loot')
        self.assertEqual(self.runs()[-1]['params'], {'items': {'909': 8, '713': 4, '938': 2}})
        self.body.complete(); self.body.t['inventory'].update({'909': 12, '713': 0, '938': 0})
        self.body.t['zeny'] = 75
        self.motor = self.make(); self.motor.action('a1')
        self.assertEqual(self.runs()[-1]['skill'], 'buy_potions')
        self.assertEqual(self.runs()[-1]['params'], {'item_id': 501, 'stock_goal': 1, 'max_zeny': 50})
        self.body.complete(); self.body.t['inventory']['501'] = 1
        self.assertEqual(self.motor.action('a1')['result'], 'done')
        self.motor.execute(execute('town_service', {}))
        self.assertEqual(len(self.runs()), 3)

    def test_idle_requires_duration_fresh_safe_snapshot_and_is_restart_safe(self):
        self.motor.execute(execute('idle', {'minutes': 1}))
        self.assertIsNone(self.motor.action('a1')['result'])
        self.assertEqual([c['op'] for c in self.body.calls].count('safe_stop'), 1)
        self.motor = self.make(); self.motor.execute(execute('idle', {'minutes': 1}))
        self.clock.advance(60)
        self.assertIsNone(self.motor.action('a1')['result'])  # old snapshot
        self.body.fresh()
        self.assertEqual(self.motor.action('a1')['result'], 'done')
        self.assertEqual([c['op'] for c in self.body.calls].count('safe_stop'), 1)

    def test_follow_tracks_observed_coordinates_and_lost_target_grace(self):
        self.body.t['near_players'] = [{'name':'Novi', 'x':160, 'y':191}]
        self.motor.execute(execute('follow', {'player':'Novi', 'minutes':1}))
        self.assertEqual(self.runs()[-1]['params'], {'map':'prontera', 'x':160, 'y':191, 'r':3})
        self.body.complete(); self.body.t['pos']['x'] = 160
        self.assertIsNone(self.motor.action('a1')['result'])
        self.body.t['near_players'] = []
        self.motor.action('a1'); self.clock.advance(29); self.body.fresh()
        self.assertIsNone(self.motor.action('a1')['result'])
        self.motor = self.make(); self.clock.advance(1); self.body.fresh()
        self.assertEqual(self.motor.action('a1')['error_code'], 'target_lost')
        self.assertEqual(len(self.runs()), 1)

    def test_meet_requires_two_minutes_observed_peer_presence(self):
        self.motor.sockets['borin'] = '/explicit/peer.sock'
        self.motor.execute(execute('meet', {'resident':'borin', 'place':'square'}))
        self.body.complete()
        self.assertIsNone(self.motor.action('a1')['result'])
        for _ in range(59):
            self.clock.advance(2); self.body.fresh()
            self.assertIsNone(self.motor.action('a1')['result'])
        self.clock.advance(2); self.body.fresh()
        self.assertEqual(self.motor.action('a1')['result'], 'done')

    def test_uncertain_dispatch_reconciles_native_id_without_resend(self):
        self.body.timeout = True
        p = execute('walk_to', {'place':'square'})
        self.assertEqual(self.motor.execute(p)['error_code'], 'transport_timeout')
        self.motor = self.make()
        self.assertEqual(self.motor.execute(p)['error_code'], 'transport_timeout')
        self.assertIsNone(self.motor.action('a1')['result'])
        self.body.complete()
        self.assertEqual(self.motor.action('a1')['result'], 'done')
        self.assertEqual(len(self.runs()), 1)

    def test_speech_stop_never_stops_parallel_motor_or_waits_for_idle(self):
        for root_action in ('walk_to','town_service'):
            for speech_action in ('say','whisper'):
                for reason in ('timeout','interrupt','stale','dead'):
                    with self.subTest(root=root_action,speech=speech_action,reason=reason):
                        self.store.close()
                        self.store=SQLiteAdapterStore(Path(self.tmp.name)/f'{root_action}-{speech_action}-{reason}.sqlite')
                        self.clock=Clock();self.body=Body(self.clock);self.motor=self.make()
                        root=execute(root_action,{'place':'square'} if root_action=='walk_to' else {})
                        self.motor.execute(root)
                        if root_action=='town_service':
                            self.body.complete();self.body.t['map']='prt_in';self.motor.action('a1')
                            self.assertEqual(self.runs()[-1]['skill'],'sell_loot')
                        self.body.t['mode']='WALKING' if root_action=='walk_to' else 'TRADING'
                        args={'text':'Привет'} if speech_action=='say' else {'to':'Tester','text':'Привет'}
                        speech=execute(speech_action,args);speech['action_id']='speech'
                        self.motor.execute(speech);native=self.body.rows[-1]['action_id']
                        before=self.motor.snapshot('mira')['current_action']
                        if reason=='timeout':self.clock.advance(4);self.body.fresh()
                        elif reason=='stale':self.clock.advance(11)
                        elif reason=='dead':self.body.t['dead']=True
                        else:self.motor.interrupt('speech')
                        self.assertIsNone(self.motor.action('speech')['result'])
                        self.assertFalse([c for c in self.body.calls if c['op']=='safe_stop'])
                        self.assertEqual([c['action_id'] for c in self.body.calls if c['op']=='cancel'],[native])
                        self.body.complete(state='cancelled',code='CANCELLED')
                        outcome=self.motor.action('speech')
                        self.assertEqual(outcome['result'],'timeout' if reason=='timeout' else 'failed')
                        self.assertEqual(self.motor.snapshot('mira')['current_action'],before)
                        with self.store.transaction() as state:
                            self.assertEqual(state['motor']['slots']['mira'],'a1')
                        self.motor=self.make();self.motor.action('speech')
                        self.assertEqual([c['action_id'] for c in self.body.calls if c['op']=='cancel'],[native])

    def test_interrupt_does_not_release_slot_before_native_terminal(self):
        self.motor.execute(execute('walk_to', {'place':'square'}))
        self.assertTrue(self.motor.interrupt('a1')['accepted'])
        self.assertIsNone(self.motor.action('a1')['result'])
        self.assertEqual(self.motor.execute(execute(action_id='a2'))['error_code'], 'conflict')
        self.body.complete(state='cancelled', code='CANCELLED')
        self.assertEqual(self.motor.action('a1')['error_code'], 'interrupted')
        self.assertTrue(self.motor.execute(execute(action_id='a2'))['accepted'])

    def test_hunt_low_hp_stops_before_next_step_even_when_native_confirms(self):
        self.motor.execute(execute('hunt', {'map':'prt_fild08', 'minutes':5}))
        self.body.complete(); self.body.t['hp'] = 54
        outcome = self.motor.action('a1')
        self.assertEqual(outcome['error_code'], 'unsafe')
        self.assertEqual(outcome['result'], 'failed')
        self.assertEqual(len(self.runs()), 1)
        self.assertIn('safe_stop', [c['op'] for c in self.body.calls])

    def test_native_error_codes_and_speech_confirmation(self):
        from packages.resident_behavior.motor import native_error
        for code in ('NO_ROUTE', 'UNWALKABLE', 'STUCK'):
            self.assertEqual(native_error(code), 'no_path')
        self.motor.execute(execute('say', {'text':'Привет'}))
        self.assertIsNone(self.motor.action('a1')['result'])
        self.body.complete(state='verifying', code='SPOKEN')
        self.assertIsNone(self.motor.action('a1')['result'])
        self.body.complete()
        self.assertEqual(self.motor.action('a1')['result'], 'done')

    def test_rest_requires_sitting_and_hp_snapshot_even_with_confirmation(self):
        self.body.t['hp'] = 70
        self.motor.execute(execute('rest', {'until_hp':90}))
        self.body.complete()
        self.assertIsNone(self.motor.action('a1')['result'])
        self.body.t.update(hp=95, sitting=True)
        self.assertEqual(self.motor.action('a1')['result'], 'done')

    def test_database_reopen_preserves_pending_native_step(self):
        p = execute('walk_to', {'place':'square'})
        self.motor.execute(p)
        self.store.close()
        self.store = SQLiteAdapterStore(Path(self.tmp.name)/'motor.sqlite')
        self.motor = self.make(); self.motor.execute(p)
        self.body.complete()
        self.assertEqual(self.motor.action('a1')['result'], 'done')
        self.assertEqual(len(self.runs()), 1)

if __name__ == '__main__': unittest.main()
