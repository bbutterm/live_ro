"""Private controller checkpoint; caller holds the lifetime budget/controller lock."""
import copy
import json
import math
import os
import pathlib
import tempfile
import time
from .policy import perceive


class ControllerState:
    def __init__(self, path, memories, maximum):
        self.path = pathlib.Path(path)
        marker = self.path.with_name(self.path.name + '.initialized')
        identity = {'version': 1, 'residents': sorted(memories)}
        if marker.exists() and json.loads(marker.read_text()) != identity:
            raise ValueError('CONTROLLER_STATE_IDENTITY')
        if marker.exists() and not self.path.exists():
            raise ValueError('CONTROLLER_STATE_MISSING')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        def mark_initialized():
            if not marker.exists():
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'w') as f:
                    json.dump(identity, f)
                    f.flush()
                    os.fsync(f.fileno())
                directory = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1 or not isinstance(data.get('actors'), dict) or set(data['actors']) != set(memories):
                raise ValueError('CONTROLLER_STATE_IDENTITY')
            self.actors = data['actors']
            migrated = False
            for rid, actor in self.actors.items():
                # Validate accounting before migration/marker writes; bool is not an integer.
                if not isinstance(actor, dict) or type(actor.get('decisions')) is not int or actor['decisions'] < 0:
                    raise ValueError('CONTROLLER_STATE_SCHEMA')
                mem = actor.get('mem')
                if not isinstance(mem, dict) or not isinstance(mem.get('heard'), list):
                    raise ValueError('CONTROLLER_STATE_SCHEMA')
                for row in mem['heard']:
                    if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] < 0 or type(row.get('char_id')) is not int or row['char_id'] < 0:
                        raise ValueError('CONTROLLER_STATE_SCHEMA')
                if 'model_gate' in actor:
                    gate = actor['model_gate']
                    if not isinstance(gate, dict) or set(gate) != {'next_at', 'human_id'} or type(gate['next_at']) not in (int, float) or not math.isfinite(gate['next_at']) or gate['next_at'] < 0 or type(gate['human_id']) is not int or gate['human_id'] < 0:
                        raise ValueError('CONTROLLER_STATE_SCHEMA')
                if 'reaction' in actor:
                    reaction = actor['reaction']
                    if (not isinstance(reaction, dict) or set(reaction) != {'action_id', 'failed_map', 'blocked_until'}
                        or not isinstance(reaction['action_id'], str) or not reaction['action_id']
                        or not isinstance(reaction['failed_map'], str) or not reaction['failed_map']
                        or type(reaction['blocked_until']) not in (int, float)
                        or not math.isfinite(reaction['blocked_until']) or reaction['blocked_until'] < 0):
                        raise ValueError('CONTROLLER_STATE_SCHEMA')
                pending = actor.get('pending')
                if pending is not None:
                    expiry = pending.get('expires_at') if isinstance(pending, dict) else None
                    if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry < 0:
                        raise ValueError('CONTROLLER_STATE_SCHEMA')
                if actor['mem']['resident'] != rid or type(actor['cursor']) is not int or actor['cursor'] < 0 or actor['cursor'] > maximum:
                    raise ValueError('CONTROLLER_STATE_CURSOR')
                if 'model_gate' not in actor and actor['decisions'] > 0:
                    # Legacy decisions already consumed historical input; do not replay it.
                    actor['model_gate'] = {'next_at': 0, 'human_id': max((r['id'] for r in actor['mem']['heard']), default=0)}
                    migrated = True
            mark_initialized()  # adopt valid pre-marker checkpoint without resetting it
            if migrated:
                self.save()
        else:
            mark_initialized()  # durable tombstone BEFORE the first checkpoint write
            self.actors = {rid: {'mem': copy.deepcopy(mem), 'cursor': max(0, maximum-100), 'pending': None, 'steps': [], 'decisions': 0} for rid, mem in memories.items()}
            self.save()

    def save(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.controller-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as f:
                persistent = {rid: {k: actor[k] for k in ('mem', 'cursor', 'pending', 'steps', 'decisions', 'model_gate', 'reaction') if k in actor} for rid, actor in self.actors.items()}
                json.dump({'version': 1, 'actors': persistent}, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def finish_native(self, rid, done, status, now, deadline):
        """One cost-free NO_ROUTE alternative per actor lifetime, not an LLM retry."""
        actor = self.actors[rid]
        pending = actor['pending']
        if pending is None or pending.get('action_id') != done['action_id']:
            return False  # replay cannot replace already queued recovery
        from packages.body_gateway.journal import TERMINAL
        if done['state'] not in TERMINAL:
            raise ValueError('CONTROLLER_OUTCOME_NOT_TERMINAL')
        actor['mem']['outcomes'] = (actor['mem']['outcomes'] + [{k: done[k] for k in ('skill', 'state', 'code')}])[-8:]
        actor['pending'] = None
        recover = False
        if done['state'] != 'confirmed':
            actor['steps'] = []
            t = status.get('telemetry') or {}
            if (rid == 'resident_a' and done['state'] == 'failed' and done['code'] == 'NO_ROUTE'
                and pending['skill'] == 'travel_to' and not actor.get('reaction')
                and status.get('age') is not None and 0 <= status['age'] <= 3
                and not status.get('actions') and t.get('dead') is False
                and t.get('map') == 'prt_fild08' and t.get('hp_max', 0) > 0
                and t.get('hp', 0) * 100 >= t['hp_max'] * 80 and deadline - now >= 115):
                actor['reaction'] = {'action_id': done['action_id'], 'failed_map': pending['params']['map'], 'blocked_until': now + 7200}
                actor['steps'] = [('hunt', {'map': 'prt_fild08', 'duration': 60, 'min_kills': 1}, 100)]
                recover = True
        self.save()  # failure consumption and alternative plan durable BEFORE dispatch
        return recover

    def decision_due(self, rid, now, resident_char_ids):
        actor = self.actors[rid]
        gate = actor.get('model_gate')
        if gate is None:
            return actor['decisions'] == 0
        human_id = max((r['id'] for r in actor['mem']['heard'] if r['char_id'] not in resident_char_ids), default=0)
        return now >= gate['next_at'] and human_id > gate['human_id']

    def reserve_decision(self, rid, now, interval, resident_char_ids):
        actor = self.actors[rid]
        actor['model_gate'] = {'next_at': now + interval, 'human_id': max((r['id'] for r in actor['mem']['heard'] if r['char_id'] not in resident_char_ids), default=0)}
        self.save()  # cooldown and consumed input durable BEFORE the paid boundary

    def dispatch(self, rid, send):
        import uuid
        actor = self.actors[rid]
        if actor['pending'] is None:
            skill, params, seconds = actor['steps'][0]
            reaction = actor.get('reaction')
            if reaction and skill == 'travel_to' and params.get('map') == reaction['failed_map'] and time.time() < reaction['blocked_until']:
                raise ValueError('CONTROLLER_MAP_QUARANTINED')
            actor['steps'].pop(0)
            actor['pending'] = {'op': 'run', 'skill': skill, 'params': params, 'deadline': seconds, 'expires_at': time.time() + seconds, 'idem': str(uuid.uuid4())}
            self.save()  # durable intention BEFORE any socket write
        pending = actor['pending']
        expiry = pending.get('expires_at')
        if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry < 0:
            raise ValueError('CONTROLLER_INTENT_UNBOUNDED')
        remaining = expiry - time.time()
        if remaining <= 0:
            # Expiry forbids new native execution, but NOT read-only reconciliation.
            rows = send({'op': 'actions'})
            row = next((r for r in rows if r.get('resident') == rid and r.get('idem_key') == pending['idem']), None)
            if row is None:
                raise ValueError('CONTROLLER_INTENT_EXPIRED')
        else:
            message = {k: v for k, v in pending.items() if k not in ('action_id', 'expires_at')}
            message['deadline'] = min(pending['deadline'], remaining)
            row = send(message)
        pending['action_id'] = row['action_id']
        self.save()
        return row

    def ingest(self, states, fetch):
        # One bounded page per fresh actor; a disconnected consumer retains its backlog.
        pages = {}
        for rid, status in states.items():
            fresh = status.get('age') is not None and status['age'] <= 3 and bool(status.get('telemetry'))
            if not fresh:
                continue
            cursor = self.actors[rid]['cursor']
            if cursor not in pages:
                pages[cursor] = fetch(cursor)
            self.hear(rid, status['telemetry'], pages[cursor], True)

    def hear(self, rid, telemetry, rows, fresh):
        if not fresh:
            return
        actor = self.actors[rid]
        unseen = [r for r in rows if r['id'] > actor['cursor']]
        if unseen:
            # Authoritative chat ID is the dedup key, including imported legacy memory.
            heard = {r['id']: r for r in actor['mem']['heard']}
            for row in perceive(telemetry, unseen):
                heard.setdefault(row['id'], row)
            actor['mem']['heard'] = sorted(heard.values(), key=lambda r: r['id'])[-8:]
            actor['cursor'] = max(r['id'] for r in unseen)
            self.save()
