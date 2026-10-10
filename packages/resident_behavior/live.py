"""Lease-bound runner with optional, separately opted-in LLM. Import starts nothing.

Every native mutation passes the request gate, including compound motor stages.
Draining is deliberately separate from motor.action(), which can advance stages.
"""
import fcntl
import json
import os
from pathlib import Path
import signal
import time
from datetime import datetime, timezone

from packages.body_gateway.cli import request as body_request
from packages.body_gateway.journal import TERMINAL
from .adapter import Adapter, SQLiteAdapterStore
from .bridge import LOGICAL_IDS
from .config import load_behavior
from .controller import Controller
from .contracts import ContractError
from .motor import OpenKoreMotor
from .ownership import read_lease, lease_active, dispatch_allowed
from .store import BehaviorStore

ROOT = Path(__file__).resolve().parents[2]
ECONOMIC = {'buy_potions', 'sell_loot'}
MUTATIONS = {'run', 'cancel', 'safe_stop'}


def atomic(path, value):
    tmp = path.with_name(path.name + '.live.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
    os.replace(tmp, path)


class LiveRunner:
    def __init__(self, resident, directory, *, clock=None, request=None):
        self.resident, self.directory = resident, Path(directory)
        self.rid = LOGICAL_IDS[resident['name']]
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.transport = request or body_request
        self.socket = str(self.directory/'actions.sock.control')
        lease = read_lease(self.directory)
        if not lease_active(lease):
            raise RuntimeError('active behavior lease required')
        self.token = lease['token']
        self.deadline = lease['deadline']  # immutable across runner lifetime/restarts
        self.stopping = False
        self.draining = False
        self.cancelled = set()
        self.config = load_behavior(ROOT/'config/behavior.yaml')
        self.adapter_store = SQLiteAdapterStore(self.directory/'behavior-adapter.sqlite')
        self.state = BehaviorStore(self.directory/'behavior-state.sqlite')
        self.motor = OpenKoreMotor({self.rid:self.socket}, self.config, self.adapter_store,
                                  clock=self.clock, request=self.request)
        self.adapter = Adapter(source=self.motor, executor=self.motor, store=self.adapter_store,
                               clock=self.clock, veto=self.veto)
        llm = None
        if self.model_enabled(self.rid):
            from .provider import from_environment
            try:
                llm = from_environment(ROOT/'.env', state_root=self.directory.parent)
            except (OSError, ValueError):
                self.record('llm_unavailable',reason='configuration_denied')
        self.controller = Controller(self.config, self.state, self.adapter,
                                     clock=self.clock, enabled=self.enabled, llm=llm, llm_enabled=self.model_enabled)

    def model_enabled(self, rid):
        try:
            return (rid == self.rid and not self.stopping and not self.draining
                    and (self.directory/'BEHAVIOR_LLM').read_text().strip() == self.token
                    and self.clock().timestamp() < self.deadline
                    and dispatch_allowed(read_lease(self.directory), self.token, self.clock().timestamp()))
        except OSError:
            return False

    def close(self):
        self.state.close()
        self.adapter_store.close()

    def record(self, kind, **value):
        row = dict(kind=kind, at=self.clock().timestamp(), resident_id=self.rid,
                   behavior_owner=self.token, source='behavior_v1', **value)
        with (self.directory/'behavior-live.jsonl').open('a') as output:
            output.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')

    def fresh(self, status):
        now = self.clock().timestamp()
        t = status.get('telemetry') or {}
        return (type(status.get('age')) in (int,float) and 0 <= status['age'] <= 3
                and type(t.get('ts')) in (int,float) and 0 <= now-t['ts'] <= 3)

    def enabled(self, rid):
        status = self.transport(self.socket, {'op':'status', 'behavior_owner':self.token})
        return (not self.stopping and not self.draining and rid == self.rid
                and self.fresh(status) and status.get('telemetry', {}).get('behavior_v1') is True
                and self.clock().timestamp() < self.deadline
                and dispatch_allowed(read_lease(self.directory), self.token, self.clock().timestamp()))

    def request(self, path, message):
        if message['op'] not in MUTATIONS:
            return self.transport(path, dict(message, behavior_owner=self.token))
        if not self.enabled(self.rid):
            raise ConnectionError('behavior dispatch gate closed')
        # Lease can change during status RPC: check again immediately before send.
        if not dispatch_allowed(read_lease(self.directory), self.token, self.clock().timestamp()):
            raise ConnectionError('behavior ownership changed')
        message = dict(message, behavior_owner=self.token)
        self.record('native_request', request=message)
        reply = self.transport(path, message)
        self.record('native_ack', reply=reply)
        return reply

    def veto(self, request, snapshot):
        # Controller already arbitrates safety/inertia; Adapter still performs
        # its own fresh-state, arguments, resources and meeting safety checks.
        if request['action'] in ('say','whisper','idle','rest','town_service'):
            return None
        if request['action'] == 'walk_to':
            return None if request['args']['place'] in self.config['places'] else 'unsafe'
        if request['action'] == 'hunt':
            fields = {p['map'] for p in self.config['places'].values() if p['kind']=='field'}
            return None if request['args']['map'] in fields else 'unsafe'
        if request['action'] == 'follow':
            visible={p['name'] for p in snapshot.get('near_players',[]) if p.get('visible') is True}
            return None if request['args']['player'] in visible else 'unsafe'
        return 'unsafe'  # no unreserved meetings

    def consume_chat_goal(self):
        # Historical legacy promises are never replayed as motor commands.
        from packages.resident_world.supervisor import clear_chat_goal
        path = self.directory/'chat-goal.json'
        if path.exists():
            goal = json.loads(path.read_text())
            self.record('chat_intent_rejected', intent=goal,
                        reason='legacy_intent_has_no_server_heard_envelope', accepted=False)
            clear_chat_goal(self.directory, goal.get('id'))
        paths=sorted((self.directory/'behavior-chat').glob('chat-*.json'))
        if not paths:
            return None
        import hashlib
        path=paths[0]
        intent=json.loads(path.read_text())
        if intent.get('lease_id') != hashlib.sha256(self.token.encode()).hexdigest():
            result={'blocked':'different_lease'}
        else:
            result=self.controller.chat(self.rid,intent)
        self.record('chat_intent',intent_id=intent.get('id'),result=result)
        path.unlink(missing_ok=True)
        return result

    def tick(self):
        lease = read_lease(self.directory)
        if (self.stopping or not lease_active(lease) or lease.get('token') != self.token
                or self.clock().timestamp() >= self.deadline or lease.get('phase') in ('rollback','blocked')):
            return self.drain()
        self.consume_chat_goal()
        pending_ids = {p['action_id'] for p in self.state.pending_actions()}
        result = self.controller.step(self.rid, dispatch=True)
        pending_ids.update(p['action_id'] for p in self.state.pending_actions())
        for aid in sorted(pending_ids):
            self.record('outcome', action_id=aid, outcome=self.state.get_action(aid))
        self.record('evaluation', result=result)
        decision = result['decision']
        atomic(self.directory/'brain.json', dict(source='behavior_v1', at=self.clock().timestamp(),
               pending=decision['action_id'], activity=decision['action'], reason=decision['trigger'],
               mode=result['mode'], evaluation=result, deadline=self.deadline))
        return result

    def phase(self, phase):
        from .ownership import set_phase
        try:
            set_phase(self.directory, phase, self.token)
            return True
        except ValueError:
            return False

    def drain(self):
        self.draining = True
        self.phase('blocked')  # failed/uncertain cleanup cannot enable legacy
        status = self.transport(self.socket, {'op':'status', 'behavior_owner':self.token})
        if not self.fresh(status):
            self.record('drain_blocked', reason='stale_status')
            return {'mode':'blocked', 'released':False}
        active = status.get('active', status.get('actions'))
        if not isinstance(active, list):
            return {'mode':'blocked', 'released':False}
        # No motor.action/reconcile while draining: those advance compound plans.
        with self.adapter_store.transaction() as state:
            owned_keys = {step['key'] for row in state['motor']['actions'].values()
                          for step in row['steps']}
        for row in active:
            if row.get('state') in TERMINAL or row.get('skill') in ECONOMIC:
                continue
            lease = read_lease(self.directory)
            if (lease and lease.get('token') == self.token and lease_active(lease)
                    and row.get('action_id') not in self.cancelled
                    and status.get('telemetry', {}).get('behavior_v1') is True
                    and (row.get('behavior_owner') == self.token
                         or row.get('idem_key') in owned_keys)):
                message = dict(op='cancel', action_id=row['action_id'], behavior_owner=self.token)
                reply = self.transport(self.socket, message)
                self.cancelled.add(row['action_id'])
                self.record('drain_cancel', request=message, reply=reply)
        # Only a NEW full observation can prove cancellation/economic completion.
        status = self.transport(self.socket, {'op':'status', 'behavior_owner':self.token})
        active = status.get('active', status.get('actions'))
        all_rows = self.transport(self.socket, {'op':'actions', 'behavior_owner':self.token})
        global_clear = isinstance(all_rows, list) and all(row.get('state') in TERMINAL for row in all_rows)
        t = status.get('telemetry') or {}
        clear = self.fresh(status) and active == [] and global_clear and 'running' in t and t['running'] is None
        if not clear:
            self.record('drain_blocked', reason='unresolved_native', status=status)
            return {'mode':'blocked', 'released':False}
        with self.adapter_store.transaction() as state:
            known = set(state['motor']['actions'])
            roots = [aid for aid,row in state['motor']['actions'].items()
                     if row['result']['result'] is None]
        if any(p['action_id'] not in known for p in self.state.pending_actions()):
            self.record('drain_blocked', reason='pending_reconciliation')
            return {'mode':'blocked', 'released':False}
        for aid in roots:
            self.motor._finish(aid, 'failed', 'interrupted')
            outcome = self.motor._record(aid)['result']
            if outcome['result'] is None:
                return {'mode':'blocked', 'released':False}
            self.state.record_outcome(aid, outcome)
            self.record('outcome', action_id=aid, outcome=outcome)
        if not self.phase('released'):
            return {'mode':'blocked', 'released':False}
        (self.directory/'BEHAVIOR_V1').unlink(missing_ok=True)
        self.record('released')
        atomic(self.directory/'brain.json', dict(source='behavior_v1', mode='released',
                                                at=self.clock().timestamp(), pending=None))
        return {'mode':'released', 'released':True}


def run(resident, directory):
    runner = LiveRunner(resident, directory)
    def stop(*_):
        runner.stopping = True
    previous = {s:signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        while True:
            started = time.monotonic()
            if (Path(directory).parent/'STOP').exists():
                runner.stopping = True
            try:
                if runner.tick().get('released'):
                    return
            except (OSError, RuntimeError, ValueError, KeyError, TypeError, ContractError) as error:
                runner.record('error', error=type(error).__name__, detail=str(error))
                runner.stopping = True
                runner.phase('blocked')
            # CLI has a 3s per-RPC timeout; target cadence is 1s, never add >2s.
            time.sleep(max(0, 1-(time.monotonic()-started)))
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        runner.close()
