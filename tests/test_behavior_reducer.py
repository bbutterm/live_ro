import copy
import unittest

from packages.resident_behavior.reducer import initial_state, reduce_event, advance, timestamp


def config():
    return {'residents': {'r': {'traits': {'stamina': 1}, 'initial_drives': {}, 'goals': [{'id': 'cash', 'type': 'zeny', 'target': 100}], 'chronotype': 'lark', 'home_place': 'home'}},
            'drives': {'tired': {'initial': 20, 'per_minute': {'hunt': 2, 'idle': -1}, 'on_event': {'died': 8}, 'trait_modifiers': {'stamina': -.5}, 'from_state': ''}},
            'mood': {'states': {'calm': {}, 'scared': {'decay_per_minute': .7}}, 'on_event': {'died': {'scared': 70}}},
            'places': {'home': {'map': 'town', 'x': 1, 'y': 1}},
            'relationships': {'initial': {'familiarity': 0, 'liking': 0, 'trust': 25}, 'on_event': {'social_turn': {'trust': 1}}}}


class ReducerTests(unittest.TestCase):
    def test_timestamp_accepts_aware_clock_datetime_but_not_naive(self):
        from datetime import datetime, timezone
        aware = datetime(2026, 10, 10, 7, tzinfo=timezone.utc)
        self.assertEqual(timestamp(aware), timestamp('2026-10-10T07:00:00Z'))
        with self.assertRaises(ValueError):
            timestamp(aware.replace(tzinfo=None))

    def test_advance_initializes_only_empty_legacy_mental_without_fabricated_crossing(self):
        c = config()
        c['drives']['fear'] = {'initial': 0, 'on_event': {'low_hp': 15}}
        snap = {'seq': 2, 'hp': 39, 'hp_max': 100, 'zeny': 50, 'current_action': None}
        empty = {}
        state = advance(c, 'r', empty, snap, 43200)
        self.assertEqual(state, initial_state(c, 'r', snap, 43200))
        self.assertEqual(empty, {})
        self.assertEqual(state['drives']['fear'], 0)
        self.assertFalse(any(row['kind'] == 'low_hp' for row in state['audit']))
        with self.assertRaises(KeyError):
            advance(c, 'r', {'rng_index': 7}, snap, 43200)

    def test_event_once_and_pure_positive_modifier(self):
        c = config()
        s = initial_state(c, 'r', {'zeny': 50}, 43200)
        before = copy.deepcopy(s)
        event = {'event_id': 'death', 'type': 'died', 'server_time': 43200, 'data': {}}
        result = reduce_event(c, 'r', s, event, {'zeny': 50})
        self.assertEqual(result['drives']['tired'], 24)
        self.assertEqual(result['active_mood'], 'scared')
        self.assertEqual(s, before)
        self.assertEqual(reduce_event(c, 'r', result, event, {}), result)
        self.assertEqual(result['goals'][0]['original_gap'], 50)
        self.assertEqual(result['rng_index'], s['rng_index'])

    def test_advance_partial_time_goal_observations_and_unknown(self):
        c = config()
        snap = {'zeny': 50, 'hp': 60, 'hp_max': 100, 'inventory': {}, 'current_action': {'action': 'hunt', 'args': {}, 'result': None}}
        s = initial_state(c, 'r', snap, 43200)
        half = advance(c, 'r', s, snap, 43230)
        self.assertAlmostEqual(half['drives']['tired'], 20.5)
        full = advance(c, 'r', half, snap, 43260)
        self.assertAlmostEqual(full['drives']['tired'], 21)
        self.assertEqual(full['derived']['hp_pct'], 60)
        done = advance(c, 'r', full, dict(snap, zeny=120), 43260)
        self.assertTrue(done['goals'][0]['completed'])
        lower = advance(c, 'r', done, dict(snap, zeny=0), 43260)
        self.assertTrue(lower['goals'][0]['completed'])
        unknown = advance(c, 'r', lower, {}, 43260)
        self.assertIsNone(unknown['derived']['hp_pct'])
        self.assertNotIn('hp', unknown)

    def test_low_hp_rearm_recovery_and_promise_terminal(self):
        c = config()
        c['drives']['fear'] = {'initial': 0, 'on_event': {'low_hp': 15}}
        c['drives']['duty'] = {'initial': 0, 'on_event': {'meet_agreed': 45}, 'from_state': '80*promise_due'}
        snap = {'hp': 70, 'hp_max': 100, 'current_action': None, 'agreements': []}
        s = initial_state(c, 'r', snap, 43200)
        low = advance(c, 'r', s, dict(snap, hp=39), 43200)
        self.assertEqual(low['drives']['fear'], 15)
        self.assertEqual(advance(c, 'r', low, dict(snap, hp=38), 43200)['drives']['fear'], 15)
        reset = advance(c, 'r', low, snap, 43200)
        self.assertEqual(advance(c, 'r', reset, dict(snap, hp=39), 43200)['drives']['fear'], 30)
        reset['drives']['tired'] = 90
        locked = advance(c, 'r', reset, snap, 43200)
        self.assertEqual(locked['derived']['recovery_lock'], 1)
        locked['drives']['tired'] = 60
        self.assertEqual(advance(c, 'r', locked, snap, 44100)['derived']['recovery_lock'], 0)
        promised = dict(snap, agreements=[{'status': 'accepted', 'participants': ['r', 'other']}])
        duty = advance(c, 'r', s, promised, 43260)
        self.assertEqual(duty['derived']['promise_due'], 1)
        self.assertEqual(duty['drives']['duty'], 8)
        cleared = advance(c, 'r', duty, snap, 43260)
        self.assertEqual(cleared['drives']['duty'], 0)

    def test_relations_require_social_echo_not_claimed_healing(self):
        c = config()
        s = initial_state(c, 'r', {}, 43200)
        fake = {'event_id': 'fake', 'type': 'social_turn', 'server_time': 43200, 'data': {'peer': 'p'}}
        s = reduce_event(c, 'r', s, fake, {})
        self.assertEqual(s['relations'], {})
        heard = {'event_id': 'heard', 'type': 'heard', 'server_time': 43200, 'data': {'from': 'p', 'channel': 'public', 'text': 'привет', 'addressed_to': 'r'}}
        s = reduce_event(c, 'r', s, heard, {})
        echo = {'event_id': 'echo', 'type': 'heard', 'server_time': 43210, 'data': {'from': 'r', 'channel': 'public', 'text': 'привет'}}
        s = reduce_event(c, 'r', s, echo, {})
        self.assertEqual(s['relations']['p']['trust'], 26)
        self.assertEqual(s['audit'][-1]['source_ids'], ['heard', 'echo'])
        healing = {'event_id': 'heal', 'type': 'healed', 'server_time': 43211, 'data': {'peer': 'p'}}
        self.assertEqual(reduce_event(c, 'r', s, healing, {})['relations'], s['relations'])

    def test_trade_events_do_not_double_zeny_and_goals_emit_once(self):
        c = config()
        c['drives']['money'] = {'initial': 50, 'on_event': {'bought': 5, 'zeny_loss': 8, 'zeny_gain': -4}}
        c['drives']['boast'] = {'initial': 0, 'on_event': {'goal_done': 25}}
        s = initial_state(c, 'r', {'zeny': 50}, 43200)
        bought = {'event_id': 'buy', 'type': 'bought', 'server_time': 43210, 'data': {'amount': 10}}
        s = reduce_event(c, 'r', s, bought, {'zeny': 40})
        change = {'event_id': 'change', 'type': 'zeny_changed', 'server_time': 43210, 'data': {'before': 50, 'after': 40}}
        s = reduce_event(c, 'r', s, change, {'zeny': 40})
        self.assertEqual(s['drives']['money'], 55)
        gain = dict(change, event_id='gain', server_time=43300, data={'before': 40, 'after': 50})
        s = reduce_event(c, 'r', s, gain, {'zeny': 50})
        self.assertEqual(s['drives']['money'], 55)  # Await the two-second trade window.
        s = advance(c, 'r', s, {'zeny': 50}, 43303)
        self.assertEqual(s['drives']['money'], 51)
        s = advance(c, 'r', s, {'zeny': 100, 'seq': 9}, 43303)
        self.assertEqual(s['drives']['boast'], 25)
        self.assertEqual(advance(c, 'r', s, {'zeny': 100, 'seq': 10}, 43303)['drives']['boast'], 25)
        self.assertEqual(s['audit'][-1]['source_ids'], ['snapshot:r:9'])

    def test_trade_dedup_both_orders_and_one_to_one_after_settlement(self):
        c = config()
        c['drives']['money'] = {'initial': 98, 'on_event': {'bought': 5, 'zeny_loss': 8}}
        bought = {'event_id': 'buy', 'type': 'bought', 'server_time': 43210, 'data': {'amount': 10}}
        change = {'event_id': 'change', 'type': 'zeny_changed', 'server_time': 43210,
                  'data': {'before': 50, 'after': 40}}
        outcomes = []
        for events in ([bought, change], [change, bought]):
            s = initial_state(c, 'r', {}, 43200)
            for event in events:
                s = reduce_event(c, 'r', s, event, {})
            s = advance(c, 'r', s, {}, 43213)
            outcomes.append(s['drives']['money'])
            matched = [row for row in s['audit'] if row['kind'] == 'trade_zeny_dedup']
            self.assertEqual(set(matched[0]['source_ids']), {'buy', 'change'})
        self.assertEqual(outcomes, [100, 100])
        c['drives']['money']['initial'] = 20
        s = initial_state(c, 'r', {}, 43200)
        for event in [change, dict(change, event_id='change2'), bought]:
            s = reduce_event(c, 'r', s, event, {})
        s = advance(c, 'r', s, {}, 43213)
        self.assertEqual(s['drives']['money'], 33)

    def test_break_due_postponed_by_accepted_meet_and_completed_home_idle(self):
        c = config()
        c['imperfections'] = {'break_after_awake_minutes': [90, 90], 'break_minutes': [6, 6]}
        snap = {'map': 'town', 'x': 1, 'y': 1, 'agreements': []}
        s = initial_state(c, 'r', snap, 43200)
        self.assertEqual(s['derived']['break_due'], 1)
        promised = dict(snap, agreements=[{'status': 'accepted', 'participants': ['r', 'p']}])
        s = advance(c, 'r', s, promised, 43200)
        self.assertEqual(s['derived']['break_due'], 0)
        s = advance(c, 'r', s, snap, 43200)
        self.assertEqual(s['derived']['break_due'], 1)
        idle = dict(snap, current_action={'action': 'idle', 'action_id': 'break', 'result': None})
        s = advance(c, 'r', s, idle, 43200)
        s = advance(c, 'r', s, idle, 43560)
        self.assertEqual(s['derived']['break_due'], 0)
        self.assertGreater(s['schedule']['break_after_at'], 43560)

    def test_hunt_streak_uses_observed_field_time_not_started_at(self):
        c = config()
        snap = {'map': 'town', 'hp': 100, 'hp_max': 100, 'alive': True,
                'current_action': {'action_id': 'h', 'action': 'hunt', 'args': {'map': 'field'},
                                   'started_at': 0, 'result': None}}
        s = initial_state(c, 'r', snap, 43200)
        s = advance(c, 'r', s, snap, 43260)
        self.assertEqual(s['derived']['hunt_streak'], 0)
        field = dict(snap, map='field')
        s = advance(c, 'r', s, field, 43260)
        for now in range(43262, 43862, 2):
            s = advance(c, 'r', s, field, now)
        self.assertAlmostEqual(s['derived']['hunt_streak'], 10)
        # Changing action ids does not reset accumulated hunting time.
        field = copy.deepcopy(field)
        field['current_action']['action_id'] = 'h2'
        s = advance(c, 'r', s, field, 43862)
        self.assertGreater(s['derived']['hunt_streak'], 10)
        stopped = dict(field, current_action=None)
        s = advance(c, 'r', s, stopped, 43862)
        s = advance(c, 'r', s, stopped, 44762)
        self.assertEqual(s['derived']['hunt_streak'], 0)

    def test_safe_hunt_requires_continuous_observed_hp_and_done(self):
        c = config()
        c['drives']['fear'] = {'initial': 20, 'on_event': {'safe_hunt': -4}}
        snap = {'map': 'field', 'hp': 100, 'hp_max': 100, 'alive': True,
                'current_action': {'action_id': 'h', 'action': 'hunt', 'args': {'map': 'field'}, 'result': None}}
        s = initial_state(c, 'r', snap, 43200)
        for now in range(43202, 43502, 2):
            s = advance(c, 'r', s, dict(snap, seq=now), now)
        done = copy.deepcopy(snap)
        done['current_action']['result'] = 'done'
        s = advance(c, 'r', s, dict(done, seq=43502), 43502)
        self.assertEqual(s['drives']['fear'], 16)
        self.assertEqual(s['audit'][-1]['kind'], 'safe_hunt')
        self.assertTrue(s['audit'][-1]['source_ids'])
        self.assertEqual(advance(c, 'r', s, done, 43502)['drives']['fear'], 16)
        for bad in ({'hp': 54}, {'hp': None}, {'alive': False}):
            s = initial_state(c, 'r', snap, 43200)
            for now in range(43202, 43502, 2):
                s = advance(c, 'r', s, dict(snap, **(bad if now == 43300 else {})), now)
            self.assertEqual(advance(c, 'r', s, done, 43502)['drives']['fear'], 20)
        sparse = initial_state(c, 'r', snap, 43200)
        self.assertEqual(advance(c, 'r', sparse, done, 43502)['drives']['fear'], 20)

    def test_goals_escalate_next_morning_and_decay_with_seven_day_cooldown(self):
        c = config()
        c['residents']['r']['goals'] = [
            {'id': 'cash', 'type': 'zeny', 'target': 101},
            {'id': 'base', 'type': 'base_level', 'target': 40},
            {'id': 'job', 'type': 'job_level', 'target': 15},
            {'id': 'item', 'type': 'item_count', 'item_id': 909, 'target': 20}]
        snap = {'zeny': 101, 'base_level': 40, 'job_level': 15, 'inventory': {'909': 20}}
        s = initial_state(c, 'r', snap, 43200)
        s = advance(c, 'r', s, snap, 86400+21600)
        self.assertEqual([g['target'] for g in s['goals']], [142, 45, 18, 30])
        self.assertTrue(all(not g['completed'] for g in s['goals']))
        self.assertEqual(advance(c, 'r', s, snap, 86400+21600)['goals'], s['goals'])
        c = config()
        s = initial_state(c, 'r', {'zeny': 10}, 43200)
        s = advance(c, 'r', s, {'zeny': 10}, 43200+7*86400)
        self.assertEqual(s['goals'][0]['target'], 80)
        self.assertEqual(advance(c, 'r', s, {'zeny': 10}, 43200+8*86400)['goals'][0]['target'], 80)
        s = advance(c, 'r', s, {'zeny': 10}, 43200+14*86400)
        self.assertEqual(s['goals'][0]['target'], 64)
        self.assertEqual(s['audit'][-1]['kind'], 'goal_decay')

    def test_meet_missed_requires_accepted_deadline_and_uninterrupted_online_alive(self):
        c = config()
        c['drives']['lonely'] = {'initial': 20, 'on_event': {'meet_missed': 8}}
        c['relationships']['on_event']['meet_missed'] = {'liking': -3, 'trust': -6}
        agreement = {'meeting_id': 'm', 'participants': ['r', 'p'], 'place': 'home',
                     'accepted_at': 43200, 'deadline': 43800, 'status': 'accepted'}
        snap = {'alive': True, 'agreements': [agreement]}
        for blocker in (None, 'arrived', 'logout', 'failure', 'died'):
            s = initial_state(c, 'r', snap, 43200)
            for now in range(43202, 43800, 2):
                s = advance(c, 'r', s, dict(snap, seq=now), now)
                if blocker and now == 43300:
                    event = {'event_id': blocker, 'server_time': now, 'type': blocker,
                             'data': {'place': 'home', 'map': 'town', 'x': 1, 'y': 1}}
                    s = reduce_event(c, 'r', s, event, snap)
            missed = dict(snap, seq=43800, agreements=[dict(agreement, status='missed')])
            s = advance(c, 'r', s, missed, 43800)
            self.assertEqual(s['drives']['lonely'], 28 if blocker is None else 20)
            self.assertEqual(s['relations'].get('p', {}).get('trust'), 19 if blocker is None else None)
            self.assertEqual(advance(c, 'r', s, missed, 43800)['relations'], s['relations'])
        s = initial_state(c, 'r', {'alive': None}, 43200)
        forged = {'event_id': 'forged', 'server_time': 43800, 'type': 'meet_missed', 'data': {'peer': 'p'}}
        self.assertEqual(reduce_event(c, 'r', s, forged, {})['drives']['lonely'], 20)
        self.assertEqual(advance(c, 'r', s, missed, 43800)['relations'], {})

    def test_retention_expires_dedup_without_new_events_and_goals_track_high_water(self):
        c = config()
        s = initial_state(c, 'r', {'zeny': 50}, 43200)
        event = {'event_id': 'death', 'type': 'died', 'server_time': 43200, 'data': {}}
        s = reduce_event(c, 'r', s, event, {})
        s = advance(c, 'r', s, {'zeny': 40}, 43260)
        s = advance(c, 'r', s, {'zeny': 50}, 43320)
        self.assertEqual(s['goals'][0]['last_progress_at'], 43200)
        s = advance(c, 'r', s, {'zeny': 50}, 43200+7*86400+1)
        self.assertNotIn('death', s['event_ids'])
        self.assertFalse(any(row.get('event_id') == 'death' for row in s['audit']))
        # The seven-day window is also checked before an incoming duplicate lookup.
        old = initial_state(c, 'r', {}, 43200)
        old = reduce_event(c, 'r', old, event, {})
        repeated = dict(event, server_time=43200+7*86400+1)
        self.assertEqual(reduce_event(c, 'r', old, repeated, {})['drives']['tired'], 28)

    def test_sleep_schedule_reproducible_minimum_seven_hours(self):
        c = config()
        snap = {'map': 'town', 'x': 1, 'y': 1, 'zeny': 0}
        a = initial_state(c, 'r', snap, 3600)
        b = initial_state(c, 'r', snap, 3600)
        self.assertEqual(a, b)
        self.assertGreaterEqual(a['schedule']['wake_at']-a['schedule']['sleep_at'], 7*3600)
        self.assertTrue(a['derived']['sleeping'])
        later = advance(c, 'r', a, snap, 3660)
        self.assertAlmostEqual(later['drives']['tired'], 19.65)
        self.assertEqual(later['active_mood'], 'calm')
