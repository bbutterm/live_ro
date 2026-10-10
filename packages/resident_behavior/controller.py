"""Explicitly injected coordinator. No listener, default motor or default paid API.

Read-only by default. Enabling dispatch requires a caller-supplied check of the
LOADED native v1 mode, not merely the existence of a marker. Meeting negotiation
and the independent speech/event collectors must be wired by the integrator;
this module does not grant promises or auto-accept an unreserved meeting.
"""
from copy import deepcopy
from datetime import datetime, timezone
from .bridge import iso
from .contracts import ContractError, validate_execute
from .coordinator import coordinate
from .engine import select
from .flags import enrich
from .reducer import initial_state, advance


class Controller:
    def __init__(self, config, store, adapter, *, clock=None, enabled=None, llm=None, llm_enabled=None):
        self.config, self.store, self.adapter = config, store, adapter
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.enabled = enabled or (lambda rid: False)
        self.llm = llm
        self.llm_enabled = llm_enabled or (lambda rid: True)

    def chat(self, rid, intent):
        from .chat_intents import handle
        return handle(self,rid,intent)

    def _reconcile(self, rid):
        missing = False
        for request in self.store.pending_actions():
            if request['resident_id'] != rid:
                continue
            try:
                result = self.adapter.action(request['action_id'])
            except ContractError:
                # A crash between claim and adapter persistence is uncertain:
                # an absent adapter row NEVER authorizes repeating a motor send.
                missing = True
                continue
            self.store.record_outcome(request['action_id'], result)
        return missing

    def _consume(self, rid, snapshot, now, peers, *, plan_habits=True):
        def apply(observation, prior):
            mental = (initial_state(self.config,rid,observation,now) if prior is None
                      else advance(self.config,rid,prior,observation,now))
            mental = enrich(self.config,rid,observation,mental,peers or {},now,plan_habits=plan_habits)
            mental['consumed_snapshot_seq'] = observation['seq']
            return mental
        return self.store.apply_snapshot(snapshot, apply)

    def step(self, rid, *, peers=None, dispatch=False, _replan=False):
        if rid not in self.config['residents']:
            raise ValueError('unknown logical resident')
        now = self.clock().timestamp()
        # Consume the durable adapter frame BEFORE refresh/reconciliation can
        # overwrite it. This closes the receive -> mental-transaction crash gap.
        prior = self.store.get_state(rid)
        with self.adapter.store.transaction() as state:
            cached = deepcopy(state.get('snapshots', {}).get(rid))
        if cached and (prior is None or cached['seq'] > prior.get('consumed_snapshot_seq',-1)):
            captured = datetime.fromisoformat(cached['server_time'].replace('Z','+00:00')).timestamp()
            replay_at = max(captured, (prior or {}).get('last_update',captured))
            self._consume(rid,cached,replay_at,peers)
        permitted = dispatch and self.enabled(rid) is True
        missing = False
        if permitted:
            self.adapter.reconcile(resident_id=rid)
            missing = self._reconcile(rid)
        self.adapter.refresh(rid)
        with self.adapter.store.transaction() as state:
            snapshot = deepcopy(state.get('snapshots', {}).get(rid))
        if snapshot is None:
            raise ContractError('stale_state')
        # RPC/reconciliation can receive a newer frame than the tick-start clock.
        # Compare telemetry with receipt-time, not a clock captured before I/O.
        now = self.clock().timestamp()
        mental = self._consume(rid,snapshot,now,peers)
        current = snapshot.get('current_action')
        active = current if current and current['result'] is None and current['action'] not in ('say','whisper') else None
        due = not missing and (active is None or now >= mental.get('next_plan_at',0))
        if due:
            ranking = select(self.config,rid,snapshot,mental,now)
            mental['rng_index'] = ranking['next_rng_index']
            mental['next_plan_at'] = now + 600
        else:
            ranking = {'primary':None, 'candidates':[]}
        decision = coordinate(self.config,rid,snapshot,mental,ranking,now)
        if not due and active and decision['source'] == 'fallback':
            decision.update(action=active['action'],args=deepcopy(active['args']),action_id=active['action_id'],
                            started_at=active['started_at'],source='continuation',trigger='cadence',
                            continuation=True,llm_allowed=False,interrupt=None)
        if active and not decision['continuation'] and not decision.get('interrupt'):
            decision['interrupt'] = active['action_id']
        mental['decision_index'] = mental.get('decision_index',0)+1
        result = {'mode':'controlled' if permitted else 'read_only','decision':decision,'llm_calls':0}
        self.store.save_state(rid,mental)
        if missing:
            result['blocked'] = 'pending_reconciliation'
        if not permitted:
            if dispatch:
                result['blocked'] = 'native_v1_not_enabled'
            return result
        if decision['continuation']:
            return result
        if decision.get('interrupt'):
            try:
                self.adapter.executor.interrupt(decision['interrupt'])
                try:
                    outcome = self.adapter.action(decision['interrupt'])
                except ContractError:
                    outcome = self.adapter.executor.action(decision['interrupt'])
            except (TimeoutError,ConnectionError,OSError):
                result['blocked'] = 'interrupt_unconfirmed'
                return result
            if outcome['result'] is None:
                result['blocked'] = 'interrupt_unconfirmed'
                return result
            if missing:
                return result
            if _replan:
                result['blocked'] = 'concurrent_motor_change'
                return result
            # Terminal interruption is a new observation, not the old pose.
            # One bounded replan obtains its new seq and rechecks safety/args.
            return self.step(rid,peers=peers,dispatch=dispatch,_replan=True)
        if missing:
            return result
        if (self.llm is not None and self.llm_enabled(rid) is True and due and active is None and decision['llm_allowed']):
            context = {'drives':mental.get('drives',{}), 'mood':mental.get('mood',{}),
                       'goals':mental.get('goals',[]), 'memory':mental.get('last_actions',[]),
                       'relations':mental.get('relations',{}),
                       'state':{k:snapshot.get(k) for k in ('map','x','y','hp','hp_max','weight','weight_max','zeny','inventory')}}
            candidates = [dict(action=c['action'],args=deepcopy(c['args']),utility=round(c['total'],2)) for c in ranking['candidates']]
            primary = {k:deepcopy(ranking['primary'][k]) for k in ('action','args')}
            try:
                reply = self.llm.complete(self.config['residents'][rid].get('persona_prompt',''), context,
                                          candidates, primary, ranking.get('override_allowed',False),
                                          rid=rid, now=now)
            except Exception:
                # Do not leak provider/credential-bearing exception strings.
                # Paid attempt is UNKNOWN; reservations remain untouched.
                reply = dict(decision=primary,fallback_reason='client_error',request_sent=None)
            result['llm'] = reply
            result['llm_calls'] = int(reply['request_sent']) if reply.get('request_sent') is not None else None
            self.adapter.refresh(rid)
            with self.adapter.store.transaction() as state:
                snapshot = deepcopy(state.get('snapshots',{}).get(rid))
            if snapshot is None or self.enabled(rid) is not True:
                result['blocked'] = 'native_v1_not_enabled'
                return result
            now = self.clock().timestamp()
            mental = self._consume(rid,snapshot,now,peers)
            from .engine import rank_candidates
            fresh_ranking = rank_candidates(self.config,rid,snapshot,mental,now)
            model_valid = reply.get('fallback_reason') is None and self.llm_enabled(rid) is True
            chosen = reply['decision'] if model_valid else ranking['primary']
            changed = any(v['action'] == chosen['action'] for v in fresh_ranking['vetoes'])
            policy = dict(ranking,primary=chosen)
            if changed:
                policy = dict(fresh_ranking,primary=next(iter(fresh_ranking['candidates']),None))
            decision = coordinate(self.config,rid,snapshot,mental,policy,now)
            if model_valid and not changed and decision['source'] == 'fallback':
                decision.update(say=chosen.get('say'),source='llm',reason=chosen.get('reason',''))
            else:
                reply['superseded_by'] = decision['trigger'] if decision['source'] == 'safety' else ('state_changed' if changed else reply.get('fallback_reason') or 'llm_opt_in_revoked')
            result['decision'] = decision
            self.store.save_state(rid,mental)
            if decision['continuation'] or decision.get('interrupt'):
                result['blocked'] = 'concurrent_motor_change'
                return result
        request = validate_execute(dict(version=1,resident_id=rid,seq=snapshot['seq'],server_time=iso(now),
            decision_id=decision['decision_id'],action_id=decision['action_id'],state_seq=snapshot['seq'],
            expires_at=iso(decision['expires_at']),action=decision['action'],args=decision['args'],say=decision['say']))
        self.store.save_state(rid,mental,action=request)
        if not self.store.claim_action(request['action_id']):
            result['blocked'] = 'pending_reconciliation'
            return result
        try:
            ack = self.adapter.execute(request)
        except (TimeoutError,ConnectionError,OSError):
            result['blocked'] = 'transport_timeout'
            return result
        result['ack'] = ack
        if not ack['accepted'] and ack['error_code'] != 'transport_timeout':
            self.store.record_outcome(request['action_id'],dict(action_id=request['action_id'],result='failed',
                error_code=ack['error_code'],source_seq=None,completed_at=iso(now)))
        return result
