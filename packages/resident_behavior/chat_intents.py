"""At-most-once server-heard chat, parallel speech through the existing motor outbox."""
from copy import deepcopy
import hashlib
import math
import re
from .bridge import iso
from .contracts import validate_execute


def enqueue(directory, intent):
    """Fsync an event envelope before acknowledging transfer to the Controller."""
    import json
    import os
    import uuid
    from pathlib import Path
    directory=Path(directory)/'behavior-chat'
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=directory/(intent['id']+'.json')
    tmp=directory/(uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('x') as stream:
            os.chmod(tmp,0o600)
            json.dump(intent,stream,ensure_ascii=False,allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,path)
        fd=os.open(directory,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:tmp.unlink(missing_ok=True)


def movement_allowed(controller, rid, snapshot, mental, action, args):
    from .contracts import validate_action, ContractError
    from .coordinator import coordinate
    from .engine import rank_candidates
    try:
        validate_action(action,args,rid)
    except ContractError:
        return False
    if action not in ('walk_to','follow'):
        return False  # meetings require the separate two-party reservation protocol
    current = snapshot.get('current_action')
    if current and current['result'] is None:
        return False  # never interrupt an existing motor for an incoming request
    if any(p['resident_id']==rid and p['action'] not in ('say','whisper') for p in controller.store.pending_actions()):
        return False
    now = controller.clock().timestamp()
    chosen = dict(action=action,args=args)
    gate = coordinate(controller.config,rid,snapshot,mental,{'primary':chosen,'candidates':[chosen]},now,trigger='chat')
    if gate['source'] != 'fallback':
        return False
    probe = deepcopy(mental)
    if action == 'walk_to':
        if args['place'] not in controller.config['places']:
            return False
        probe['derived'].update(habit_due=0,favorite_place=0,novelty=0,place=args['place'])
    ranked = rank_candidates(controller.config,rid,snapshot,probe,now)
    if any(v['action']==action for v in ranked['vetoes']):
        return False
    if action == 'follow' and args['player'] != mental['derived'].get('player'):
        return False
    return True


def dispatch(controller, rid, intent_id, kind, action, args):
    controller.adapter.refresh(rid)
    snapshot = controller.store.get_snapshot(rid)
    # Consume the latest frame before allocating a normal durable command.
    with controller.adapter.store.transaction() as state:
        snapshot = deepcopy(state['snapshots'][rid])
    now = controller.clock().timestamp()
    mental = controller._consume(rid,snapshot,now,None,plan_habits=False)
    identity = hashlib.sha256(f'{rid}|{intent_id}|{kind}'.encode()).hexdigest()[:32]
    request = validate_execute(dict(version=1,resident_id=rid,seq=snapshot['seq'],server_time=iso(now),
        decision_id='chat-d-'+identity,action_id='chat-a-'+identity,state_seq=snapshot['seq'],
        expires_at=iso(now+30),action=action,args=args,say=None))
    if controller.enabled(rid) is not True or (kind=='movement' and not movement_allowed(controller,rid,snapshot,mental,action,args)):
        return dict(action_id=request['action_id'],accepted=False,error_code='unsafe')
    controller.store.save_state(rid,mental,action=request)
    if not controller.store.claim_action(request['action_id']):
        return dict(action_id=request['action_id'],accepted=False,error_code='transport_timeout')
    try:
        ack = controller.adapter.execute(request)
    except (TimeoutError,ConnectionError,OSError):
        ack = dict(action_id=request['action_id'],accepted=False,error_code='transport_timeout')
    if not ack['accepted'] and ack['error_code'] != 'transport_timeout':
        controller.store.record_outcome(request['action_id'],dict(action_id=request['action_id'],result='failed',
            error_code=ack['error_code'],source_seq=None,completed_at=iso(now)))
    return ack


def handle(controller, rid, intent):
    if rid not in controller.config['residents']:
        raise ValueError('unknown resident')
    event = intent['event']
    if (type(event.get('id')) is not int or event['id'] < 0 or intent.get('id') != 'chat-'+str(event['id'])
            or not isinstance(event.get('name'),str) or not 1<=len(event['name'])<=24
            or not isinstance(event.get('text'),str) or not 1<=len(event['text'])<=4096
            or event.get('type') not in ('O','W') or type(event.get('ts')) not in (int,float)
            or not math.isfinite(event['ts'])):
        raise ValueError('invalid chat intent')
    fresh, receipt = controller.store.claim_chat_intent(rid,intent)
    if not fresh:
        return dict(receipt or {'blocked':'pending_reconciliation'},duplicate=True)
    def finish(result):
        controller.store.finish_chat_intent(rid,intent['id'],result)
        return result
    if not 0<=controller.clock().timestamp()-event['ts']<=10 or controller.enabled(rid) is not True:
        return finish({'blocked':'expired_or_unowned'})
    # Chat consumes observations and consults the same policy, not the autonomous
    # planning tick: no motor cancellation, cadence reset or RNG consumption.
    from .coordinator import coordinate
    controller.adapter.refresh(rid)
    with controller.adapter.store.transaction() as state:
        snapshot=deepcopy(state['snapshots'][rid])
    now=controller.clock().timestamp()
    mental=controller._consume(rid,snapshot,now,None,plan_habits=False)
    plan={'decision':coordinate(controller.config,rid,snapshot,mental,
                              {'primary':None,'candidates':[]},now,trigger='chat')}
    mental['heard'] = dict(event_id='heard:'+intent['id'],server_time=iso(event['ts']),
                          data={'from':event['name'],'text':event['text'],'channel':'whisper' if event['type']=='W' else 'public'})
    controller.store.save_state(rid,mental)
    proposed = intent.get('decision') or {}
    movement_ack = None
    if proposed.get('action') in ('walk_to','follow'):
        movement_ack = dispatch(controller,rid,intent['id'],'movement',proposed['action'],proposed['args'])
    accepted = movement_ack is not None and movement_ack['accepted'] is True
    if accepted:
        phrase = ('Иду к фонтану.' if proposed.get('args',{}).get('place')=='square' else
                  'Пойдём, буду рядом.' if proposed['action']=='follow' else 'Принял маршрут.')
    elif proposed.get('action') == 'meet':
        phrase = 'Встречу пока не подтверждаю. Можем поговорить здесь.'
    elif proposed.get('action') in ('walk_to','follow'):
        phrase = 'Сейчас не могу пойти. Можем поговорить здесь.'
    else:
        phrase = 'Да, я ИИ-житель.' if re.search(r'\b(?:бот|ии|bot|ai)\b',event['text'],re.I) else 'Привет!' if 'привет' in event['text'].lower() else 'Слышу тебя. Пока не обещаю движение.'
    action = 'whisper' if event['type']=='W' else 'say'
    alternative = ('Я принял этот маршрут.' if accepted else
                   'Да, я искусственный житель.' if phrase=='Да, я ИИ-житель.' else
                   'Давай пока поговорим здесь.')
    lines = [{'text':phrase},{'text':alternative}]
    def speech_args(text):
        return {'to':event['name'],'text':text} if action=='whisper' else {'text':text}
    candidates=[dict(action=action,args=speech_args(line['text'])) for line in lines]
    selected=candidates[0]
    reply=None
    if controller.llm is not None and controller.llm_enabled(rid) is True and controller.enabled(rid) is True:
        from .llm import validate_decision
        mental=controller.store.get_state(rid)
        snapshot=controller.store.get_snapshot(rid)
        context=dict(lines=lines,heard=mental.get('heard'),drives=mental.get('needs',{}),mood=mental.get('mood'),
                     goals=mental.get('goals',[]),memory=mental.get('last_actions',[]),relations=mental.get('relations',{}),
                     state={'movement_accepted':accepted,'map':snapshot['map'],'hp':snapshot['hp'],'hp_max':snapshot['hp_max']},
                     sleeping=mental.get('schedule',{}).get('sleeping') is True,safety=plan['decision']['source']=='safety')
        try:
            reply=controller.llm.complete(controller.config['residents'][rid].get('traits',{}),context,
                candidates,candidates[0],True,kind='chat',rid=rid,now=controller.clock().timestamp())
            if reply.get('fallback_reason') is None and controller.llm_enabled(rid) is True:
                selected=validate_decision(reply['decision'],candidates,candidates[0],True,lines,'chat')
        except Exception:
            reply={'fallback_reason':'client_error','request_sent':None}
    # A model wait is not proof that the previously accepted movement is still valid.
    if accepted:
        outcome=controller.adapter.action(movement_ack['action_id'])
        if outcome.get('result') in ('failed','unknown'):
            selected=dict(action=action,args=speech_args('Не могу подтвердить движение. Можем поговорить здесь.'))
        elif outcome.get('result')=='done':
            selected=dict(action=action,args=speech_args('Маршрут выполнен.'))
    args=selected['args']
    if not 0<=controller.clock().timestamp()-event['ts']<=10:
        return finish(dict(blocked='expired',movement_accepted=accepted,movement_ack=movement_ack,llm=reply))
    ack = dispatch(controller,rid,intent['id'],'speech',action,args)
    return finish(dict(mode='controlled',speech_ack=ack,speech=dict(action=action,args=args),
                       arbiter=plan['decision'],movement_accepted=accepted,movement_ack=movement_ack,
                       llm=reply,llm_calls=int(reply is not None and reply.get('request_sent') is True)))
