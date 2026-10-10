"""Translate existing packet observations into v1. Missing observations stay null."""
from datetime import datetime, timezone

LOGICAL_IDS = {'ResidentA': 'resident_a', 'Mira': 'mira', 'Borin': 'borin'}


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace('+00:00', 'Z')


def monsters_v1(near):
    """Native frames carry {id: hex GID, nameID: class, x, y}; v1 wants exactly
    {id, x, y} with id = monster class ('1002' is Poring). The extra nameID key
    was the ContractError('invalid_keys') of E01, the GID the hunt veto of R02."""
    if not isinstance(near, list):
        return near
    out = []
    for m in near:
        if not isinstance(m, dict):
            out.append(m); continue
        kind = m.get('nameID', m.get('id'))
        out.append({'id': None if kind is None else str(kind), 'x': m.get('x'), 'y': m.get('y')})
    return out


def snapshot_from_status(status, resident_id, seq, *, current_action=None, agreements=None):
    t = status.get('telemetry') or {}
    pos = t.get('pos') or {}
    timestamp = t.get('ts')
    snapshot = {'version': 1, 'resident_id': resident_id, 'seq': seq,
                'server_time': iso(timestamp) if timestamp is not None else None,
                'map': t.get('map'), 'x': pos.get('x'), 'y': pos.get('y')}
    for field in ('hp', 'hp_max', 'sp', 'sp_max', 'weight', 'weight_max', 'zeny', 'inventory',
                  'base_level', 'job_level', 'online_minutes'):
        snapshot[field] = t.get(field)
    snapshot['free_skill_points'] = t.get('free_skill_points', t.get('skill_points'))
    dead = t.get('dead')
    snapshot['alive'] = not dead if isinstance(dead, bool) else None
    snapshot['sitting'] = t.get('sitting')
    snapshot['near_players'] = t.get('near_players')
    if snapshot['near_players'] is None and isinstance(t.get('nearby_players'), list):
        snapshot['near_players'] = [{'name': p['name'], 'x': p['pos']['x'], 'y': p['pos']['y']}
                                    for p in t['nearby_players']]
    snapshot['near_monsters'] = monsters_v1(t.get('near_monsters'))
    snapshot['current_action'] = current_action
    snapshot['agreements'] = agreements if agreements is not None else []
    return snapshot
