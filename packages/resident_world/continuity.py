"""Bounded application-only task state, shared by chat and brain.

Legacy plan strings migrate lazily; no diaries, journals or server facts change.
Task completion is supplied only by existing witness-gated executors.
"""
import contextlib,fcntl,json,os,time,uuid
from typing import Any

TERMINAL={'done','failed','expired','cancelled'}

@contextlib.contextmanager
def state_file(d):
    with (d/'continuity.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        path=d/'plan.json'
        data:dict[str,Any]
        try:data=json.loads(path.read_text())
        except (OSError,ValueError):data={}
        if not isinstance(data,dict):data={'plan':data if isinstance(data,str) else ''}
        original=json.dumps(data,ensure_ascii=False)
        yield data
        if json.dumps(data,ensure_ascii=False)!=original:
            tmp=d/('plan.'+uuid.uuid4().hex+'.tmp')
            tmp.write_text(json.dumps(data,ensure_ascii=False))
            os.replace(tmp,path)

def _task(data,now):
    task=data.get('task')
    if not isinstance(task,dict):
        label=data.get('plan','')
        task={'id':'legacy','label':label,'created':data.get('at',0),'expires':float(data.get('at',0) or 0)+1800,
              'status':'planned' if label else 'done','action':None,'args':{}}
        data.update(version=2,task=task)
    if task.get('status') not in TERMINAL and now>=task.get('expires',0):
        task.update(status='expired',ended=now,outcome='timeout')
    return task

def task_state(d,now=None):
    now=time.time() if now is None else now
    with state_file(d) as data:return dict(_task(data,now))

def past_tasks(d,sender=None):
    with state_file(d) as data:
        return [dict(t) for t in data.get('recent',[])[-12:] if sender is None or t.get('sender') in (None,sender)]

def intent(d):
    """The model's own longer intention ("мой план"). It is not the task lifecycle:
    it survives the end of the current action and changes only when the model says so."""
    with state_file(d) as data:
        plan=data.get('plan','')
        return (plan if isinstance(plan,str) else ''),float(data.get('plan_at',data.get('at',0)) or 0)

def begin_task(d,task_id,decision,label,now=None,sender=None,plan=None):
    now=time.time() if now is None else now
    with state_file(d) as data:
        old=_task(data,now)
        if old.get('id')==task_id:return dict(old)
        if sender is None and old.get('sender') is not None and old.get('status')=='active':return dict(old)
        if old.get('status') not in TERMINAL:old.update(status='cancelled',ended=now,outcome='superseded')
        data['recent']=(data.get('recent',[])+[dict(old)])[-12:]
        duration=min(1800,max(120,int(decision.get('args',{}).get('minutes',25))*60+90))
        task={'id':task_id,'label':label[:200],'created':now,'expires':now+duration,'status':'active',
              'action':decision['action'],'args':decision['args'],'sender':sender}
        data.update(version=2,at=now,task=task)
        if plan:data.update(plan=plan[:200],plan_at=now)  # only the model's own words replace its plan
        return dict(task)

def finish_task(d,task_id,outcome,now=None):
    now=time.time() if now is None else now
    with state_file(d) as data:
        task=_task(data,now)
        if task.get('id')!=task_id or task.get('status') in TERMINAL:return
        status='done' if outcome=='done' else 'expired' if outcome=='timeout' else 'cancelled' if outcome=='cancelled' else 'failed'
        task.update(status=status,outcome=outcome,ended=now)

def confirm_chat_reply(d,event,decision,outcome,now=None):
    now=time.time() if now is None else now
    if outcome!='done' or decision['action'] in ('say','whisper'):return
    from .supervisor import atomic
    key='chat-'+str(event['id'])
    task=begin_task(d,key,decision,decision.get('reason','Договорились в разговоре'),now,event['name'])
    atomic(d/'chat-goal.json',{'id':key,'event_id':event['id'],'decision':decision,'at':now,'expires':task['expires']})

def cancel_chat_task(d,event,now=None):
    now=time.time() if now is None else now
    task=task_state(d,now)
    if task.get('sender') not in (None,event['name']):return False
    finish_task(d,task['id'],'cancelled',now)
    # Called with manual.lock held by the chat writer, after reversible actions stop.
    (d/'chat-goal.json').unlink(missing_ok=True)
    (d/'model-intent.json').unlink(missing_ok=True)
    return True

def chat_goal_ready(d,proposal,now=None):
    now=time.time() if now is None else now
    expires=proposal.get('expires',proposal.get('at',now)+1800)
    task=task_state(d,now)
    return now<expires and not (task.get('id')==proposal['id'] and task.get('status') in TERMINAL)
