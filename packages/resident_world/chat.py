"""Server-observed incoming speech; one spatially selected responder, no name allowlist."""
import re,json,sqlite3
from .supervisor import online
ALIASES={'Mira':('мира','миру','mira'), 'Borin':('борин','борина','borin'), 'ResidentA':('residenta','резидент','резидента')}
LEGACY_FALLBACK='Слышу тебя. Сейчас я рядом.'
PLACES={'prontera':'в Пронтере','prt_fild08':'на поле к югу от Пронтеры','prt_in':'в лавке в Пронтере'}
COME=r'пойд[её]м со мной|иди за мной|следуй за мной|(?:иди|приходи|подойди|беги)\s+(?:ко мне|сюда)|найди меня'

def stop_requested(event):
    text=event.get('text','').strip().lower().rstrip('.!')
    for aliases in ALIASES.values():
        for alias in aliases:text=re.sub(r'^'+re.escape(alias)+r'[, :]+','',text)
    return bool(re.fullmatch(r'стой|стоп|остановись|отмени(?: задачу| встречу)?|не (?:иди|следуй)(?: за мной)?',text))

def select_responder(event,residents,states,now,radius=14,partner=None):
    """partner: the resident this speaker was just talking with — a follow-up without a name is theirs."""
    eligible=[]
    for r in residents:
        s=states.get(r['resident_id'],{})
        if not r.get('enabled',True) or r['char_id']==event['char_id'] or not online(s,now):continue
        if event.get('type')=='W':
            if event.get('to')==r['name']:return r
            continue
        proj=s['projection'];pos=proj['server_pos']['value']
        if proj['server_map']['value']!=event['map']:continue
        distance=max(abs(pos['x']-event['x']),abs(pos['y']-event['y']))
        if distance<=radius:eligible.append((distance,r))
    if not eligible:return None
    mentioned=[]
    for r in residents:
        for alias in ALIASES.get(r['name'],(r['name'],)):
            match=re.search(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',event['text'],re.I)
            if match:mentioned.append((match.start(),r['name']))
    if mentioned:
        addressed=min(mentioned)[1]
        return next((r for _,r in eligible if r['name']==addressed),None)
    if partner:
        same=next((r for _,r in eligible if r['name']==partner),None)
        if same:return same
    return min(eligible,key=lambda pair:(pair[0],pair[1]['name']))[1]

class ChatStore:
    def __init__(self,path):
        self.db=sqlite3.connect(path,timeout=2)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS inbox(id INTEGER PRIMARY KEY,payload TEXT,resident TEXT,status TEXT,claimed REAL,result TEXT); CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value REAL);'
                              'CREATE TABLE IF NOT EXISTS outgoing(resident TEXT,text TEXT,at REAL,to_human INTEGER);')
    def note_outgoing(self,resident,text,to_human,now):
        """Written before the words are spoken, so the poller already knows whose answer it hears."""
        with self.db:
            self.db.execute('DELETE FROM outgoing WHERE at<?',(now-300,))
            self.db.execute('INSERT INTO outgoing VALUES(?,?,?,?)',(resident,text,now,1 if to_human else 0))
    def answer_to_human(self,resident,text,now):
        """This resident line is its answer to a player: other residents must not take it as addressed to them."""
        return self.db.execute('SELECT 1 FROM outgoing WHERE resident=? AND text=? AND to_human=1 AND at>? LIMIT 1',(resident,text,now-60)).fetchone() is not None
    def partner(self,name,now):
        """The resident who answered this player during the last 90 seconds."""
        row=self.db.execute("SELECT resident FROM inbox WHERE json_extract(payload,'$.name')=? AND resident IS NOT NULL AND claimed>? ORDER BY id DESC LIMIT 1",(name,now-90)).fetchone()
        return row[0] if row else None
    def get(self,key,default=0):
        row=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return row[0] if row else default
    def put(self,key,value):self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,value))
    def enqueue(self,event,resident,is_resident,now):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM inbox WHERE id=?',(event['id'],)).fetchone():return False
            chain=(self.get('chain')+1 if now-self.get('chain_last')<120 else 1) if is_resident else 0
            self.put('chain',chain);self.put('chain_last',now)
            status='queued' if resident and now-event['ts']<=10 and (not is_resident or chain<=4) else 'ignored'
            if is_resident:
                prior=self.db.execute('SELECT 1 FROM inbox WHERE resident=? AND json_extract(payload,\'$.name\')=? AND json_extract(payload,\'$.text\')=? AND json_extract(payload,\'$.ts\')>? LIMIT 1',
                                      (resident,event.get('name'),event.get('text'),now-120)).fetchone()
                if prior or event.get('text')==LEGACY_FALLBACK:status='ignored'
            self.db.execute('INSERT INTO inbox VALUES(?,?,?,?,NULL,NULL)',(event['id'],json.dumps(dict(event,human=not is_resident),ensure_ascii=False),resident,status))
            self.put('cursor',event['id']);return status=='queued'
    def claim(self,name,now):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute("UPDATE inbox SET status='expired' WHERE status='queued' AND json_extract(payload,'$.ts')<?",(now-10,))
            if self.db.execute('SELECT COUNT(*) FROM inbox WHERE resident=? AND claimed>?',(name,now-60)).fetchone()[0]>=30:return None  # flood guard: a person talking fast is still answered
            # A player first: residents' small talk never makes a person wait.
            row=self.db.execute("SELECT id,payload FROM inbox WHERE resident=? AND status='queued' ORDER BY COALESCE(json_extract(payload,'$.human'),0) DESC,id LIMIT 1",(name,)).fetchone()
            if not row:return None
            self.db.execute("UPDATE inbox SET status='claimed',claimed=? WHERE id=?",(now,row[0]));return json.loads(row[1])
    def finish(self,event_id,result):
        with self.db:self.db.execute("UPDATE inbox SET status='finished',result=? WHERE id=?",(json.dumps(result,ensure_ascii=False),event_id))
    def recent_replies(self,event,now,resident=None):
        """Texts this resident already said to this sender in the last 2 minutes."""
        rows=self.db.execute('SELECT json_extract(result,\'$.decision.args.text\') FROM inbox WHERE (? IS NULL OR resident=?) AND json_extract(payload,\'$.name\')=? AND json_extract(payload,\'$.ts\')>? AND json_extract(result,\'$.outcome\')=\'done\'',
                             (resident,resident,event['name'],now-120)).fetchall()
        return {r[0] for r in rows if r[0]}
    def suppress_repeat(self,event,decision,now,resident=None):
        if decision is None or decision['action'] not in ('say','whisper') or stop_requested(event):return decision
        row=self.db.execute('SELECT 1 FROM inbox WHERE (? IS NULL OR resident=?) AND json_extract(payload,\'$.name\')=? AND json_extract(payload,\'$.ts\')>? AND json_extract(result,\'$.outcome\')=\'done\' AND json_extract(result,\'$.decision.args.text\')=? LIMIT 1',
                            (resident,resident,event['name'],now-120,decision['args']['text'])).fetchone()
        return None if row else decision


def activity_phrase(state):
    """What the resident is visibly doing, from its own body telemetry (no promises)."""
    t=(state or {}).get('telemetry') or {}
    skill=str((t.get('running') or {}).get('skill') or '')
    if t.get('dead'):return 'прихожу в себя после боя'
    if skill=='hunt' or t.get('mode')=='HUNT':return 'охочусь на поле' if t.get('map')=='prt_fild08' else 'охочусь'
    if skill in ('travel_to','follow'):return 'в дороге'
    if skill=='rest':return 'отдыхаю'
    if skill in ('sell_loot','buy_potions'):return 'у торговца'
    return {'prt_fild08':'на поле','prontera':'в городе','prt_in':'в торговой лавке'}.get(t.get('map'),'здесь')

def human_templates(name,text,state):
    """Honest short answers for a human when the model is not used: never silence.
    Simple questions about the resident itself are answered from its own body telemetry."""
    a=activity_phrase(state);t=(state or {}).get('telemetry') or {}
    if re.search(r'кто ты|ты кто',text):return ['Я '+name+', ИИ-персонаж этого мира.','Я '+name+'. Сейчас '+a+'.']
    if re.search(r'уров',text) and t.get('base_level'):
        from .mind import CLASSES
        job=CLASSES.get(t.get('class_id'),'')
        return [f"У меня {t['base_level']} уровень"+(f", я {job}" if job else '')+'.',f"Уровень {t['base_level']}"+(f", профессия — {job}" if job else '')+'.']
    if re.search(r'зени|деньг|денег',text) and t.get('zeny') is not None:return [f"У меня {t['zeny']} зени.",f"Сейчас {t['zeny']} зени."]
    if re.search(r'где ты|ты где|где сейчас',text):return ['Я '+PLACES.get(t.get('map'),'здесь')+', '+a+'.','Сейчас '+a+', '+PLACES.get(t.get('map'),'здесь')+'.']
    if re.search(r'привет|здравств|добр(ый|ое|ого)|хай|салют',text):return ['Привет!','Привет! Сейчас '+a+'.','Здравствуй! Я '+a+'.']
    return ['Слышу тебя. Сейчас '+a+'.','Прости, секунду. Повтори?','Слышу. Пока '+a+'.']

def fallback_reply(name,event,state,human=True,avoid=()):
    """Quick reply without the model. A human player always gets an answer;
    for another resident's line there is no model-free chatter (None)."""
    text=event['text'].lower();base={'reason':'Ответ на услышанную реплику'}
    if stop_requested(event):
        return dict(base,action='whisper' if event.get('type')=='W' else 'say',args=({'to':event['name'],'text':'Остановлюсь.'} if event.get('type')=='W' else {'text':'Остановлюсь.'}))
    visible={p['name'] for p in (state.get('telemetry') or {}).get('nearby_players',[])}
    negated=bool(re.search(r'(?<!\w)не\s+(?:надо\s+)?(?:иди|идти|ходи|следуй|пойд|встреч|приход|подход|беги)',text))  # «не иди за мной» is not «иди за мной»
    if not negated and re.search(COME,text):
        # From afar (a whisper): first come to the player by the witnessed position, then stay near.
        near=event['name'] in visible
        return dict(base,action='follow',args={'player':event['name'],'minutes':3},say='Пойдём, буду рядом.' if near else 'Иду к тебе.')
    if not negated and re.search(r'фонтан',text) and re.search(r'встрет|пойд|иди|приход',text):
        for peer,aliases in ALIASES.items():
            if peer!=name and any(re.search(r'(?<!\w)'+re.escape(a)+r'(?!\w)',text) for a in aliases):
                return dict(base,action='meet',args={'resident':peer,'place':'fountain'},say='Встретимся у фонтана.')
        return dict(base,action='walk_to',args={'place':'fountain'},say='Иду к фонтану.')
    if not human:
        phrase='Привет!' if 'привет' in text else 'Я '+name+', ИИ-персонаж этого мира.' if 'кто ты' in text else None
        if phrase is None:return None
    else:
        options=human_templates(name,text,state)
        phrase=next((o for o in options if o not in avoid),options[0])
    if event.get('type')=='W':return dict(base,action='whisper',args={'to':event['name'],'text':phrase})
    return dict(base,action='say',args={'text':phrase})


def validate_reply(d,event,c,s):
    from .mind import validate_decision
    validate_decision(d,c,s,requester=event['name'])
    if d['action'] not in ('say','whisper','follow','walk_to','meet'):raise ValueError('NOT_A_CHAT_REPLY')
    if d['action'] in ('say','whisper') and d.get('say'):raise ValueError('DOUBLE_REPLY')
    if d['action'] in ('follow','whisper') and d['args'].get('player',d['args'].get('to'))!=event['name']:raise ValueError('WRONG_CHAT_TARGET')
    if d['action'] not in ('say','whisper') and not d.get('say'):raise ValueError('MISSING_CHAT_REPLY')
    return d


def meeting_protocol(event,task,c=None):
    from .skills import place_phrase
    if task.get('action')!='meet':return False
    if event['text'] in task.get('protocol_speech',[]):return True
    label=place_phrase(c or {},task.get('args',{}).get('place'))
    return event['text'] in ('Предлагаю встречу '+label+'.','Договорились: встреча '+label+'.')

def ingest(store,c):
    import time,fcntl
    from .supervisor import STATE
    from packages.body_gateway.cli import request
    from tools.events import query,DEFAULTS
    with (STATE/'chat-poll.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        if time.time()-store.get('last_poll')<.4:return
        cursor=int(store.get('cursor',-1))
        if cursor<0:
            cursor=int(query(DEFAULTS,'SELECT COALESCE(MAX(id),0) FROM ro_residents_logs.chatlog').strip())
            with store.db:store.put('cursor',cursor)
        raw=query(DEFAULTS,f"SELECT JSON_OBJECT('id',l.id,'ts',UNIX_TIMESTAMP(l.time),'name',c.name,'char_id',l.src_charid,'type',l.type,'to',l.dst_charname,'map',l.src_map,'x',l.src_map_x,'y',l.src_map_y,'text',l.message) FROM ro_residents_logs.chatlog l JOIN ro_residents_main.`char` c ON c.char_id=l.src_charid WHERE l.id>{cursor} AND l.type IN ('O','W') ORDER BY l.id LIMIT 100")
        rows=[json.loads(line) for line in raw.splitlines() if line]
        states={}
        if rows:
            for r in c['residents']:
                try:states[r['resident_id']]=request(str(STATE/r['resident_id']/'actions.sock.control'),{'op':'status'})
                except (OSError,RuntimeError,ValueError):pass
        ids={r['char_id'] for r in c['residents']}
        for event in rows:
            internal=False
            source=next((r for r in c['residents'] if r['char_id']==event['char_id']),None)
            if source:
                try:
                    task=json.loads((STATE/source['resident_id']/'manual_task.json').read_text())
                    internal=task.get('started',0)<=event['ts']+1<=min(task.get('ended',time.time()),task.get('expires',time.time()))+2 and meeting_protocol(event,task,c)
                except (OSError,ValueError):pass
            if source and not internal:internal=store.answer_to_human(source['name'],event['text'],time.time())
            partner=None if source else store.partner(event['name'],time.time())
            responder=None if internal else select_responder(event,c['residents'],states,time.time(),c.get('chat_radius',14),partner)
            store.enqueue(event,responder['name'] if responder else None,event['char_id'] in ids,time.time())
        with store.db:store.put('last_poll',time.time())


def queue_behavior_chat(d,event,r,c,s,lease):
    """Only parse an intent here: no legacy model, cancellation, motor or speech."""
    import hashlib
    from packages.resident_behavior.chat_intents import enqueue
    from packages.resident_behavior.config import load_behavior
    decision=fallback_reply(r['name'],event,s,human=False) or {'action':'unsupported','args':{}}  # V1 keeps its own narrow intents
    if decision.get('action') in ('walk_to','meet'):
        requested=decision['args'].get('place')
        point=c.get('places',{}).get(requested)
        places=load_behavior('config/behavior.yaml')['places']
        matched=next((name for name,p in places.items() if point and all(point.get(k)==p.get(k) for k in ('map','x','y'))),None)
        if matched:
            decision['args']['place']=matched
        else:
            decision={'action':'unsupported','args':{}}
    # Unknown/draining leases fence legacy too, but never enqueue for a different owner.
    if lease.get('phase') not in ('controlled','draining') or not lease.get('token'):
        return {'outcome':'behavior_unavailable','mode':'controlled'}
    intent=dict(id='chat-'+str(event['id']),event=event,decision=decision,
                lease_id=hashlib.sha256(lease['token'].encode()).hexdigest())
    enqueue(d,intent)
    return {'outcome':'queued_behavior','mode':'controlled','intent_id':intent['id']}


def legacy_chat_gate(d):
    """Hold the cutover lock from the final legacy permission check through dispatch."""
    from contextlib import contextmanager
    @contextmanager
    def locked():
        import fcntl
        from packages.resident_behavior.ownership import read_lease
        with (d/'behavior-lease.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_SH)
            try:
                lease=read_lease(d)
                from packages.resident_behavior.ownership import lease_active
                yield lease if lease_active(lease) else None
            finally:
                fcntl.flock(lock,fcntl.LOCK_UN)
    return locked()


def handle(event,r,c,store,model=None):
    import time
    from .supervisor import STATE,atomic
    from .skills import manual,run_native
    from .mind import record
    from packages.body_gateway.cli import request
    import os
    d=STATE/r['resident_id'];marker=None
    # Stale-proof conversation marker (supervisor.chat_pending): only for replies that need the motor.
    pending={'id':event['id'],'pid':os.getpid(),'deadline':float(event.get('ts') or time.time())+15}
    # Speak within the 10 s the player waits; the server's confirmation of that speech may land later.
    started=time.time();task={'id':'chat-'+str(event['id']),'resident':r['name'],'origin':'chat','action':'reply','started':started,'expires':event['ts']+30}
    try:
        s=request(str(d/'actions.sock.control'),{'op':'status'})
        from packages.resident_behavior.ownership import read_lease,lease_active
        lease=read_lease(d)
        if lease_active(lease):
            store.finish(event['id'],queue_behavior_chat(d,event,r,c,s,lease))
            return
        from .memory import day_decision
        daily=day_decision(r,event,d)
        human=event.get('char_id') not in {x.get('char_id') for x in c.get('residents',[])}
        decision=daily or fallback_reply(r['name'],event,s,human=human)
        if decision is not None:decision=validate_reply(decision,event,c,s)
        rules=decision
        if model is not None and daily is None and not stop_requested(event):
            decision=model(event,s,decision)
        task['by']='память' if daily else 'правила' if decision is rules else 'LLM'  # the model returns the quick default untouched when it is not used
        why=getattr(getattr(model,'__self__',None),'last_skip',None) if task['by']=='правила' and model is not None else None
        if why:task['by']='правила: '+why
        silence='NO_REPLY' if decision is None else 'REPEATED_REPLY'
        if human:
            # A player is never left in silence: a repeated line becomes another honest phrase.
            if decision is None or store.suppress_repeat(event,decision,time.time(),r['name']) is None:
                decision=validate_reply(fallback_reply(r['name'],event,s,human=True,avoid=store.recent_replies(event,time.time(),r['name'])),event,c,s);task['by']='правила'
        else:decision=store.suppress_repeat(event,decision,time.time(),r['name'])
        if decision is None:
            store.finish(event['id'],{'outcome':'silent('+silence+')'})
            record('CHAT_SILENT',resident=r['name'],event_id=event['id'],reason=silence)
            return
        t=s.get('telemetry') or {}
        if decision['action'] in ('say','whisper') and not stop_requested(event) and (t.get('parallel_speech') is True or t.get('duo_speech') is True):
            # Words only: the body's speech slot answers while the hunt or walk goes on.
            with legacy_chat_gate(d) as lease:
                if lease:store.finish(event['id'],queue_behavior_chat(d,event,r,c,s,lease));return
                from .continuity import confirm_chat_reply
                skill='whisper' if decision['action']=='whisper' else 'say'
                params={'to_name':event['name'],'text':decision['args']['text']} if skill=='whisper' else {'text':decision['args']['text']}
                task['decision']=decision
                speech_dir=d/'chat-speech';speech_dir.mkdir(mode=0o700,exist_ok=True)  # never the motor's manual_task.json
                store.note_outgoing(r['name'],params['text'],human,time.time())
                try:result=run_native(str(d/'actions.sock.control'),{'skill':skill,'params':params,'timeout':5},task['id']+'-reply',task,speech_dir)
                except (OSError,RuntimeError,ValueError) as error:result='failed('+(str(error) or type(error).__name__)[:40]+')'
                confirm_chat_reply(d,event,decision,result)
                task.update(outcome=result,ended=time.time());store.finish(event['id'],task)
                record('CHAT_REPLY',resident=r['name'],input=event,decision=decision,outcome=result,channel='parallel_speech',seconds=time.time()-event['ts'],by=task['by'])
                return
        # Movement (follow/walk_to/meet), «стой», or a body without a speech slot: the motor is needed.
        marker=d/'CHAT_PENDING';atomic(marker,pending)
        # The same resident writer owns speech and its persisted movement commitment.
        while time.time()<event['ts']+8:
            try:
                with legacy_chat_gate(d) as lease:
                    if lease:
                        store.finish(event['id'],queue_behavior_chat(d,event,r,c,s,lease))
                        return
                    with manual(r,wait_limit=2) as (d,sock):
                        from .continuity import cancel_chat_task,confirm_chat_reply
                        if stop_requested(event):cancel_chat_task(d,event)
                        phrase=decision['args'].get('text',decision.get('say',''))
                        # A whisper is answered by a whisper, also «иду к тебе» to a player far away.
                        skill='whisper' if decision['action']=='whisper' or event.get('type')=='W' else 'say'
                        params={'to_name':event['name'],'text':phrase} if skill=='whisper' else {'text':phrase}
                        task['decision']=decision
                        store.note_outgoing(r['name'],phrase,human,time.time())
                        try:result=run_native(sock,{'skill':skill,'params':params,'timeout':5},task['id']+'-reply',task,d)
                        except (OSError,RuntimeError,ValueError) as error:
                            result='failed('+(str(error) or type(error).__name__)[:40]+')'
                        if result!='done':  # no silent movement after an unspoken answer (manual.lock is ours already)
                            try:
                                if json.loads((d/'chat-goal.json').read_text()).get('id')==task['id']:(d/'chat-goal.json').unlink()
                            except (OSError,ValueError):pass
                        confirm_chat_reply(d,event,decision,result)
                        task.update(outcome=result,ended=time.time());store.finish(event['id'],task)
                        record('CHAT_REPLY',resident=r['name'],input=event,decision=decision,outcome=result,seconds=time.time()-event['ts'],by=task['by'])
                        return
            except BlockingIOError:time.sleep(.1)
        store.finish(event['id'],{'outcome':'failed(WRITER_BUSY)'})
    finally:
        if marker is not None:
            try:
                if json.loads(marker.read_text()).get('id')==event['id']:marker.unlink(missing_ok=True)
            except (OSError,ValueError):pass


def start_listener(r):
    import threading,time,logging,os
    from .supervisor import STATE,config,atomic
    def listen():
        store=ChatStore(STATE/'chat.sqlite');c=config();model=None
        if c.get('llm',{}).get('enabled'):
            try:
                from .mind import Mind
                model=Mind(r,STATE/r['resident_id'],c).respond
            except Exception:pass
        (STATE/r['resident_id']/'CHAT_PENDING').unlink(missing_ok=True)
        atomic(STATE/r['resident_id']/'chat-ready.json',{'pid':os.getpid(),'at':time.time()})
        while not (STATE/'STOP').exists():
            try:
                ingest(store,c)
                event=store.claim(r['name'],time.time())
                if event:handle(event,r,c,store,model)
            except Exception as error:
                logging.getLogger('world').warning('chat listener %s: %s',r['name'],type(error).__name__)
            time.sleep(.2)
    thread=threading.Thread(target=listen,name='chat-'+r['name'],daemon=True);thread.start();return thread


def serve(until=None):
    """until: a threading.Event for tests; the service itself never exits."""
    import time,os,fcntl
    from .supervisor import STATE,config
    os.umask(0o077)
    with (STATE/'chat-service.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        threads=[];paused=False
        while until is None or not until.is_set():
            if (STATE/'STOP').exists():
                # `ro stop`: pause, never exit — an exit loop would exhaust systemd's restart limit
                # and leave the chat dead after `ro start`. Listeners leave their loops by themselves.
                paused=True;time.sleep(1);continue
            if paused:
                for t in threads:t.join(timeout=15)  # let a reply in progress finish
                threads=[];paused=False
            if not threads:threads=[start_listener(r) for r in config()['residents'] if r.get('enabled',True)]
            elif not all(t.is_alive() for t in threads):raise RuntimeError('CHAT_LISTENER_DIED')
            time.sleep(.2)


if __name__=='__main__':serve()
