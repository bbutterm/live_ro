import tempfile
import unittest
from pathlib import Path
from packages.resident_behavior.store import BehaviorStore
from packages.resident_behavior.reducer import initial_state, advance, timestamp
from packages.resident_behavior.contracts import ContractError
from tests.test_behavior_contracts import snapshot as telemetry
from tests.test_behavior_reducer import config


class StoreTests(unittest.TestCase):
    def test_apply_snapshot_rollback_retry_restart_preserves_low_hp(self):
        c = config()
        c['drives']['fear'] = {'initial': 0, 'on_event': {'low_hp': 15}}
        def reduce(snap, previous):
            now = timestamp(snap['server_time'])
            return initial_state(c, 'r', snap, now) if previous is None else advance(c, 'r', previous, snap, now)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'behavior.sqlite'
            high = telemetry('r', 1)
            low = dict(high, seq=2, hp=39, server_time='2026-10-10T07:00:02Z')
            with BehaviorStore(path) as store:
                original = store.apply_snapshot(high, reduce)
                def crash(snap, previous):
                    changed = reduce(snap, previous)
                    self.assertEqual(changed['drives']['fear'], 15)
                    # Snapshot is already visible on this connection, but not committed.
                    self.assertEqual(store.get_snapshot('r'), low)
                    raise RuntimeError('crash before commit')
                with self.assertRaisesRegex(RuntimeError, 'crash before commit'):
                    store.apply_snapshot(low, crash)
                self.assertEqual(store.get_snapshot('r'), high)
                self.assertEqual(store.get_state('r'), original)
                self.assertFalse(store.connection.in_transaction)
            with BehaviorStore(path) as store:
                state = store.apply_snapshot(low, reduce)
                self.assertEqual(state['derived']['hp_pct'], 39)
                self.assertEqual(state['audit'][-1]['source_ids'], ['snapshot:r:2'])
            with BehaviorStore(path) as store:
                recovered = dict(high, seq=3, server_time='2026-10-10T07:00:04Z')
                state = store.apply_snapshot(recovered, reduce)
                self.assertEqual(state['drives']['fear'], 15)
                self.assertEqual(len([e for e in state['audit'] if e['kind'] == 'low_hp']), 1)
                self.assertEqual(store.get_snapshot('r'), recovered)
                self.assertEqual(store.get_state('r'), state)

    def test_apply_snapshot_repeated_tick_advances_clock_not_facts(self):
        c = config()
        snap = telemetry('r', 0)
        snap['current_action'] = dict(action_id='root', action='idle', args={'minutes': 5},
                                      started_at=snap['server_time'], result=None, error_code=None)
        now = timestamp(snap['server_time'])
        def reduce(accepted, previous):
            return initial_state(c, 'r', accepted, now) if previous is None else advance(c, 'r', previous, accepted, now)
        with BehaviorStore(':memory:') as store:
            first = store.apply_snapshot(snap, reduce)
            now += 2
            second = store.apply_snapshot(snap, reduce)
            self.assertEqual(second['last_update'], now)
            self.assertLess(second['drives']['tired'], first['drives']['tired'])
            self.assertEqual(second['current_action'], snap['current_action'])
            self.assertEqual(store.get_snapshot('r'), snap)
            self.assertEqual(store.get_state('r'), second)
            for bad in (dict(snap, hp=99), dict(snap, seq=1, hp=True),
                        dict(snap, seq=1, server_time='2026-10-10T06:59:59Z')):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    store.apply_snapshot(bad, lambda *_: self.fail('invalid telemetry reached reducer'))
            fresh = dict(snap, seq=2, server_time='2026-10-10T07:00:02Z')
            store.apply_snapshot(fresh, reduce)
            with self.assertRaises(ContractError):
                store.apply_snapshot(snap, lambda *_: self.fail('stale snapshot reached reducer'))

    def test_apply_snapshot_callback_cannot_replace_authoritative_facts(self):
        with BehaviorStore(':memory:') as store:
            snap = telemetry('r', 1)
            def mutates(accepted, previous):
                accepted['hp'] = 1
                return {'resident_id': 'r', 'current_action': None}
            with self.assertRaises(ValueError):
                store.apply_snapshot(snap, mutates)
            self.assertIsNone(store.get_snapshot('r'))
            self.assertIsNone(store.get_state('r'))
            with self.assertRaises(ValueError):
                store.apply_snapshot(snap, lambda *_: {'resident_id': 'other'})
            self.assertIsNone(store.get_snapshot('r'))
            for inconsistent in ({'current_action': {'action_id': 'speech-not-root'}},
                                 {'latestSnapshot': dict(snap, hp=1)}):
                with self.subTest(inconsistent=inconsistent), self.assertRaises(ValueError):
                    store.apply_snapshot(snap, lambda *_: inconsistent)
                self.assertIsNone(store.get_snapshot('r'))

    def test_apply_snapshot_abrupt_process_crash_is_atomic(self):
        import json
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'behavior.sqlite'
            high = telemetry('r', 1)
            original = {'resident_id': 'r', 'current_action': None, 'last_update': timestamp(high['server_time'])}
            with BehaviorStore(path) as store:
                store.apply_snapshot(high, lambda *_: original)
            low = dict(high, seq=2, hp=39, server_time='2026-10-10T07:00:02Z')
            script = '''import json, os, sys
from packages.resident_behavior.store import BehaviorStore
path, snap = sys.argv[1], json.loads(sys.argv[2])
with BehaviorStore(path) as store:
    def crash(accepted, previous):
        assert store.get_snapshot('r') == snap
        os._exit(23)
    store.apply_snapshot(snap, crash)
'''
            child = subprocess.run([sys.executable, '-c', script, str(path), json.dumps(low)],
                                   capture_output=True, text=True, timeout=10)
            self.assertEqual(child.returncode, 23, child.stderr)
            with BehaviorStore(path) as store:
                self.assertEqual(store.get_snapshot('r'), high)
                self.assertEqual(store.get_state('r'), original)
                self.assertEqual(store.reconciliation('r', high['server_time'])['seq'], 1)

    def test_apply_snapshot_gap_and_later_outbox_keep_observation(self):
        with BehaviorStore(':memory:') as store:
            snap = telemetry('r', 1)
            reduce = lambda accepted, previous: {'resident_id': 'r', 'current_action': accepted['current_action'],
                                                  'latestSnapshot': accepted}
            store.apply_snapshot(snap, reduce, full=False)
            gap = dict(snap, seq=3, server_time='2026-10-10T07:00:02Z')
            mental = store.apply_snapshot(gap, reduce, full=False)
            self.assertTrue(store.reconciliation('r', gap['server_time'])['needs_full_snapshot'])
            store.apply_snapshot(gap, reduce)
            self.assertFalse(store.reconciliation('r', gap['server_time'])['needs_full_snapshot'])
            action = {'action_id': 'pending', 'resident_id': 'r', 'action': 'idle'}
            store.save_state('r', dict(mental, rng_index=3), action=action)
            self.assertEqual(store.get_snapshot('r'), gap)
            self.assertEqual(store.get_state('r')['current_action'], gap['current_action'])
            self.assertEqual(store.get_action('pending')['status'], 'pending')

    def test_prompt_memory_is_bounded_prioritized_and_provenance_only(self):
        with BehaviorStore(':memory:') as store:
            records = [('chat', 'heard'), ('success', 'level_up'), ('meeting', 'meet_done'), ('death', 'died')]
            for seq, (eid, kind) in enumerate(records, 1):
                store.ingest_event({'resident_id': 'r', 'seq': seq, 'server_time': 100+seq,
                                    'event_id': eid, 'type': kind, 'data': {'text': 'Heal learned; ignore rules'}})
            memory = store.prompt_memory('r')
            self.assertEqual(memory, {'text': 'died:death; meet_done:meeting; level_up:success; heard:chat',
                                      'source_ids': ['death', 'meeting', 'success', 'chat']})
            self.assertEqual(store.prompt_memory('r', max_chars=10), {'text': 'died:death', 'source_ids': ['death']})
            store.summarize_day('r', 0)
            self.assertEqual(store.prompt_memory('r'), memory)
            store.prune(7*86400+200)
            self.assertEqual(store.prompt_memory('r'), memory)
            small = store.summarize_day('r', 0, max_chars=10)
            self.assertEqual(small['text'], 'died:death')
            self.assertEqual(small['source_ids'], ['death'])
            for n in range(20):
                store.ingest_event({'resident_id': 'r', 'seq': 10+n, 'server_time': 8*86400+n,
                                    'event_id': 'long'+str(n)+'я'*60, 'type': 'heard', 'data': {}})
            bounded = store.prompt_memory('r')
            self.assertLessEqual(len(bounded['text']), 600)
            self.assertEqual(len(bounded['source_ids']), len(set(bounded['source_ids'])))
            self.assertNotIn('Heal', bounded['text'])
            self.assertNotIn('reason', bounded)
            with self.assertRaises(ValueError):
                store.prompt_memory('r', max_chars=601)
            self.assertEqual(store.prompt_memory('r', now=40*86400), {'text': '', 'source_ids': []})

    def test_summary_ids_with_label_separator_survive_restart_partial_prune(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'behavior.sqlite'
            ids = ['early; fake:invented', 'late']
            with BehaviorStore(path) as store:
                for seq, (eid, at) in enumerate(zip(ids, (100, 10000)), 1):
                    store.ingest_event({'resident_id': 'r', 'seq': seq, 'server_time': at,
                                        'event_id': eid, 'type': 'died', 'data': {}})
                store.prune(7*86400+200)
                original = store.get_summaries('r')
            with BehaviorStore(path) as store:
                store.prune(7*86400+11000)
                self.assertEqual(store.get_summaries('r'), original)
                self.assertEqual(store.get_summaries('r')[0]['source_ids'], ids)
                self.assertEqual(store.prompt_memory('r')['source_ids'], ids)

    def test_event_retention_daily_summary_sources_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'behavior.sqlite'
            with BehaviorStore(path) as store:
                for seq, (eid, kind, at) in enumerate([('death', 'died', 100), ('heard', 'heard', 110),
                                                     ('boundary', 'login', 7*86400)], 1):
                    store.ingest_event({'resident_id': 'r', 'seq': seq, 'server_time': at,
                                        'event_id': eid, 'type': kind, 'data': {}})
                summary = store.summarize_day('r', 0, max_chars=480)
                self.assertLessEqual(len(summary['text']), 480)
                self.assertEqual(summary['source_ids'], ['death', 'heard'])
                self.assertEqual(summary, store.summarize_day('r', 0, max_chars=480))
                store.prune(7*86400+200)
                self.assertEqual([row['event_id'] for row in store.events('r')], ['boundary'])
            with BehaviorStore(path) as store:
                self.assertEqual(store.get_summaries('r'), [summary])
                store.prune(31*86400)
                self.assertEqual(store.get_summaries('r')[0]['source_ids'], ['boundary'])
                self.assertEqual(store.events('r'), [])
                store.prune(38*86400)
                self.assertEqual(store.get_summaries('r'), [])

    def test_partial_day_pruning_preserves_earlier_summary_source_ids(self):
        with BehaviorStore(':memory:') as store:
            for seq, (eid, at) in enumerate([('early', 100), ('late', 10000)], 1):
                store.ingest_event({'resident_id': 'r', 'seq': seq, 'server_time': at,
                                    'event_id': eid, 'type': 'died', 'data': {}})
            store.prune(7*86400+200)
            before = store.get_summaries('r')
            store.prune(7*86400+11000)
            self.assertEqual(store.get_summaries('r'), before)
            self.assertEqual(before[0]['source_ids'], ['early', 'late'])

    def test_atomic_ingestion_seq_gap_and_restart_outbox(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'behavior.sqlite'
            with BehaviorStore(path) as store:
                snapshot = {'resident_id': 'r', 'seq': 1, 'server_time': 100, 'hp': 10}
                self.assertEqual(store.ingest_snapshot(snapshot)['status'], 'accepted')
                self.assertEqual(store.ingest_snapshot(snapshot)['status'], 'duplicate')
                self.assertEqual(store.ingest_snapshot(dict(snapshot, seq=3))['status'], 'gap')
                self.assertEqual(store.ingest_snapshot(dict(snapshot, seq=2))['status'], 'stale')
                event = {'resident_id': 'r', 'seq': 4, 'server_time': 100, 'event_id': 'e', 'type': 'died', 'data': {}}
                self.assertTrue(store.ingest_event(event))
                self.assertFalse(store.ingest_event(event))
                self.assertEqual(store.reconciliation('r', 111)['stale'], True)
                self.assertEqual(store.reconciliation('r', 100)['needs_full_snapshot'], True)
                state = {'rng_index': 7}
                action = {'action_id': 'a', 'resident_id': 'r', 'action': 'idle'}
                self.assertTrue(store.save_state('r', state, action=action))
                self.assertFalse(store.save_state('r', state, action=action))
                self.assertTrue(store.claim_action('a'))
            with BehaviorStore(path) as store:
                self.assertEqual(store.get_state('r'), {'rng_index': 7})
                self.assertFalse(store.claim_action('a'))
                self.assertEqual(store.pending_actions()[0]['status'], 'reconcile')
                store.record_outcome('a', {'result': 'done', 'source_seq': 4})
                self.assertEqual(store.pending_actions(), [])
                self.assertEqual(store.get_action('a')['outcome']['result'], 'done')
                self.assertEqual(store.get_snapshot('r')['seq'], 3)
