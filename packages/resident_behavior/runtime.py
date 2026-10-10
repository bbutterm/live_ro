"""Read-only rollout observer. Never sends commands or calls a language model."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from packages.body_gateway.cli import request
from .bridge import LOGICAL_IDS, snapshot_from_status, iso
from .config import load_behavior, config_hash
from .contracts import validate_telemetry
from .coordinator import coordinate
from .engine import select
from .reducer import initial_state, advance
from .flags import enrich
from copy import deepcopy
from .store import BehaviorStore


def observe(config, resident, status, state, seq, now, peers=None):
    rid = LOGICAL_IDS[resident['name']]
    before = deepcopy(state)
    snapshot = validate_telemetry(snapshot_from_status(status, rid, seq))
    state = initial_state(config, rid, snapshot, now) if state is None else advance(config, rid, state, snapshot, now)
    state = enrich(config, rid, snapshot, state, peers or {}, now)
    ranking = select(config, rid, snapshot, state, now)
    decision = coordinate(config, rid, snapshot, state, ranking, now)
    state['rng_index'] = ranking['next_rng_index']
    state['decision_index'] = state.get('decision_index', 0) + 1
    record = dict(version=1, resident_id=rid, seq=seq, server_time=iso(now), type='decision',
                  decision_id=decision['decision_id'], action_id=decision['action_id'],
                  state={'snapshot': snapshot, 'derived': state['derived'], 'rollout_mode': 'read_only_shadow'},
                  event_ids=[], config_hash=config_hash(config), seed=ranking['seed'], trigger=decision['trigger'],
                  drives_before=before['drives'] if before else None, drives_after=state['drives'], mood=state['mood'], goals=state['goals'],
                  relations_before=before['relations'] if before else None, relations_after=state['relations'], utilities=ranking['utilities'],
                  vetoes=ranking['vetoes'], primary=ranking['primary'], override_allowed=ranking['override_allowed'],
                  selected={'action': decision['action'], 'args': decision['args']}, source=decision['source'],
                  llm_kind=None, prompt_bytes=None, tokens=None, cost_usd=None, reserved_usd=None,
                  fact_refs=['snapshot:'+rid+':'+str(seq)], speech=None, outcome=None)
    return snapshot, state, record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default='config/behavior.yaml')
    p.add_argument('--world-config', default='config/world.yaml')
    p.add_argument('--state-root', default='/var/lib/ro-world')
    p.add_argument('--output', required=True)
    p.add_argument('--duration', type=float, default=900)
    args = p.parse_args()
    if args.duration <= 0: p.error('duration must be positive')
    config = load_behavior(args.config)
    world = json.loads(Path(args.world_config).read_text())
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    store = BehaviorStore(out/'observer.sqlite')
    # This directory is distinct from live state. There is no outbox dispatch.
    end = time.monotonic() + args.duration
    counts = {}; errors = []; polls = 0
    def status(resident):
        path = str(Path(args.state_root)/resident['resident_id']/'actions.sock.control')
        return request(path, {'op':'status'})
    try:
        with ThreadPoolExecutor(max_workers=3) as pool, (out/'decisions.jsonl').open('a', encoding='utf-8') as log:
            while time.monotonic() < end:
                started = time.monotonic()
                futures = [(r, pool.submit(status, r)) for r in world['residents'] if r['name'] in LOGICAL_IDS]
                for resident, future in futures:
                    rid = LOGICAL_IDS[resident['name']]
                    try:
                        observed = future.result(timeout=4)
                        previous = store.get_snapshot(rid)
                        seq = 1 if previous is None else previous['seq'] + 1
                        peers = {}
                        for peer in LOGICAL_IDS.values():
                            if peer == rid:
                                continue
                            ps, pm = store.get_snapshot(peer), store.get_state(peer)
                            if ps is not None and pm is not None:
                                peers[peer] = {'snapshot': ps, 'derived': pm['derived'], 'schedule': pm.get('schedule', {})}
                        snapshot, mental, record = observe(config, resident, observed, store.get_state(rid), seq, time.time(), peers)
                        store.ingest_snapshot(snapshot, full=True)
                        store.save_state(rid, mental)
                        log.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n'); log.flush()
                        counts[rid] = counts.get(rid, 0)+1
                    except Exception as exc:
                        errors.append({'resident_id':rid, 'error':type(exc).__name__ + ':' + str(exc), 'at':iso(time.time())})
                polls += 1
                time.sleep(min(max(0, 2 - (time.monotonic()-started)), max(0, end-time.monotonic())))
    finally:
        store.close()
        report = dict(mode='read_only_shadow', motor_commands=0, llm_calls=0, duration_s=args.duration,
                      polls=polls, decisions=counts, evaluations=counts, live_v1_decisions=0,
                      acceptance_gate_passed=False, errors=errors, config_hash=config_hash(config))
        (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False))
    return int(bool(errors))

if __name__ == '__main__':
    raise SystemExit(main())
