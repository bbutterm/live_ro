"""Versioned menu skills. Native ACK is never completion; economics never blind-retry."""
import contextlib,fcntl,json,os,pathlib,re,sqlite3,time,uuid
from .supervisor import STATE,config,atomic
from packages.body_gateway.cli import request
from packages.body_gateway.journal import TERMINAL
from packages.resident_demo.life_cycle import LOOT
MENU={'say':{'text'},'whisper':{'to','text'},'walk_to':{'place'},'hunt':{'map','minutes'},'rest':{'until_hp'},'town_service':set(),'follow':{'player','minutes'},'meet':{'resident','place'},'idle':{'minutes'}}
from .actions import EXTRA,discover,validate_extra,unavailable
MENU.update(EXTRA)
REVERSIBLE={'walk_to','hunt','rest','follow','say','whisper','idle','meet'}

def has_sellable(t):
    # The plugin reports junk it can sell by item type; older bodies only the fixed list.
    sellable=t.get('sellable')
    if isinstance(sellable,dict):return any(type(n) is int and n>0 for n in sellable.values())
    return any(t.get('inventory',{}).get(k,0)>0 for k in LOOT)

def place_phrase(c,place):
    return c.get('place_labels',{}).get(place) or {'fountain':'у фонтана','field':'на поле'}.get(place) or 'у места '+str(place)

def number(value,low,high):
    if type(value) is not int or not low<=value<=high:raise ValueError('ARGUMENT_RANGE')
    return value

# A spoken promise to move must come with the movement (honesty rule); whole words only.
PROMISE=re.compile(r'\b(?:иду|ид[её]м(?:те)?|пойду|пойд[её]м(?:те)?|встретимся|следую|подойду|приду|прибегу|прибуду|бегу|догоню|выхожу|направляюсь|скоро буду|уже в пути|буду (?:у|на|возле|через|рядом))\b',re.I)

def speech(text):
    # ';;' and '\\' are OpenKore console/message separators: never in speech.
    if not isinstance(text,str) or not 0<len(text)<=80 or re.search(r'[\x00-\x1f\x7f]|;;|\\',text) or text.lstrip().startswith(('@','#','/')) or not re.search('[А-Яа-яЁё]',text):raise ValueError('INVALID_TEXT')
    if PROMISE.search(text):raise ValueError('PROMISE_REQUIRES_ACTION')
    return text

def validate(action,args,c):
    if action in EXTRA:return validate_extra(action,args,c)
    if action not in MENU or not isinstance(args,dict) or set(args)!=MENU[action]:raise ValueError('INVALID_ACTION_ARGS')
    a=dict(args)
    if action in ('say','whisper'):a['text']=speech(a['text'])
    if action in ('follow','whisper'):
        field='player' if action=='follow' else 'to'
        if not isinstance(a[field],str) or not re.fullmatch(r'[\w -]{1,24}',a[field]):raise ValueError('INVALID_NAME')
    if action in ('walk_to','meet') and (not isinstance(a['place'],str) or a['place'] not in c['places']):raise ValueError('UNKNOWN_PLACE')
    if action=='meet' and a['resident'] not in {r['name'] for r in c['residents']}:raise ValueError('UNKNOWN_RESIDENT')
    if action in ('hunt','hunt_solo'):
        if a['map']!='prt_fild08':raise ValueError('UNKNOWN_HUNT_MAP')
        number(a['minutes'],5,30)
    if action=='rest':number(a['until_hp'],80,100)
    if action=='idle':number(a['minutes'],1,10)
    if action=='follow':number(a['minutes'],1,15)
    return a

def outcome(a):
    state=a['state'];code=a.get('code','UNKNOWN')
    if state=='confirmed':return 'done'
    if state not in TERMINAL:return None
    if state in ('timed_out','escalated') or code in ('TIMEOUT','VERIFY_TIMEOUT','NO_EVIDENCE'):return 'timeout'
    if state in TERMINAL:return 'failed('+code+')'
    return None

def witness_at(status,point,now,radius=5):
    proj=status.get('projection',{});p=proj.get('server_pos',{})
    pos=p.get('value',{});age=status.get('age')
    return age is not None and age<4 and now-p.get('ts',0)<15 and proj.get('server_map',{}).get('value')==point['map'] and max(abs(pos.get('x',-999)-point['x']),abs(pos.get('y',-999)-point['y']))<=radius

def follow_verified(samples):return bool(samples) and sum(bool(p.get('near')) for p in samples)*100>=len(samples)*70

def chat_goal_task(goal_id):
    """Task id of a goal agreed in conversation: the spoken reply already used goal_id."""
    return str(goal_id)+'-goal'

def task_path(d,task_id):
    if not isinstance(task_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',task_id):raise ValueError('INVALID_TASK_ID')
    return d/'action_tasks'/(task_id+'.json')

def persist_task(task,d):
    atomic(d/'manual_task.json',task)
    if task.get('id'):
        path=task_path(d,task['id']);path.parent.mkdir(mode=0o700,exist_ok=True);atomic(path,task)

def resident_path(name):
    r=next((r for r in config()['residents'] if name in (r['name'],r['resident_id'])),None)
    if r is None:raise ValueError('UNKNOWN_RESIDENT')
    return STATE/r['resident_id']

def cancelled(task,d):
    path=d/'action_tasks'/(str(task.get('id',''))+'.cancel')
    return bool(task.get('id') and path.exists())

def action_status(name,task_id):
    d=resident_path(name);path=task_path(d,task_id)
    if not path.exists():raise ValueError('UNKNOWN_TASK')
    task=json.loads(path.read_text());result=dict(task)
    result['state']='done' if task.get('outcome')=='done' else 'cancelled' if task.get('outcome')=='failed(CANCELLED)' else 'failed' if task.get('ended') else 'cancel_requested' if cancelled(task,d) else 'unconfirmed' if task.get('expires',float('inf'))<time.time() else 'active'
    if task.get('unresolved'):result['state']='unconfirmed'
    if task.get('native_action') and (not task.get('ended') or task.get('unresolved') or task.get('outcome')=='failed(CANCELLED)'):
        result['native']=request(str(d/'actions.sock.control'),{'op':'action','action_id':task['native_action']})
        if task.get('outcome')=='failed(CANCELLED)' and result['native']['state'] not in TERMINAL:result['state']='cancel_requested'
    return result

def cancel_action(name,task_id):
    d=resident_path(name);status=action_status(name,task_id)
    if status.get('ended') and not status.get('unresolved'):return status
    native=status.get('native') or {}
    if native.get('skill') in ('buy_potions','sell_loot') and native.get('state') not in TERMINAL:raise BlockingIOError('ECONOMIC_ACTION_IN_FLIGHT')
    flag=task_path(d,task_id).with_suffix('.cancel');atomic(flag,{'task_id':task_id,'at':time.time()})
    if native and native.get('state') not in TERMINAL:request(str(d/'actions.sock.control'),{'op':'cancel','action_id':status['native_action']})
    return action_status(name,task_id)

def execution_deadline(action,started):return started+(60 if action=='walk_to' else 300 if action in ('meet','find_party') else 1200)

def retry_allowed(action,attempt,result):return action in REVERSIBLE and action!='meet' and attempt<3 and result not in ('done','failed(CHAT_INTERRUPT)','failed(CANCELLED)')

def interrupted(task,skill,d):
    if skill not in ('buy_potions','sell_loot') and cancelled(task,d):return True
    if skill not in ('buy_potions','sell_loot') and task.get('meeting_chat_dirs'):
        return any((pathlib.Path(path)/'CHAT_PENDING').exists() for path in task['meeting_chat_dirs'])
    return task.get('origin')!='chat' and skill not in ('buy_potions','sell_loot') and (d/'CHAT_PENDING').exists()


def native_plan(action,args,c):
    if action=='walk_to':return dict(skill='travel_to',params=dict(c['places'][args['place']],r=3),timeout=55)
    if action=='say':return dict(skill='say',params=args,timeout=15)
    if action=='whisper':return dict(skill='whisper',params={'to_name':args['to'],'text':args['text']},timeout=15)
    if action=='town_service':return dict(skill='travel_to',params={'map':'prt_in','x':126,'y':76,'r':3},timeout=240)
    if action=='rest':return dict(skill='rest',params={'hp_pct':args['until_hp']},timeout=240)
    if action in ('hunt','hunt_solo'):
        duration=args['minutes']*60
        return dict(skill='hunt',params={'map':args['map'],'duration':duration,'min_kills':1},timeout=duration+30)
    raise ValueError('COMPOSITE_ACTION')

@contextlib.contextmanager
def manual(r,wait_limit=None):
    d=STATE/r['resident_id'];lock=(d/'manual.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    flag=d/'MANUAL';atomic(flag,{'pid':os.getpid(),'expires':time.time()+7200})
    try:
        # Allow the brain's already-in-flight local RPC to settle before taking over.
        time.sleep(.6)
        sock=str(d/'actions.sock.control');s=request(sock,{'op':'status'})
        for a in s['actions']:
            if wait_limit is not None and a['skill'] in ('buy_potions','sell_loot'):raise BlockingIOError('ECONOMIC_ACTION_IN_FLIGHT')
            if a['skill'] not in ('buy_potions','sell_loot'):request(sock,{'op':'cancel','action_id':a['action_id']})
            end=time.monotonic()+max(5,a['deadline']-time.time()+65)
            if wait_limit is not None:end=min(end,time.monotonic()+wait_limit)
            while time.monotonic()<end:
                if request(sock,{'op':'action','action_id':a['action_id']})['state'] in TERMINAL:break
                time.sleep(.25)
            else:
                if wait_limit is not None:raise BlockingIOError('EXISTING_ACTION_UNRESOLVED')
                raise RuntimeError('EXISTING_ACTION_UNRESOLVED')
        yield d,sock
    finally:
        flag.unlink(missing_ok=True);lock.close()

def run_native(sock,plan,key,task,d):
    if cancelled(task,d):return 'failed(CANCELLED)'
    remaining=task.get('expires',float('inf'))-time.time()
    if remaining<=1:return 'timeout'
    if plan['skill']=='hunt' and plan['params']['duration']>remaining:return 'failed(TEST_WINDOW_LIMIT)'
    timeout=min(plan['timeout'],remaining-1)
    a=request(sock,dict(op='run',skill=plan['skill'],params=plan['params'],deadline=timeout,idem=key))
    task['native_action']=a['action_id'];persist_task(task,d)
    end=min(time.monotonic()+timeout+65,time.monotonic()+remaining)
    while time.monotonic()<end:
        if interrupted(task,plan['skill'],d):
            request(sock,{'op':'cancel','action_id':a['action_id']})
            return 'failed(CANCELLED)' if cancelled(task,d) else 'failed(CHAT_INTERRUPT)'
        a=request(sock,{'op':'action','action_id':a['action_id']})
        result=outcome(a)
        if result is not None:
            confirmed_at=time.time()
            task.setdefault('native_results',[]).append({'action_id':a['action_id'],'skill':plan['skill'],'outcome':result,'at':confirmed_at});persist_task(task,d)
            # A slow polling RPC may cross the caller's lease. Preserve the
            # actual native terminal (especially economics), but do not turn
            # late confirmation into successful bounded goal completion.
            if result=='done' and confirmed_at>=task.get('expires',float('inf')):return 'timeout'
            return result
        time.sleep(.25)
    task['unresolved']=True
    if plan['skill'] not in ('buy_potions','sell_loot'):request(sock,{'op':'cancel','action_id':a['action_id']})
    return 'timeout'

def recent_whisper_contacts(d,name):
    """Read only this resident's bounded inbox; absent/unreadable evidence grants nothing."""
    now=time.time()
    try:
        with contextlib.closing(sqlite3.connect('file:'+str(d.parent/'chat.sqlite')+'?mode=ro',uri=True)) as db:
            rows=db.execute("SELECT json_extract(payload,'$.name') FROM inbox WHERE resident=? AND json_extract(payload,'$.ts') BETWEEN ? AND ? AND status NOT IN ('ignored','expired') ORDER BY id DESC LIMIT 16",(name,now-1800,now)).fetchall()
    except sqlite3.Error:return []
    return [row[0] for row in rows if isinstance(row[0],str)]


def execute_native_menu(action,args,c,sock,key,task,d):
    if action in EXTRA:
        s=request(sock,{'op':'status'});s={**s,'_resident_id':d.name}
        reason=unavailable(action,args,c,s)
        if reason:return 'failed('+reason+')'
        if action=='native_control' and args['skill']=='whisper':
            from .actions import whisper_target_allowed
            own=next((r for r in c.get('residents',[]) if r.get('resident_id')==d.name),None)
            if own is None or not whisper_target_allowed(c,s,args['params']['to_name'],recent_whisper_contacts(d,own['name'])):return 'failed(WHISPER_TARGET)'
    if action in ('travel_to','respawn','native_control'):
        skill=args['skill'] if action=='native_control' else action
        params=args['params'] if action=='native_control' else args
        timeout=params['duration']+30 if skill=='hunt' else 40 if skill in ('buy_potions','sell_loot') else 15 if skill in ('say','whisper') else 240
        return run_native(sock,{'skill':skill,'params':params,'timeout':timeout},key,task,d)
    if action in ('sell_loot','sell_equipment','buy_potions'):
        result=run_native(sock,native_plan('town_service',{},c),key+'-route',task,d)
        if result!='done':return result
        # The route may outlive the stock, loot and body observations that
        # admitted this goal. Do not dispatch economics from that old snapshot.
        arrived={**request(sock,{'op':'status'}),'_resident_id':d.name}
        reason=unavailable(action,args,c,arrived)
        if reason:return 'failed('+reason+')'
        if action=='buy_potions':plan={'skill':'buy_potions','params':{'item_id':501,**args},'timeout':40}
        else:plan={'skill':'sell_loot','params':{'category':'loot' if action=='sell_loot' else 'equipment'},'timeout':40}
        return run_native(sock,plan,key+'-trade',task,d)
    if action=='find_party':
        from .actions import party_candidates,party_member
        candidates=party_candidates(c,s);target=candidates[0]['name']
        task['party_candidates']=candidates;persist_task(task,d)
        result=run_native(sock,{'skill':'whisper','params':{'to_name':target,'text':'Я ИИ-житель. Хочешь охотиться вместе? Пригласи меня в группу.'},'timeout':15},key+'-offer',task,d)
        if result!='done':return result
        end=time.monotonic()+args['minutes']*60
        deadline=task.get('expires',float('inf'))
        while time.monotonic()<end:
            if interrupted(task,action,d):return 'failed(CANCELLED)' if cancelled(task,d) else 'failed(CHAT_INTERRUPT)'
            if time.time()>=deadline:return 'timeout'
            observed=request(sock,{'op':'status'})
            # A status RPC may finish after the caller's task lease; late party
            # membership is not completion within that bounded execution window.
            if time.time()>=deadline:return 'timeout'
            age=observed.get('age')
            if type(age) not in (int,float) or not 0<=age<=4:return 'failed(STALE_BODY)'
            if party_member(observed.get('telemetry') or {},target):
                telemetry=observed['telemetry'];pos=telemetry.get('pos') or {}
                if not {'x','y'}<=set(pos) or not witness_at(observed,{'map':telemetry.get('map'),**pos},time.time()):return 'failed(STALE_WITNESS)'
                task['native_party_confirmation']=observed['telemetry']['party'];persist_task(task,d);return 'done'
            time.sleep(min(.5,max(0,deadline-time.time())))
        return 'timeout' if time.time()>=deadline else 'failed(PARTY_NOT_CONFIRMED)'
    if action in ('support','hunt_party'):
        plan=native_plan('hunt',args,c)
        support=next((r.get('support_target') for r in c.get('residents',[]) if r.get('resident_id')==d.name),None)
        if action=='support' or support==args['player']:
            reason=unavailable('support',args,c,s)
            if reason:return 'failed('+reason+')'
            plan['params']['support_target']=args['player']
        else:plan['params'].update(solo=True)
        if action=='hunt_party':plan['params']['party_target']=args['player']
        if s['telemetry'].get('map')!=args['map']:
            result=run_native(sock,{'skill':'travel_to','params':{'map':args['map'],'x':170,'y':240,'r':3},'timeout':240},key+'-route',task,d)
            if result!='done':return result
            # Travel can outlast membership/health/skill observations. Recheck the
            # same eligibility contract before starting the cooperative hunt.
            arrived={**request(sock,{'op':'status'}),'_resident_id':d.name}
            reason=unavailable(action,args,c,arrived)
            if reason:return 'failed('+reason+')'
        return run_native(sock,plan,key,task,d)
    if action in ('say','whisper','walk_to','rest'):
        plan=native_plan(action,args,c)
        if action=='walk_to' and task.get('origin')=='brain':plan={**plan,'timeout':240}
        return run_native(sock,plan,key,task,d)
    if action in ('hunt','hunt_solo'):
        t=request(sock,{'op':'status'}).get('telemetry') or {}
        if t.get('map')!=args['map']:
            go=dict(skill='travel_to',params={'map':args['map'],'x':170,'y':240,'r':3},timeout=240 if task.get('origin')=='brain' else 55)
            result=run_native(sock,go,key+'-route',task,d)
            if result!='done':return result
            if action=='hunt_solo':
                # Admit combat from current facts, not the pre-travel snapshot.
                arrived={**request(sock,{'op':'status'}),'_resident_id':d.name}
                reason=unavailable(action,args,c,arrived)
                if reason:return 'failed('+reason+')'
        plan=native_plan(action,args,c)
        support=next((r.get('support_target') for r in c.get('residents',[]) if r.get('resident_id')==d.name),None)
        if action=='hunt_solo':plan={**plan,'params':{**plan['params'],'solo':True}}
        elif support:
            params=plan['params'];assert isinstance(params,dict)
            plan={**plan,'params':{**params,'support_target':support}}
        return run_native(sock,plan,key,task,d)
    if action=='town_service':
        result=run_native(sock,native_plan(action,args,c),key+'-route',task,d)
        if result!='done':return result
        t=request(sock,{'op':'status'}).get('telemetry') or {}
        sold='done'
        if has_sellable(t):
            # A refused or unproven sale must not leave the resident without potions.
            sold=run_native(sock,{'skill':'sell_loot','params':{},'timeout':40},key+'-sell',task,d)
        t=request(sock,{'op':'status'}).get('telemetry') or {};stock=t.get('inventory',{}).get('501',0)
        if stock>=17:return sold
        goal=min(20,stock+int(t.get('zeny',0)//10))
        if goal<=stock:return 'failed(INSUFFICIENT_EARNED_ZENY)'
        return run_native(sock,{'skill':'buy_potions','params':{'item_id':501,'stock_goal':goal,'max_zeny':300},'timeout':40},key+'-buy',task,d)
    if action=='idle':
        request(sock,{'op':'safe_stop'});time.sleep(.5)
        start=time.time();end=time.monotonic()+args['minutes']*60
        while time.monotonic()<end:
            if interrupted(task,action,d):return 'failed(CANCELLED)' if cancelled(task,d) else 'failed(CHAT_INTERRUPT)'
            s=request(sock,{'op':'status'});p=s.get('projection',{}).get('server_pos',{})
            if s.get('age') is None or s['age']>4 or time.time()-p.get('ts',0)>15:return 'failed(STALE_WITNESS)'
            if s.get('telemetry',{}).get('mode')!='IDLE_SAFE':return 'failed(IDLE_NOT_SAFE)'
            time.sleep(.5)
        s=request(sock,{'op':'status'});p=s.get('projection',{}).get('server_pos',{})
        if s.get('telemetry',{}).get('mode')!='IDLE_SAFE':return 'failed(IDLE_NOT_SAFE)'
        if p.get('ts',0)<start:return 'failed(NO_EVIDENCE)'
        task['idle_witness']=p;persist_task(task,d);return 'done'
    if action=='follow':
        end=time.monotonic()+args['minutes']*60;proof=[]
        while time.monotonic()<end:
            if interrupted(task,action,d):return 'failed(CANCELLED)' if cancelled(task,d) else 'failed(CHAT_INTERRUPT)'
            s=request(sock,{'op':'status'});t=s.get('telemetry') or {}
            now=time.time()
            if s.get('age') is None or s['age']>4:return 'failed(STALE_BODY)'
            target=next((p for p in t.get('nearby_players',[]) if p['name']==args['player']),None)
            if not target:return 'failed(TARGET_NOT_VISIBLE)'
            pos=target['pos'];mine=t['pos']
            if max(abs(pos['x']-mine['x']),abs(pos['y']-mine['y']))>3:
                result=run_native(sock,{'skill':'travel_to','params':dict(map=t['map'],x=pos['x'],y=pos['y'],r=3),'timeout':15},key+'-'+str(len(proof)),task,d)
                if result!='done':return result
            peer=next((r for r in c['residents'] if r['name']==args['player']),None)
            if peer:
                target_status=request(str(STATE/peer['resident_id']/'actions.sock.control'),{'op':'status'})
            else:
                from tools.events import query,DEFAULTS
                raw=query(DEFAULTS,"SELECT e.map,e.x,e.y,UNIX_TIMESTAMP(e.ts) FROM residents.resident_events e JOIN ro_residents_main.char c ON c.char_id=e.char_id WHERE c.name='"+args['player']+"' AND e.kind IN('pos','loadmap') ORDER BY e.id DESC LIMIT 1")
                if not raw:return 'failed(NO_TARGET_WITNESS)'
                m,x,y,ts=raw.split('\t');target_status={'age':0,'projection':{'server_map':{'value':m},'server_pos':{'value':{'x':int(x),'y':int(y)},'ts':float(ts)}}}
            p=target_status.get('projection',{}).get('server_pos',{});v=p.get('value',{})
            if now-p.get('ts',0)>15:return 'failed(STALE_TARGET_WITNESS)'
            target_point={'map':target_status['projection']['server_map']['value'],'x':v['x'],'y':v['y']}
            s=request(sock,{'op':'status'})
            proof.append({'at':time.time(),'target_witness':p,'self_witness':s.get('projection',{}).get('server_pos'),'near':witness_at(s,target_point,time.time())});time.sleep(1)
        task['follow_samples']=proof;persist_task(task,d)
        return 'done' if follow_verified(proof) else 'failed(FOLLOW_DISTANCE)'
    if action=='meet':
        from .meeting import execute as negotiate_meeting
        return negotiate_meeting(args,c,sock,key,task,d)
    raise ValueError('UNSUPPORTED')

def execute(name,action,args,*,origin='manual',task_id=None,say=''):
    c=config();args=validate(action,args,c)
    from .actions import UNSUPPORTED
    if action in UNSUPPORTED:raise ValueError(UNSUPPORTED[action])
    r=next((r for r in c['residents'] if name in (r['name'],r['resident_id'])),None)
    if r is None:raise ValueError('UNKNOWN_RESIDENT')
    started=time.time();task={'id':task_id or str(uuid.uuid4()),'resident':r['name'],'origin':origin,'action':action,'args':args,'started':started,'outcome':None,'attempts':0,'expires':execution_deadline(action,started)}
    if origin=='brain' and action=='walk_to':task['expires']=started+300
    if action in ('hunt_solo','hunt_party','support') or origin=='brain' and action=='hunt':task['expires']=started+args['minutes']*60+300
    saved_path=task_path(STATE/r['resident_id'],task['id'])
    if saved_path.exists():
        saved=json.loads(saved_path.read_text())
        if saved.get('action')!=action or saved.get('args')!=args:raise ValueError('ACTION_ID_CONFLICT')
        return saved # Includes unresolved economic tasks: never replay them.
    previous=STATE/r['resident_id']/'manual_task.json'
    if action=='meet' and task_id and previous.exists():
        saved=json.loads(previous.read_text())
        if saved.get('id')==task_id:
            if saved.get('action')!=action or saved.get('args')!=args:raise ValueError('MEETING_ID_CONFLICT')
            if saved.get('ended'):return saved
            task=saved
    with manual(r) as (d,sock):
        if saved_path.exists():
            saved=json.loads(saved_path.read_text())
            if saved.get('action')!=action or saved.get('args')!=args:raise ValueError('ACTION_ID_CONFLICT')
            return saved
        if origin=='brain':
            lease=json.loads((d/'MANUAL').read_text());lease['expires']=max(lease['expires'],task['expires']);atomic(d/'MANUAL',lease)
        for attempt in range(1,4):
            task['attempts']=attempt;persist_task(task,d)
            # Speech actions already own their text and channel. An optional
            # prelude would duplicate say or publicly disclose a whisper.
            # A confirmed prelude belongs to the goal, not each movement retry.
            spoken='done'
            if say and action not in ('say','whisper') and not (action=='native_control' and args['skill'] in ('say','whisper')) and task.get('prelude_outcome')!='done':
                spoken=run_native(sock,{'skill':'say','params':{'text':say},'timeout':10},task['id']+'-speech-'+str(attempt),task,d)
                task['prelude_outcome']=spoken;persist_task(task,d)
            result=execute_native_menu(action,args,c,sock,task['id']+'-'+str(attempt),task,d) if spoken=='done' else spoken
            task['outcome']=result;persist_task(task,d)
            if not retry_allowed(action,attempt,result) or task.get('unresolved') or time.time()+5>=task['expires']:break
            time.sleep(5)
        task['ended']=time.time();persist_task(task,d)
        with (STATE/'skills.jsonl').open('a') as f:f.write(json.dumps(task,ensure_ascii=False)+'\n')
    return task
