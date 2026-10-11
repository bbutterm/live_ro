"""Permanent native world: bodies, gateways and one brain per resident; no global child-failure shutdown."""
import argparse,fcntl,json,logging,os,pathlib,signal,subprocess,time,sys
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from packages.body_gateway.cli import request
from packages.body_gateway.journal import TERMINAL
from packages.resident_demo.life_cycle import choose, LOOT
ROOT=pathlib.Path(__file__).resolve().parents[2]
STATE=pathlib.Path('/var/lib/ro-world')

@dataclass
class RestartPolicy:
    attempts:int=0
    next_start:float=0
    started:float=0
    def failed(self,now):
        self.attempts+=1;self.next_start=now+min(300,10*2**min(self.attempts-1,5));return self.next_start
    def healthy(self,now):
        if now-self.started>=300:self.attempts=0

def online(status,now):
    p=status.get('projection',{});server=p.get('server_online',{});pos=p.get('server_pos',{})
    return status.get('age') is not None and status['age']<4 and server.get('value') is True and now-pos.get('ts',0)<15

def regeneration_blocked(t):
    return bool(t.get('weight_max',0) and t.get('weight',0)*2>=t['weight_max'])

def fallback(t):
    from packages.resident_world.skills import has_sellable
    if t.get('dead'):return dict(skill='respawn',params={},timeout=60,reason='Возрождаюсь')
    if regeneration_blocked(t) and t.get('hp',0)*100<max(1,t.get('hp_max',0))*80:
        if has_sellable(t):
            return dict(skill='sell_loot',params={},timeout=40,reason='Продаю добычу: перевес') if t.get('map')=='prt_in' else choose('town',t,300)
        return dict(skill='travel_to',params=dict(config()['places']['fountain'],r=3),timeout=240,reason='В безопасный город: регенерация отключена перевесом')
    if t.get('hp',0)*100<max(1,t.get('hp_max',0))*60:
        return dict(skill='rest',params={'hp_pct':85},timeout=240,reason='Восстанавливаю здоровье')
    if t.get('map')=='prt_in':
        if has_sellable(t):return dict(skill='sell_loot',params={},timeout=40,reason='Продаю добычу')
        try:return choose('buy',t,300)
        except RuntimeError as error:
            if str(error)!='INSUFFICIENT_EARNED_ZENY':raise
            return choose('field',t,300)
    if t.get('zeny',0)<30 and not has_sellable(t):
        return choose('hunt',t,300)
    if t.get('inventory',{}).get('501',0)<3 or (has_sellable(t) and t.get('weight',0)>t.get('weight_max',100000)*.6):
        return choose('town',t,300)
    return choose('hunt',t,300)

def atomic(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False));os.replace(tmp,path)

def config():return json.loads((ROOT/'config/world.yaml').read_text())

def clear_chat_goal(d,task_id):
    with (d/'manual.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        p=d/'chat-goal.json'
        if p.exists() and json.loads(p.read_text()).get('id')==task_id:p.unlink()

def brain(r):
    d=STATE/r['resident_id'];sock=str(d/'actions.sock.control');pending=None;retry=0;last=None
    from packages.resident_world.chat import start_listener
    listener=start_listener(r) if config().get('chat_service')!='standalone' and 'name' in r and 'char_id' in r else None
    mind=None;mind_retry=0;settle_seconds=float(config().get('brain_settle_seconds',5))
    try:pending=json.loads((d/'brain.json').read_text()).get('pending')
    except (OSError,ValueError,AttributeError):pending=None
    while not (STATE/'STOP').exists():
        try:
            if (d/'CHAT_PENDING').exists():time.sleep(.1);continue
            # Lazy and retried: inserting the token in .env needs no restart.
            if mind is None and config().get('llm',{}).get('enabled') and 'name' in r and 'char_id' in r and time.time()>=mind_retry:
                try:
                    from packages.resident_world.mind import Mind
                    mind=Mind(r,d,config())
                except Exception as error:
                    mind_retry=time.time()+60
                    logging.getLogger('world').warning('LLM mind unavailable for %s: %s',r['name'],type(error).__name__)
            chat_goal=d/'chat-goal.json'
            if chat_goal.exists():
                from packages.resident_world.skills import execute
                from packages.resident_world.mind import record
                proposal=json.loads(chat_goal.read_text());goal=proposal['decision']
                result=execute(r['name'],goal['action'],goal['args'],origin='brain',task_id=proposal['id'])
                record('CHAT_ACTION_OUTCOME',resident=r['name'],event_id=proposal['event_id'],task=result)
                if mind is not None:mind.note(goal['action'],result['outcome'],'договорились в разговоре',proposal['id'])
                clear_chat_goal(d,proposal['id']);pending=None;retry=time.time()+settle_seconds;continue
            hold=d/'BRAIN_HOLD'
            if hold.exists() and json.loads(hold.read_text()).get('expires',0)>time.time():time.sleep(.2);continue
            lease=d/'MANUAL'
            if lease.exists():
                try:
                    owner=json.loads(lease.read_text())
                    os.kill(int(owner['pid']),0)
                    if owner['expires']>time.time():time.sleep(.2);continue
                except (OSError,ValueError,KeyError,TypeError):pass
            s=request(sock,{'op':'status'})
            if pending:
                a=request(sock,{'op':'action','action_id':pending})
                if a['state'] not in TERMINAL:time.sleep(.5);continue
                last=dict(state=a['state'],code=a['code'],at=time.time());pending=None
                retry=time.time()+settle_seconds
            t=s.get('telemetry')
            if not t or s.get('age') is None or s['age']>3 or time.time()<retry:time.sleep(.5);continue
            if s['actions']:
                pending=s['actions'][0]['action_id'];continue
            if mind is not None and not t.get('dead') and online(s,time.time()):
                # Between two thoughts a healthy mind just lets the body be.
                if mind.waiting():time.sleep(.5);continue
                try:proposal=mind.choose(s)
                except Exception as error:  # a brain bug or odd network error must not kill the process
                    logging.getLogger('world').warning('mind.choose %s: %s',r['name'],type(error).__name__)
                    mind.next_call=mind.unavailable_until=time.time()+120;mind.pause_reason='ERROR';proposal=None
                if (d/'CHAT_PENDING').exists() or (d/'chat-goal.json').exists():
                    # A conversation started meanwhile: the saved intent is re-checked afterwards
                    # (no new paid call unless it no longer fits).
                    time.sleep(.1);continue
                if proposal:
                    from packages.resident_world.skills import execute
                    goal=proposal['decision']
                    atomic(d/'brain.json',dict(pending=None,activity=goal['action'],reason=goal['reason'],thought=proposal.get('thought',''),plan=proposal.get('plan',''),source='llm',decision_id=proposal['id'],at=time.time(),last=last))
                    try:result=execute(r['name'],goal['action'],goal['args'],origin='brain',task_id=proposal['id'],say=goal.get('say',''))
                    except BlockingIOError:raise
                    except Exception as error:  # the model must learn the real reason, not INTERRUPTED
                        result={'id':proposal['id'],'action':goal['action'],'outcome':'failed('+(str(error) or type(error).__name__)[:40]+')'}
                    mind.finished(result);last={'state':result['outcome'],'at':time.time()};retry=time.time()+settle_seconds;continue
                if mind.waiting():time.sleep(.5);continue
            from packages.resident_world.fallback_life import city_goal,completed
            cycle_file=d/'fallback-life.json'
            cycle=json.loads(cycle_file.read_text()) if cycle_file.exists() else {}
            city=city_goal(s,cycle,time.time(),config()) if 'name' in r and 'char_id' in r else None
            if city:
                from packages.resident_world.skills import execute
                atomic(d/'brain.json',dict(pending=None,activity=city['action'],reason=city['reason'],source='fallback_city',at=time.time(),last=last))
                result=execute(r['name'],city['action'],city['args'],origin='brain')
                completed(cycle,city,result['outcome'],time.time());atomic(cycle_file,cycle)
                last={'state':result['outcome'],'at':time.time()};retry=time.time()+settle_seconds;continue
            intent=fallback(t)
            # Serialize dispatch with the manual writer, including the RPC reply.
            with (d/'manual.lock').open('a') as writer:
                fcntl.flock(writer,fcntl.LOCK_EX|fcntl.LOCK_NB)
                a=request(sock,dict(op='run',skill=intent['skill'],params=intent['params'],deadline=intent['timeout']))
                pending=a['action_id'];atomic(d/'brain.json',dict(pending=pending,activity=intent['skill'],reason=intent['reason'],source='fallback',last=last,at=time.time()))
        except (OSError,RuntimeError,ValueError,KeyError,TypeError):
            retry=time.time()+settle_seconds
            time.sleep(.5)

def commands(r):
    rid=r['resident_id'];d=STATE/rid;d.mkdir(mode=0o700,parents=True,exist_ok=True)
    profile=pathlib.Path(r['profile']);sock=d/'actions.sock'
    gateway=['/usr/bin/python3','-m','packages.body_gateway.service','--persistent','--socket',str(sock),'--store',str(d/'journal.sqlite'),'--resident',rid,'--char-id',str(r['char_id']),'--char-name',r['name']]
    body=['/usr/bin/perl','openkore.pl','--control='+str(profile/'control'),'--tables='+str(profile/'tables')+':tables','--fields='+str(profile/'fields'),'--plugins='+str(ROOT/'plugins'),'--logs='+str(d),'--ai=auto']
    env=dict(TERM='dumb',RESIDENT_CHAT_UTF8='1',RESIDENT_ID=rid,RESIDENT_SOCKET=str(sock),RESIDENT_RUN_DIR=str(d))
    return [(rid+'-gateway',gateway,str(ROOT),{}),(rid+'-body',body,'/root/ragnarok/repos/openkore',env),(rid+'-brain',['/usr/bin/python3','-m','packages.resident_world.supervisor','--brain',rid],str(ROOT),{})]

def supervise():
    os.umask(0o077);STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    lock=(STATE/'world.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    log=logging.getLogger('world');log.setLevel(logging.INFO)
    handler=RotatingFileHandler(STATE/'world.log',maxBytes=1048576,backupCount=3);log.addHandler(handler)
    stopping=[]
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stopping.append(True))
    rows={}
    for r in config()['residents']:
        if r.get('enabled',True):
            for name,argv,cwd,env in commands(r):rows[name]=dict(argv=argv,cwd=cwd,env=env,policy=RestartPolicy(),proc=None)
    try:
        while not stopping and not (STATE/'STOP').exists():
            now=time.monotonic()
            for name,x in rows.items():
                p=x['proc'];policy=x['policy']
                if p is not None and p.poll() is not None:
                    x['proc']=None;policy.failed(now);log.warning('%s exited; independent backoff',name)
                    if name.endswith('-gateway'):
                        for suffix in ('',' .control'):
                            path=STATE/name.removesuffix('-gateway')/('actions.sock'+suffix.strip())
                            if path.is_socket():path.unlink()
                elif p is not None:policy.healthy(now)
                if x['proc'] is None and now>=policy.next_start:
                    with (STATE/(name+'.log')).open('ab',buffering=0) as output:
                        x['proc']=subprocess.Popen(x['argv'],cwd=x['cwd'],env=dict(os.environ,**x['env']),stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
                    policy.started=now;log.info('%s started',name)
            atomic(STATE/'identity.json',{'at':time.time(),'pid':os.getpid(),'children':{n:{'pid':x['proc'].pid if x['proc'] else None,'restarts':x['policy'].attempts} for n,x in rows.items()}})
            time.sleep(.2)
    finally:
        for x in rows.values():
            p=x['proc']
            if p is not None:
                try:os.killpg(p.pid,signal.SIGTERM)
                except ProcessLookupError:pass
        end=time.monotonic()+1
        for x in rows.values():
            p=x['proc']
            if p is not None:
                try:p.wait(timeout=max(.01,end-time.monotonic()))
                except subprocess.TimeoutExpired:
                    try:os.killpg(p.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    p.wait()
        atomic(STATE/'identity.json',{'at':time.time(),'stopped':True,'children':{}})

def main():
    p=argparse.ArgumentParser();p.add_argument('--brain');a=p.parse_args()
    if a.brain:
        # Ownership routing MUST precede the legacy virtualenv re-exec.
        from packages.resident_behavior.ownership import read_lease, lease_active
        if lease_active(read_lease(STATE/a.brain)):
            interpreter='/usr/bin/python3'
            if pathlib.Path(interpreter).resolve()!=pathlib.Path(sys.executable).resolve():
                return os.execv(interpreter,[interpreter,'-m','packages.resident_world.supervisor','--brain',a.brain])
            from packages.resident_behavior.live import run
            r=next(r for r in config()['residents'] if r['resident_id']==a.brain)
            return run(r,STATE/a.brain)
        interpreter=config().get('brain_python',sys.executable)
        if pathlib.Path(interpreter).resolve()!=pathlib.Path(sys.executable).resolve():
            return os.execv(interpreter,[interpreter,'-m','packages.resident_world.supervisor','--brain',a.brain])
        r=next(r for r in config()['residents'] if r['resident_id']==a.brain);brain(r)
    else:supervise()
if __name__=='__main__':main()
