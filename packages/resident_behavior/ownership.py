"""Durable bounded per-resident cutover gate. Invalid files never mean legacy."""
import fcntl
import json
import math
import os
from pathlib import Path
import time
import uuid

PHASES={'draining','controlled','rollback','blocked','released'}

def read_lease(directory):
    p=Path(directory)/'behavior-lease.json'
    try:
        with p.open() as stream: value=json.load(stream)
    except FileNotFoundError:
        return None
    except (OSError,ValueError):
        return {'phase':'blocked','invalid':True}
    if not isinstance(value,dict):return {'phase':'blocked','invalid':True}
    a,b=value.get('started_at'),value.get('deadline')
    valid=(isinstance(value.get('token'),str) and bool(value['token'])
           and value.get('phase') in PHASES
           and type(a) in (int,float) and type(b) in (int,float)
           and math.isfinite(a) and math.isfinite(b) and 0<b-a<=1200)
    return value if valid else {'phase':'blocked','invalid':True}

def lease_active(lease):
    return lease is not None and lease.get('phase')!='released'

def dispatch_allowed(lease,token,now):
    return (lease is not None and not lease.get('invalid')
            and lease.get('phase')=='controlled' and lease.get('token')==token
            and type(now) in (int,float) and math.isfinite(now)
            and lease['started_at']<=now<lease['deadline'])

def neutral_speech(lease,text):
    """Legacy chat remains audible but cannot announce an unaccepted movement."""
    if not lease_active(lease):return text
    if text in ('Пойдём, буду рядом.','Встретимся у фонтана.','Иду к фонтану.'):
        return 'Слышу тебя. Пока не обещаю движение.'
    if isinstance(text,str) and text.startswith(('Договорились: встреча ','Предлагаю встречу ')):
        return 'Слышу тебя. Встречу пока не подтверждаю.'
    return text

def _write(directory,value):
    d=Path(directory);p=d/'behavior-lease.json';tmp=d/('behavior-lease.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('x') as stream:
            os.chmod(tmp,0o600);json.dump(value,stream,allow_nan=False);stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,p)
        fd=os.open(d,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:tmp.unlink(missing_ok=True)

def create_lease(directory,*,now=None,duration=900,token=None):
    now=time.time() if now is None else now
    if type(now) not in (int,float) or not math.isfinite(now) or type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=1200:raise ValueError('invalid deadline')
    d=Path(directory);d.mkdir(parents=True,exist_ok=True)
    with (d/'behavior-lease.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if lease_active(read_lease(d)):raise ValueError('active lease cannot be renewed')
        value={'token':token or uuid.uuid4().hex,'started_at':now,'deadline':now+duration,'phase':'draining'}
        if not isinstance(value['token'],str):raise ValueError('invalid owner')
        _write(d,value);return value

def set_phase(directory,phase,token):
    if phase not in PHASES:raise ValueError('invalid phase')
    d=Path(directory)
    with (d/'behavior-lease.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        value=read_lease(d)
        if value is None or value.get('invalid') or value.get('token')!=token:raise ValueError('owner mismatch')
        if value['phase']=='released' and phase!='released':raise ValueError('released lease cannot resume')
        if value['phase'] in ('rollback','blocked') and phase=='controlled':raise ValueError('rollback cannot resume')
        value={**value,'phase':phase};_write(d,value);return value
