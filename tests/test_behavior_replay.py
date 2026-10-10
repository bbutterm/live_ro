import copy
import unittest
from packages.resident_behavior.replay import replay
from tests.test_behavior_reducer import config


class ReplayTests(unittest.TestCase):
    def test_joint_hunt_requires_both_kills_continuous_nearby_field_samples(self):
        c = config()
        c['residents']['p'] = copy.deepcopy(c['residents']['r'])
        c['relationships']['on_event']['joint_hunt'] = {'trust': 3, 'familiarity': 4}
        records = []
        for now in range(43200, 43502, 2):
            for rid in ('r', 'p'):
                records.append({'kind': 'snapshot', 'resident_id': rid, 'seq': now,
                                'server_time': now, 'alive': True, 'map': 'field',
                                'x': 10 if rid == 'r' else 22, 'y': 10, 'hp': 100, 'hp_max': 100,
                                'current_action': {'action': 'hunt', 'action_id': rid+'h',
                                                   'args': {'map': 'field'}, 'result': None}})
        for rid in ('r', 'p'):
            records.append({'resident_id': rid, 'seq': 43301, 'server_time': 43300,
                            'event_id': rid+'kill', 'type': 'kill', 'data': {'monster_id': 1002}})
        result = replay(c, records)
        self.assertEqual(result['states']['r']['relations']['p']['trust'], 28)
        self.assertEqual(result, replay(c, list(reversed(records))))
        derived = [row for row in result['audit'] if row['kind'] == 'joint_hunt']
        self.assertEqual(len(derived), 2)
        self.assertTrue({'rkill', 'pkill'} <= set(derived[0]['source_ids']))
        variants = [
            [r for r in records if r.get('event_id') != 'pkill'],
            [dict(r, x=23) if r['resident_id'] == 'p' and 'event_id' not in r else r for r in records],
            [r for r in records if r['server_time'] <= 43200 or r['server_time'] >= 43400],
        ]
        for variant in variants:
            self.assertEqual(replay(c, variant)['states']['r']['relations'], {})

    def test_order_events_before_ticks_dedup_and_repeatability(self):
        c = config()
        records = [
            {'kind': 'snapshot', 'resident_id': 'r', 'seq': 1, 'server_time': 43200, 'zeny': 50},
            {'kind': 'snapshot', 'resident_id': 'r', 'seq': 3, 'server_time': 43260, 'zeny': 50},
            {'kind': 'event', 'resident_id': 'r', 'seq': 2, 'server_time': 43260, 'event_id': 'd', 'type': 'died', 'data': {}},
        ]
        before = copy.deepcopy(records)
        result = replay(c, records)
        self.assertEqual(result, replay(c, list(reversed(records))))
        self.assertEqual(records, before)
        self.assertEqual(result['states']['r']['drives']['tired'], 23)
        self.assertEqual(result['audit'][0]['source_ids'], ['d'])
        self.assertEqual(result['evidence'], 'input_records_not_live_server_verification')
        self.assertEqual(replay(c, records+[records[2]]), result)
