"""Strict version-1 wire validation. Unknown authoritative facts remain None."""
from copy import deepcopy
from datetime import datetime, timezone
import math
import re


class ContractError(ValueError):
    def __init__(self, code='invalid_payload', field=None):
        self.code, self.field = code, field
        super().__init__(code + (':' + field if field else ''))


ENVELOPE = {'version', 'resident_id', 'seq', 'server_time'}
TELEMETRY = set('map x y hp hp_max sp sp_max weight weight_max zeny inventory base_level job_level free_skill_points alive sitting online_minutes current_action near_players near_monsters agreements'.split())
ACTIONS = {'say', 'whisper', 'walk_to', 'hunt', 'rest', 'town_service', 'follow', 'meet', 'idle'}
PLACES = {'home_a', 'home_mira', 'home_borin', 'kafra', 'square', 'south_gate', 'poring_edge', 'east_view'}
ERROR_CODES = {'stale_state', 'dead', 'unsafe', 'no_path', 'no_funds', 'target_lost', 'conflict', 'interrupted', 'transport_timeout'}
RESULTS = {None, 'done', 'failed', 'timeout'}


def _object(p, keys):
    if type(p) is not dict or set(p) != set(keys):
        raise ContractError('invalid_keys')


def _text(v, limit=64):
    if type(v) is not str or not 1 <= len(v) <= limit or any(ord(ch) < 32 for ch in v):
        raise ContractError('invalid_string')
    try:
        v.encode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise ContractError('invalid_string') from exc


def _int(v, low=0, high=None):
    if type(v) is not int or v < low or (high is not None and v > high):
        raise ContractError('invalid_integer')


def utc_time(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)', value):
        raise ContractError('invalid_time')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ContractError('invalid_time') from exc


def _envelope(p, keys):
    _object(p, ENVELOPE | set(keys))
    if type(p['version']) is not int or p['version'] != 1:
        raise ContractError('unsupported_version')
    _text(p['resident_id']); _int(p['seq']); utc_time(p['server_time'])


def _nullable(v, fn):
    if v is not None: fn(v)


def validate_telemetry(payload):
    _envelope(payload, TELEMETRY)
    for key in TELEMETRY - {'map', 'inventory', 'alive', 'sitting', 'current_action', 'near_players', 'near_monsters', 'agreements'}:
        _nullable(payload[key], lambda v, k=key: _int(v, 1 if k.endswith('_max') else 0))
    _nullable(payload['map'], _text)
    for key in ('alive', 'sitting'):
        if payload[key] is not None and type(payload[key]) is not bool:
            raise ContractError('invalid_boolean', key)
    for value, maximum in (('hp', 'hp_max'), ('sp', 'sp_max'), ('weight', 'weight_max')):
        if payload[value] is not None and payload[maximum] is not None and payload[value] > payload[maximum]:
            raise ContractError('out_of_range', value)
    inv = payload['inventory']
    if inv is not None:
        if type(inv) is not dict: raise ContractError('invalid_inventory')
        for key, count in inv.items():
            _text(key); _int(count)
    for key in ('near_players', 'near_monsters'):
        near = payload[key]
        if near is not None:
            if type(near) is not list: raise ContractError('invalid_near')
            for entity in near:
                if type(entity) is not dict: raise ContractError('invalid_near')
                identifier = 'name' if 'name' in entity else 'id'
                _object(entity, {identifier, 'x', 'y'})
                _nullable(entity[identifier], lambda v: _text(v, 24 if identifier == 'name' else 64))
                _nullable(entity['x'], _int); _nullable(entity['y'], _int)
    current = payload['current_action']
    if current is not None:
        _object(current, {'action_id', 'action', 'args', 'started_at', 'result', 'error_code'})
        _text(current['action_id']); validate_action(current['action'], current['args'], payload['resident_id'])
        utc_time(current['started_at']); _result(current['result'], current['error_code'])
    agreements = payload['agreements']
    if agreements is not None:
        if type(agreements) is not list: raise ContractError('invalid_agreements')
        for agreement in agreements:
            _object(agreement, {'meeting_id', 'participants', 'place', 'accepted_at', 'deadline', 'status'})
            _text(agreement['meeting_id']); _place(agreement['place'])
            people = agreement['participants']
            if type(people) is not list or len(people) != 2: raise ContractError('invalid_participants')
            for person in people: _text(person)
            if people[0] == people[1]: raise ContractError('invalid_participants')
            start = utc_time(agreement['accepted_at']); end = utc_time(agreement['deadline'])
            if (end-start).total_seconds() != 600: raise ContractError('invalid_deadline')
            if agreement['status'] not in ('accepted', 'fulfilled', 'missed'): raise ContractError('invalid_status')
    return deepcopy(payload)


def _place(value):
    _text(value)
    if value not in PLACES: raise ContractError('unsafe_place')


def _result(result, error):
    if type(result) not in (str, type(None)) or result not in RESULTS: raise ContractError('invalid_result')
    if error is not None:
        _text(error)
        if error not in ERROR_CODES: raise ContractError('invalid_error_code')
    if result == 'done' and error is not None: raise ContractError('invalid_result')


def validate_action(action, args, resident_id=None):
    _text(action)
    if action not in ACTIONS: raise ContractError('invalid_action')
    keys = {'say': {'text'}, 'whisper': {'to', 'text'}, 'walk_to': {'place'}, 'hunt': {'map', 'minutes'}, 'rest': {'until_hp'}, 'town_service': set(), 'follow': {'player', 'minutes'}, 'meet': {'resident', 'place'}, 'idle': {'minutes'}}[action]
    _object(args, keys)
    if action in ('say', 'whisper'): _text(args['text'], 80)
    if action == 'whisper': _text(args['to'], 24)
    if action in ('walk_to', 'meet'): _place(args['place'])
    if action == 'hunt':
        if args['map'] != 'prt_fild08': raise ContractError('unsafe_map')
        minutes = args['minutes']
        if type(minutes) not in (int, float) or not 5 <= minutes <= 30 or not math.isfinite(minutes):
            raise ContractError('invalid_duration', 'minutes')
    if action == 'follow': _text(args['player'], 24); _int(args['minutes'], 1, 15)
    if action == 'idle': _int(args['minutes'], 1, 10)
    if action == 'rest': _int(args['until_hp'], 1, 100)
    if action == 'meet':
        _text(args['resident'])
        if args['resident'] == resident_id: raise ContractError('conflict')
    return deepcopy(args)


EVENT_FIELDS = {
    'heard': {'from', 'channel', 'text'}, 'kill': {'monster_id'},
    'pickup': {'item_id', 'count'}, 'bought': {'item_id', 'count', 'amount'},
    'sold': {'item_id', 'count', 'amount'}, 'zeny_changed': {'before', 'after'},
    'map_changed': {'map', 'x', 'y'}, 'arrived': {'place', 'map', 'x', 'y'},
    'level_up': {'base_before', 'base_after', 'job_before', 'job_after'},
    'failure': {'action_id', 'error_code'}, 'login': set(), 'logout': set(), 'died': set(),
}


def validate_event(payload):
    _envelope(payload, {'event_id', 'type', 'data'})
    _text(payload['event_id']); _text(payload['type'])
    kind, data = payload['type'], payload['data']
    if kind not in EVENT_FIELDS: raise ContractError('invalid_event')
    _object(data, EVENT_FIELDS[kind])
    for key, value in data.items():
        if key in ('x', 'y', 'count', 'amount', 'before', 'after', 'base_before', 'base_after', 'job_before', 'job_after', 'monster_id', 'item_id'):
            _nullable(value, _int)
        elif key == 'from': _nullable(value, lambda v: _text(v, 24))
        elif key == 'text': _nullable(value, lambda v: _text(v, 4096))
        elif key == 'place': _nullable(value, _place)
        elif key == 'error_code':
            _text(value)
            if value not in ERROR_CODES: raise ContractError('invalid_error_code')
        else: _nullable(value, _text)
    return deepcopy(payload)


def validate_execute(payload):
    _envelope(payload, {'decision_id', 'action_id', 'state_seq', 'expires_at', 'action', 'args', 'say'})
    _text(payload['decision_id']); _text(payload['action_id']); _int(payload['state_seq'])
    if (utc_time(payload['expires_at']) - utc_time(payload['server_time'])).total_seconds() != 30:
        raise ContractError('invalid_expiry')
    validate_action(payload['action'], payload['args'], payload['resident_id'])
    _nullable(payload['say'], lambda v: _text(v, 80))
    return deepcopy(payload)


def validate_meeting(payload):
    _envelope(payload, {'meeting_id', 'initiator', 'resident', 'place', 'state_seq'})
    for key in ('meeting_id', 'initiator', 'resident'): _text(payload[key])
    _place(payload['place']); _int(payload['state_seq'])
    if payload['initiator'] != payload['resident_id'] or payload['initiator'] == payload['resident']:
        raise ContractError('conflict')
    return deepcopy(payload)


def validate_ack(payload):
    _object(payload, {'action_id', 'accepted', 'error_code'}); _text(payload['action_id'])
    if type(payload['accepted']) is not bool: raise ContractError('invalid_boolean')
    _result(None, payload['error_code'])
    if payload['accepted'] == (payload['error_code'] is not None): raise ContractError('invalid_ack')
    return deepcopy(payload)


def validate_result(payload):
    _object(payload, {'action_id', 'result', 'error_code', 'source_seq', 'completed_at'})
    _text(payload['action_id']); _result(payload['result'], payload['error_code'])
    _nullable(payload['source_seq'], _int); _nullable(payload['completed_at'], utc_time)
    if payload['result'] is None and payload['completed_at'] is not None: raise ContractError('invalid_result')
    if payload['result'] is not None and (payload['source_seq'] is None or payload['completed_at'] is None):
        raise ContractError('invalid_result')
    return deepcopy(payload)
