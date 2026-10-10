"""Translate existing packet observations into v1. Missing observations stay null."""
from datetime import datetime, timezone

LOGICAL_IDS = {'ResidentA': 'resident_a', 'Mira': 'mira', 'Borin': 'borin'}


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace('+00:00', 'Z')


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
    snapshot['near_monsters'] = t.get('near_monsters')
    snapshot['current_action'] = current_action
    snapshot['agreements'] = agreements if agreements is not None else []
    return snapshot
