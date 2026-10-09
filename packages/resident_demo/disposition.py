"""Explicit offline disposition, not automatic recovery or success reconciliation.

Caller must own supervisor/gameplay locks and verify offline/terminal admission.
Only one non-economic unconfirmed hunt may be parked per durable checkpoint.
"""
import copy


def park_unconfirmed_hunt(life, action, now):
    import json, math
    state = life.state
    pending = state.get('pending')
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    try:
        valid = (isinstance(action, dict) and isinstance(pending, dict)
            and state.get('terminal_disposition') is None
            and not any(key in state for key in ('reaction','stuck_recovery','death_recovery'))
            and state.get('blocked') == 'NATIVE_INTENT_EXPIRED' and state['phase'] == 'hunt'
            and type(state['cycles']) is int and state['cycles'] >= 0
            and isinstance(state['history'], list) and type(state['has_hunted']) is bool
            and action['state'] == 'escalated' and action['code'] == 'NO_EVIDENCE'
            and action['resident'] == 'resident_a' and action['skill'] == pending['skill'] == 'hunt'
            and isinstance(pending['action_id'], str) and bool(pending['action_id'])
            and action['action_id'] == pending['action_id']
            and isinstance(pending['idem'], str) and bool(pending['idem'])
            and action['idem_key'] == pending['idem']
            and pending['reason'] == 'HUNT_INTERVAL' and pending['from_phase'] == 'hunt'
            and pending['next_phase'] == 'town'
            and type(pending['timeout']) is int and pending['timeout'] == life.hunt_seconds+40
            and pending['params'] == json.loads(action['params'])
            and set(pending['params']) == {'map','duration','min_kills'}
            and pending['params']['map'] == 'prt_fild08'
            and type(pending['params']['duration']) is int
            and pending['params']['duration'] == life.hunt_seconds
            and type(pending['params']['min_kills']) is int and pending['params']['min_kills'] == 1
            and all(finite(value) for value in (now, pending['expires_at'], action['created'],
                action['deadline'], action['updated']))
            and action['created'] <= pending['expires_at']
            # Gateway time is measured just after the persisted intent (sub-second IPC skew).
            and abs(action['deadline']-pending['expires_at']) <= 1
            and action['deadline']+60 <= action['updated'] <= now
            and now < life.deadline)
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise RuntimeError('TERMINAL_DISPOSITION_REJECTED')
    entry = dict(action_id=action['action_id'], skill='hunt', state=action['state'],
        code=action['code'], at=now, reason='OFFLINE_PARK_UNCONFIRMED_HUNT',
        pending=copy.deepcopy(pending), action=copy.deepcopy(action))
    state['history'].append(entry)
    state['terminal_disposition'] = dict(action_id=action['action_id'], at=now)
    state.update(pending=None, blocked=None, phase='town', has_hunted=False)
    life.save()
    return 'PARKED_UNCONFIRMED_HUNT'


def park_unconfirmed_return(life, action, now):
    """One offline return parking; caller independently proves terminal/offline admission."""
    import json, math
    state = life.state
    pending = state.get('pending')
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    try:
        prior = state.get('terminal_disposition')
        valid = (isinstance(action, dict) and isinstance(pending, dict)
            and 'return_disposition' not in state
            and not any(key in state for key in ('reaction','stuck_recovery','death_recovery'))
            and (prior is None or (isinstance(prior, dict) and
                isinstance(prior['action_id'], str) and bool(prior['action_id']) and finite(prior['at'])))
            and state.get('blocked') == 'NATIVE_INTENT_EXPIRED' and state['phase'] == 'return'
            and type(state['cycles']) is int and state['cycles'] >= 0
            and isinstance(state['history'], list) and type(state['has_hunted']) is bool
            and action['state'] == 'escalated' and action['code'] == 'NO_EVIDENCE'
            and action['resident'] == 'resident_a' and action['skill'] == pending['skill'] == 'travel_to'
            and isinstance(pending['action_id'], str) and bool(pending['action_id'])
            and action['action_id'] == pending['action_id']
            and isinstance(pending['idem'], str) and bool(pending['idem'])
            and action['idem_key'] == pending['idem']
            and pending['reason'] == 'LIFE_RETURN' and pending['from_phase'] == 'return'
            and pending['next_phase'] == 'hunt'
            and type(pending['timeout']) is int and pending['timeout'] == 240
            and pending['params'] == json.loads(action['params'])
            and pending['params'] == dict(map='prt_fild08', x=170, y=240, r=3)
            and all(type(pending['params'][key]) is int for key in ('x','y','r'))
            and all(finite(value) for value in (now, pending['expires_at'], action['created'],
                action['deadline'], action['updated']))
            and action['created'] <= pending['expires_at']
            and abs(action['deadline']-pending['expires_at']) <= 1
            and action['deadline']+60 <= action['updated'] <= now < life.deadline)
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise RuntimeError('RETURN_DISPOSITION_REJECTED')
    state['history'].append(dict(action_id=action['action_id'], skill='travel_to',
        state=action['state'], code=action['code'], at=now,
        reason='OFFLINE_PARK_UNCONFIRMED_RETURN', pending=copy.deepcopy(pending),
        action=copy.deepcopy(action), discarded_partial_work=state['has_hunted']))
    state['return_disposition'] = dict(action_id=action['action_id'], at=now)
    state.update(pending=None, blocked=None, phase='town', has_hunted=False)
    life.save()
    return 'PARKED_UNCONFIRMED_RETURN'


def admit_corrected_native_hunt(life, action, evidence, now):
    """One exact incident after independently verified native-source remediation.

    Offline operator admission, NOT automatic recovery. Caller verifies QA journals,
    server kill IDs, source bytes, offline state and canonical locks before invoking.
    Previous consumption markers are retained; this cannot authorize another incident.
    """
    from types import SimpleNamespace
    import math
    try:
        evidence_valid = (isinstance(evidence, dict) and
            evidence['offline'] is True and evidence['checkpoint_preserved'] is True and
            evidence['old_plugin'] == 'b105e3fdd3b7d76ce73bb58b7ccf798f42fb8e69311706dc02edcbedce0c7fe4' and
            evidence['fixed_plugin'] == '1ec429f869e2292d678bc33600accaac98c5b95d81040d87f919745d18af6c96' and
            evidence['qa_status'] == 'PASS_TWO_AUTHORITATIVE_HUNTS' and
            evidence['hunt_ids'] == ['60dc65bf-62d5-45ab-993e-de828f228ca8',
                                     '2e058263-3516-4de4-b233-07d0a5a0f7ec'] and
            isinstance(evidence['kill_ids'], list) and len(evidence['kill_ids']) == 6 and
            all(type(i) is int and i > 0 for i in evidence['kill_ids']) and
            len(set(evidence['kill_ids'])) == 6 and
            all(isinstance(life.state[k], dict) and isinstance(life.state[k]['action_id'], str)
                and bool(life.state[k]['action_id']) and
                type(life.state[k]['at']) in (int,float) and math.isfinite(life.state[k]['at'])
                and 0 <= life.state[k]['at'] < now
                for k in ('terminal_disposition','return_disposition')))
    except (KeyError, TypeError, ValueError):
        evidence_valid = False
    if (not evidence_valid or not isinstance(action, dict) or
            life.state.get('native_remediation_admission') is not None or
            action.get('action_id') != '702b4620-95c4-4a4d-90ae-fbf7ef82082c' or
            action.get('idem_key') != 'e0d36ad2-c687-4ffd-aa70-47f9405faf2e'):
        raise RuntimeError('REMEDIATION_ADMISSION_REJECTED')
    # Reuse the existing strict non-economic intent validator on a private copy.
    # Neither an old marker nor the live state is erased or saved by this probe.
    probe = SimpleNamespace(state=copy.deepcopy(life.state), deadline=life.deadline,
                            hunt_seconds=life.hunt_seconds, save=lambda: None)
    probe.state.pop('terminal_disposition', None)
    park_unconfirmed_hunt(probe, action, now)
    entry = probe.state['history'][-1]
    entry.update(reason='OFFLINE_NATIVE_SOURCE_REMEDIATION', evidence=copy.deepcopy(evidence),
                 discarded_partial_work=life.state['has_hunted'])
    life.state['history'].append(entry)
    life.state['native_remediation_admission'] = dict(action_id=action['action_id'], at=now,
                                                    evidence=copy.deepcopy(evidence))
    life.state.update(pending=None, blocked=None, phase='town', has_hunted=False)
    life.save()
    return 'ADMITTED_CORRECTED_NATIVE_SOURCE'
