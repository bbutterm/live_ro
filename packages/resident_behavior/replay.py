"""Offline deterministic replay. No body transport, service or model calls."""
from copy import deepcopy
from itertools import groupby
from .reducer import initial_state, reduce_event, advance, timestamp, observe_joint_hunts


def replay(config, records, initial=None):
    """Equal-time events precede ticks; paired observations see the whole time batch.

    Input order is irrelevant: server time, resident ID, seq and event ID order
    the stream. Initial maps rid->state; no body or model is called.
    """
    states = deepcopy(initial or {})
    snapshots = {}
    ordered = sorted(deepcopy(list(records)), key=lambda r: (timestamp(r['server_time']), r['resident_id'], r['seq'], r.get('event_id', '')))
    for now, time_group in groupby(ordered, key=lambda r: timestamp(r['server_time'])):
        for rid, grouped in groupby(time_group, key=lambda r: r['resident_id']):
            group = list(grouped)
            telemetry = [r for r in group if r.get('kind') in ('snapshot', 'telemetry') or 'event_id' not in r]
            snapshot = max(telemetry, key=lambda r: r['seq']) if telemetry else snapshots.get(rid, {})
            if rid not in states:
                states[rid] = initial_state(config, rid, snapshot, now)
            for record in group:
                if 'event_id' in record:
                    states[rid] = reduce_event(config, rid, states[rid], record, snapshot)
            states[rid] = advance(config, rid, states[rid], snapshot, now)
            snapshots[rid] = snapshot
        states = observe_joint_hunts(config, states, snapshots, now)
    audit = sorted([dict(row, resident_id=rid) for rid in sorted(states) for row in states[rid]['audit']],
                   key=lambda row: (row['server_time'], row['resident_id'], row.get('event_id', ''), row['kind']))
    return {'states': states, 'audit': audit, 'evidence': 'input_records_not_live_server_verification'}
