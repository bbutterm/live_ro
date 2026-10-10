"""Pure priority/inertia policy; the integrator persists and executes intents."""
from copy import deepcopy
from datetime import datetime
import hashlib
import math


def stamp(value):
    if type(value) in (int, float):
        return float(value) if math.isfinite(value) else None
    if not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return d.timestamp() if d.tzinfo is not None else None
    except ValueError:
        return None


def ratio(snapshot, field):
    value, maximum = snapshot.get(field), snapshot.get(field + '_max')
    if (type(value) not in (int, float) or type(maximum) not in (int, float)
            or not math.isfinite(value) or not math.isfinite(maximum)
            or maximum <= 0 or not 0 <= value <= maximum):
        return None
    return 100 * value / maximum


def at_place(config, snapshot, place):
    target = config.get('places', {}).get(place)
    if (not target or snapshot.get('map') is None
            or any(type(snapshot.get(k)) not in (int, float)
                   or not math.isfinite(snapshot[k]) for k in ('x', 'y'))):
        return None
    return int(snapshot['map'] == target['map'] and
               max(abs(snapshot['x'] - target['x']), abs(snapshot['y'] - target['y'])) <= 3)


def coordinate(config, resident_id, snapshot, mental, ranking, now, trigger='ten_minutes'):
    """Motor policy. Speech completion never releases a motor minimum timer.

    Sleep/break wait for min_minutes (§48); only dead/stale, danger and
    accepted meetings interrupt that minimum. No flags are trusted for HP.
    """
    current = snapshot.get('current_action')
    active = current if isinstance(current, dict) and current.get('result') is None and current.get('action') not in ('say', 'whisper') else None
    ts = stamp(snapshot.get('server_time'))
    action_id = active.get('action_id') if active else None
    derived = mental.get('derived', {})

    def decision(action, args, source, cause, interrupt=None, continuation=False):
        seq = snapshot.get('seq', 0)
        index = mental.get('decision_index', 0)
        material = f'{resident_id}|{seq}|{index}|{cause}'
        identity = hashlib.sha256(material.encode()).hexdigest()[:32]
        result = {'decision_id': 'd-' + identity, 'action_id': action_id if continuation else 'a-' + identity,
                  'state_seq': seq, 'expires_at': now + 30, 'action': action, 'args': deepcopy(args),
                  'say': None, 'source': source, 'trigger': cause, 'interrupt': interrupt,
                  'continuation': continuation, 'llm_allowed': False}
        if continuation:
            result['started_at'] = active.get('started_at')
        return result

    def intent(action, args, cause):
        if active and active.get('action') == action and active.get('args') == args:
            return decision(action, args, 'continuation', cause, continuation=True)
        return decision(action, args, 'safety', cause, action_id)

    if ts is None or now - ts > 10 or ts > now:
        return decision('idle', {'minutes': 1}, 'safety', 'stale_state', action_id)
    if snapshot.get('alive') is not True:
        return decision('idle', {'minutes': 1}, 'safety', 'dead', action_id)
    hp_pct, weight_pct = ratio(snapshot, 'hp'), ratio(snapshot, 'weight')
    monsters = snapshot.get('near_monsters')
    safe_rest = isinstance(monsters, list) and not monsters
    if hp_pct is not None and hp_pct < 25 and monsters:
        return intent('town_service', {}, 'critical_hp')
    if mental.get('consecutive_no_path', 0) >= 2:
        return decision('idle', {'minutes': 1}, 'safety', 'no_path', action_id)
    if active and active.get('action') == 'meet' and derived.get('meet_available') == 0:
        return decision('idle', {'minutes': 1}, 'safety', 'meet_unavailable', action_id)
    if active and active.get('action') in ('hunt', 'follow', 'rest'):
        action = active['action']
        drives = mental.get('drives', {})
        unsafe = not isinstance(monsters, list) or any(not isinstance(m, dict) or str(m.get('id', m.get('monster_id'))) != '1002' for m in monsters)
        inventory = snapshot.get('inventory')
        potions = inventory.get('501', inventory.get(501, 0)) if isinstance(inventory, dict) else None
        danger = hp_pct is None or weight_pct is None
        if action in ('hunt', 'follow'):
            danger = danger or hp_pct < 55 or weight_pct >= 85
        if action == 'hunt':
            danger = (danger or unsafe or drives.get('fear', 0) >= 80 or drives.get('tired', 0) >= 88
                      or derived.get('recovery_lock') or derived.get('hunt_streak', 0) >= 90
                      or potions is None or (potions == 0 and hp_pct is not None and hp_pct < 85))
        if action == 'rest':
            danger = danger or not safe_rest
        if danger:
            if hp_pct is not None and hp_pct < 90 and safe_rest and weight_pct is not None and weight_pct < 50:
                return intent('rest', {'until_hp': 90}, 'unsafe_' + action)
            return decision('idle', {'minutes': 1}, 'safety', 'unsafe_' + action, action_id)

    agreements = sorted(snapshot.get('agreements') or [], key=lambda a: (stamp(a.get('deadline')) or 0, a.get('meeting_id', '')))
    if not derived.get('sleeping') and hp_pct is not None and hp_pct >= 55:
        for agreement in agreements:
            participants = agreement.get('participants', [])
            deadline, accepted = stamp(agreement.get('deadline')), stamp(agreement.get('accepted_at'))
            places, residents = config.get('places'), config.get('residents')
            if (agreement.get('status') == 'accepted' and resident_id in participants and len(participants) == 2
                    and len(set(participants)) == 2 and deadline is not None and accepted is not None
                    and accepted <= now < deadline
                    and (places is None or agreement.get('place') in places)
                    and (residents is None or all(p in residents for p in participants))
                    and derived.get('meet_available', 1) == 1):
                peer = next(p for p in participants if p != resident_id)
                return intent('meet', {'resident': peer, 'place': agreement['place']}, 'accepted_meet')
    if active:
        started = stamp(active.get('started_at'))
        minimum = config.get('actions', {}).get(active.get('action'), {}).get('min_minutes', 0) * 60
        if started is None or now - started < minimum:
            return decision(active['action'], active.get('args', {}), 'continuation', 'inertia', continuation=True)
    if derived.get('sleeping') or derived.get('break_due'):
        cause = 'sleep' if derived.get('sleeping') else 'break'
        resident = config.get('residents', {}).get(resident_id, {})
        home = resident.get('home_place')
        arrived = at_place(config, snapshot, home)
        if arrived == 0:
            return intent('walk_to', {'place': home}, cause + '_home')
        if arrived == 1:
            if cause == 'sleep':
                kind = resident.get('chronotype', 'lark')
                minutes = config.get('schedule', {}).get('chronotypes', {}).get(kind, {}).get('sleep_idle_minutes', [8, 10])[0]
            else:
                minutes = math.ceil(min(10, max(1, mental.get('schedule', {}).get('break_minutes', 6))))
            return intent('idle', {'minutes': min(10, max(1, minutes))}, cause)
        return decision('idle', {'minutes': 1}, 'safety', 'unknown_home', action_id)
    primary = ranking.get('primary')
    if not primary:
        return decision('idle', {'minutes': 1}, 'fallback', 'no_candidates')
    result = decision(primary['action'], primary['args'], 'fallback', trigger)
    result['llm_allowed'] = len(ranking.get('candidates', [])) > 1
    return result
