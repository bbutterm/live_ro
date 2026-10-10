"""Explicit, poll-driven v1 bridge to the existing OpenKore body control socket.

No listener, thread, live-state default, manual lease, console command or retry.
Use a separate SQLiteAdapterStore for durability (it may be shared with Adapter).
Every native step is claimed durably BEFORE I/O; an uncertain claim is looked up
by idem_key via ``actions`` and NEVER resubmitted, including after restart.
Call Adapter.reconcile() at least every two seconds. Optional request.say is
owned by the speech coordinator, not an unconfirmed motor prelude.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math

from packages.body_gateway.cli import request as body_request
from packages.body_gateway.journal import TERMINAL
from .bridge import snapshot_from_status
from .contracts import ContractError, validate_execute, validate_telemetry, utc_time


def _iso(now):
    return now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _ack(aid, error=None):
    return dict(action_id=aid, accepted=error is None, error_code=error)


def _pending(aid):
    return dict(action_id=aid, result=None, error_code=None, source_seq=None, completed_at=None)


def native_error(code):
    """Translate legacy/native codes to the closed v1 error vocabulary."""
    code = (code or '').upper()
    if code in ('DIED', 'DEAD'): return 'dead'
    if 'STALE' in code: return 'stale_state'
    if code in ('BUSY', 'CONFLICT', 'DUPLICATE'): return 'conflict'
    if code in ('CANCELLED', 'INTERRUPTED', 'CHAT_INTERRUPT'): return 'interrupted'
    if code in ('BUDGET', 'NO_FUNDS', 'INSUFFICIENT_ZENY'): return 'no_funds'
    if code in ('NO_PATH', 'NO_ROUTE', 'UNWALKABLE', 'STUCK', 'ROUTE_FAILED', 'UNREACHABLE'): return 'no_path'
    if code in ('TARGET_LOST', 'TARGET_NOT_VISIBLE'): return 'target_lost'
    if code in ('TIMEOUT', 'VERIFY_TIMEOUT', 'NO_EVIDENCE', 'DISPATCH_UNCERTAIN', 'BODY_RESTARTED', 'COORDINATOR_RESTART'): return 'transport_timeout'
    return 'unsafe'


class OpenKoreMotor:
    """Adapter source/executor. sockets maps logical IDs to explicit .control paths.

    config is the validated behavior config; store implements transaction/lock
    (SQLiteAdapterStore recommended). request(path, message) may be injected for
    offline tests. There is deliberately no default socket or database path.
    """
    def __init__(self, sockets, config, store, *, clock=None, request=None):
        self.sockets = dict(sockets)
        self.config, self.store = config, store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.request = request or body_request
        with self.store.transaction() as state:
            state.setdefault('motor', {'actions': {}, 'sources': {}, 'slots': {}})

    def _rpc(self, resident, message):
        if resident not in self.sockets: raise ContractError('unsafe')
        try:
            return self.request(str(self.sockets[resident]), message)
        except RuntimeError as exc:
            # Existing CLI reports closed socket/control errors as RuntimeError.
            raise ConnectionError(str(exc)) from exc

    def snapshot(self, resident_id):
        status = self._rpc(resident_id, {'op': 'status'})
        with self.store.transaction() as state:
            motor = state['motor']
            previous = motor['sources'].get(resident_id)
            rows = [row for row in motor['actions'].values()
                    if row['request']['resident_id'] == resident_id
                    and row['request']['action'] not in ('say', 'whisper')]
            current = None
            if rows:
                slot = motor['slots'].get(resident_id)
                row = motor['actions'].get(slot) if slot else max(rows, key=lambda r:(r.get('created_index',0),r['started']))
                p, result = row['request'], row['result']
                current = dict(action_id=p['action_id'], action=p['action'], args=deepcopy(p['args']),
                               started_at=_iso(datetime.fromtimestamp(row['started'], timezone.utc)),
                               result=result['result'], error_code=result['error_code'])
            snapshot = snapshot_from_status(status, resident_id, 0, current_action=current)
            # Synthesized sequence is durable; polling identical observations does
            # not invalidate the caller's state_seq on Adapter.refresh().
            if previous and {k:v for k,v in previous.items() if k != 'seq'} == {k:v for k,v in snapshot.items() if k != 'seq'}:
                snapshot['seq'] = previous['seq']
            else:
                snapshot['seq'] = previous['seq']+1 if previous else 1
            validate_telemetry(snapshot)
            motor['sources'][resident_id] = deepcopy(snapshot)
            return snapshot

    def _record(self, aid):
        with self.store.transaction() as state:
            row = state['motor']['actions'].get(aid)
            if row is None: raise ContractError('not_found')
            return deepcopy(row)

    def _plan(self, p):
        action, args = p['action'], p['args']
        if action in ('walk_to', 'meet'):
            point = self.config['places'][args['place']]
            return [('route', 'travel_to', {k:point[k] for k in ('map','x','y')} | {'r':3}, 300)]
        if action == 'say': return [('speech', 'say', args, 3)]
        if action == 'whisper': return [('speech', 'whisper', {'to_name':args['to'], 'text':args['text']}, 3)]
        if action == 'rest': return [('rest', 'rest', {'hp_pct':args['until_hp']}, 600)]
        if action == 'hunt':
            return [('route', 'travel_to', {'map':args['map'], 'x':170, 'y':355, 'r':3}, 300),
                    ('hunt', 'hunt', {'map':args['map'], 'duration':math.ceil(args['minutes']*60), 'min_kills':0}, args['minutes']*60+60)]
        if action == 'town_service':
            return [('route', 'travel_to', {'map':'prt_in', 'x':126, 'y':76, 'r':3}, 300)]
        return []

    def execute(self, payload):
        p = validate_execute(payload); aid = p['action_id']; resident = p['resident_id']
        with self.store.lock:
            with self.store.transaction() as state:
                motor = state['motor']; old = motor['actions'].get(aid)
                if old: return deepcopy(old['ack']) if old['request'] == p else _ack(aid, 'conflict')
                if resident not in self.sockets: return _ack(aid, 'unsafe')
                speech = p['action'] in ('say', 'whisper')
                if not speech and resident in motor['slots']: return _ack(aid, 'conflict')
                now = self.clock()
                if now >= utc_time(p['expires_at']) or now < utc_time(p['server_time']): return _ack(aid, 'stale_state')
                durations = {'say':3, 'whisper':3, 'walk_to':300, 'rest':600, 'town_service':480,
                             'hunt':360+p['args'].get('minutes',0)*60, 'meet':600,
                             'follow':60+p['args'].get('minutes',0)*60, 'idle':60+p['args'].get('minutes',0)*60}
                motor['next_action_index'] = motor.get('next_action_index',0)+1
                row = dict(request=p, ack=_ack(aid), result=_pending(aid), started=now.timestamp(),
                           created_index=motor['next_action_index'],
                           deadline=now.timestamp()+durations[p['action']], steps=[], index=0,
                           plan=self._plan(p), active_since=None, interrupted=False)
                motor['actions'][aid] = row
                if not speech: motor['slots'][resident] = aid
            try: self._advance(aid)
            except (TimeoutError, ConnectionError, OSError):
                with self.store.transaction() as state:
                    state['motor']['actions'][aid]['ack'] = _ack(aid, 'transport_timeout')
            return self._record(aid)['ack']

    def _dispatch(self, aid, label, skill, params, deadline):
        row = self._record(aid); resident = row['request']['resident_id']
        key = 'v1-'+hashlib.sha256((resident+'|'+aid+'|'+label).encode()).hexdigest()
        with self.store.transaction() as state:
            row = state['motor']['actions'][aid]
            if any(s['key'] == key for s in row['steps']): return
            row['steps'].append(dict(key=key, label=label, skill=skill, params=deepcopy(params), native_id=None, consumed=False))
            if skill == 'hunt': row['active_since'] = self.clock().timestamp()
        native = self._rpc(resident, dict(op='run', skill=skill, params=params, deadline=deadline, idem=key))
        with self.store.transaction() as state:
            state['motor']['actions'][aid]['steps'][-1]['native_id'] = native['action_id']

    def _finish(self, aid, result, error=None):
        row = self._record(aid)
        try: snapshot = self.snapshot(row['request']['resident_id'])
        except (TimeoutError, ConnectionError, OSError, ContractError): return
        with self.store.transaction() as state:
            motor = state['motor']; row = motor['actions'][aid]
            row['result'] = dict(action_id=aid, result=result, error_code=error,
                                 source_seq=snapshot['seq'], completed_at=_iso(self.clock()))
            row['ack'] = _ack(aid)
            resident = row['request']['resident_id']
            if motor['slots'].get(resident) == aid: motor['slots'].pop(resident)

    def _advance(self, aid):
        row = self._record(aid)
        if row['result']['result'] is not None: return
        resident = row['request']['resident_id']
        error = self._danger(row['request'])
        if row.get('stop_reason') or error or self.clock().timestamp() >= row['deadline']:
            reason = row.get('stop_reason') or error or 'transport_timeout'
            self._stop(aid, reason)
            return
        if row['steps'] and not row['steps'][-1]['consumed']:
            step = row['steps'][-1]
            if step['native_id'] is None:
                native = next((a for a in self._rpc(resident, {'op':'actions'}) if a['idem_key'] == step['key']), None)
                if native is None: return
                with self.store.transaction() as state: state['motor']['actions'][aid]['steps'][-1]['native_id'] = native['action_id']
            else: native = self._rpc(resident, {'op':'action', 'action_id':step['native_id']})
            if native['state'] not in TERMINAL: return
            if native['state'] != 'confirmed':
                error = native_error(native.get('code'))
                self._finish(aid, 'timeout' if error == 'transport_timeout' else 'failed', error)
                return
            with self.store.transaction() as state:
                current = state['motor']['actions'][aid]
                current['steps'][-1]['consumed'] = True
                current['index'] += 1
            row = self._record(aid)
        if row['index'] < len(row['plan']):
            self._dispatch(aid, *row['plan'][row['index']])
            return
        if row['request']['action'] == 'meet' and self._meet(aid): return
        if row['request']['action'] == 'follow' and self._follow(aid): return
        if row['request']['action'] == 'idle':
            if not row.get('stop_claimed'):
                with self.store.transaction() as state:
                    state['motor']['actions'][aid]['stop_claimed'] = True
                self._rpc(resident, {'op':'safe_stop'})
            status = self._rpc(resident, {'op':'status'})
            snapshot = self.snapshot(resident)
            if not self._fresh(snapshot) or status.get('telemetry', {}).get('mode') != 'IDLE_SAFE': return
            if self.clock().timestamp()-row['started'] < row['request']['args']['minutes']*60: return
        if row['request']['action'] == 'rest':
            snapshot = self.snapshot(resident)
            if (not self._fresh(snapshot) or snapshot['sitting'] is not True
                    or snapshot['hp'] is None or snapshot['hp_max'] is None
                    or snapshot['hp']*100 < row['request']['args']['until_hp']*snapshot['hp_max']):
                return
        if row['request']['action'] == 'town_service' and self._service(aid):
            return
        if row['request']['action'] == 'hunt':
            snapshot = self.snapshot(resident)
            if not self._fresh(snapshot) or snapshot['map'] != row['request']['args']['map']: return
            if row['active_since'] is None or self.clock().timestamp()-row['active_since'] < row['request']['args']['minutes']*60: return
        self._finish(aid, 'done')

    def _danger(self, p):
        action = p['action']
        if action == 'idle': return None  # dead/stale idle(1) is a valid safety wait
        snapshot = self.snapshot(p['resident_id'])
        if not self._fresh(snapshot): return 'stale_state'
        if snapshot['alive'] is False: return 'dead'
        if snapshot['alive'] is not True: return 'unsafe'
        if action in ('hunt', 'follow', 'meet'):
            if snapshot['hp'] is None or snapshot['hp_max'] is None: return 'unsafe'
            if snapshot['hp']*100 < 55*snapshot['hp_max']: return 'unsafe'
        if action in ('hunt', 'follow'):
            if snapshot['weight'] is None or snapshot['weight_max'] is None: return 'unsafe'
            if snapshot['weight']*100 >= 85*snapshot['weight_max']: return 'unsafe'
        if action == 'hunt':
            monsters, inventory = snapshot['near_monsters'], snapshot['inventory']
            if monsters is None or inventory is None: return 'unsafe'
            if any(m.get('id') != '1002' for m in monsters): return 'unsafe'
            if inventory.get('501', 0) == 0 and snapshot['hp']*100 < 85*snapshot['hp_max']: return 'unsafe'
        if action == 'rest' and (snapshot['near_monsters'] is None or snapshot['near_monsters']): return 'unsafe'
        return None

    def _stop(self, aid, reason):
        row = self._record(aid); resident = row['request']['resident_id']
        speech = row['request']['action'] in ('say', 'whisper')
        with self.store.transaction() as state:
            current = state['motor']['actions'][aid]
            current.setdefault('stop_reason', reason)
            send = not current.get('cancel_stop_claimed')
            current['cancel_stop_claimed'] = True
        # Cancellation is not terminal evidence. Unknown native IDs are recovered
        # by key, never by replaying their run request.
        unresolved = False
        for step in row['steps']:
            if step['consumed']: continue
            if step['native_id']:
                native = self._rpc(resident, {'op':'action', 'action_id':step['native_id']})
            else:
                native = next((a for a in self._rpc(resident, {'op':'actions'}) if a['idem_key'] == step['key']), None)
            if speech and native is None:
                unresolved = True  # uncertain dispatch is not proof of cancellation
            if native and native['state'] not in TERMINAL:
                unresolved = True
                with self.store.transaction() as state:
                    stored = next(s for s in state['motor']['actions'][aid]['steps'] if s['key'] == step['key'])
                    cancel = not stored.get('cancel_claimed')
                    stored['cancel_claimed'] = True
                    stored['native_id'] = native['action_id']
                if cancel: self._rpc(resident, {'op':'cancel', 'action_id':native['action_id']})
        if send and not speech: self._rpc(resident, {'op':'safe_stop'})
        if unresolved: return
        if speech:
            # Speech has no motor slot: its own native terminal witness is enough.
            self._finish(aid, 'timeout' if reason == 'transport_timeout' else 'failed', reason)
            return
        status = self._rpc(resident, {'op':'status'})
        snapshot = self.snapshot(resident)
        if self._fresh(snapshot) and status.get('telemetry', {}).get('mode') == 'IDLE_SAFE':
            self._finish(aid, 'timeout' if reason == 'transport_timeout' else 'failed', reason)

    def _meet(self, aid):
        row = self._record(aid); p = row['request']; now = self.clock().timestamp()
        mine = self.snapshot(p['resident_id'])
        try: peer = self.snapshot(p['args']['resident'])
        except (ContractError, TimeoutError, ConnectionError, OSError): peer = None
        near = (peer is not None and self._fresh(mine) and self._fresh(peer)
                and mine['alive'] is True and peer['alive'] is True
                and mine['map'] is not None and mine['map'] == peer['map']
                and all(s[k] is not None for s in (mine, peer) for k in ('x', 'y')))
        if near: near = max(abs(mine['x']-peer['x']), abs(mine['y']-peer['y'])) <= 5
        with self.store.transaction() as state:
            current = state['motor']['actions'][aid]
            current.setdefault('wait_since', now)
            last = current.get('last_near_check')
            if not near or last is None or now-last > 10: current['near_since'] = None
            if near and current.get('near_since') is None: current['near_since'] = now
            current['last_near_check'] = now
            done = near and now-current['near_since'] >= 120
            timeout = now-current['wait_since'] >= 300
        if done: return False
        if timeout: self._finish(aid, 'timeout', 'transport_timeout')
        return True

    def _follow(self, aid):
        row = self._record(aid); p = row['request']; now = self.clock().timestamp()
        snapshot = self.snapshot(p['resident_id'])
        if not self._fresh(snapshot) or snapshot['near_players'] is None: return True
        target = next((t for t in snapshot['near_players'] if t.get('name') == p['args']['player']), None)
        if not target or target['x'] is None or target['y'] is None:
            with self.store.transaction() as state:
                current = state['motor']['actions'][aid]
                if current.get('lost_since') is None: current['lost_since'] = now
                lost = now-current['lost_since'] >= 30
            if lost: self._finish(aid, 'failed', 'target_lost')
            return True
        with self.store.transaction() as state: state['motor']['actions'][aid]['lost_since'] = None
        if now-row['started'] >= p['args']['minutes']*60: return False
        if snapshot['map'] is None or snapshot['x'] is None or snapshot['y'] is None: return True
        if max(abs(snapshot['x']-target['x']), abs(snapshot['y']-target['y'])) > 3:
            self._dispatch(aid, 'follow-'+str(len(row['steps'])), 'travel_to',
                           dict(map=snapshot['map'], x=target['x'], y=target['y'], r=3), 15)
        return True

    def _service(self, aid):
        row = self._record(aid); resident = row['request']['resident_id']
        phase = row.get('service_phase', 'sell')
        if phase == 'done': return False
        snapshot = self.snapshot(resident)
        if not self._fresh(snapshot) or snapshot['map'] not in ('prontera', 'prt_in'): return True
        bag, funds = snapshot['inventory'], snapshot['zeny']
        if bag is None or funds is None: return True
        plan = None
        if phase == 'sell':
            targets = {}
            for goal in self.config['residents'][resident]['goals']:
                if goal['type'] == 'item_count':
                    key = str(goal['item_id'])
                    targets[key] = max(targets.get(key, 0), goal['target'])
            items = {k:min(1000, bag.get(k, 0)-targets.get(k, 0)) for k in ('909','713','938')
                     if bag.get(k, 0) > targets.get(k, 0)}
            if items: plan = ('sell', 'sell_loot', {'items':items}, 60)
            next_phase = 'buy'
        else:
            stock = bag.get('501', 0)
            goal = min({'resident_a':8, 'mira':2, 'borin':3}.get(resident, 3), stock+funds//50)
            if goal > stock: plan = ('buy', 'buy_potions', {'item_id':501, 'stock_goal':goal, 'max_zeny':50*(goal-stock)}, 60)
            next_phase = 'done'
        with self.store.transaction() as state:
            current = state['motor']['actions'][aid]
            # Freeze each economic plan before dispatch; no restart recalculation
            # of an already claimed purchase/sale and no whole-bag legacy sale.
            if current.get('service_phase', 'sell') != phase: return True
            current['service_phase'] = next_phase
            if plan: current['plan'].append(plan)
        if plan:
            self._dispatch(aid, *plan)
            return True
        return self._service(aid) if next_phase != 'done' else False

    def _fresh(self, snapshot):
        age = (self.clock()-utc_time(snapshot['server_time'])).total_seconds()
        return 0 <= age <= 10

    def action(self, action_id):
        with self.store.lock:
            self._record(action_id)
            try: self._advance(action_id)
            except (TimeoutError, ConnectionError, OSError): pass
            return self._record(action_id)['result']

    def interrupt(self, action_id):
        with self.store.lock:
            row = self._record(action_id)
            if row['result']['result'] is not None: return _ack(action_id)
            try: self._stop(action_id, 'interrupted')
            except (TimeoutError, ConnectionError, OSError): return _ack(action_id, 'transport_timeout')
            return _ack(action_id)
