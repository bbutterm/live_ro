"""Pure deterministic candidate ranking. Priority/inertia and execution are external."""
import ast
import copy
import hashlib
import math
import random
import sys
from datetime import datetime, timezone
from .expressions import evaluate, identifiers


def _utc(now):
    if type(now) in (int, float) and math.isfinite(now): now = datetime.fromtimestamp(now, timezone.utc)
    if isinstance(now, str): now = datetime.fromisoformat(now.replace('Z', '+00:00'))
    if not isinstance(now, datetime) or now.tzinfo is None: raise ValueError('now must be UTC-aware')
    return now.astimezone(timezone.utc)


def rng_seed(resident_id, now, index, purpose):
    if purpose not in ('schedule','selection','speech') or type(index) is not int or index < 0:
        raise ValueError('invalid RNG input')
    value = f'{resident_id}|{_utc(now).date().isoformat()}|{index}|{purpose}'
    return int.from_bytes(hashlib.sha256(value.encode('utf-8')).digest()[:8], 'big')


def _number(value):
    return type(value) in (int,float) and math.isfinite(value)


def _ratio(snapshot, field):
    value, maximum = snapshot.get(field), snapshot.get(field+'_max')
    if not _number(value) or not _number(maximum) or maximum <= 0 or value < 0 or value > maximum:
        return None
    return 100*value/maximum


def _context(config, rid, snapshot, mental):
    resident = config['residents'][rid]
    context = dict(mental.get('derived', {}))
    for field in ('hp','hp_max','sp','sp_max','weight','weight_max','zeny','base_level','job_level','free_skill_points','online_minutes'):
        value = snapshot.get(field)
        context[field] = value if _number(value) else None
    for field in ('alive', 'sitting'):
        value = snapshot.get(field)
        context[field] = value if type(value) is bool else None
    context.update(resident['traits'])
    context.update(mental.get('drives', {}))
    context.update(mental.get('mood', {}))
    # Actual telemetry wins over derived guesses.
    context['hp_pct'] = _ratio(snapshot, 'hp')
    context['hp_need'] = None if context['hp_pct'] is None else 100-context['hp_pct']
    context['weight_pct'] = _ratio(snapshot, 'weight')
    inventory = snapshot.get('inventory')
    context['potions'] = inventory.get('501',0) if isinstance(inventory,dict) else None
    if not _number(context['potions']) or context['potions'] < 0: context['potions'] = None
    monsters = snapshot.get('near_monsters')
    context['unsafe_monster'] = None
    context['rest_safe'] = None
    if isinstance(monsters, list):
        context['unsafe_monster'] = int(any(not isinstance(m,dict) or str(m.get('id',m.get('monster_id'))) != '1002' for m in monsters))
        context['rest_safe'] = int(not monsters)
    players = snapshot.get('near_players')
    context['crowd'] = min(len(players),5)/5 if isinstance(players,list) else None
    place = config['places'][resident['home_place']]
    context['at_home'] = None
    if snapshot.get('map') is not None and _number(snapshot.get('x')) and _number(snapshot.get('y')):
        context['at_home'] = int(snapshot['map']==place['map'] and max(abs(snapshot['x']-place['x']),abs(snapshot['y']-place['y'])) <= 3)
    return context


def _args(config, rid, action, context, mental, snapshot, rng=None):
    resident = config['residents'][rid]
    args = copy.deepcopy(config['actions'][action]['default_args'])
    d = mental.get('derived', {})
    if action == 'walk_to':
        if context.get('sleeping') == 1 or context.get('break_due') == 1:
            args['place'] = resident['home_place']
        elif context.get('meet_due') == 1:
            args['place'] = d.get('meet_place', d.get('place'))
        elif context.get('habit_due') == 1:
            args['place'] = d.get('habit_place', d.get('place'))
        elif context.get('favorite_place') == 1:
            args['place'] = d.get('favorite_place_id', d.get('place'))
        elif context.get('novelty') == 1:
            args['place'] = d.get('novelty_place', d.get('place'))
        else:
            args['place'] = d.get('place',args['place'])
        target = config['places'].get(args['place'])
        context['place_valid'] = int(target is not None)
        if target and snapshot.get('map') is not None and _number(snapshot.get('x')) and _number(snapshot.get('y')):
            context['already_at_place'] = int(snapshot['map']==target['map'] and max(abs(snapshot['x']-target['x']),abs(snapshot['y']-target['y']))<=3)
        else: context['already_at_place'] = None
    elif action == 'hunt':
        args['map'] = 'prt_fild08'
        # Ranking uses valid defaults; only the sampled primary consumes args RNG.
        if context.get('potions') == 0:
            args['minutes'] = 5
        elif rng is not None:
            args['minutes'] = rng.uniform(10,20)
    elif action in ('follow','whisper'):
        target = d.get('player') if action == 'follow' else d.get('whisper_target')
        args['player' if action == 'follow' else 'to'] = target
        flag = 'player_valid' if action == 'follow' else 'whisper_target_valid'
        if not isinstance(target,str) or not 1<=len(target)<=24: context[flag] = 0
        relation = mental.get('relations',{}).get(target,{})
        for dim in ('trust','familiarity','liking'): context[dim] = relation.get(dim)
        context['bond'] = (context['trust']+context['liking'])/2 if all(_number(context[k]) for k in ('trust','liking')) else None
        if action == 'follow':
            familiarity = context['familiarity']
            args['minutes'] = 5 if not _number(familiarity) or familiarity < 10 else 5+math.floor(10*resident['traits']['patience']+.5)
    elif action == 'meet':
        args['resident'] = d.get('resident',d.get('meet_resident'))
        args['place'] = d.get('meet_place',d.get('place',args['place']))
        if args['resident'] not in config['residents'] or args['resident'] == rid or args['place'] not in config['places']:
            context['resident_valid'] = 0
        relation = mental.get('relations',{}).get(args['resident'], {})
        context['bond'] = (relation['trust']+relation['liking'])/2 if all(_number(relation.get(k)) for k in ('trust','liking')) else None
    elif action == 'idle':
        args['minutes'] = min(10,max(1,args['minutes']))
    elif action == 'rest':
        context['until_hp'] = args['until_hp']
    return args


def _terms(source, context):
    """Signed additive terms, preserving interactions; these sum to raw U."""
    root = ast.parse(source, mode='eval').body
    terms = {}
    def collect(node, sign=1):
        if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub)):
            collect(node.left,sign); collect(node.right,sign*(-1 if isinstance(node.op,ast.Sub) else 1))
        else:
            expression = ast.unparse(node)
            key = ('-' if sign<0 else '')+expression
            terms[key] = terms.get(key,0)+sign*evaluate(expression,context)
    collect(root)
    return terms


def rank_candidates(config, logical_resident_id, snapshot, mental, now):
    now = _utc(now)
    if logical_resident_id not in config['residents']: raise ValueError('unknown logical resident')
    index = mental.get('rng_index',0)
    rng_seed(logical_resident_id,now,index,'selection')  # validate the persisted index
    base = _context(config,logical_resident_id,snapshot,mental)
    unsafe_state = snapshot.get('alive') is not True
    try:
        age = (now-_utc(snapshot['server_time'])).total_seconds()
        if age>10 or age<0: unsafe_state = True
    except (KeyError,ValueError,TypeError): unsafe_state = True
    utilities, vetoes, feasible = [], [], []
    for action, spec in sorted(config['actions'].items()):
        context = dict(base)
        args = _args(config,logical_resident_id,action,context,mental,snapshot)
        reasons = []
        if unsafe_state and action != 'idle': reasons.append('unknown/dead/stale state')
        for source in spec['veto_if']:
            try:
                if evaluate(source,context,predicate=True): reasons.append(source)
            except ValueError as exc: reasons.append(f'{source}: {exc}')
        if action in ('say','whisper') and base.get('sleeping') == 1: reasons.append('sleeping')
        if action == 'meet' and base.get('sleeping') == 1: reasons.append('sleeping')
        if action == 'hunt' and snapshot.get('alive') is not True: reasons.append('alive unknown/false')
        repetitions = mental.get('repetitions')
        if 'last_actions' in mental or repetitions is None:
            reps = sum((previous.get('action') if isinstance(previous, dict) else previous) == action
                       for previous in mental.get('last_actions', [])[-3:])
        elif isinstance(repetitions, dict):
            reps = repetitions.get(action, 0)
        else:
            current = snapshot.get('current_action') or {}
            reps = repetitions if current.get('action') == action else 0
        if not _number(reps) or reps<0: raise ValueError('invalid repetitions')
        penalty = 0 if base.get('sleeping') == 1 else 12*min(reps,3)
        terms, trait, mood_terms, total = {}, None, {}, None
        try:
            raw = evaluate(spec['utility'],context)
            terms = _terms(spec['utility'],context)
            neutral = dict(context, **dict.fromkeys(config['traits'],.5))
            trait = raw-evaluate(spec['utility'],neutral)
            modifier = config['mood']['utility_modifiers'][action]
            mood = evaluate(modifier,context)
            for name in identifiers(modifier):
                mood_terms[name] = evaluate(modifier,{k:context[k] if k==name else 0 for k in config['mood']['states']})
            total = raw+mood-penalty
            if not math.isfinite(total):
                total = None
                raise ValueError('non-finite utility total')
        except ValueError as exc: reasons.append('utility: '+str(exc))
        if action == 'idle' and (unsafe_state or total is None):
            args={'minutes':1}; total=0.; terms={'safe_idle':0.}; trait=0.; mood_terms={}; penalty=0
            reasons=[]
        entry = {'action':action,'args':args,'drive_terms':terms,'trait_terms':trait,'mood_terms':mood_terms,'repeat_penalty':penalty,'total':total,'p':0.}
        utilities.append(entry)
        if reasons: vetoes.append({'action':action,'args':args,'reasons':reasons})
        else: feasible.append(entry)
    candidates = sorted(feasible,key=lambda entry:(-entry['total'],entry['action']))[:3]
    if candidates:
        weights=[math.exp((c['total']-candidates[0]['total'])/12) for c in candidates]
        denominator=sum(weights)
        for candidate,weight in zip(candidates,weights): candidate['p']=weight/denominator
    return {'candidates':copy.deepcopy(candidates),'vetoes':vetoes,'utilities':utilities}


def select(config, rid, snapshot, mental, now):
    result = rank_candidates(config,rid,snapshot,mental,now)
    index=mental.get('rng_index',0); seed=rng_seed(rid,now,index,'selection'); rng=random.Random(seed)
    candidates=result['candidates']
    primary=None
    if candidates:
        roll=rng.random(); cumulative=0
        primary=candidates[-1]
        for candidate in candidates:
            cumulative+=candidate['p']
            if roll<cumulative: primary=candidate; break
    if primary is not None and primary['action'] == 'hunt':
        context = _context(config,rid,snapshot,mental)
        args = _args(config,rid,'hunt',context,mental,snapshot,rng)
        primary['args'] = args
        # Keep the copied candidate and audit utility views byte-for-byte consistent.
        for entry in result['utilities']:
            if entry['action'] == 'hunt': entry['args'] = copy.deepcopy(args)
    override=len(candidates)>1 and rng.random()<config['selection']['llm_override_rate']
    llm_required=len(candidates)>1 and mental.get('derived',{}).get('sleeping') != 1
    result.update(primary=copy.deepcopy(primary),selected=copy.deepcopy(primary),override_allowed=override if llm_required else False,seed=seed,rng_index=index,next_rng_index=index+1,rng_purpose='selection',rng_runtime=f'{sys.version_info.major}.{sys.version_info.minor}',rng_spec_compatible=sys.version_info[:2]==(3,12),llm_required=llm_required,source='fallback')
    return result
