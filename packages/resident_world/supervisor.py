"""Permanent native world. No LLM calls in P1; no global child-failure shutdown."""
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

def fallback(t):
    if t.get('dead'):return dict(skill='respawn',params={},timeout=60,reason='Возрождаюсь')
    if t.get('hp',0)*100<max(1,t.get('hp_max',0))*60:
        return dict(skill='rest',params={'hp_pct':85},timeout=240,reason='Восстанавливаю здоровье')
    if t.get('map')=='prt_in':
        try:return choose('sell',t,300)
        except RuntimeError as error:
            if str(error)!='INSUFFICIENT_EARNED_ZENY':raise
            return choose('field',t,300)
    if t.get('zeny',0)<30 and not any(t.get('inventory',{}).get(k,0)>0 for k in LOOT):
        return choose('hunt',t,300)
    if t.get('inventory',{}).get('501',0)<3 or t.get('weight',0)>t.get('weight_max',100000)*.8:
        return choose('town',t,300)
    return choose('hunt',t,300)

def atomic(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False));os.replace(tmp,path)

def config():return json.loads((ROOT/'config/world.yaml').read_text())

def brain(r):
    d=STATE/r['resident_id'];sock=str(d/'actions.sock.control');pending=None;retry=0;last=None
    mind=None
    if config().get('llm',{}).get('enabled') and 'name' in r and 'char_id' in r:
        try:
            from packages.resident_world.mind import Mind
            mind=Mind(r,d,config())
        except Exception:
            logging.getLogger('world').warning('LLM init unavailable; fallback for %s',r['name'])
    if (d/'brain.json').exists():
        old=json.loads((d/'brain.json').read_text());pending=old.get('pending')
    while not (STATE/'STOP').exists():
        try:
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
                retry=time.time()+5
            t=s.get('telemetry')
            if not t or s.get('age') is None or s['age']>3 or time.time()<retry:time.sleep(.5);continue
            if s['actions']:
                pending=s['actions'][0]['action_id'];continue
            if mind is not None and not t.get('dead') and online(s,time.time()):
                proposal=mind.choose(s)
                if proposal:
                    from packages.resident_world.skills import execute
                    goal=proposal['decision']
                    atomic(d/'brain.json',dict(pending=None,activity=goal['action'],reason=goal['reason'],source='llm',decision_id=proposal['id'],at=time.time(),last=last))
                    result=execute(r['name'],goal['action'],goal['args'],origin='brain',task_id=proposal['id'],say=goal.get('say',''))
                    mind.finished(result);last={'state':result['outcome'],'at':time.time()};retry=time.time()+5;continue
            intent=fallback(t)
            # Serialize dispatch with the manual writer, including the RPC reply.
            with (d/'manual.lock').open('a') as writer:
                fcntl.flock(writer,fcntl.LOCK_EX|fcntl.LOCK_NB)
                a=request(sock,dict(op='run',skill=intent['skill'],params=intent['params'],deadline=intent['timeout']))
                pending=a['action_id'];atomic(d/'brain.json',dict(pending=pending,activity=intent['skill'],reason=intent['reason'],last=last,at=time.time()))
        except (OSError,RuntimeError,ValueError,KeyError,TypeError):
            retry=time.time()+5
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
        interpreter=config().get('brain_python',sys.executable)
        if pathlib.Path(interpreter).resolve()!=pathlib.Path(sys.executable).resolve():
            return os.execv(interpreter,[interpreter,'-m','packages.resident_world.supervisor','--brain',a.brain])
        r=next(r for r in config()['residents'] if r['resident_id']==a.brain);brain(r)
    else:supervise()
if __name__=='__main__':main()
