"""Strict v1 YAML loader and canonical configuration fingerprint."""
import hashlib
import json
import math
import re
import yaml
from .expressions import identifiers

TRAITS = set('greed ambition stamina laziness social courage giving curious vanity patience talk humor chronotype'.split())
DRIVES = set('tired money leveling boredom lonely fear loot boast duty drowsy'.split())
ACTIONS = set('say whisper walk_to hunt rest town_service follow meet idle'.split())
MOODS = set('calm happy irritated scared bored proud'.split())
EVENTS = set('login logout map_changed kill died bought sold pickup zeny_changed level_up arrived failure heard zeny_loss zeny_gain social_turn meet_done meet_missed goal_drop goal_done low_hp safe_hunt boast_sent rude_chat meet_agreed joint_hunt help_acknowledged'.split())
FIELDS = set('hp hp_max sp sp_max weight weight_max zeny base_level job_level free_skill_points alive sitting online_minutes hp_pct hp_need weight_pct potions money_target level_gap sleep_target at_home sleeping pre_sleep morning_awake night_awake chat_due help_request meet_due break_due rest_safe novelty habit_due favorite_place service_need service_possible place_valid already_at_place whisper_target_valid player_valid resident_valid meet_available speech_cooldown unsafe_monster hunt_streak recovery_lock promise_due bond crowd trust familiarity liking until_hp awake_minutes sleep_entry sleep_minutes near_players_count'.split())
NAMESPACE = TRAITS | DRIVES | MOODS | FIELDS

class _Loader(yaml.SafeLoader):
    pass

def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, (str, int)) or key in result:
            raise ValueError('duplicate/invalid YAML key: ' + str(key))
        result[key] = loader.construct_object(value_node, deep=deep)
    return result
_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)

def _keys(obj, allowed, required=None):
    allowed = set(allowed.split()) if isinstance(allowed, str) else set(allowed)
    required = allowed if required is None else set(required)
    if not isinstance(obj, dict) or set(obj)-allowed or required-set(obj):
        raise ValueError('unknown/missing keys: ' + repr(obj))

def _num(v, lo=-1e9, hi=1e9, integer=False):
    if type(v) not in (int, float) or (integer and type(v) is not int) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError('invalid numeric range: ' + repr(v))

def _range(v, lo, hi, integer=False):
    if not isinstance(v, list) or len(v) != 2: raise ValueError('expected range')
    for x in v: _num(x, lo, hi, integer)
    if v[0] > v[1]: raise ValueError('reversed range')

def _text(v, maximum=4096):
    if not isinstance(v, str) or not 1 <= len(v) <= maximum: raise ValueError('invalid text')

def _expr(v, predicate=False, namespace=NAMESPACE):
    if identifiers(v, predicate)-namespace: raise ValueError('unknown expression identifier')

def _numbers(obj, allowed, lo=-1e9, hi=1e9, complete=False):
    _keys(obj, allowed, allowed if complete else ())
    for v in obj.values(): _num(v, lo, hi)

def _speech(obj, defaults=False):
    allowed = 'max_chars typo_rate emoji_rate reply_delay_s skip_reply_in_combat_rate' if defaults else 'slang_rate typo_rate emoji_rate reply_delay_s skip_reply_in_combat_rate'
    _keys(obj, allowed)
    for key, value in obj.items():
        if key == 'reply_delay_s': _range(value, 0, 40)
        elif key == 'max_chars': _num(value, 1, 80, True)
        else: _num(value, 0, 1)

def validate_behavior(c):
    _keys(c, 'version tick_seconds signals_used traits drives mood goals actions selection relationships memory speech imperfections schedule places residents')
    if c['version'] != 1 or type(c['version']) is not int: raise ValueError('version must be 1')
    _num(c['tick_seconds'], 1, 60, True)
    signals = set('map coordinates hp hp_max sp sp_max weight weight_max zeny inventory base_level job_level free_skill_points alive sitting current_action action_result near_players near_monsters heard server_time online_minutes login logout map_changed kill died bought sold pickup zeny_changed level_up arrived failure agreements'.split())
    if not isinstance(c['signals_used'], list) or set(c['signals_used'])-signals: raise ValueError('unknown signal')
    _keys(c['traits'], TRAITS)
    for spec in c['traits'].values():
        _keys(spec, 'description range'); _text(spec['description']); _range(spec['range'], 0, 1)
        if spec['range'] != [0, 1]: raise ValueError('trait range must be 0..1')
    _keys(c['drives'], DRIVES)
    for spec in c['drives'].values():
        _keys(spec, 'description range initial per_minute on_event from_state trait_modifiers thresholds')
        _text(spec['description']); _range(spec['range'], 0, 100)
        if spec['range'] != [0, 100]: raise ValueError('drive range must be 0..100')
        _num(spec['initial'], 0, 100)
        _numbers(spec['per_minute'], ACTIONS | {'offline'})
        _numbers(spec['on_event'], EVENTS); _numbers(spec['trait_modifiers'], TRAITS)
        _numbers(spec['thresholds'], {'high','critical'}, 0, 100, True)
        if spec['thresholds']['high'] > spec['thresholds']['critical']: raise ValueError('threshold order')
        if spec['from_state'] != '': _expr(spec['from_state'])
    _keys(c['mood'], 'states on_event utility_modifiers'); _keys(c['mood']['states'], MOODS)
    for spec in c['mood']['states'].values():
        _keys(spec, 'decay_per_minute speech_hint from_state', {'decay_per_minute','speech_hint'})
        _num(spec['decay_per_minute'], 0, 100); _text(spec['speech_hint'])
        if 'from_state' in spec: _expr(spec['from_state'])
    _keys(c['mood']['on_event'], EVENTS, ())
    for spec in c['mood']['on_event'].values(): _numbers(spec, MOODS, -100, 100)
    _keys(c['mood']['utility_modifiers'], ACTIONS)
    for expr in c['mood']['utility_modifiers'].values(): _expr(expr, namespace=MOODS)
    _keys(c['goals'], 'types'); _keys(c['goals']['types'], 'zeny base_level job_level item_count')
    # Inventory indexing is a fixed goal selector, never part of the numeric AST language.
    for name, spec in c['goals']['types'].items():
        _keys(spec, 'measure done_when')
        measure = 'inventory[item_id]' if name == 'item_count' else name
        if spec['measure'] != measure or spec['done_when'] != measure+'>=target': raise ValueError('invalid goal selector')
    _keys(c['places'], c['places'], ())
    if not c['places']: raise ValueError('no places')
    for name, spec in c['places'].items():
        _text(name, 64); _keys(spec, 'map x y kind'); _text(spec['map'], 32)
        _num(spec['x'], 0, 65535, True); _num(spec['y'], 0, 65535, True)
        if spec['kind'] not in ('home','shop','square','field'): raise ValueError('place kind')
    _keys(c['actions'], ACTIONS)
    argkeys = {'say':'text','whisper':'to text','walk_to':'place','hunt':'map minutes','rest':'until_hp','town_service':'','follow':'player minutes','meet':'resident place','idle':'minutes'}
    for name, spec in c['actions'].items():
        _keys(spec, 'utility veto_if min_minutes default_args'); _expr(spec['utility'])
        if not isinstance(spec['veto_if'], list): raise ValueError('veto list')
        for expr in spec['veto_if']: _expr(expr, True)
        _num(spec['min_minutes'], 0, 30); args = spec['default_args']; _keys(args, argkeys[name])
        if 'place' in args and args['place'] not in c['places']: raise ValueError('unknown place')
        if 'resident' in args and args['resident'] not in c['residents']: raise ValueError('unknown resident')
        if 'text' in args: _text(args['text'], 80)
        if name == 'hunt' and args['map'] != 'prt_fild08': raise ValueError('hunt map')
        if 'minutes' in args: _num(args['minutes'], 5 if name == 'hunt' else 1, 30 if name == 'hunt' else 15 if name == 'follow' else 10)
        if 'until_hp' in args: _num(args['until_hp'], 1, 100)
        for target in ('to','player'):
            if target in args and (not isinstance(args[target], str) or len(args[target]) > 24): raise ValueError('target name')
    s = c['selection']; _keys(s, 'top_k softmax_temperature same_action_penalty llm_override_rate llm_triggers max_llm_decisions_per_hour')
    for key, value in s.items():
        if key == 'llm_triggers':
            if not isinstance(value, list) or set(value)-set('action_done failure action_timeout actionable_chat ten_minutes'.split()): raise ValueError('trigger')
        else: _num(value, 0, 100)
    for key, exact in {'top_k':3,'softmax_temperature':12,'same_action_penalty':12,'llm_override_rate':.1,'max_llm_decisions_per_hour':6}.items():
        if s[key] != exact: raise ValueError('v1 selection constant: '+key)
    r = c['relationships']; _keys(r, 'dims initial on_event decay_per_day')
    if r['dims'] != ['familiarity','liking','trust']: raise ValueError('relationship dims')
    for field in ('initial','decay_per_day'): _numbers(r[field], r['dims'], complete=True)
    for dim, value in r['initial'].items(): _num(value, -100 if dim == 'liking' else 0, 100)
    _keys(r['on_event'], EVENTS, ())
    for spec in r['on_event'].values(): _numbers(spec, r['dims'], -100, 100)
    m = c['memory']; _keys(m, 'keep_events daily_summary_chars prompt_chars')
    if not isinstance(m['keep_events'], list) or set(m['keep_events'])-EVENTS: raise ValueError('memory event')
    _num(m['daily_summary_chars'], 1, 480, True); _num(m['prompt_chars'], 1, 600, True)
    s = c['speech']; _keys(s, 'defaults sender_cooldown_s spontaneous_cooldown_minutes resident_pair_lines_per_hour resident_reply_hops'); _speech(s['defaults'], True)
    for key in set(s)-{'defaults'}: _num(s[key], 0, 3600, True)
    i = c['imperfections']; _keys(i, 'break_after_awake_minutes break_minutes reply_delay_fatigue_scale reply_delay_combat_scale missed_reply_expiry_s')
    _range(i['break_after_awake_minutes'], 1, 1440); _range(i['break_minutes'], 1, 10)
    for key in set(i)-{'break_after_awake_minutes','break_minutes'}: _num(i[key], 0, 100)
    s = c['schedule']; _keys(s, 'daily_sleep_shift_minutes daily_wake_shift_minutes night_start_rate chronotypes')
    for key in ('daily_sleep_shift_minutes','daily_wake_shift_minutes'): _range(s[key], -120, 120, True)
    _num(s['night_start_rate'], 0, 1); _keys(s['chronotypes'], 'owl lark')
    for spec in s['chronotypes'].values():
        _keys(spec, 'sleep_start wake sleep_idle_minutes')
        for key in ('sleep_start','wake'):
            if not isinstance(spec[key], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', spec[key]): raise ValueError('clock time')
        _range(spec['sleep_idle_minutes'], 1, 10, True)
        start, wake = (sum(int(v)*mult for v,mult in zip(spec[key].split(':'), (60,1))) for key in ('sleep_start','wake'))
        if (wake-start)%1440 < 420: raise ValueError('sleep shorter than seven hours')
    if not isinstance(c['residents'], dict) or not c['residents']: raise ValueError('no residents')
    for rid, resident in c['residents'].items():
        _text(rid, 64); _keys(resident, 'display_name class traits initial_drives goals chronotype sleep_offset_minutes home_place favorite_places habits speech persona_prompt')
        _text(resident['display_name'], 24); _text(resident['class'], 32); _text(resident['persona_prompt'])
        _numbers(resident['traits'], TRAITS, 0, 1, True); _numbers(resident['initial_drives'], DRIVES, 0, 100, True)
        if resident['chronotype'] not in s['chronotypes']: raise ValueError('chronotype')
        _num(resident['sleep_offset_minutes'], -120, 120, True)
        if resident['home_place'] not in c['places'] or c['places'][resident['home_place']]['kind'] != 'home': raise ValueError('home place')
        if not isinstance(resident['favorite_places'], list) or set(resident['favorite_places'])-set(c['places']): raise ValueError('favorite place')
        for field in ('goals', 'habits'):
            if type(resident[field]) is not list: raise ValueError(field+' must be a list')
        for habit in resident['habits']:
            _keys(habit, 'id probability place cooldown_minutes'); _text(habit['id'], 64); _num(habit['probability'], 0, 1); _num(habit['cooldown_minutes'], 0, 10080)
            if habit['place'] not in c['places']: raise ValueError('habit place')
        goal_ids = set()
        for goal in resident['goals']:
            _keys(goal, 'id type target item_id', {'id','type','target'})
            _text(goal['id'], 64)
            if goal['id'] in goal_ids: raise ValueError('duplicate goal id')
            goal_ids.add(goal['id'])
            if goal['type'] not in c['goals']['types']: raise ValueError('goal type')
            _num(goal['target'], 1, {'zeny':50000,'base_level':60,'job_level':30,'item_count':50}[goal['type']], True)
            if goal['type'] == 'item_count': _num(goal.get('item_id'), 1, 2**31-1, True)
            elif 'item_id' in goal: raise ValueError('unexpected item_id')
        _speech(resident['speech'])
    return c

def load_behavior(path):
    try:
        with open(path, encoding='utf-8') as stream: config = yaml.load(stream, Loader=_Loader)
        return validate_behavior(config)
    except (yaml.YAMLError, TypeError, KeyError, RecursionError) as exc:
        raise ValueError('invalid behavior configuration') from exc

def config_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
