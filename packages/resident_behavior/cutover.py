"""Addressed, drained canary cutover. Never stops the world or chat service."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import time
from .ownership import create_lease, read_lease, set_phase
from packages.body_gateway.cli import request

ROOT=Path(__file__).resolve().parents[2]
STATE=Path('/var/lib/ro-world')

def child_matches(argv,kind,resident):
    rid=resident['resident_id']
    if kind=='body':return 'openkore.pl' in argv and '--control='+resident['profile']+'/control' in argv
    module='packages.resident_world.supervisor' if kind=='brain' else 'packages.body_gateway.service'
    flag='--brain' if kind=='brain' else '--resident'
    return any(argv[i:i+2]==['-m',module] for i in range(len(argv)-1)) and any(argv[i:i+2]==[flag,rid] for i in range(len(argv)-1))

def checked_pid(identity,kind,resident):
    pid=identity['children'][resident['resident_id']+'-'+kind]['pid']
    argv=Path('/proc/'+str(pid)+'/cmdline').read_bytes().decode().split('\0')[:-1]
    fields=Path('/proc/'+str(pid)+'/status').read_text().splitlines()
    parent=int(next(l.split(':')[1] for l in fields if l.startswith('PPid:')))
    if parent!=identity['pid'] or not child_matches(argv,kind,resident):raise RuntimeError('child identity mismatch')
    return pid

def config_actor(rid):
    world=json.loads((ROOT/'config/world.yaml').read_text())
    return next(r for r in world['residents'] if r['resident_id']==rid)

def status(rid,token=None):
    m={'op':'status'}
    if token is not None:m['behavior_owner']=token
    return request(str(STATE/rid/'actions.sock.control'),m)

def enable(rid):
    r=config_actor(rid);d=STATE/rid
    identity=json.loads((STATE/'identity.json').read_text())
    pids={kind:checked_pid(identity,kind,r) for kind in ('brain','gateway','body')}
    if any((d/name).exists() for name in ('CHAT_PENDING','chat-goal.json','BRAIN_HOLD')):raise RuntimeError('resident has pending external writer')
    manual=d/'MANUAL'
    if manual.exists() and json.loads(manual.read_text()).get('pid')!=pids['brain']:raise RuntimeError('external manual writer')
    # Freeze only the idle legacy brain first; its flock keeps chat motor writes
    # out until the new gateway has loaded its ownership fence. Never freeze a body.
    os.kill(pids['brain'],signal.SIGSTOP)
    try:
        s=status(rid)
        if s.get('actions') or (s.get('telemetry') or {}).get('running'):raise RuntimeError('drain required; no action cancelled automatically')
        if s.get('age') is None or s['age']>3:raise RuntimeError('stale body')
        lease=create_lease(d,duration=1200)
        token=lease['token']
        baseline={'at':time.time(),'resident':rid,'identity':identity,'status':s}
        (d/'behavior-cutover-before.json').write_text(json.dumps(baseline,ensure_ascii=False))
        (d/'BEHAVIOR_V1').touch(mode=0o600)
        for kind in ('gateway','body'):
            fresh=json.loads((STATE/'identity.json').read_text())
            if checked_pid(fresh,kind,r)!=pids[kind]:raise RuntimeError('child changed during cutover')
            os.kill(pids[kind],signal.SIGTERM)
        gate_ready=False;gate_end=time.monotonic()+75
        while time.monotonic()<gate_end:
            try:
                check=status(rid,token)
                ownership=check.get('behavior_ownership',check.get('ownership',{}))
                if ownership.get('phase')=='draining' and (check.get('telemetry') or {}).get('behavior_v1') is True and check.get('age',99)<3:
                    gate_ready=True;break
            except (OSError,RuntimeError,ValueError,KeyError):pass
            time.sleep(.5)
        if not gate_ready:
            set_phase(d,'blocked',token);raise RuntimeError('loaded gateway/native fence not proven')
        os.kill(pids['brain'],signal.SIGTERM)
    finally:
        try:os.kill(pids['brain'],signal.SIGCONT)
        except ProcessLookupError:pass
    ready=False;end=time.monotonic()+45
    while time.monotonic()<end:
        try:
            s=status(rid,token);t=s.get('telemetry') or {}
            # This proves the loaded native plugin, not only the marker file.
            if t.get('behavior_v1') is True and s.get('age') is not None and s['age']<3:
                identity_after=json.loads((STATE/'identity.json').read_text())
                if all(checked_pid(identity_after,k,r)!=pids[k] for k in pids):
                    ready=True;break
        except (OSError,RuntimeError,ValueError,KeyError,StopIteration):pass
        time.sleep(.5)
    if not ready:
        set_phase(d,'blocked',token);raise RuntimeError('loaded canary gate not proven; ownership retained, no dispatch')
    # Remove only the departed brain's advisory marker, never a chat/manual writer.
    with (d/'manual.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if manual.exists() and json.loads(manual.read_text()).get('pid')==pids['brain']:
                manual.unlink()
        except BlockingIOError:pass
    set_phase(d,'controlled',token)
    result={'resident':rid,'phase':'controlled','started_at':lease['started_at'],'deadline':lease['deadline'],'world_pid':identity_after['pid'],'body_reconnected':True}
    (d/'behavior-cutover.json').write_text(json.dumps(result))
    return result

def observe(rid,duration):
    d=STATE/rid;lease=read_lease(d)
    if lease is None:raise RuntimeError('no canary')
    before=json.loads((d/'behavior-cutover-before.json').read_text())
    baseline=before['identity'];token=lease['token'];samples=[];errors=[];started=time.time();end=time.monotonic()+duration
    while time.monotonic()<end:
        try:
            s=status(rid,token);identity=json.loads((STATE/'identity.json').read_text());t=s.get('telemetry') or {}
            samples.append({'at':time.time(),'age':s.get('age'),'pos':t.get('pos'),'map':t.get('map'),'loaded':t.get('behavior_v1'),'server_pos':s.get('projection',{}).get('server_pos'),'actions':[{'action_id':a['action_id'],'skill':a['skill'],'state':a['state']} for a in s.get('actions',[])]})
            if identity['pid']!=baseline['pid']:errors.append('world PID changed')
            for name,row in baseline['children'].items():
                if not name.startswith(rid+'-') and identity['children'].get(name,{}).get('pid')!=row['pid']:errors.append('unrelated child changed: '+name)
        except (RuntimeError,OSError,ValueError,KeyError) as exc:errors.append(type(exc).__name__)
        time.sleep(2)
    result={'mode':'live_canary_observation','resident':rid,'started':started,'ended':time.time(),'samples':samples,'errors':sorted(set(errors)),'lease':read_lease(d)}
    (d/'behavior-observation.json').write_text(json.dumps(result,ensure_ascii=False))
    print(json.dumps({'mode':result['mode'],'resident':rid,'samples':len(samples),'errors':result['errors'],'phase':result['lease']['phase']},ensure_ascii=False),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('resident');p.add_argument('--observe',type=float);a=p.parse_args()
    if a.observe is not None:
        if not 0<a.observe<=1200:p.error('observation bounded to 1200 seconds')
        observe(a.resident,a.observe)
    else:print(json.dumps(enable(a.resident)),flush=True)

if __name__=='__main__':main()
