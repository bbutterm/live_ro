"""Native-only autonomous activities; no model, shell or database capabilities."""
from typing import Any
LOOT = {'909','914','705','935','919','908','916'}
FIELD = 'prt_fild08'

def choose(phase, telemetry, hunt_seconds=180) -> dict[str, Any]:
    def travel(map_name,x,y,next_phase):
        return dict(skill='travel_to',params=dict(map=map_name,x=x,y=y,r=3),
                    timeout=240,next_phase=next_phase,reason='LIFE_'+phase.upper())
    if (type(telemetry.get('hp')) is not int or type(telemetry.get('hp_max')) is not int
            or not 0<=telemetry['hp']<=telemetry['hp_max'] or telemetry['hp_max']<=0
            or not isinstance(telemetry.get('inventory'),dict) or not isinstance(telemetry.get('map'),str)):
        raise ValueError('INVALID_TELEMETRY')
    if telemetry.get('dead'):
        return dict(skill='respawn',params={},timeout=60,next_phase='town',reason='DEATH')
    if phase in ('field','hunt') and telemetry['hp']*100<telemetry['hp_max']*80:
        return travel('prt_in',126,76,'rest')
    if phase=='hunt' and telemetry['map']!=FIELD:
        return travel(FIELD,170,240,'hunt')
    if phase in ('field','return'):
        return travel(FIELD,170,240,'hunt')
    if phase=='hunt':
        return dict(skill='hunt',params=dict(map=FIELD,duration=hunt_seconds,min_kills=1),
                    timeout=hunt_seconds+40,next_phase='town',reason='HUNT_INTERVAL')
    if phase=='town':return travel('prt_in',126,76,'sell')
    if phase=='sell':
        if any(telemetry['inventory'].get(k,0)>0 for k in LOOT):
            return dict(skill='sell_loot',params={},timeout=40,next_phase='buy',reason='SELL_EARNED_LOOT')
        return choose('buy',telemetry,hunt_seconds)
    if phase=='buy':
        stock=telemetry['inventory'].get('501',0)
        if stock<17:
            goal=min(20,stock+int(telemetry['zeny']//10))
            if goal<=stock or goal<3:raise RuntimeError('INSUFFICIENT_EARNED_ZENY')
            return dict(skill='buy_potions',params=dict(item_id=501,stock_goal=goal,max_zeny=300),
                        timeout=40,next_phase='rest',reason='LOW_POTION_STOCK')
        return choose('rest',telemetry,hunt_seconds)
    if phase=='rest':
        if telemetry['hp']*100<telemetry['hp_max']*80:
            return dict(skill='rest',params=dict(hp_pct=80),timeout=240,next_phase='return',reason='LOW_HP')
        return travel(FIELD,170,240,'hunt')
    raise ValueError('UNKNOWN_LIFE_PHASE')


class LifeCycle:
    """Caller holds the canonical writer lock; each tick executes at most one RPC intent."""
    def __init__(self,path,deadline,hunt_seconds=180):
        import pathlib,json,math
        self.path=pathlib.Path(path)
        if not math.isfinite(deadline) or not 30<=hunt_seconds<=900:raise ValueError('LIFE_CONFIG')
        self.deadline=deadline
        self.hunt_seconds=hunt_seconds
        self.state: dict[str, Any]
        marker=self.path.with_suffix('.initialized')
        if marker.exists() and not self.path.exists():raise RuntimeError('LIFE_STATE_MISSING')
        if self.path.exists():
            self.state=json.loads(self.path.read_text())
            if (self.state.get('version')!=1 or self.state.get('resident')!='resident_a'
                    or self.state.get('hunt_seconds')!=hunt_seconds
                    or self.state.get('phase') not in ('field','hunt','town','sell','buy','rest','return')
                    or type(self.state.get('cycles')) is not int or self.state['cycles']<0):
                raise RuntimeError('LIFE_STATE_IDENTITY')
            pending=self.state.get('pending')
            if pending is not None and (not isinstance(pending,dict)
                    or type(pending.get('expires_at')) not in (int,float)
                    or not math.isfinite(pending['expires_at']) or pending['expires_at']<0):
                raise RuntimeError('LIFE_PENDING_EXPIRY')
            if 'reaction' in self.state:
                reaction=self.state['reaction']
                if (not isinstance(reaction,dict)
                        or set(reaction)!={'action_id','failed_map','blocked_until','expires_at'}
                        or not isinstance(reaction['action_id'],str) or not reaction['action_id']
                        or not isinstance(reaction['failed_map'],str) or not reaction['failed_map']
                        or type(reaction['blocked_until']) not in (float,int)
                        or not math.isfinite(reaction['blocked_until']) or reaction['blocked_until']<0
                        or type(reaction['expires_at']) not in (float,int)
                        or not math.isfinite(reaction['expires_at']) or reaction['expires_at']<0):
                    raise RuntimeError('LIFE_REACTION_SCHEMA')
            if 'stuck_recovery' in self.state:
                recovery=self.state['stuck_recovery']
                if (not isinstance(recovery,dict)
                        or set(recovery)!={'action_id','map','x','y','expires_at','recovered'}
                        or not isinstance(recovery['action_id'],str) or not recovery['action_id']
                        or recovery['map']!=FIELD
                        or any(type(recovery[axis]) is not int or not 0<=recovery[axis]<=2048 for axis in ('x','y'))
                        or type(recovery['expires_at']) not in (int,float)
                        or not math.isfinite(recovery['expires_at']) or recovery['expires_at']<0
                        or type(recovery['recovered']) is not bool):
                    raise RuntimeError('LIFE_STUCK_RECOVERY_SCHEMA')
            if 'death_recovery' in self.state:
                recovery=self.state['death_recovery']
                if (not isinstance(recovery,dict)
                        or set(recovery)!={'action_id','expires_at','recovered'}
                        or not isinstance(recovery['action_id'],str) or not recovery['action_id']
                        or type(recovery['expires_at']) not in (int,float)
                        or not math.isfinite(recovery['expires_at']) or recovery['expires_at']<0
                        or type(recovery['recovered']) is not bool):
                    raise RuntimeError('LIFE_DEATH_RECOVERY_SCHEMA')
        else:
            self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            import os
            fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w') as f:f.write('resident_a\n');f.flush();os.fsync(f.fileno())
            self.state=dict(version=1,resident='resident_a',phase='field',cycles=0,
                            hunt_seconds=hunt_seconds,pending=None,history=[],blocked=None,has_hunted=False)
            self.save()

    def save(self):
        import tempfile,json,os
        fd,name=tempfile.mkstemp(prefix='.life-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'w') as f:
                json.dump(self.state,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
            os.replace(name,self.path)
            directory=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            if os.path.exists(name):os.unlink(name)

    def tick(self,rpc,now):
        import uuid
        from packages.body_gateway.journal import TERMINAL
        if self.state['blocked']:raise RuntimeError(self.state['blocked'])
        if now>=self.deadline:raise RuntimeError('LIFE_DEADLINE')
        status=rpc({'op':'status'})
        import math
        age=status.get('age')
        if not status.get('telemetry') or type(age) not in (float,int) or not math.isfinite(age) or not 0<=age<=3:
            raise RuntimeError('STALE_BODY')
        pending=self.state['pending']
        if pending is None:
            if status.get('actions'):raise RuntimeError('OTHER_ACTIVE_ACTION')
            intent: dict[str,Any]=choose(self.state['phase'],status['telemetry'],self.hunt_seconds)
            reaction=self.state.get('reaction')
            recovery_expiry=None
            death_recovery=self.state.get('death_recovery')
            if death_recovery and not death_recovery['recovered']:
                recovery_expiry=death_recovery['expires_at']
                if now>=recovery_expiry:raise RuntimeError('LIFE_DEATH_RECOVERY_EXPIRED')
                if intent['skill']!='respawn':raise RuntimeError('LIFE_DEATH_DIVERGENCE')
            stuck_recovery=self.state.get('stuck_recovery')
            if stuck_recovery and not stuck_recovery['recovered']:
                recovery_expiry=stuck_recovery['expires_at']
                if now>=recovery_expiry:raise RuntimeError('LIFE_STUCK_RECOVERY_EXPIRED')
                t=status['telemetry'];pos=t.get('pos',{})
                projection=status.get('projection',{})
                server_pos=projection.get('server_pos',{})
                server_map=projection.get('server_map',{})
                witness_pos=server_pos.get('value',{})
                if (not isinstance(witness_pos,dict) or not isinstance(pos,dict)
                        or any(type(pos.get(axis)) is not int or type(witness_pos.get(axis)) is not int
                            or abs(pos[axis]-witness_pos[axis])>3 for axis in ('x','y'))
                        or server_pos.get('source')!='witness' or server_map.get('source')!='witness'
                        or server_map.get('value')!=FIELD
                        or any(type(item.get('ts')) not in (int,float) or not math.isfinite(item['ts'])
                            or not 0<=now-item['ts']<=3 for item in (server_pos,server_map))
                        or projection.get('server_dead',{}).get('value') is not False):
                    raise RuntimeError('LIFE_STUCK_DIVERGENCE')
                if (t.get('dead') is not False or t.get('map')!=stuck_recovery['map']
                        or type(t.get('hp')) is not int or type(t.get('hp_max')) is not int
                        or t['hp_max']<=0 or t['hp']*100<t['hp_max']*80
                        or not isinstance(t.get('inventory'),dict)
                        or type(t['inventory'].get('501')) is not int or t['inventory']['501']<17
                        or not isinstance(pos,dict) or type(pos.get('x')) is not int
                        or type(pos.get('y')) is not int
                        or abs(pos['x']-stuck_recovery['x'])!=4 or pos['y']!=stuck_recovery['y']):
                    raise RuntimeError('LIFE_STUCK_DIVERGENCE')
                intent=dict(skill='travel_to',params=dict(map=stuck_recovery['map'],
                    x=stuck_recovery['x'],y=stuck_recovery['y'],r=0),timeout=30,
                    next_phase='town',reason='STUCK_LOCAL_STEP')
            if reaction and self.state['phase']=='hunt':
                recovery_expiry=reaction['expires_at']
                if now>=recovery_expiry:raise RuntimeError('LIFE_RECOVERY_EXPIRED')
            if (reaction and intent['skill']=='travel_to'
                    and intent['params']['map']==reaction['failed_map']
                    and now<reaction['blocked_until']):
                raise RuntimeError('LIFE_MAP_QUARANTINED')
            if now+intent['timeout']+10>self.deadline:
                return dict(action_id=None,state='drained',code='LIFE_WINDOW_EXHAUSTED')
            pending=dict(intent,idem=str(uuid.uuid4()),expires_at=min(now+intent['timeout'],recovery_expiry) if recovery_expiry is not None else now+intent['timeout'],
                         action_id=None,from_phase=self.state['phase'])
            self.state['pending']=pending
            self.save()  # persist BEFORE the first possibly side-effecting request
        if now>=pending['expires_at']:
            # Expiry never authorizes dispatch/cancel. Read the original journal only.
            action=rpc({'op':'action','action_id':pending['action_id']}) if pending['action_id'] else None
            if (not action or action.get('state')!='confirmed'
                    or action.get('action_id')!=pending['action_id']
                    or action.get('resident')!='resident_a' or action.get('skill')!=pending['skill']
                    or action.get('idem_key')!=pending['idem']
                    or type(action.get('created')) not in (int,float)
                    or not math.isfinite(action['created']) or action['created']<0
                    or type(action.get('updated')) not in (int,float)
                    or not math.isfinite(action['updated'])
                    or not action['created']<=action['updated']<=pending['expires_at']):
                self.state['blocked']='NATIVE_INTENT_EXPIRED';self.save()
                raise RuntimeError(self.state['blocked'])
        elif pending['action_id']:
            action=rpc({'op':'action','action_id':pending['action_id']})
        else:
            action=rpc(dict(op='run',caller='autonomous_life_cycle',skill=pending['skill'],
                            params=pending['params'],idem=pending['idem'],
                            deadline=min(pending['timeout'],pending['expires_at']-now)))
            pending['action_id']=action['action_id'];self.save()
        if action['state'] not in TERMINAL:return action
        if action['state']!='confirmed':
            t=status['telemetry']
            death=status.get('projection',{}).get('server_dead',{})
            if (action['state']=='failed' and action.get('code')=='DIED'
                    and pending['skill'] in ('hunt','travel_to','rest')
                    and not self.state.get('death_recovery') and not status.get('actions')
                    and t.get('dead') is True and type(t.get('hp')) is int and t['hp']==0
                    and type(t.get('hp_max')) is int and t['hp_max']>0
                    and death.get('value') is True and death.get('source')=='witness'
                    and type(death.get('ts')) in (int,float) and math.isfinite(death['ts'])
                    and type(action.get('created')) in (int,float) and math.isfinite(action['created'])
                    and action['created']<=death['ts']<=now+2 and now+70<=self.deadline):
                self.state['death_recovery']=dict(action_id=action['action_id'],expires_at=now+60,recovered=False)
                self.state['history'].append(dict(action_id=action['action_id'],skill=pending['skill'],
                    code='DIED',at=now,reason='WITNESSED_DEATH_RECOVERY'))
                self.state['history']=self.state['history'][-200:]
                self.state['pending']=None
                self.state['phase']='town'
                self.state['has_hunted']=False
                self.save()  # death consumes the old intent; respawn is a later durable intent
                return action
            if (action['state']=='failed' and action.get('code')=='NO_ROUTE'
                    and pending['skill']=='travel_to' and not self.state.get('reaction')
                    and not self.state.get('stuck_recovery')
                    and not status.get('actions') and t.get('dead') is False
                    and t.get('map')==FIELD and type(t.get('hp')) is int
                    and type(t.get('hp_max')) is int and t['hp_max']>0
                    and t['hp']*100>=t['hp_max']*80
                    and isinstance(t.get('inventory'),dict)
                    and type(t['inventory'].get('501')) is int and t['inventory']['501']>=17
                    and now+self.hunt_seconds+50<=self.deadline):
                self.state['reaction']=dict(action_id=action['action_id'],
                    failed_map=pending['params']['map'],blocked_until=now+7200,expires_at=now+self.hunt_seconds+40)
                self.state['history'].append(dict(action_id=action['action_id'],
                    skill=pending['skill'],code='NO_ROUTE',at=now,reason='NO_ROUTE_ALTERNATIVE'))
                self.state['history']=self.state['history'][-200:]
                self.state['pending']=None
                self.state['phase']='hunt'
                self.state['has_hunted']=False
                self.save()  # consume the failure before a later tick can dispatch recovery
                return action
            projection=status.get('projection',{})
            server_pos=projection.get('server_pos',{})
            server_map=projection.get('server_map',{})
            pos=t.get('pos',{})
            witness_pos=server_pos.get('value',{})
            if (action['state']=='failed' and action.get('code')=='STUCK'
                    and pending['skill']=='travel_to' and not self.state.get('stuck_recovery')
                    and not self.state.get('reaction') and not self.state.get('death_recovery')
                    and not status.get('actions') and t.get('dead') is False and t.get('map')==FIELD
                    and type(t.get('hp')) is int and type(t.get('hp_max')) is int and t['hp_max']>0
                    and t['hp']*100>=t['hp_max']*80 and isinstance(t.get('inventory'),dict)
                    and type(t['inventory'].get('501')) is int and t['inventory']['501']>=17
                    and isinstance(pos,dict) and isinstance(witness_pos,dict)
                    and all(type(pos.get(axis)) is int and type(witness_pos.get(axis)) is int
                        and 0<=pos[axis]<=2048 and abs(pos[axis]-witness_pos[axis])<=3 for axis in ('x','y'))
                    and server_pos.get('source')=='witness' and server_map.get('source')=='witness'
                    and server_map.get('value')==FIELD
                    and all(type(item.get('ts')) in (int,float) and math.isfinite(item['ts'])
                        and 0<=now-item['ts']<=3 for item in (server_pos,server_map))
                    and projection.get('server_dead',{}).get('value') is False
                    and now+40<=self.deadline):
                self.state['stuck_recovery']=dict(action_id=action['action_id'],map=FIELD,
                    x=pos['x']+4 if pos['x']<=2044 else pos['x']-4,y=pos['y'],expires_at=now+30,recovered=False)
                self.state['history'].append(dict(action_id=action['action_id'],skill=pending['skill'],
                    code='STUCK',at=now,reason='STUCK_LOCAL_STEP'))
                self.state['history']=self.state['history'][-200:]
                self.state['pending']=None
                self.state['phase']='town'
                self.state['has_hunted']=False
                self.save()  # consume before the single native walkability-checked local step
                return action
            self.state['blocked']='NATIVE_FAILURE:'+str(action.get('code'));self.save()
            raise RuntimeError(self.state['blocked'])
        if pending['reason']=='STUCK_LOCAL_STEP' and self.state.get('stuck_recovery'):
            self.state['stuck_recovery']['recovered']=True
        if pending['skill']=='respawn' and self.state.get('death_recovery'):
            self.state['death_recovery']['recovered']=True
        self.state['history'].append(dict(action_id=action['action_id'],skill=pending['skill'],
                code=action.get('code'),at=now,reason=pending['reason']))
        self.state['history']=self.state['history'][-200:]
        self.state['phase']=pending['next_phase']
        if pending['skill']=='hunt':self.state['has_hunted']=True
        if pending['next_phase']=='hunt' and pending['from_phase'] in ('sell','buy','rest','return') and self.state.get('has_hunted'):
            self.state['cycles']+=1
            self.state['has_hunted']=False
        self.state['pending']=None;self.save()
        return action


def main():
    import argparse,pathlib,time,os,fcntl,json,signal
    from packages.body_gateway.cli import request
    p=argparse.ArgumentParser()
    p.add_argument('--execute',action='store_true',required=True)
    p.add_argument('--state',required=True)
    p.add_argument('--socket',required=True)
    p.add_argument('--duration',type=int,default=3600)
    p.add_argument('--deadline',type=float,help='Original absolute UTC lease expiry (Unix seconds)')
    p.add_argument('--hunt-seconds',type=int,default=180)
    p.add_argument('--writer-lock',default='/root/ragnarok/run/private/night-budget.json.lock')
    a=p.parse_args()
    if not 300<=a.duration<=21600 or not 30<=a.hunt_seconds<=900:p.error('bounded duration/hunt required')
    if a.deadline is not None:
        import math
        if not math.isfinite(a.deadline) or not 0<a.deadline-time.time()<=a.duration:
            p.error('absolute deadline must be future and within bounded duration')
    os.umask(0o077)
    lease=open(a.writer_lock,'a');os.chmod(a.writer_lock,0o600)
    fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    stopping=[]
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stopping.append(True))
    def rpc(msg):return request(a.socket,msg)
    deadline=a.deadline if a.deadline is not None else time.time()+a.duration
    try:
        ready=time.monotonic()+45
        while time.monotonic()<ready and not stopping:
            try:
                s=rpc({'op':'status'})
                if s.get('telemetry') and s.get('age',99)<=3:break
            except (OSError,RuntimeError):pass
            time.sleep(.5)
        life=LifeCycle(a.state,deadline,a.hunt_seconds)
        previous=None
        while time.time()<deadline and not stopping and not pathlib.Path(a.state).parent.joinpath('ESTOP').exists():
            action=life.tick(rpc,time.time())
            key=(action['action_id'],action['state'],life.state['cycles'])
            if key!=previous:
                print(json.dumps(dict(at=time.time(),phase=life.state['phase'],cycles=life.state['cycles'],
                                     action_id=action['action_id'],state=action['state'],code=action.get('code'))),flush=True)
                previous=key
            if action['state']=='drained':break
            time.sleep(.5)
    finally:
        try:rpc({'op':'safe_stop'})
        except (OSError,RuntimeError):pass
        lease.close()

if __name__=='__main__':main()

