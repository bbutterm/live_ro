"""Ограниченные переговоры: никакого согласия без отдельного решения модели."""
import contextlib,re,time
from .supervisor import atomic


def validate_vote(v,c,place,allow_counter):
    if not isinstance(v,dict) or set(v)!={'choice','place','reason'}:raise ValueError('INVALID_MEETING_SCHEMA')
    if not isinstance(v['choice'],str) or v['choice'] not in ('accept','decline','counter'):raise ValueError('INVALID_MEETING_CHOICE')
    if not isinstance(v['place'],str) or v['place'] not in c['places']:raise ValueError('UNKNOWN_PLACE')
    if not isinstance(v['reason'],str) or not 1<=len(v['reason'])<=200 or re.search(r'[\x00-\x1f\x7f]',v['reason']):raise ValueError('INVALID_REASON')
    if v['choice']=='counter':
        if not allow_counter or v['place']==place:raise ValueError('INVALID_COUNTER')
    elif v['place']!=place:raise ValueError('CHANGED_ACCEPTANCE_PLACE')
    return v


def decide(r,d,c,offer,s,allow_counter):
    from .mind import Mind
    if not c.get('llm',{}).get('enabled'):return None
    try:return Mind(r,d,c).negotiate(offer,s,allow_counter)
    except Exception:
        from .mind import record
        record('MEETING_MODEL_UNAVAILABLE',resident=r['name'],error='INIT_OR_STATE')
        return None


def execute(args,c,sock,key,task,d):
    from concurrent.futures import ThreadPoolExecutor
    from . import skills
    target=next(r for r in c['residents'] if r['name']==args['resident'])
    other_d=skills.STATE/target['resident_id']
    other_sock=str(other_d/'actions.sock.control')
    if target['name']==task['resident']:return 'failed(SELF_MEETING)'
    task['meeting_chat_dirs']=[str(d),str(other_d)]
    def busy():return (d/'CHAT_PENDING').exists() or (other_d/'CHAT_PENDING').exists() or (other_d/'chat-goal.json').exists()
    def label(place):return 'у фонтана' if place=='fountain' else 'на поле'
    def speak(sock,path,owner,text,suffix,to):
        owner.setdefault('protocol_speech',[]).append(text)
        atomic(path/'manual_task.json',owner)
        return skills.run_native(sock,dict(skill='whisper',params={'to_name':to,'text':text},timeout=10),key+'-'+suffix,owner,path)
    def vote(r,path,s,place,allow_counter,slot):
        votes=task.setdefault('meeting_votes',{})
        if slot in votes:return votes[slot].get('decision')
        if time.time()+5>=task.get('expires',float('inf')):return None
        # A crash after claiming a model call must not silently request a new vote.
        votes[slot]={'resident':r['name'],'offer':place,'at':time.time(),'status':'claimed'}
        atomic(d/'manual_task.json',task)
        result=decide(r,path,c,{'resident':target['name'] if r['name']==task['resident'] else task['resident'],'place':place,'meeting_id':task['id']},s,allow_counter)
        if result is not None:result=validate_vote(result,c,place,allow_counter)
        votes[slot].update(status='decided' if result else 'unavailable',decision=result)
        atomic(d/'manual_task.json',task)
        return result
    if busy():return 'failed(CHAT_INTERRUPT)'
    s=skills.request(other_sock,{'op':'status'})
    if not target.get('enabled',True) or s.get('age') is None or s['age']>4 or (s.get('telemetry') or {}).get('dead'):return 'failed(TARGET_UNAVAILABLE)'
    if any(a['skill'] in ('buy_potions','sell_loot') for a in s['actions']):return 'failed(TARGET_BUSY)'
    result=speak(sock,d,task,'Предлагаю встречу '+label(args['place'])+'.','proposal',target['name'])
    if result!='done':return result
    if busy():return 'failed(CHAT_INTERRUPT)'
    answer=vote(target,other_d,s,args['place'],True,'peer')
    if answer is None:return 'failed(MEETING_MODEL_UNAVAILABLE)'
    if busy():return 'failed(CHAT_INTERRUPT)'
    def reply(choice,result):
        task['meeting_reply']={'choice':choice,'outcome':result}
        atomic(d/'manual_task.json',task)
        if result=='failed(CHAT_INTERRUPT)' or busy():return 'failed(CHAT_INTERRUPT)'
        return 'failed(MEETING_DECLINED)' if choice=='decline' else result
    peer=None
    @contextlib.contextmanager
    def peer_writer():
        # A model-owned hunt keeps manual.lock until its terminal outcome.
        # Wait for that writer, not for another model vote, within the same lease.
        expires=task.get('expires',time.time()+300)
        with contextlib.ExitStack() as stack:
            while True:
                if busy() or time.time()+5>=expires:raise BlockingIOError('MEETING_WAIT_ENDED')
                try:
                    owned=stack.enter_context(skills.manual(target,wait_limit=2))
                    break
                except BlockingIOError:
                    # A refusal is already terminal; speech must not hold the
                    # initiator for the rest of the lease or cancel economics.
                    if answer['choice']=='decline':raise
                    time.sleep(.2)
            try:yield owned
            finally:
                if peer is not None:
                    peer['ended']=time.time();atomic(owned[0]/'manual_task.json',peer)
    try:
        with peer_writer() as (other_d,other_sock):
            if busy():return 'failed(CHAT_INTERRUPT)'
            peer={'id':key+'-peer','resident':target['name'],'origin':'brain','action':'meet','args':{'resident':task['resident'],'place':answer['place']},'started':time.time(),'expires':task.get('expires',time.time()+300),'meeting_chat_dirs':task['meeting_chat_dirs']}
            place=answer['place'];choice=answer['choice']
            text=('Договорились: встреча ' if choice=='accept' else 'Предлагаю вместо этого встречу ' if choice=='counter' else 'Сегодня встречу отклоняю, занят.')
            if choice!='decline':text+=label(place)+'.'
            result=speak(other_sock,other_d,peer,text,'peer-vote',task['resident'])
            result=reply(choice,result)
            if result!='done':return result
            if choice=='counter':
                initiator=next(r for r in c['residents'] if r['name']==task['resident'])
                if busy():return 'failed(CHAT_INTERRUPT)'
                final=vote(initiator,d,skills.request(sock,{'op':'status'}),place,False,'initiator')
                if final is None:return 'failed(MEETING_MODEL_UNAVAILABLE)'
                text='Договорились: встреча '+label(place)+'.' if final['choice']=='accept' else 'Сегодня встречу отклоняю, занят.'
                result=speak(sock,d,task,text,'final-vote',target['name'])
                result=reply(final['choice'],result)
                if result!='done':return result
            if busy():return 'failed(CHAT_INTERRUPT)'
            p=c['places'][place];task['meeting_place']=place
            route=dict(skill='travel_to',params=dict(p,r=3),timeout=180)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(skills.run_native,sock,route,key+'-self',task,d)
                second=pool.submit(skills.run_native,other_sock,route,key+'-peer',peer,other_d)
                results=[first.result(),second.result()]
            if results!=['done','done']:return next(r for r in results if r!='done')
            a=skills.request(sock,{'op':'status'});b=skills.request(other_sock,{'op':'status'})
            if not skills.witness_at(a,p,time.time()) or not skills.witness_at(b,p,time.time()):return 'failed(MEETING_NO_WITNESS)'
            task['meeting_witness']={'self':a['projection']['server_pos'],'peer':b['projection']['server_pos'],'peer_actions':peer.get('native_results',[])}
            atomic(d/'manual_task.json',task)
            return 'done'
    except BlockingIOError:
        if busy():return 'failed(CHAT_INTERRUPT)'
        if answer['choice']=='decline':return reply('decline','not_sent(TARGET_BUSY)')
        return 'timeout' if time.time()+5>=task.get('expires',float('inf')) else 'failed(TARGET_BUSY)'
