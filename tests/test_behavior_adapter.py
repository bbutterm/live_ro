import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from packages.resident_behavior.adapter import Adapter, MemoryAdapterStore, dispatch
from packages.resident_behavior.contracts import ContractError
from tests.test_behavior_contracts import snapshot, execute, envelope, TIME


class Clock:
    def __init__(self): self.now = datetime.fromisoformat(TIME.replace('Z', '+00:00'))
    def __call__(self): return self.now
    def advance(self, seconds): self.now += timedelta(seconds=seconds)


class Executor:
    def __init__(self): self.calls = []; self.polls = []; self.results = {}; self.error = None
    def execute(self, p):
        self.calls.append(copy.deepcopy(p))
        if self.error == 'transport_timeout': raise TimeoutError()
        return dict(action_id=p['action_id'], accepted=self.error is None, error_code=self.error)
    def action(self, action_id):
        self.polls.append(action_id)
        return self.results.get(action_id, dict(action_id=action_id, result=None,
                    error_code=None, source_seq=None, completed_at=None))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(); self.executor = Executor(); self.store = MemoryAdapterStore()
        self.adapter = Adapter(executor=self.executor, store=self.store, clock=self.clock,
                               veto=lambda p, s: None, meeting_context=lambda r: {'awake': True, 'minutes_until_sleep': 30},
                               coordinator=lambda p, s: True)
        self.adapter.telemetry(snapshot())

    def test_execute_idempotent_restart_and_chat_parallel(self):
        p = execute('hunt', {'map': 'prt_fild08', 'minutes': 5})
        self.assertTrue(self.adapter.execute(p)['accepted'])
        self.assertEqual(self.adapter.execute(p), self.adapter.execute(p))
        restarted = Adapter(executor=self.executor, store=self.store, clock=self.clock, veto=lambda p,s: None)
        self.assertTrue(restarted.execute(p)['accepted'])
        self.assertEqual(len(self.executor.calls), 1)
        altered = copy.deepcopy(p); altered['args']['minutes'] = 6
        self.assertEqual(restarted.execute(altered)['error_code'], 'conflict')
        self.assertEqual(self.adapter.execute(execute(action_id='a2'))['error_code'], 'conflict')
        speech = execute('say', {'text': 'Привет'}, action_id='chat')
        self.assertTrue(self.adapter.execute(speech)['accepted'])
        self.executor.results['chat'] = dict(action_id='chat', result='done', error_code=None, source_seq=1, completed_at=TIME)
        self.adapter.action('chat')
        self.assertEqual(self.adapter.execute(execute(action_id='a3'))['error_code'], 'conflict')

    def test_sequences_gap_stale_and_unknown_veto(self):
        self.assertEqual(self.adapter.telemetry(snapshot())['status'], 'duplicate')
        e = envelope(seq=3); e.update(event_id='e3', type='login', data={})
        self.assertEqual(self.adapter.event(e)['status'], 'snapshot_required')
        self.assertEqual(self.adapter.execute(execute('hunt', {'map': 'prt_fild08', 'minutes': 5}, action_id='gap'))['error_code'], 'stale_state')
        self.adapter.telemetry(snapshot(seq=4))
        p = execute(action_id='fresh'); p['state_seq'] = 4
        self.assertTrue(self.adapter.execute(p)['accepted'])
        self.assertEqual(self.adapter.telemetry(snapshot(seq=2))['status'], 'old')
        self.clock.advance(11)
        h = execute('hunt', {'map': 'prt_fild08', 'minutes': 5}, 'stale'); h['state_seq'] = 4
        self.assertEqual(self.adapter.execute(h)['error_code'], 'stale_state')
        a = Adapter(executor=self.executor, clock=self.clock, veto=lambda p,s: None)
        q = snapshot(); q['server_time'] = self.clock.now.isoformat(); q['alive'] = None
        a.telemetry(q)
        h['server_time'] = self.clock.now.isoformat(); h['expires_at'] = (self.clock.now+timedelta(seconds=30)).isoformat(); h['state_seq']=1
        self.assertEqual(a.execute(h)['error_code'], 'unsafe')

    def test_timeout_poll_before_retry_and_funds_cooldown(self):
        self.executor.error = 'transport_timeout'
        p = execute('town_service', {}, 'timeout')
        self.assertEqual(self.adapter.execute(p)['error_code'], 'transport_timeout')
        self.adapter.execute(p)
        self.assertEqual(self.executor.polls, ['timeout'])
        self.assertEqual(len(self.executor.calls), 1)
        self.executor.results['timeout'] = dict(action_id='timeout', result='failed', error_code='no_funds', source_seq=1, completed_at=TIME)
        self.adapter.action('timeout'); self.executor.error = None
        self.assertEqual(self.adapter.execute(execute('town_service', {}, 'funds'))['error_code'], 'no_funds')
        q = snapshot(seq=2); q['zeny'] = 101; self.adapter.telemetry(q)
        p = execute('town_service', {}, 'funds2'); p['state_seq']=2
        self.assertTrue(self.adapter.execute(p)['accepted'])

    def test_http_strict_json_and_routes_no_listener(self):
        status, result = dispatch(self.adapter, 'POST', '/v1/telemetry', json.dumps(snapshot()).encode(), 'application/json; charset=utf-8')
        self.assertEqual(status, 200)
        self.assertEqual(result['status'], 'duplicate')
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff', b'[]'):
            self.assertEqual(dispatch(self.adapter, 'POST', '/v1/events', raw, 'application/json')[0], 400)
        self.assertEqual(dispatch(self.adapter, 'POST', '/v1/events', b'{}', 'text/plain')[0], 415)
        self.assertEqual(dispatch(self.adapter, 'GET', '/bad')[0], 404)

    def meeting(self, meeting_id='m1'):
        p = envelope(); p.update(meeting_id=meeting_id, initiator='mira', resident='borin', place='square', state_seq=1)
        self.adapter.telemetry(snapshot('borin'))
        return p

    def accepted_meeting(self):
        p = self.meeting()
        self.assertEqual(self.adapter.reserve_meeting(p)['status'], 'reserved')
        for resident, other in [('mira', 'borin'), ('borin', 'mira')]:
            request = execute('meet', {'resident': other, 'place': 'square'}, resident, resident)
            self.assertTrue(self.adapter.execute(request)['accepted'])
            self.adapter.acknowledge_meeting('m1', resident, resident)
        self.assertEqual(self.adapter.echo_meeting('m1', 'mira', 'mira')['status'], 'reserved')
        self.assertEqual(self.adapter.echo_meeting('m1', 'borin', 'borin')['status'], 'accepted')
        self.adapter.update_meetings()

    def test_meeting_both_autonomous_ack_echo_and_continuous_proximity(self):
        self.accepted_meeting()
        for seq in range(2, 62):
            self.clock.advance(2)
            for resident in ('mira', 'borin'):
                p = snapshot(resident, seq); p['server_time'] = self.clock.now.isoformat()
                self.adapter.telemetry(p)
            meetings = self.adapter.update_meetings()
        self.assertEqual(meetings['m1']['status'], 'fulfilled')

    def test_meeting_no_false_fulfillment_on_unobserved_gap(self):
        self.accepted_meeting()
        self.clock.advance(120)
        for resident in ('mira', 'borin'):
            p = snapshot(resident, 2); p['server_time'] = self.clock.now.isoformat()
            self.adapter.telemetry(p)
        self.assertEqual(self.adapter.update_meetings()['m1']['status'], 'accepted')

    def test_reservation_conflict_expiry_and_second_choice(self):
        p = self.meeting()
        self.adapter.coordinator = lambda p,s: False
        with self.assertRaises(ContractError): self.adapter.reserve_meeting(p)
        self.adapter.coordinator = lambda p,s: True
        self.assertEqual(self.adapter.reserve_meeting(p)['status'], 'reserved')
        self.assertEqual(self.adapter.reserve_meeting(p)['status'], 'reserved')
        q = copy.deepcopy(p); q['meeting_id']='m2'
        with self.assertRaises(ContractError): self.adapter.reserve_meeting(q)
        with self.assertRaises(ContractError): self.adapter.echo_meeting('m1', 'mira', 'x')
        self.clock.advance(30)
        self.assertEqual(self.adapter.update_meetings()['m1']['status'], 'conflict')

    def test_durable_restart_polls_without_double_send(self):
        import tempfile
        from packages.resident_behavior.adapter import SQLiteAdapterStore
        with tempfile.TemporaryDirectory() as directory:
            path = directory+'/adapter.sqlite'
            store = SQLiteAdapterStore(path)
            a = Adapter(executor=self.executor, store=store, clock=self.clock, veto=lambda p,s: None)
            a.telemetry(snapshot()); p = execute()
            self.assertTrue(a.execute(p)['accepted']); store.close()
            store = SQLiteAdapterStore(path)
            a = Adapter(executor=self.executor, store=store, clock=self.clock)
            self.assertTrue(a.execute(p)['accepted'])
            self.assertEqual(len(self.executor.calls), 1)
            store.close()

    def test_reconcile_interrupts_unsafe_hunt_without_inventing_completion(self):
        self.executor.interrupts = []
        def interrupt(action_id):
            self.executor.interrupts.append(action_id)
            return dict(action_id=action_id, accepted=True, error_code=None)
        self.executor.interrupt = interrupt
        self.assertTrue(self.adapter.execute(execute('hunt', {'map':'prt_fild08', 'minutes':5}))['accepted'])
        q = snapshot(seq=2); q['hp']=54; self.adapter.telemetry(q)
        report = self.adapter.reconcile()
        self.assertEqual(self.executor.interrupts, ['a1'])
        self.assertEqual(report['interruptions'][0]['error_code'], 'unsafe')
        self.assertIsNone(self.adapter.action('a1')['result'])
        self.assertEqual(self.adapter.execute(execute(action_id='next'))['error_code'], 'stale_state')
        self.adapter.reconcile()
        self.assertEqual(self.executor.interrupts, ['a1'])

    def test_meeting_releases_reservation_when_ack_action_fails(self):
        p = self.meeting(); self.adapter.reserve_meeting(p)
        request = execute('meet', {'resident':'borin', 'place':'square'}, 'meet_mira')
        self.assertTrue(self.adapter.execute(request)['accepted'])
        self.adapter.acknowledge_meeting('m1', 'mira', 'meet_mira')
        self.executor.results['meet_mira'] = dict(action_id='meet_mira', result='failed', error_code='no_path', source_seq=1, completed_at=TIME)
        self.adapter.action('meet_mira')
        self.assertEqual(self.adapter.update_meetings()['m1']['status'], 'conflict')

    def test_other_error_cooldown_five_minutes(self):
        self.executor.error = 'no_path'
        p = execute('walk_to', {'place':'square'})
        self.assertEqual(self.adapter.execute(p)['error_code'], 'no_path')
        self.executor.error = None
        self.assertEqual(self.adapter.execute(execute('walk_to', {'place':'square'}, 'b'))['error_code'], 'no_path')
        self.clock.advance(300)
        q = snapshot(seq=2); q['server_time']=self.clock.now.isoformat(); self.adapter.telemetry(q)
        p = execute('walk_to', {'place':'square'}, 'c'); p['state_seq']=2
        p['server_time']=self.clock.now.isoformat(); p['expires_at']=(self.clock.now+timedelta(seconds=30)).isoformat()
        self.assertTrue(self.adapter.execute(p)['accepted'])

    def test_authoritative_died_event_vetoes_before_next_snapshot(self):
        e = envelope(seq=2); e.update(event_id='death', type='died', data={})
        self.assertEqual(self.adapter.event(e)['status'], 'accepted')
        p = execute('hunt', {'map':'prt_fild08', 'minutes':5}); p['state_seq']=2
        self.assertEqual(self.adapter.execute(p)['error_code'], 'dead')
        self.assertEqual(self.executor.calls, [])


if __name__ == '__main__': unittest.main()
