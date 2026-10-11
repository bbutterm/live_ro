"""Ограниченные переговоры: никакого согласия без отдельного решения модели."""
import contextlib,json,re,time
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


MEET_MARGIN=60  # after the peer is free: whisper the answer and still walk to the place


def goal_end(t):
    """When the peer's writer is free again. A goal's own lease (`expires`) already includes
    the route to the field and retries; speech is seconds; idle/follow end with their minutes."""
    started=t.get('started',0) if isinstance(t.get('started'),(int,float)) else 0
    expires=t.get('expires') if isinstance(t.get('expires'),(int,float)) else None
    minutes=(t.get('args') or {}).get('minutes') if isinstance(t.get('args'),dict) else None
    if t.get('action') in ('say','whisper'):return started+30
    if t.get('action') in ('idle','follow') and type(minutes) is int:
        return min(started+minutes*60+30,expires) if expires is not None else started+minutes*60+30
    if expires is not None:return expires
    return started+minutes*60+300 if type(minutes) is int else started+1200


def peer_long_goal(other_d,window_end):
    """The peer's own goal still holds its writer past our meet window (minus the time to
    answer and walk): no proposal, no vote, no false accept. A meeting cannot interrupt it.
    A stale task file (writer lock free) never blocks; a reciprocal meet is left to the
    collision rule."""
    import fcntl
    try:t=json.loads((other_d/'manual_task.json').read_text())
    except (OSError,ValueError):return False
    if not isinstance(t,dict) or t.get('ended') or t.get('action')=='meet':return False
    if goal_end(t)<=window_end-MEET_MARGIN:return False
    try:
        with (other_d/'manual.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(lock,fcntl.LOCK_UN)
        return False  # nobody holds the writer: the task file is stale
    except BlockingIOError:return True
    except OSError:return False


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
    def label(place):return skills.place_phrase(c,place)
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
    if not target.get('enabled',True):return 'failed(TARGET_UNAVAILABLE)'
    try:s=skills.request(other_sock,{'op':'status'})
    except (OSError,RuntimeError,ValueError):return 'failed(TARGET_UNAVAILABLE)'
    if peer_long_goal(other_d,task.get('expires',float('inf'))):return 'failed(TARGET_BUSY)'
    if s.get('age') is None or s['age']>4 or (s.get('telemetry') or {}).get('dead'):return 'failed(TARGET_UNAVAILABLE)'
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
                    # Two reciprocal initiators each own their own writer.
                    # Only one yields; the survivor still needs its saved vote,
                    # normal peer arbitration and server-confirmed arrival.
                    if answer['choice']!='decline' and task['resident']>target['name']:
                        try:other=json.loads((other_d/'manual_task.json').read_text())
                        except (OSError,ValueError):other={}
                        if (other.get('id') and other.get('resident')==target['name'] and other.get('action')=='meet'
                            and other.get('args',{}).get('resident')==task['resident']
                            and not other.get('ended') and other.get('outcome') is None
                            and other.get('started',float('inf'))<=time.time()<other.get('expires',0)):
                            task['meeting_collision']={'peer_task':other['id'],'yield_to':target['name']}
                            atomic(d/'manual_task.json',task)
                            raise BlockingIOError('MEETING_COLLISION')
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
    except BlockingIOError as error:
        if busy():return 'failed(CHAT_INTERRUPT)'
        if str(error)=='MEETING_COLLISION':return 'failed(MEETING_COLLISION)'
        if answer['choice']=='decline':return reply('decline','not_sent(TARGET_BUSY)')
        return 'timeout' if time.time()+5>=task.get('expires',float('inf')) else 'failed(TARGET_BUSY)'
