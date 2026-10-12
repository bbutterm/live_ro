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
    # Unique temp name: brain, chat and gateway threads/processes may write the same file.
    import threading
    tmp=path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False));os.replace(tmp,path)

def chat_pending(d,now=None):
    """A live conversation marker. A stale one (its chat process died, or the reply window
    passed) is removed, so a crash mid-reply can never freeze a resident forever."""
    p=d/'CHAT_PENDING'
    try:raw=p.read_text();m=json.loads(raw)
    except FileNotFoundError:return False
    except (OSError,ValueError):raw=None;m={}
    now=time.time() if now is None else now
    if not isinstance(m,dict):m={}
    deadline=m.get('deadline')
    if not isinstance(deadline,(int,float)) or isinstance(deadline,bool):
        try:deadline=p.stat().st_mtime+60  # an old-format marker: at most a minute
        except OSError:return False
    alive=True
    if type(m.get('pid')) is int:
        try:os.kill(m['pid'],0)
        except ProcessLookupError:alive=False
        except PermissionError:pass
    if alive and now<=deadline:return True
    try:
        if raw is None or p.read_text()==raw:p.unlink()  # never remove a newer marker
    except OSError:pass
    return False

# Host state of one concrete world (who is enabled, duo roles, operator personas) lives on
# that host in STATE/world.local.json, never in git: config/world.yaml is the shared base.
OVERLAY_RESIDENT_KEYS={'enabled':bool,'persona':str,'support_target':str,'duo_partner':str,'party_search_consent':list}
OVERLAY_LLM_KEYS={'enabled':bool,'max_hunt_minutes':int,'min_decision_seconds':(int,float),'min_speech_seconds':(int,float),
                  'short_pause_seconds':(int,float),'chat_calls_per_sender_2min':int,'timeout_seconds':(int,float),'temperature':(int,float)}

def apply_overlay(base,overlay):
    """Merge a host overlay into the base config by an explicit whitelist; ValueError explains a bad overlay."""
    import copy,re
    if not isinstance(overlay,dict) or set(overlay)-{'residents','llm','daily_llm_usd','note'}:raise ValueError('OVERLAY_KEYS')
    c=copy.deepcopy(base);by_id={r['resident_id']:r for r in c['residents']};names={r['name'] for r in c['residents']}
    for rid,fields in (overlay.get('residents') or {}).items():
        if rid not in by_id or not isinstance(fields,dict):raise ValueError('OVERLAY_RESIDENT:'+str(rid)[:40])
        for k,v in fields.items():
            if k not in OVERLAY_RESIDENT_KEYS or type(v) is not OVERLAY_RESIDENT_KEYS[k]:raise ValueError('OVERLAY_FIELD:'+rid+'.'+str(k)[:40])
            if k=='persona' and not 1<=len(v)<=1500:raise ValueError('OVERLAY_PERSONA:'+rid)
            if k in ('support_target','duo_partner') and (v not in names or v==by_id[rid]['name']):raise ValueError('OVERLAY_PARTNER:'+rid)
            if k=='party_search_consent' and (len(v)>8 or not all(isinstance(n,str) and re.fullmatch(r'[\w -]{1,24}',n) for n in v)):raise ValueError('OVERLAY_CONSENT:'+rid)
            by_id[rid][k]=v
    for k,v in (overlay.get('llm') or {}).items():
        if k not in OVERLAY_LLM_KEYS or isinstance(v,bool)!=(OVERLAY_LLM_KEYS[k] is bool) or not isinstance(v,OVERLAY_LLM_KEYS[k]):raise ValueError('OVERLAY_LLM:'+str(k)[:40])
        c.setdefault('llm',{})[k]=v
    if 'daily_llm_usd' in overlay:
        v=overlay['daily_llm_usd']
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not 0<=v<=50:raise ValueError('OVERLAY_DAILY_USD')
        c['daily_llm_usd']=v
    return c

def code_version(root=None):
    """Which code is this: short commit plus '+правки' for local edits (or VERSION of a release dir)."""
    root=pathlib.Path(root or ROOT)
    try:
        sha=subprocess.run(['git','-C',str(root),'rev-parse','--short','HEAD'],capture_output=True,text=True,timeout=5,check=True).stdout.strip()
        dirty=subprocess.run(['git','-C',str(root),'status','--porcelain','--untracked-files=no'],capture_output=True,text=True,timeout=5,check=True).stdout.strip()
        return sha+('+правки' if dirty else '')
    except (OSError,subprocess.SubprocessError):
        try:return (root/'VERSION').read_text().strip()[:40] or 'неизвестно'
        except OSError:return 'неизвестно'

def config():
    """Base config from git plus this host's validated overlay. A broken overlay never stops
    the world: the last valid copy is used and the reason is shown by `ro status`."""
    base=json.loads((ROOT/'config/world.yaml').read_text())
    path=STATE/'world.local.json';good=STATE/'world.local.last-good.json';error=STATE/'world.local.error'
    try:raw=path.read_text()
    except OSError:return base
    try:
        merged=apply_overlay(base,json.loads(raw))
        try:
            if not good.exists() or good.read_text()!=raw:good.write_text(raw)
            error.unlink(missing_ok=True)
        except OSError:pass
        return merged
    except ValueError as e:  # includes JSON errors
        try:error.write_text(str(e)[:200])
        except OSError:pass
        try:return apply_overlay(base,json.loads(good.read_text()))
        except (OSError,ValueError):return base

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
            if chat_pending(d):time.sleep(.1);continue
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
                from packages.resident_world.skills import execute,chat_goal_task
                from packages.resident_world.mind import record
                try:
                    proposal=json.loads(chat_goal.read_text());goal=proposal['decision'];proposal['id']
                    if not (isinstance(goal,dict) and isinstance(goal.get('action'),str) and isinstance(goal.get('args'),dict)):raise TypeError
                except (OSError,ValueError,KeyError,TypeError):chat_goal.unlink(missing_ok=True);continue
                from .continuity import chat_goal_ready,finish_task
                if not chat_goal_ready(d,proposal):
                    finish_task(d,proposal['id'],'timeout')
                    clear_chat_goal(d,proposal['id'])
                    record('CHAT_ACTION_EXPIRED',resident=r['name'],event_id=proposal.get('event_id'))
                    continue
                try:
                    # Own task id: the spoken reply already used proposal['id'] in manual_task.json
                    # (a meet under the same id raised MEETING_ID_CONFLICT forever).
                    result=execute(r['name'],goal['action'],goal['args'],origin='brain',task_id=chat_goal_task(proposal['id']))
                except BlockingIOError:raise
                except Exception as error:result={'id':proposal['id'],'action':goal.get('action'),'outcome':'failed('+(str(error) or type(error).__name__)[:40]+')'}
                finally:
                    if not isinstance(sys.exc_info()[1],BlockingIOError):clear_chat_goal(d,proposal['id'])
                record('CHAT_ACTION_OUTCOME',resident=r['name'],event_id=proposal.get('event_id'),task=result)
                finish_task(d,proposal['id'],result['outcome'])
                if mind is not None:
                    mind.note(goal['action'],result['outcome'],'договорились в разговоре',proposal['id'])
                    # What was agreed changes the situation: think again instead of the pre-chat intent
                    # (after the usual gap: a chatty player must not buy extra paid decisions).
                    mind.path.unlink(missing_ok=True)
                pending=None;retry=time.time()+settle_seconds;continue
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
                # Between two thoughts a healthy mind just lets the body be; a saved intent
                # (interrupted by a conversation) is re-checked and carried out right away.
                if mind.waiting() and not mind.path.exists():time.sleep(.5);continue
                try:proposal=mind.choose(s)
                except Exception as error:  # a brain bug or odd network error must not kill the process
                    logging.getLogger('world').warning('mind.choose %s: %s',r['name'],type(error).__name__)
                    mind.next_call=mind.unavailable_until=time.time()+120;mind.pause_reason='ERROR';proposal=None
                if chat_pending(d) or (d/'chat-goal.json').exists():
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
    version=code_version();log.info('code version %s',version)
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
            atomic(STATE/'identity.json',{'at':time.time(),'pid':os.getpid(),'version':version,'children':{n:{'pid':x['proc'].pid if x['proc'] else None,'restarts':x['policy'].attempts} for n,x in rows.items()}})
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
        if not os.access(interpreter,os.X_OK):
            # The new brain needs only the standard library: run here instead of crash-looping.
            logging.getLogger('world').warning('brain_python %s missing; using %s',interpreter,sys.executable);interpreter=sys.executable
        if pathlib.Path(interpreter).resolve()!=pathlib.Path(sys.executable).resolve():
            return os.execv(interpreter,[interpreter,'-m','packages.resident_world.supervisor','--brain',a.brain])
        r=next(r for r in config()['residents'] if r['resident_id']==a.brain);brain(r)
    else:supervise()
if __name__=='__main__':main()
