import copy
import unittest

from packages.resident_behavior import contracts as c


TIME = '2026-10-10T07:00:00Z'


def envelope(resident='mira', seq=1):
    return dict(version=1, resident_id=resident, seq=seq, server_time=TIME)


def snapshot(resident='mira', seq=1):
    p = envelope(resident, seq)
    p.update(map='prontera', x=156, y=191, hp=100, hp_max=100,
             sp=10, sp_max=20, weight=1, weight_max=100, zeny=100,
             inventory={'501': 2}, base_level=20, job_level=10,
             free_skill_points=0, alive=True, sitting=False, online_minutes=20,
             current_action=None, near_players=[], near_monsters=[], agreements=[])
    return p


def execute(action='idle', args=None, action_id='a1', resident='mira', seq=1):
    p = envelope(resident, seq)
    p.update(decision_id='d1', action_id=action_id, state_seq=1,
             expires_at='2026-10-10T07:00:30Z', action=action,
             args={'minutes': 1} if args is None else args, say=None)
    return p


class ContractTests(unittest.TestCase):
    def test_snapshot_strict_nullable_and_no_aliases(self):
        p = snapshot()
        self.assertEqual(c.validate_telemetry(p), p)
        got = c.validate_telemetry(p)
        got['inventory']['501'] = 9
        self.assertEqual(p['inventory']['501'], 2)
        for key in ('hp', 'hp_max', 'map', 'alive', 'inventory', 'near_players'):
            q = snapshot(); q[key] = None
            self.assertEqual(c.validate_telemetry(q), q)
        for key, value in [('hp', True), ('hp', 1.5), ('hp_max', 0), ('zeny', -1),
                           ('alive', 1), ('version', True), ('seq', False),
                           ('online_minutes', float('nan')), ('extra', 1),
                           ('server_time', '2026-10-10T07:00:00+01:00')]:
            q = snapshot(); q[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(c.ContractError):
                c.validate_telemetry(q)
        q = snapshot(); del q['sp']
        with self.assertRaises(c.ContractError): c.validate_telemetry(q)
        q = snapshot(); q['resident_id'] = '\ud800'
        with self.assertRaises(c.ContractError): c.validate_telemetry(q)

    def test_all_actions_and_exact_bounds(self):
        menu = {'say': {'text': 'Привет'}, 'whisper': {'to': 'Novi', 'text': 'Сек'},
                'walk_to': {'place': 'square'}, 'hunt': {'map': 'prt_fild08', 'minutes': 5},
                'rest': {'until_hp': 90}, 'town_service': {},
                'follow': {'player': 'Novi', 'minutes': 15},
                'meet': {'resident': 'borin', 'place': 'square'}, 'idle': {'minutes': 10}}
        for action, args in menu.items():
            p = execute(action, args)
            self.assertEqual(c.validate_execute(p), p)
            q = copy.deepcopy(p); q['args']['extra'] = 1
            with self.assertRaises(c.ContractError): c.validate_execute(q)
        for action, args in [('hunt', {'map': 'prt_fild07', 'minutes': 5}),
                             ('hunt', {'map': 'prt_fild08', 'minutes': 31}),
                             ('idle', {'minutes': True}), ('rest', {'until_hp': 1.1}),
                             ('walk_to', {'place': 'unknown'}),
                             ('meet', {'resident': 'mira', 'place': 'square'}),
                             ('whisper', {'to': 'x'*25, 'text': 'Hi'})]:
            with self.subTest(action=action), self.assertRaises(c.ContractError):
                c.validate_execute(execute(action, args))
        q = execute(); q['expires_at'] = TIME
        with self.assertRaises(c.ContractError): c.validate_execute(q)

    def test_hunt_continuous_minutes_execute_and_current_action(self):
        for minutes in (5, 5.0, 10.283352323250373, 20.0, 30.0):
            with self.subTest(minutes=minutes):
                args = {'map': 'prt_fild08', 'minutes': minutes}
                p = execute('hunt', args)
                self.assertEqual(c.validate_execute(p), p)
                q = snapshot()
                q['current_action'] = dict(action_id='a1', action='hunt', args=args,
                                          started_at=TIME, result=None, error_code=None)
                self.assertEqual(c.validate_telemetry(q), q)
        for minutes in (True, False, float('nan'), float('inf'), -float('inf'), 4.99, 30.01, '10', None):
            with self.subTest(minutes=minutes), self.assertRaises(c.ContractError):
                c.validate_execute(execute('hunt', {'map': 'prt_fild08', 'minutes': minutes}))
        for action, args in [('follow', {'player': 'Novi', 'minutes': 5.5}),
                             ('idle', {'minutes': 5.5})]:
            with self.subTest(action=action), self.assertRaises(c.ContractError):
                c.validate_execute(execute(action, args))

    def test_events_nested_and_result_contracts(self):
        for kind, fields in c.EVENT_FIELDS.items():
            values = {'from': 'Novi', 'channel': 'public', 'text': 'Hi', 'map': 'prontera',
                      'place': 'square', 'action_id': 'a', 'error_code': 'no_path'}
            p = envelope(); p.update(event_id='e', type=kind,
                                    data={k: values.get(k, 1) for k in fields})
            self.assertEqual(c.validate_event(p), p)
            p['data']['extra'] = 0
            with self.assertRaises(c.ContractError): c.validate_event(p)
        q = snapshot(); q['near_players'] = [{'name': 'Novi', 'id': 'x', 'x': 1, 'y': 1}]
        with self.assertRaises(c.ContractError): c.validate_telemetry(q)
        q['near_players'] = [{'id': 'x', 'x': None, 'y': 1}]
        self.assertEqual(c.validate_telemetry(q), q)
        ack = {'action_id': 'a', 'accepted': True, 'error_code': None}
        self.assertEqual(c.validate_ack(ack), ack)
        ack['error_code'] = 'no_funds'
        with self.assertRaises(c.ContractError): c.validate_ack(ack)
        result = dict(action_id='a', result='done', error_code=None, source_seq=1, completed_at=TIME)
        self.assertEqual(c.validate_result(result), result)
        result['source_seq'] = None
        with self.assertRaises(c.ContractError): c.validate_result(result)
        p = envelope(); p.update(meeting_id='m', initiator='mira', resident='borin', place='square', state_seq=1)
        self.assertEqual(c.validate_meeting(p), p)
        p['resident'] = 'mira'
        with self.assertRaises(c.ContractError): c.validate_meeting(p)

    def test_hostile_json_shapes_only_raise_contract_error(self):
        for validator, base in [(c.validate_telemetry, snapshot()), (c.validate_execute, execute())]:
            for key in base:
                for value in ([], {}, True, 0, 1.5, '', None):
                    p = copy.deepcopy(base); p[key] = value
                    try: validator(p)
                    except c.ContractError: pass
                    except Exception as exc: self.fail(f'{validator.__name__} {key}: {type(exc).__name__}')
        p = envelope(); p.update(event_id='e', type='failure', data={'action_id':'a', 'error_code': []})
        with self.assertRaises(c.ContractError): c.validate_event(p)
        p = snapshot(); p['agreements'] = [dict(meeting_id='m', participants=['mira', 'borin'], place='square', accepted_at=TIME, deadline='2026-10-10T07:10:00Z', status=[])]
        with self.assertRaises(c.ContractError): c.validate_telemetry(p)


if __name__ == '__main__':
    unittest.main()
