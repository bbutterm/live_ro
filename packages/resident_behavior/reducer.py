"""Pure JSON mental-state reducer. Server observations are never modified."""
from copy import deepcopy
from datetime import datetime, timezone

DAY = 86400


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('UTC timestamp requires timezone')
    return parsed.timestamp()


def clamp(value, low=0, high=100):
    return max(low, min(high, value))


def _measure(goal, snapshot):
    if goal['type'] == 'item_count':
        inventory = snapshot.get('inventory')
        return None if inventory is None else inventory.get(str(goal['item_id']), inventory.get(goal['item_id'], 0))
    return snapshot.get(goal['type'])


def initial_state(config, rid, snapshot, now):
    now = timestamp(now)
    resident = config['residents'][rid]
    goals = deepcopy(resident.get('goals', []))
    for goal in goals:
        value = _measure(goal, snapshot)
        goal.update(progress=value, peak_progress=value, original_gap=None if value is None else max(0, goal['target']-value),
                    completed=value is not None and value >= goal['target'], created_at=now,
                    last_progress_at=now, completed_at=now if value is not None and value >= goal['target'] else None)
    state = {'resident_id': rid, 'drives': {name: resident.get('initial_drives', {}).get(name, spec.get('initial', 0)) for name, spec in config['drives'].items()},
            'mood': {name: 0 for name in config.get('mood', {}).get('states', {})}, 'active_mood': 'calm',
            'goals': goals, 'relations': {}, 'derived': {}, 'rng_index': 0, 'schedule': {},
            'current_action': deepcopy(snapshot.get('current_action')), 'last_actions': [],
            'last_update': now, 'last_tick': now, 'event_ids': {}, 'audit': [], 'offline': False}
    _hunt_observation(config, rid, state, snapshot, now)
    _meet_observation(config, rid, state, snapshot, now)
    _observe(config, rid, state, snapshot, now)
    return state


def _source(rid, snapshot):
    return f'snapshot:{rid}:{snapshot.get("seq", "unknown")}'


def _hunt_observation(config, rid, state, snapshot, now):
    """Only real fresh samples establish hunting; never trust a planned timer."""
    observed_at = timestamp(snapshot.get('server_time', now))
    action = snapshot.get('current_action') or {}
    target = action.get('args', {}).get('map')
    fresh = 0 <= now-observed_at <= 10
    hunting = (fresh and not state['offline'] and snapshot.get('alive') is True
               and action.get('action') == 'hunt' and action.get('result') is None
               and target is not None and snapshot.get('map') == target)
    previous = state.get('hunt_observation', {})
    dt = observed_at-previous.get('time', observed_at)
    continuous = 0 <= dt <= 10
    streak = state['derived'].get('hunt_streak', 0)
    if hunting:
        if previous.get('hunting') and continuous:
            streak += dt/60
        state['last_hunt_at'] = observed_at
    state.setdefault('last_hunt_at', now)
    if now-state['last_hunt_at'] >= 900:
        streak = 0
    state['derived']['hunt_streak'] = streak
    evidence = state.get('safe_hunt_observation')
    aid = action.get('action_id')
    if hunting and aid:
        if not evidence or evidence['action_id'] != aid:
            evidence = {'action_id': aid, 'seconds': 0, 'valid': True, 'source_ids': []}
            state['safe_hunt_observation'] = evidence
        elif previous.get('hunting') and continuous:
            evidence['seconds'] += dt
        else:
            evidence['valid'] = False
        hp, maximum = snapshot.get('hp'), snapshot.get('hp_max')
        evidence['valid'] = bool(evidence['valid'] and hp is not None and maximum and 100*hp/maximum >= 55)
        source = _source(rid, snapshot)
        if source not in evidence['source_ids']:
            evidence['source_ids'].append(source)
    elif evidence and aid == evidence['action_id'] and action.get('result') == 'done':
        hp, maximum = snapshot.get('hp'), snapshot.get('hp_max')
        valid = (evidence['valid'] and evidence['seconds'] >= 300 and continuous and fresh
                 and snapshot.get('alive') is True and not state['offline']
                 and target is not None and snapshot.get('map') == target
                 and hp is not None and maximum and 100*hp/maximum >= 55)
        if valid and not evidence.get('emitted'):
            _apply(config, rid, state, 'safe_hunt')
            state['audit'].append({'type': 'derived', 'kind': 'safe_hunt', 'action_id': aid,
                                   'source_ids': evidence['source_ids']+[_source(rid, snapshot)], 'server_time': now})
            evidence['emitted'] = True
        evidence['valid'] = False
    elif evidence:
        evidence['valid'] = False
    if observed_at >= previous.get('time', observed_at):
        state['hunt_observation'] = {'time': observed_at, 'hunting': hunting}


def _meet_observation(config, rid, state, snapshot, now):
    sessions = state.setdefault('meeting_observations', {})
    observed_at = timestamp(snapshot.get('server_time', now))
    live = snapshot.get('alive') is True and not state['offline'] and 0 <= now-observed_at <= 10
    for agreement in snapshot.get('agreements') or []:
        mid = agreement.get('meeting_id')
        people = agreement.get('participants', [])
        if not mid or len(people) != 2 or len(set(people)) != 2 or rid not in people:
            continue
        accepted_at, deadline = agreement.get('accepted_at'), agreement.get('deadline')
        if accepted_at is None or deadline is None:
            continue
        accepted_at, deadline = timestamp(accepted_at), timestamp(deadline)
        if accepted_at > observed_at or deadline <= accepted_at:
            continue
        session = sessions.get(mid)
        if agreement.get('status') == 'accepted' and not session:
            session = {'peer': next(p for p in people if p != rid), 'place': agreement.get('place'),
                       'accepted_at': accepted_at, 'deadline': deadline, 'last_at': observed_at,
                       'valid': live and observed_at-accepted_at <= 10, 'arrived': False,
                       'source_ids': [_source(rid, snapshot)]}
            sessions[mid] = session
        if not session:
            continue  # A terminal assertion alone is not evidence of an accepted meeting.
        session['valid'] = bool(session['valid'] and live and 0 <= observed_at-session['last_at'] <= 10
                                and session['deadline'] == deadline and session['accepted_at'] == accepted_at
                                and session['peer'] in people and session['place'] == agreement.get('place'))
        session['last_at'] = max(session['last_at'], observed_at)
        place = config.get('places', {}).get(session['place'], {})
        if (place and snapshot.get('map') == place['map']
                and all(snapshot.get(k) is not None for k in ('x', 'y'))
                and max(abs(snapshot['x']-place['x']), abs(snapshot['y']-place['y'])) <= 3):
            session['arrived'] = True
        if agreement.get('status') == 'missed' and not session.get('terminal'):
            if now >= deadline and session['valid'] and not session['arrived']:
                _apply(config, rid, state, 'meet_missed')
                _relation(config, state, session['peer'], 'meet_missed')
                state['audit'].append({'type': 'derived', 'kind': 'meet_missed', 'meeting_id': mid,
                                       'source_ids': session['source_ids']+[_source(rid, snapshot)], 'server_time': now})
            session['terminal'] = True
        elif agreement.get('status') == 'fulfilled':
            session['terminal'] = True
    state['meeting_observations'] = {mid: value for mid, value in sessions.items() if value['deadline'] >= now-7*DAY}


def _mood(state, sleeping=False):
    order = ['scared', 'irritated', 'proud', 'happy', 'bored']
    winner = max(order, key=lambda name: state['mood'].get(name, 0))
    state['active_mood'] = winner if not sleeping and state['mood'].get(winner, 0) >= 20 else 'calm'


def _apply(config, rid, state, kind):
    traits = config['residents'][rid].get('traits', {})
    for name, spec in config['drives'].items():
        delta = spec.get('on_event', {}).get(kind, 0)
        modifier = max(.2, 1+sum(amount*traits.get(trait, .5) for trait, amount in spec.get('trait_modifiers', {}).items())) if delta > 0 else 1
        state['drives'][name] = clamp(state['drives'][name]+delta*modifier)
    for name, delta in config.get('mood', {}).get('on_event', {}).get(kind, {}).items():
        state['mood'][name] = clamp(state['mood'].get(name, 0)+delta)
    _mood(state)


def reduce_event(config, rid, mental, event, snapshot):
    eid = event['event_id']
    now = timestamp(event['server_time'])
    if eid in mental['event_ids'] and mental['event_ids'][eid] >= now-7*DAY:
        return deepcopy(mental)
    state = deepcopy(mental)
    state['event_ids'] = {key: time for key, time in state['event_ids'].items() if time >= now-7*DAY}
    state['event_ids'][eid] = now
    kind = event['type']
    data = event.get('data', {})
    # These are local derived facts, never trusted incoming assertions.
    derived_kinds = {'social_turn', 'help_acknowledged', 'joint_hunt', 'safe_hunt', 'low_hp', 'goal_done', 'goal_drop', 'boast_sent', 'meet_missed', 'meet_done'}
    if kind not in derived_kinds:
        _apply(config, rid, state, kind)
    if kind in ('logout', 'died', 'failure', 'arrived'):
        for session in state.get('meeting_observations', {}).values():
            if session['accepted_at'] <= now <= session['deadline'] and not session.get('terminal'):
                if kind != 'arrived':
                    session['valid'] = False
                elif data.get('place') == session['place']:
                    place = config.get('places', {}).get(session['place'], {})
                    if (place and data.get('map') == place['map']
                            and all(data.get(k) is not None for k in ('x', 'y'))
                            and max(abs(data['x']-place['x']), abs(data['y']-place['y'])) <= 3):
                        session['arrived'] = True
    if kind in ('login', 'logout'):
        state['offline'] = kind == 'logout'
    state['audit'].append({'type': 'event', 'event_id': eid, 'source_ids': [eid], 'server_time': now, 'kind': kind})
    if kind == 'kill':
        action = snapshot.get('current_action') or {}
        target = action.get('args', {}).get('map')
        if (snapshot.get('alive') is True and not state['offline'] and action.get('action') == 'hunt'
                and action.get('result') is None and target is not None and snapshot.get('map') == target
                and 0 <= now-timestamp(snapshot.get('server_time', now)) <= 10):
            kills = state.setdefault('observed_kills', [])
            kills.append({'time': now, 'event_id': eid, 'map': target, 'action_id': action.get('action_id')})
            state['observed_kills'] = [kill for kill in kills if kill['time'] >= now-600]
    if kind in ('bought', 'sold'):
        state.setdefault('trade_changes', []).append({'time': now, 'amount': data.get('amount'), 'kind': kind, 'event_id': eid})
    elif kind == 'zeny_changed':
        before, after = data.get('before'), data.get('after')
        if before is not None and after is not None and before != after:
            state.setdefault('pending_zeny', []).append({'time': now, 'delta': after-before, 'event_id': eid})
    _settle_zeny(config, rid, state, now)
    if kind == 'heard':
        sender = data.get('from')
        own = {rid, config['residents'][rid].get('display_name', rid)}
        if sender not in own and data.get('addressed_to') in own:
            state['pending_social'] = {'peer': sender, 'channel': data.get('channel'), 'time': now, 'event_id': eid}
        elif sender in own:
            pending = state.get('pending_social')
            if pending and pending['channel'] == data.get('channel') and 0 <= now-pending['time'] <= 120 and now-state.get('last_social_at', -1000) >= 60:
                _relation(config, state, pending['peer'], 'social_turn')
                _apply(config, rid, state, 'social_turn')
                state['last_social_at'] = now
                state.pop('pending_social', None)
                state['audit'].append({'type': 'derived', 'event_id': eid+':social', 'kind': 'social_turn', 'source_ids': [pending['event_id'], eid], 'server_time': now})
    state['audit'] = [row for row in state['audit'] if row['server_time'] >= now-7*DAY]
    return state


def _settle_zeny(config, rid, state, now):
    """Wait out the correlation window: subtraction cannot undo clamping safely."""
    pending = state.setdefault('pending_zeny', [])
    trades = state.setdefault('trade_changes', [])
    for change in sorted(pending, key=lambda row: (row['time'], row['event_id'])):
        if now-change['time'] <= 2:
            continue
        delta = change['delta']
        matched = next((trade for trade in sorted(trades, key=lambda row: (row['time'], row['event_id']))
                        if abs(change['time']-trade['time']) <= 2
                        and trade['kind'] == ('bought' if delta < 0 else 'sold')
                        and trade['amount'] == abs(delta)), None)
        if matched:
            trades.remove(matched)
            kind, sources = 'trade_zeny_dedup', [matched['event_id'], change['event_id']]
        else:
            kind, sources = ('zeny_gain' if delta > 0 else 'zeny_loss'), [change['event_id']]
            _apply(config, rid, state, kind)
        state['audit'].append({'type': 'derived', 'kind': kind, 'source_ids': sources, 'server_time': now})
        pending.remove(change)
    state['trade_changes'] = [trade for trade in trades if trade['time'] >= now-4]


def _relation(config, state, peer, kind):
    spec = config.get('relationships', {})
    relation = state['relations'].setdefault(peer, deepcopy(spec.get('initial', {'familiarity': 0, 'liking': 0, 'trust': 25})))
    for dimension, delta in spec.get('on_event', {}).get(kind, {}).items():
        relation[dimension] = clamp(relation.get(dimension, 0)+delta, -100 if dimension == 'liking' else 0, 100)


def _schedule(config, rid, state, now):
    import hashlib
    import random
    date = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    if state['schedule'].get('date') != date:
        index = state['rng_index']
        seed = int.from_bytes(hashlib.sha256(f'{rid}|{date}|{index}|schedule'.encode()).digest()[:8], 'big')
        rng = random.Random(seed)
        resident = config['residents'][rid]
        spec = config.get('schedule', {})
        kind = resident.get('chronotype', 'owl' if resident.get('traits', {}).get('chronotype', .5) >= .6 else 'lark')
        hours = spec.get('chronotypes', {}).get(kind, {'sleep_start': '03:00' if kind == 'owl' else '22:00', 'wake': '11:00' if kind == 'owl' else '06:00'})
        def seconds(text):
            h, m = map(int, text.split(':'))
            return h*3600+m*60
        offset = resident.get('sleep_offset_minutes', 0)*60
        start = seconds(hours['sleep_start'])+offset+60*rng.uniform(*spec.get('daily_sleep_shift_minutes', [0, 0]))
        wake = seconds(hours['wake'])+offset+60*rng.uniform(*spec.get('daily_wake_shift_minutes', [0, 0]))
        if wake <= start:
            wake += DAY
        wake = max(wake, start+7*3600)
        midnight = now-now%DAY
        if now < midnight+wake-DAY:
            midnight -= DAY
        break_spec = config.get('imperfections', {})
        break_delay = rng.uniform(*break_spec.get('break_after_awake_minutes', [90, 150]))*60
        break_minutes = rng.uniform(*break_spec.get('break_minutes', [6, 10]))
        state['schedule'] = {'date': date, 'seed': seed, 'sleep_at': midnight+start, 'wake_at': midnight+wake,
                             'break_delay_s': break_delay, 'break_minutes': break_minutes}
        state['rng_index'] += 1
    schedule = state['schedule']
    sleeping = schedule['sleep_at'] <= now < schedule['wake_at']
    was_sleeping = state['derived'].get('sleeping', False)
    if sleeping and not was_sleeping:
        schedule['sleep_entry'] = state['drives'].get('drowsy', 10)
    wake_origin = schedule['wake_at'] if now >= schedule['wake_at'] else schedule['wake_at']-DAY
    awake_minutes = max(0, (now-wake_origin)/60)
    pre_sleep = not sleeping and 0 <= schedule['sleep_at']-now <= 3600
    target = clamp(schedule.get('sleep_entry', 10)-.25*(now-schedule['sleep_at'])/60) if sleeping else clamp(10+.07*awake_minutes+30*pre_sleep)
    state['derived'].update(sleeping=int(sleeping), pre_sleep=int(pre_sleep), awake_minutes=awake_minutes, sleep_target=target,
                            morning_awake=int(not sleeping and 6 <= (now%DAY)/3600 < 10),
                            night_awake=int(not sleeping and ((now%DAY)/3600 >= 22 or (now%DAY)/3600 < 3)))


def _observe(config, rid, state, snapshot, now):
    _schedule(config, rid, state, now)
    derived = state['derived']
    def ratio(numerator, denominator):
        a, b = snapshot.get(numerator), snapshot.get(denominator)
        return None if a is None or b is None or b <= 0 else 100*a/b
    hp = ratio('hp', 'hp_max')
    previous_hp = derived.get('hp_pct')
    weight = ratio('weight', 'weight_max')
    if hp is not None:
        if hp >= 60:
            state['low_hp_armed'] = True
        if hp < 40 and state.get('low_hp_armed', previous_hp is not None and previous_hp >= 40):
            _apply(config, rid, state, 'low_hp')
            state['low_hp_armed'] = False
            state['audit'].append({'type': 'derived', 'kind': 'low_hp', 'source_ids': [f'snapshot:{rid}:{snapshot.get("seq", "unknown")}'], 'server_time': now})
    home = config.get('places', {}).get(config['residents'][rid].get('home_place'), {})
    at_home = bool(home) and snapshot.get('map') == home.get('map') and all(snapshot.get(k) is not None for k in ('x', 'y')) and max(abs(snapshot['x']-home['x']), abs(snapshot['y']-home['y'])) <= 3
    derived.update(hp_pct=hp, hp_need=None if hp is None else 100-hp, weight_pct=weight,
                   potions=None if snapshot.get('inventory') is None else snapshot['inventory'].get('501', snapshot['inventory'].get(501, 0)), at_home=int(at_home))
    for goal in state['goals']:
        value = _measure(goal, snapshot)
        if value is None:
            continue
        peak = goal.get('peak_progress', goal['progress'])
        if peak is None or value > peak:
            goal['last_progress_at'] = now
            goal['peak_progress'] = value
        goal['progress'] = value
        if goal['original_gap'] is None:
            goal['original_gap'] = max(0, goal['target']-value)
        if not goal['completed'] and value >= goal['target']:
            goal.update(completed=True, completed_at=now)
            _apply(config, rid, state, 'goal_done')
            state['audit'].append({'type': 'derived', 'kind': 'goal_done', 'goal_id': goal['id'], 'source_ids': [f'snapshot:{rid}:{snapshot.get("seq", "unknown")}'], 'server_time': now})
    money = [g['target'] for g in state['goals'] if g['type'] == 'zeny']
    gaps = [clamp((g['target']-g['progress'])/max(g['original_gap'] or 1, 1), 0, 1) for g in state['goals'] if g['type'] in ('base_level', 'job_level') and g['progress'] is not None]
    derived.update(money_target=max(money, default=1), level_gap=max(gaps, default=0))
    current = snapshot.get('current_action') or {}
    no_hunt = now-state.get('last_hunt_at', now) >= 900
    if no_hunt:
        derived['hunt_streak'] = 0
    tired = state['drives'].get('tired', 0)
    if tired >= 88:
        derived['recovery_lock'] = 1
    elif tired <= 65 and no_hunt:
        derived['recovery_lock'] = 0
    else:
        derived.setdefault('recovery_lock', 0)
    accepted = any(a.get('status') == 'accepted' and rid in a.get('participants', []) for a in snapshot.get('agreements') or [])
    promise = accepted or bool(state.get('promise_action_id') and state.get('promise_action_id') == current.get('action_id') and current.get('result') is None)
    derived.update(meet_due=int(accepted), promise_due=int(promise))
    schedule = state['schedule']
    wake_origin = schedule['wake_at'] if now >= schedule['wake_at'] else schedule['wake_at']-DAY
    schedule.setdefault('break_after_at', wake_origin+schedule['break_delay_s'])
    due = not derived['sleeping'] and not accepted and now >= schedule['break_after_at']
    resting_at_home = due and at_home and current.get('action') == 'idle' and current.get('result') is None
    if resting_at_home:
        schedule.setdefault('break_started_at', now)
        if now-schedule['break_started_at'] >= schedule['break_minutes']*60:
            schedule['break_after_at'] = now+schedule['break_delay_s']
            schedule.pop('break_started_at', None)
            due = False
    else:
        schedule.pop('break_started_at', None)
    derived['break_due'] = int(due)
    if not promise and 'duty' in state['drives']:
        state['drives']['duty'] = 0
    previous = state.get('current_action') or {}
    if previous.get('action_id') and previous.get('action_id') != current.get('action_id'):
        state['last_actions'] = (state['last_actions']+[previous])[-20:]
    state['current_action'] = deepcopy(snapshot.get('current_action'))


def observe_joint_hunts(config, mental_states, snapshots, now):
    """Pure multi-resident observation, usable by replay or a snapshot-only caller."""
    from itertools import combinations
    now = timestamp(now)
    states = deepcopy(mental_states)
    for left, right in combinations(sorted(states), 2):
        a, b = snapshots.get(left, {}), snapshots.get(right, {})
        def hunting(rid, snap):
            action = snap.get('current_action') or {}
            target = action.get('args', {}).get('map')
            return (snap.get('alive') is True and not states[rid]['offline']
                    and action.get('action') == 'hunt' and action.get('result') is None
                    and target is not None and snap.get('map') == target
                    and all(snap.get(k) is not None for k in ('x', 'y', 'server_time'))
                    and 0 <= now-timestamp(snap['server_time']) <= 10)
        pair = states[left].setdefault('joint_observations', {}).get(right)
        valid = (hunting(left, a) and hunting(right, b) and a.get('map') == b.get('map')
                 and max(abs(a['x']-b['x']), abs(a['y']-b['y'])) <= 12)
        if not valid:
            states[left].setdefault('joint_observations', {}).pop(right, None)
            continue
        observed_at = min(timestamp(a['server_time']), timestamp(b['server_time']))
        if not pair or not 0 <= observed_at-pair['last_at'] <= 10 or pair['map'] != a['map']:
            pair = {'start_at': observed_at, 'last_at': observed_at, 'seconds': 0,
                    'map': a['map'], 'source_ids': []}
            states[left]['joint_observations'][right] = pair
        pair['seconds'] += observed_at-pair['last_at']
        pair['last_at'] = observed_at
        for rid, snap in ((left, a), (right, b)):
            source = _source(rid, snap)
            if source not in pair['source_ids']:
                pair['source_ids'].append(source)
        kills = [[kill for kill in states[rid].get('observed_kills', [])
                  if pair['start_at'] <= kill['time'] <= now and kill['map'] == pair['map']]
                 for rid in (left, right)]
        last = states[left].setdefault('last_joint_at', {}).get(right, -float('inf'))
        if pair['seconds'] >= 300 and all(kills) and now-last >= 600:
            sources = pair['source_ids']+[kill['event_id'] for group in kills for kill in group]
            for rid, peer in ((left, right), (right, left)):
                _relation(config, states[rid], peer, 'joint_hunt')
                states[rid]['audit'].append({'type': 'derived', 'kind': 'joint_hunt', 'resident_id': rid,
                                             'peer': peer, 'source_ids': sources, 'server_time': now})
                states[rid].setdefault('last_joint_at', {})[peer] = now
    return states


def _maintain_goals(config, rid, state, snapshot, now):
    from math import ceil
    caps = {'zeny': 50000, 'base_level': 60, 'job_level': 30, 'item_count': 50}
    morning = bool(state['derived'].get('morning_awake'))
    # Owls wake after the UTC morning window; their first waking hour is morning.
    wake = state['schedule'].get('wake_at', now+1)
    morning = morning or (not state['derived'].get('sleeping') and 0 <= now-wake < 3600)
    for goal in state['goals']:
        value = _measure(goal, snapshot)
        if value is None:
            continue
        kind = None
        if goal['completed'] and morning and int(now//DAY) > int((goal['completed_at'] or now)//DAY):
            target = goal['target']
            if target < caps[goal['type']]:
                target = min(caps[goal['type']], ceil(target*1.4) if goal['type'] == 'zeny'
                             else target+{'base_level': 5, 'job_level': 3, 'item_count': 10}[goal['type']])
            else:
                # At a type's cap, only a not-yet-capped money goal can replace it.
                money = [g for g in state['goals'] if g['type'] == 'zeny']
                if any(g['target'] >= 50000 for g in money) or snapshot.get('zeny') is None or snapshot['zeny'] >= 50000:
                    continue
                goal['type'] = 'zeny'
                goal.pop('item_id', None)
                value = snapshot['zeny']
                target = min(50000, max(1000, value+1000))
            goal.update(target=target, completed=False, completed_at=None, created_at=now,
                        last_progress_at=now, original_gap=max(0, target-value), peak_progress=value)
            kind = 'goal_escalated'
        elif (not goal['completed'] and now-goal['last_progress_at'] >= 7*DAY
              and now-goal.get('last_decay_at', goal['created_at']) >= 7*DAY):
            target = max(value+1, ceil(goal['target']*.8))
            goal.update(target=target, original_gap=max(0, target-value), last_decay_at=now)
            kind = 'goal_decay'
        if kind:
            state['audit'].append({'type': 'derived', 'kind': kind, 'goal_id': goal['id'],
                                   'target': goal['target'], 'source_ids': [_source(rid, snapshot)], 'server_time': now})
    money = [g['target'] for g in state['goals'] if g['type'] == 'zeny']
    gaps = [clamp((g['target']-g['progress'])/max(g['original_gap'] or 1, 1), 0, 1)
            for g in state['goals'] if g['type'] in ('base_level', 'job_level') and g['progress'] is not None]
    state['derived'].update(money_target=max(money, default=1), level_gap=max(gaps, default=0))


def advance(config, rid, mental, snapshot, now):
    """Integrate rates continuously; relax expressions only on minute boundaries."""
    from .expressions import evaluate
    now = timestamp(now)
    # Legacy callers can persist an empty mental dict alongside an outbox
    # claim before the first observation. It contains no history to discard.
    # Do not silently reset nonempty malformed state (RNG/evidence may exist).
    if type(mental) is dict and not mental:
        return initial_state(config, rid, snapshot, now)
    if now < mental['last_update']:
        raise ValueError('mental clock cannot move backwards')
    state = deepcopy(mental)
    _settle_zeny(config, rid, state, now)
    _observe(config, rid, state, snapshot, state['last_update'])
    cursor = state['last_update']
    traits = config['residents'][rid].get('traits', {})
    while cursor < now:
        end = min(now, state['last_tick']+60)
        dt = (end-cursor)/60
        action = snapshot.get('current_action') or {}
        kind = 'offline' if state['offline'] else action.get('action', 'idle')
        kind = 'say' if kind == 'whisper' else kind
        if kind == 'hunt' and action.get('args', {}).get('map') and snapshot.get('map') != action['args']['map']:
            kind = 'walk_to'
        sleeping = state['derived']['sleeping']
        for name, spec in config['drives'].items():
            rate = spec.get('per_minute', {}).get(kind, 0)
            if sleeping:
                if name == 'tired': rate = -.35
                if name in ('boredom', 'lonely'): rate = 0
            modifier = max(.2, 1+sum(amount*traits.get(trait, .5) for trait, amount in spec.get('trait_modifiers', {}).items())) if rate > 0 else 1
            state['drives'][name] = clamp(state['drives'][name]+rate*modifier*dt)
        cursor = end
        _observe(config, rid, state, snapshot, cursor)
        if cursor >= state['last_tick']+60:
            state['last_tick'] += 60
            context = dict(snapshot, **traits, **state['drives'], **state['derived'])
            for name, spec in config['drives'].items():
                source = spec.get('from_state')
                if source:
                    try:
                        target = evaluate(str(source), context)
                    except (ValueError, ZeroDivisionError):
                        continue  # Unknown server values never become invented facts.
                    state['drives'][name] = clamp(state['drives'][name]+.1*(target-state['drives'][name]))
            for name, spec in config.get('mood', {}).get('states', {}).items():
                state['mood'][name] = clamp(state['mood'].get(name, 0)-spec.get('decay_per_minute', 0))
            if not sleeping and 'bored' in state['mood']:
                state['mood']['bored'] = max(state['mood']['bored'], clamp(state['drives'].get('boredom', 0)-40, 0, 60))
    _hunt_observation(config, rid, state, snapshot, now)
    _meet_observation(config, rid, state, snapshot, now)
    _observe(config, rid, state, snapshot, now)
    _maintain_goals(config, rid, state, snapshot, now)
    state['event_ids'] = {eid: at for eid, at in state['event_ids'].items() if at >= now-7*DAY}
    state['audit'] = [row for row in state['audit'] if row['server_time'] >= now-7*DAY]
    state['last_update'] = now
    _mood(state, bool(state['derived']['sleeping']))
    return state
