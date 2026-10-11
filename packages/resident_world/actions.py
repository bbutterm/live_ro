"""Discoverable bounded action contracts; the gateway remains completion authority."""
import copy,re

FIELDS={
    'text':{'type':'string','minLength':1,'maxLength':80},
    'to':{'type':'string','pattern':r'^[\w -]{1,24}$'},
    'player':{'type':'string','pattern':r'^[\w -]{1,24}$'},
    'resident':{'type':'string'},'place':{'type':'string'},
    'map':{'type':'string','pattern':r'^[a-zA-Z0-9_]{1,24}$'},
    'minutes':{'type':'integer','minimum':1,'maximum':30},
    'until_hp':{'type':'integer','minimum':80,'maximum':100},
}
FIELDS.update(stock_goal={'type':'integer','minimum':1,'maximum':20},max_zeny={'type':'integer','minimum':1,'maximum':300})
FIELDS.update(x={'type':'integer','minimum':0,'maximum':1023},y={'type':'integer','minimum':0,'maximum':1023},r={'type':'integer','minimum':1,'maximum':5},hp_pct={'type':'integer','minimum':80,'maximum':100},duration={'type':'integer','minimum':1,'maximum':900},min_kills={'type':'integer','minimum':1,'maximum':10},solo={'type':'boolean','enum':[True]},item_id={'type':'integer','enum':[501]},category={'type':'string','enum':['loot','equipment']},to_name={'type':'string','pattern':r'^[\w -]{1,24}$'})
NATIVE_KEYS={'travel_to':{'map','x','y','r'},'rest':{'hp_pct'},'say':{'text'},'whisper':{'to_name','text'},'hunt':{'map','duration','min_kills','solo'},'respawn':set(),'buy_potions':{'item_id','stock_goal','max_zeny'},'sell_loot':{'category'}}
FIELDS['skill']={'type':'string','enum':sorted(NATIVE_KEYS)}
EXTRA={'hunt_party':{'player','map','minutes'},'find_party':{'minutes'},'party_invite':{'player'},'native_control':{'skill','params'},'respawn':set(),'travel_to':{'map','x','y','r'},'sell_loot':set(),'sell_equipment':set(),'buy_potions':{'stock_goal','max_zeny'},'hunt_solo':{'map','minutes'},'support':{'player','map','minutes'},'heal':{'player'}}
DESCRIPTIONS={
    'hunt_party':'Охота с подтверждённым native party партнёром; настроенный healer поддерживает, frontline убивает.',
    'find_party':'Ранжировать nearby кандидатов с operator opt-in; одно сообщение; ждать реальной native party.',
    'party_invite':'Послать native party invitation (недоступно в этом gateway).',
    'native_control':'Свободный вызов allowlisted native skill с params; без консоли/SQL и без повтора.',
    'travel_to':'Пройти к координатам на разрешённой карте, radius r; сервер подтверждает позицию.',
    'respawn':'Нативное возрождение погибшего персонажа (не V1).',
    'sell_loot':'Дойти к торговцу и продать только безопасную добычу, сохранив экипировку.',
    'sell_equipment':'Продать только ненадетое разрешённое оружие/броню; защищённые предметы сохраняются.',
    'buy_potions':'Купить красные зелья до stock_goal по реальной цене, не превысив max_zeny.',
    'say':'Сказать в локальный чат; подтверждение server chatlog.',
    'whisper':'Шепнуть знакомому игроку; подтверждение server chatlog.',
    'walk_to':'Дойти до настроенной точки; native route + server position.',
    'hunt':'Совместимая охота, включая настроенный support_target.',
    'hunt_solo':'Дойти на поле и охотиться самому, отключив поддержку профиля.',
    'support':'Поддержка настроенного партнёра через native follow/partySkill; done означает серверный прогресс, НЕ доказанное лечение.',
    'heal':'Один подтверждённый каст лечения персонажа.',
    'rest':'Сесть и восстановить HP до порога, с серверным свидетелем.',
    'town_service':'Дойти до торговца, продать разрешённое и пополнить зелья; без повторов экономики.',
    'follow':'Ограниченное следование; нужны независимые позиции обеих сторон.',
    'meet':'Согласовать встречу с жителем и подтвердить позиции обеих сторон.',
    'idle':'Безопасно стоять с непрерывным свежим свидетелем.',
}
UNSUPPORTED={'party_invite':'NO_NATIVE_PARTY_INVITE: gateway/plugin не предоставляют invitation/accept API','heal':'NO_NATIVE_CAST_RESULT: нет callable кастинга и свидетеля результата лечения'}

def schema(action,keys):
    properties={k:copy.deepcopy(FIELDS[k]) for k in sorted(keys) if k!='params'}
    if 'params' in keys:properties['params']={'anyOf':[schema(n,k) for n,k in NATIVE_KEYS.items()]}
    if action in ('hunt','hunt_solo','support','hunt_party'):properties['map']['enum']=['prt_fild08']
    if action in ('hunt','hunt_solo','support','hunt_party') and 'minutes' in properties:properties['minutes']['minimum']=5
    if action=='find_party':properties['minutes']['maximum']=3
    if action=='follow':properties['minutes']['maximum']=15
    if action=='idle':properties['minutes']['maximum']=10
    return {'type':'object','additionalProperties':False,'required':sorted(keys),'properties':properties}

def validate_extra(action,args,c):
    check(schema(action,EXTRA[action]),args)
    if action in ('hunt_solo','hunt_party','support') and args['minutes']>c.get('llm',{}).get('max_hunt_minutes',30):raise ValueError('GOAL_DURATION_CAP')
    if action=='native_control' and args.get('skill')=='hunt' and args.get('params',{}).get('duration',0)>c.get('llm',{}).get('max_hunt_minutes',30)*60:raise ValueError('GOAL_DURATION_CAP')
    if action=='native_control':
        check(schema(args['skill'],NATIVE_KEYS[args['skill']]),args['params'])
        if args['skill']=='hunt' and args['params']['map']!='prt_fild08':raise ValueError('UNKNOWN_HUNT_MAP')
        if args['skill'] in ('say','whisper'):
            from .skills import speech
            speech(args['params']['text'])
        if args['skill']=='travel_to':validate_extra('travel_to',args['params'],c)
    if action=='travel_to' and args['map'] not in {p['map'] for p in c.get('places',{}).values()}|{'prt_in'}:raise ValueError('UNKNOWN_MAP')
    return copy.deepcopy(args)

def check(spec,value):
    if 'anyOf' in spec:
        for option in spec['anyOf']:
            try:check(option,value);return
            except ValueError:pass
        raise ValueError('INVALID_ACTION_ARGS')
    kind=spec['type']
    if kind=='object':
        if not isinstance(value,dict) or set(value)!=set(spec['required']):raise ValueError('INVALID_ACTION_ARGS')
        for key,child in spec['properties'].items():check(child,value[key])
    elif kind=='boolean':
        if type(value) is not bool:raise ValueError('INVALID_ARGUMENT')
    elif kind=='integer':
        if type(value) is not int or not spec.get('minimum',value)<=value<=spec.get('maximum',value):raise ValueError('ARGUMENT_RANGE')
    elif kind=='string':
        if not isinstance(value,str) or not spec.get('minLength',0)<=len(value)<=spec.get('maxLength',1000):raise ValueError('INVALID_ARGUMENT')
        if 'pattern' in spec and not re.fullmatch(spec['pattern'],value):raise ValueError('INVALID_NAME')
    if 'enum' in spec and value not in spec['enum']:raise ValueError('INVALID_ARGUMENT')

def unavailable(action,args,c,s):
    if action in UNSUPPORTED:return UNSUPPORTED[action]
    t=s.get('telemetry') or {}
    if not t:return 'NO_TELEMETRY'
    age=s.get('age')
    if type(age) not in (int,float) or not 0<=age<=4:return 'STALE_BODY'
    if action=='native_control':
        if not args:return 'DEAD' if t.get('dead') else None
        skill=args.get('skill','');params=args.get('params',{})
        if skill=='sell_loot':skill='sell_equipment' if params.get('category')=='equipment' else 'sell_loot'
        return unavailable(skill,params,c,s)
    if action=='respawn':return 'RESPAWN_DISABLED_V1' if t.get('behavior_v1') else None if t.get('dead') else 'NOT_DEAD'
    if t.get('dead'):return 'DEAD'
    if action=='rest':
        from .supervisor import regeneration_blocked
        if regeneration_blocked(t):return 'OVERWEIGHT'
    if action in ('hunt','hunt_solo','hunt_party','support'):
        from .mind import hunt_blocked
        blocked=hunt_blocked(t)
        if blocked:return blocked
    if action in ('sell_loot','sell_equipment'):
        if t.get('behavior_v1'):return 'CATEGORY_SALE_DISABLED_V1'
        category='loot' if action=='sell_loot' else 'equipment'
        categories=t.get('sellable_by_category')
        if not isinstance(categories,dict):return 'NO_CATEGORY_TELEMETRY'
        if not any(type(n) is int and n>0 for n in categories.get(category,{}).values()):return 'NO_SELLABLE_ITEMS'
    if action=='buy_potions':
        if t.get('behavior_v1') and args.get('stock_goal',1)>8:return 'STOCK_LIMIT_V1'
        if args.get('stock_goal',20)<=t.get('inventory',{}).get('501',0):return 'STOCK_ALREADY_MET'
        if t.get('zeny',0)<=0:return 'INSUFFICIENT_EARNED_ZENY'
    if action=='find_party' and not party_candidates(c,s):return 'NO_CONSENTING_CANDIDATES'
    if action=='hunt_party':
        own=next((r for r in c.get('residents',[]) if r.get('resident_id')==s.get('_resident_id') or r.get('name')==s.get('_me')), {})
        target=args.get('player') or own.get('support_target')
        if own.get('support_target') and target!=own['support_target']:return 'SUPPORT_PROFILE_REQUIRED'
        if not party_member(t,target):return 'INVALID_PARTY'
        if own.get('support_target'):return unavailable('support',{**args,'player':target},c,s)
    if action=='support':
        if t.get('behavior_v1'):return 'SUPPORT_DISABLED_V1'
        own=next((r for r in c.get('residents',[]) if r.get('resident_id')==s.get('_resident_id') or r.get('name')==s.get('_me')), {})
        target=args.get('player') or own.get('support_target')
        if not target or target!=own.get('support_target'):return 'SUPPORT_PROFILE_REQUIRED'
        party=t.get('party') or {}
        if party.get('source')!='native_client' or party.get('share')!=1 or not any(u.get('name')==target and u.get('online') is True for u in party.get('members',[])):return 'INVALID_SUPPORT_PARTY'
        if type(t.get('job_level')) is not int:return 'NO_JOB_LEVEL'
        if t.get('learned_skills',{}).get('AL_HEAL',0)<1:return 'NO_LEARNED_HEAL'
    return None

def party_member(t,target=None):
    party=t.get('party') or {}
    return party.get('source')=='native_client' and party.get('share')==1 and any(u.get('online') is True and (not target or u.get('name')==target) for u in party.get('members',[]))

def party_candidates(c,s):
    own=next((r for r in c.get('residents',[]) if r.get('resident_id')==s.get('_resident_id') or r.get('name')==s.get('_me')),{})
    consent=own.get('party_search_consent',[])
    if not isinstance(consent,list):return []
    t=s.get('telemetry') or {};mine=t.get('pos') or {};out=[]
    for p in t.get('nearby_players',[])[:32]:
        name=p.get('name');pos=p.get('pos') or {}
        if name not in consent or not isinstance(name,str) or not re.fullmatch(r'[\w -]{1,24}',name):continue
        if any(type(q) is not int for q in (pos.get('x'),pos.get('y'),mine.get('x'),mine.get('y'))):continue
        out.append({'name':name,'distance':max(abs(pos['x']-mine['x']),abs(pos['y']-mine['y'])),'consent_source':'operator_config'})
    return sorted(out,key=lambda p:(p['distance'],p['name']))[:8]

def discover(c,s=None):
    from .skills import MENU
    from .mind import availability
    s=s or {};legacy=availability(c,s)
    entries={}
    for name,keys in MENU.items():
        reason=unavailable(name,{},c,s) if name in EXTRA else ('NO_TELEMETRY' if not s.get('telemetry') else None)
        if name not in EXTRA and str(legacy.get(name,'')).startswith('нельзя'):reason=legacy[name]
        entries[name]={'description':DESCRIPTIONS[name],'parameters':schema(name,keys),'available':reason is None,'reason':reason,'confirmation':'gateway witness; ACK is not completion','automatic_retry':False if name in EXTRA or name in ('town_service','meet') else True}
    return {'version':1,'actions':entries}
