"""Side-by-side adapter building blocks; never starts a listener or a motor.

Executor protocol: execute(validated_request)->ack; action(action_id)->result;
optional interrupt(action_id)->ack. Source: snapshot(resident_id)->full telemetry.
Store protocol: transaction() yields a JSON-serializable dict, lock is an RLock.
Only explicitly injected executor calls can have external effects. A pending
outbox is polled, never blindly resent after a crash/transport timeout.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading
from urllib.parse import unquote

from .contracts import (ContractError, validate_telemetry, validate_event,
                        validate_execute, validate_meeting, validate_ack,
                        validate_result, utc_time)


class MemoryAdapterStore:
    """Shared in-process state; not durable. Reuse instance across adapters."""
    def __init__(self):
        self.lock = threading.RLock()
        self.state = {}

    @contextmanager
    def transaction(self):
        with self.lock:
            pending = deepcopy(self.state)
            yield pending
            self.state = pending


class SQLiteAdapterStore:
    """Explicit separate adapter database. No default path / existing game SQL."""
    def __init__(self, path):
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute('CREATE TABLE IF NOT EXISTS adapter_state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        self.connection.commit()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.connection.execute('BEGIN IMMEDIATE')
            try:
                row = self.connection.execute('SELECT body FROM adapter_state WHERE id=1').fetchone()
                state = json.loads(row[0]) if row else {}
                yield state
                self.connection.execute('INSERT OR REPLACE INTO adapter_state(id,body) VALUES(1,?)',
                                        (json.dumps(state, ensure_ascii=False, allow_nan=False),))
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def close(self):
        self.connection.close()


def _initialize(state):
    for key in ('snapshots', 'sequences', 'seen', 'gaps', 'actions', 'slots', 'cooldowns', 'meetings', 'reservations'):
        state.setdefault(key, {})


def _iso(now):
    return now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _ack(action_id, error=None):
    return dict(action_id=action_id, accepted=error is None, error_code=error)


def _pending(action_id):
    return dict(action_id=action_id, result=None, error_code=None, source_seq=None, completed_at=None)


class Adapter:
    def __init__(self, source=None, executor=None, store=None, *, clock=None,
                 veto=None, coordinator=None, meeting_context=None):
        self.source, self.executor = source, executor
        self.store = store if store is not None else MemoryAdapterStore()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.veto = veto
        self.coordinator = coordinator
        self.meeting_context = meeting_context
        with self._transaction(): pass

    @contextmanager
    def _transaction(self):
        with self.store.transaction() as state:
            _initialize(state)
            yield state

    def _ingest(self, payload, full):
        p = validate_telemetry(payload) if full else validate_event(payload)
        now = self.clock(); resident, seq = p['resident_id'], p['seq']
        identity = resident + ':' + ('snapshot:' + str(seq) if full else 'event:' + p['event_id'])
        with self._transaction() as state:
            state['seen'] = {k: v for k, v in state['seen'].items()
                             if (now-utc_time(v['at'])).total_seconds() <= 7*86400}
            if identity in state['seen']:
                if state['seen'][identity]['body'] != p: raise ContractError('conflict')
                return {'status': 'duplicate', 'seq': seq}
            last = state['sequences'].get(resident, -1)
            if seq <= last: return {'status': 'old', 'seq': last}
            if not full and (resident in state['gaps'] or seq != last+1):
                state['gaps'][resident] = True
                return {'status': 'snapshot_required', 'seq': last}
            if full:
                state['snapshots'][resident] = deepcopy(p)
                state['gaps'].pop(resident, None)
            elif resident in state['snapshots']:
                # Critical facts from events veto immediately, before the next
                # two-second full snapshot. Never infer revival from login.
                if p['type'] == 'died': state['snapshots'][resident]['alive'] = False
                elif p['type'] in ('login', 'logout'):
                    state['gaps'][resident] = True
            state['sequences'][resident] = seq
            state['seen'][identity] = {'body': deepcopy(p), 'at': _iso(now)}
            return {'status': 'accepted', 'seq': seq}

    def telemetry(self, payload):
        return self._ingest(payload, True)

    def event(self, payload):
        return self._ingest(payload, False)

    def refresh(self, resident_id):
        if self.source is None: return None
        p = self.source.snapshot(resident_id)
        if p['resident_id'] != resident_id: raise ContractError('conflict')
        self.telemetry(p)
        return p

    def _fresh(self, state, resident, now):
        snapshot = state['snapshots'].get(resident)
        if not snapshot or resident in state['gaps']: return False
        age = (now-utc_time(snapshot['server_time'])).total_seconds()
        return 0 <= age <= 10

    def _safety(self, p, state, now):
        resident, action = p['resident_id'], p['action']
        snap = state['snapshots'].get(resident)
        if now >= utc_time(p['expires_at']) or utc_time(p['server_time']) > now: return 'stale_state'
        if not self._fresh(state, resident, now):
            return None if action == 'idle' else 'stale_state'
        if p['state_seq'] != state['sequences'].get(resident): return 'stale_state'
        if action == 'idle':
            return 'dead' if snap['alive'] is False and p['args']['minutes'] != 1 else None
        if snap['alive'] is False: return 'dead'
        if snap['alive'] is not True: return 'unsafe'
        if action in ('say', 'whisper'): return None
        required = ('map', 'x', 'y', 'hp', 'hp_max', 'near_monsters')
        if any(snap[k] is None for k in required): return 'unsafe'
        hp = 100*snap['hp']/snap['hp_max']
        if action in ('hunt', 'follow', 'meet') and hp < 55: return 'unsafe'
        if action in ('hunt', 'follow'):
            if snap['weight'] is None or snap['weight_max'] is None or 100*snap['weight']/snap['weight_max'] >= 85: return 'unsafe'
            if action == 'hunt':
                if snap['inventory'] is None: return 'unsafe'
                if snap['inventory'].get('501', 0) == 0 and hp < 85: return 'unsafe'
                if any(entity.get('id') != '1002' for entity in snap['near_monsters']): return 'unsafe'
            if action == 'follow' and (snap['near_players'] is None or not any(entity.get('name') == p['args']['player'] for entity in snap['near_players'])): return 'target_lost'
        if action == 'rest' and (snap['near_monsters'] or hp >= p['args']['until_hp']): return 'unsafe'
        if action == 'meet':
            other = p['args']['resident']
            meeting = next((m for m in state['meetings'].values() if m['status'] in ('reserved', 'accepted') and set(m['participants']) == {resident, other} and m['place'] == p['args']['place']), None)
            if meeting is None: return 'conflict'
        # All derived flags/routes/skills remain authoritative caller responsibility.
        # Absence of a veto provider is deliberately fail-closed for motors.
        return self.veto(p, deepcopy(snap)) if self.veto else 'unsafe'

    @staticmethod
    def _resources(snapshot):
        return {'zeny': snapshot.get('zeny'), 'inventory': snapshot.get('inventory'),
                'weight': snapshot.get('weight'), 'weight_max': snapshot.get('weight_max')}

    def _cooldown(self, state, p, now):
        key = p['resident_id'] + ':' + p['action']
        block = state['cooldowns'].get(key)
        if not block: return None
        snapshot = state['snapshots'].get(p['resident_id'], {})
        if block['error'] == 'no_funds':
            if block['resources'] == self._resources(snapshot): return 'no_funds'
        elif now < utc_time(block['until']): return block['error']
        state['cooldowns'].pop(key, None)
        return None

    def _block(self, state, p, error, now):
        if error is None: return
        state['cooldowns'][p['resident_id'] + ':' + p['action']] = {
            'error': error, 'until': _iso(now+timedelta(minutes=5)),
            'resources': self._resources(state['snapshots'].get(p['resident_id'], {}))}

    def execute(self, payload):
        p = validate_execute(payload); action_id = p['action_id']
        # Reconcile already-persisted requests before touching source or executor.
        with self.store.lock:
            with self._transaction() as state:
                record = state['actions'].get(action_id)
                if record:
                    if record['request'] != p: return _ack(action_id, 'conflict')
                    cached = deepcopy(record['ack'])
                    poll = record['uncertain']
                else: cached = None; poll = False
            if cached is not None:
                if poll:
                    result = self.action(action_id)
                    if result['result'] is not None:
                        with self._transaction() as state: return deepcopy(state['actions'][action_id]['ack'])
                return cached
            try: self.refresh(p['resident_id'])
            except (TimeoutError, ConnectionError, OSError): return _ack(action_id, 'stale_state')
            now = self.clock()
            with self._transaction() as state:
                # SQLite transaction also prevents a cross-process double allocation.
                if action_id in state['actions']:
                    record = state['actions'][action_id]
                    return deepcopy(record['ack']) if record['request'] == p else _ack(action_id, 'conflict')
                error = self._safety(p, state, now) or self._cooldown(state, p, now)
                motor = p['action'] not in ('say', 'whisper')
                if not error and motor and p['resident_id'] in state['slots']: error = 'conflict'
                if not error and self.executor is None: error = 'unsafe'
                ack = _ack(action_id, error or 'transport_timeout')
                state['actions'][action_id] = {'request': p, 'ack': ack, 'result': _pending(action_id),
                                               'uncertain': error is None, 'created_at': _iso(now)}
                if error: return _ack(action_id, error)
                if motor: state['slots'][p['resident_id']] = action_id
            # Outbox committed BEFORE external effect. Crash => query same ID, not retry.
            try:
                ack = validate_ack(self.executor.execute(deepcopy(p)))
                if ack['action_id'] != action_id: raise ContractError('conflict')
            except (TimeoutError, ConnectionError, OSError, ContractError):
                return _ack(action_id, 'transport_timeout')
            with self._transaction() as state:
                record = state['actions'][action_id]; record['ack'] = ack
                record['uncertain'] = ack['error_code'] == 'transport_timeout'
                if not ack['accepted'] and not record['uncertain']:
                    if state['slots'].get(p['resident_id']) == action_id: state['slots'].pop(p['resident_id'])
                    self._block(state, p, ack['error_code'], now)
            return deepcopy(ack)

    def action(self, action_id):
        with self.store.lock:
            with self._transaction() as state:
                if action_id not in state['actions']: raise ContractError('not_found')
                record = deepcopy(state['actions'][action_id])
            if record['result']['result'] is not None: return record['result']
            if self.executor is None: return record['result']
            try:
                result = validate_result(self.executor.action(action_id))
                if result['action_id'] != action_id: raise ContractError('conflict')
            except (TimeoutError, ConnectionError, OSError): return record['result']
            with self._transaction() as state:
                record = state['actions'][action_id]
                record['result'] = result
                if result['result'] is not None:
                    p = record['request']; resident = p['resident_id']
                    record['uncertain'] = False
                    # A result proves execution was accepted, not necessarily successful.
                    record['ack'] = _ack(action_id)
                    if state['slots'].get(resident) == action_id: state['slots'].pop(resident)
                    self._block(state, p, result['error_code'], self.clock())
            return deepcopy(result)

    def reconcile(self, resident_id=None):
        """Parent calls every <=2 seconds; no scheduler or background thread.

        Polls authoritative results first. Interrupt acknowledgment does NOT
        complete a motor slot; only a terminal action result can release it.
        """
        report = {'results': [], 'interruptions': []}
        with self._transaction() as state:
            slots = deepcopy(state['slots'])
        for resident, action_id in slots.items():
            if resident_id is not None and resident != resident_id:
                continue
            try: self.refresh(resident)
            except (TimeoutError, ConnectionError, OSError): pass
            result = self.action(action_id)
            report['results'].append(result)
            if result['result'] is not None: continue
            now = self.clock()
            with self._transaction() as state:
                record = state['actions'][action_id]
                request = deepcopy(record['request'])
                request['server_time'] = _iso(now)
                request['expires_at'] = _iso(now+timedelta(seconds=30))
                request['state_seq'] = state['sequences'].get(resident, 0)
                error = self._safety(request, state, now)
                if not error or record.get('interrupt_requested'): continue
                if not hasattr(self.executor, 'interrupt'):
                    report['interruptions'].append({'action_id': action_id, 'error_code': error, 'sent': False})
                    continue
                record['interrupt_requested'] = True
            try:
                ack = validate_ack(self.executor.interrupt(action_id))
                if ack['action_id'] != action_id: raise ContractError('conflict')
                sent = ack['accepted']
            except (TimeoutError, ConnectionError, OSError, ContractError): sent = False
            report['interruptions'].append({'action_id': action_id, 'error_code': error, 'sent': sent})
        report['meetings'] = self.update_meetings()
        return report

    def reserve_meeting(self, payload):
        p = validate_meeting(payload); now = self.clock()
        with self.store.lock:
            self.update_meetings()
            with self._transaction() as state:
                if p['meeting_id'] in state['meetings']:
                    old = state['meetings'][p['meeting_id']]
                    if old['request'] != p: raise ContractError('conflict')
                    return deepcopy(old)
                people = [p['initiator'], p['resident']]
                if p['state_seq'] != state['sequences'].get(p['initiator']): raise ContractError('stale_state')
                for resident in people:
                    if resident in state['reservations'] or resident in state['slots'] or not self._fresh(state, resident, now): raise ContractError('conflict')
                    snap = state['snapshots'][resident]
                    if snap['alive'] is not True or snap['hp'] is None or snap['hp_max'] is None or 100*snap['hp']/snap['hp_max'] < 55: raise ContractError('conflict')
                    context = self.meeting_context(resident) if self.meeting_context else {}
                    minutes = context.get('minutes_until_sleep')
                    if context.get('awake') is not True or type(minutes) not in (int, float) or not 15 <= minutes < float('inf'): raise ContractError('conflict')
                if not self.coordinator or self.coordinator(deepcopy(p), deepcopy(state['snapshots'][p['resident']])) is not True: raise ContractError('conflict')
                meeting = {'request': p, 'meeting_id': p['meeting_id'], 'participants': people,
                           'place': p['place'], 'status': 'reserved', 'reserved_until': _iso(now+timedelta(seconds=30)),
                           'acks': {}, 'echoes': [], 'accepted_at': None, 'deadline': None, 'near_since': None}
                state['meetings'][p['meeting_id']] = meeting
                for resident in people: state['reservations'][resident] = p['meeting_id']
                return deepcopy(meeting)

    def acknowledge_meeting(self, meeting_id, resident_id, action_id):
        self.update_meetings()
        with self._transaction() as state:
            meeting = state['meetings'].get(meeting_id)
            if not meeting or meeting['status'] != 'reserved' or resident_id not in meeting['participants']: raise ContractError('conflict')
            record = state['actions'].get(action_id)
            if not record or not record['ack']['accepted']: raise ContractError('conflict')
            request = record['request']
            other = next(r for r in meeting['participants'] if r != resident_id)
            if request['resident_id'] != resident_id or request['action'] != 'meet' or request['args'] != {'resident': other, 'place': meeting['place']} or state['slots'].get(resident_id) != action_id: raise ContractError('conflict')
            meeting['acks'][resident_id] = action_id
            return deepcopy(meeting)

    def echo_meeting(self, meeting_id, resident_id, action_id):
        """Call only after gateway confirms promise echo for this active action."""
        self.update_meetings()
        now = self.clock()
        with self._transaction() as state:
            meeting = state['meetings'].get(meeting_id)
            if not meeting or meeting['status'] != 'reserved' or len(meeting['acks']) != 2 or meeting['acks'].get(resident_id) != action_id or state['slots'].get(resident_id) != action_id: raise ContractError('conflict')
            if resident_id not in meeting['echoes']: meeting['echoes'].append(resident_id)
            if len(meeting['echoes']) == 2:
                meeting['status'] = 'accepted'; meeting['accepted_at'] = _iso(now)
                meeting['deadline'] = _iso(now+timedelta(minutes=10))
            return deepcopy(meeting)

    def update_meetings(self):
        now = self.clock()
        with self._transaction() as state:
            for meeting in state['meetings'].values():
                if meeting['status'] == 'reserved':
                    invalid = now >= utc_time(meeting['reserved_until'])
                    for resident in meeting['participants']:
                        snap = state['snapshots'].get(resident, {})
                        invalid = invalid or not self._fresh(state, resident, now) or snap.get('alive') is not True
                        action_id = meeting['acks'].get(resident)
                        if action_id is not None:
                            record = state['actions'].get(action_id, {})
                            invalid = invalid or not record.get('ack', {}).get('accepted') or state['slots'].get(resident) != action_id
                    if invalid: meeting['status'] = 'conflict'
                if meeting['status'] == 'accepted':
                    people = meeting['participants']
                    snaps = [state['snapshots'].get(r, {}) for r in people]
                    near = all(self._fresh(state, r, now) and s.get('alive') is True and s.get('map') is not None and s.get('x') is not None and s.get('y') is not None for r, s in zip(people, snaps))
                    near = near and snaps[0]['map'] == snaps[1]['map'] and max(abs(snaps[0]['x']-snaps[1]['x']), abs(snaps[0]['y']-snaps[1]['y'])) <= 5
                    # No inference of continuous presence across unobserved gaps.
                    previous = meeting.get('last_check')
                    if previous is None or (now-utc_time(previous)).total_seconds() > 10:
                        meeting['near_since'] = None
                    if not near: meeting['near_since'] = None
                    elif meeting['near_since'] is None: meeting['near_since'] = _iso(now)
                    elif now < utc_time(meeting['deadline']) and (now-utc_time(meeting['near_since'])).total_seconds() >= 120: meeting['status'] = 'fulfilled'
                    meeting['last_check'] = _iso(now)
                    if meeting['status'] == 'accepted' and now >= utc_time(meeting['deadline']): meeting['status'] = 'missed'
                if meeting['status'] not in ('reserved', 'accepted'):
                    for resident in meeting['participants']:
                        if state['reservations'].get(resident) == meeting['meeting_id']: state['reservations'].pop(resident)
            return deepcopy(state['meetings'])


MAX_BODY_BYTES = 1024*1024


def decode_json(body):
    if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES: raise ContractError('invalid_json')
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out: raise ContractError('duplicate_key')
            out[key] = value
        return out
    def constant(value): raise ContractError('invalid_json')
    try:
        return json.loads(body.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ContractError): raise
        raise ContractError('invalid_json') from exc


def dispatch(adapter, method, path, body=b'', content_type='application/json'):
    """Pure HTTP router for the parent's existing Unix gateway; no bind/start."""
    try:
        routes = {'/v1/telemetry': adapter.telemetry, '/v1/events': adapter.event,
                  '/v1/execute': adapter.execute, '/v1/meetings': adapter.reserve_meeting}
        if method == 'POST' and path in routes:
            media = content_type.lower().replace(' ', '')
            if media not in ('application/json', 'application/json;charset=utf-8'): return 415, {'error_code': 'unsupported_media_type'}
            return 200, routes[path](decode_json(body))
        if method == 'GET' and path.startswith('/v1/actions/'):
            action_id = unquote(path[len('/v1/actions/'):])
            if not action_id or '/' in action_id or len(action_id) > 64: raise ContractError('not_found')
            return 200, adapter.action(action_id)
        return 404, {'error_code': 'not_found'}
    except ContractError as exc:
        return {'not_found': 404, 'conflict': 409, 'stale_state': 409}.get(exc.code, 400), {'error_code': exc.code}
