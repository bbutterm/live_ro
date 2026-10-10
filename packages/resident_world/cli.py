"""Owner CLI for the permanent resident world."""
import json,pathlib,subprocess,sys,time
from .supervisor import ROOT,STATE,config,online
from packages.body_gateway.cli import request

def describe(activity,reason):
    activities={'walk_to':'идёт к цели','follow':'следует за игроком','meet':'встреча','idle':'ожидание','say':'говорит','whisper':'шёпот','town_service':'снабжение','hunt':'охота','travel_to':'идёт к цели','rest':'отдых','respawn':'возрождение','sell_loot':'продаёт добычу','buy_potions':'покупает зелья'}
    reasons={'HUNT_INTERVAL':'Охочусь на поле','LIFE_HUNT':'Иду на охоту','LIFE_TOWN':'Иду за снабжением','LOW_POTION_STOCK':'Пополняю зелья','SELL_EARNED_LOOT':'Продаю добычу','LIFE_FIELD':'Иду на поле','LIFE_REST':'Возвращаюсь на охоту'}
    russian_reason=reason if any('А'<=c<='я' or c in 'Ёё' for c in reason) else reasons.get(reason,'Выбираю занятие по состоянию')
    return activities.get(activity,'ожидание'),russian_reason

def status_intent(memory,manual_task,manual_live):
    if manual_live and manual_task and not manual_task.get('ended'):
        return manual_task['action'],memory.get('reason','Выбираю занятие') if manual_task.get('origin')=='brain' else 'Ручная проверка навыка'
    return memory.get('activity',''),memory.get('reason','')

def main():
    op=sys.argv[1] if len(sys.argv)>1 else 'status'
    if op=='stop':
        (STATE/'STOP').touch(mode=0o600)
        subprocess.run(['systemctl','stop','ro-world.service'],check=True)
        print('Все жители остановлены.');return
    if op=='start':
        (STATE/'STOP').unlink(missing_ok=True)
        subprocess.run(['systemctl','start','ro-world.service'],check=True);return
    if op=='do':
        from .skills import execute
        if len(sys.argv)!=5:raise SystemExit('Формат: ro do житель action JSON-аргументы')
        try:result=execute(sys.argv[2],sys.argv[3],json.loads(sys.argv[4]))
        except (ValueError,RuntimeError,OSError) as e:raise SystemExit('Навык отклонён: '+str(e))
        print(json.dumps(result,ensure_ascii=False));return
    if op=='doctor':return doctor()
    if op!='status':raise SystemExit('Доступно: ro status, ro doctor, ro stop, ro start, ro do.')
    for r in config()['residents']:
        d=STATE/r['resident_id'];name=r['name']
        try:
            s=request(str(d/'actions.sock.control'),{'op':'status'});t=s.get('telemetry') or {}
            memory=json.loads((d/'brain.json').read_text()) if (d/'brain.json').exists() else {}
            connected=online(s,time.time());pos=t.get('pos',{})
            place=f"{t.get('map','—')} ({pos.get('x','—')}, {pos.get('y','—')})"
            manual_task=json.loads((d/'manual_task.json').read_text()) if (d/'manual_task.json').exists() else {}
            manual_live=False
            if (d/'MANUAL').exists():
                owner=json.loads((d/'MANUAL').read_text())
                try:
                    import os
                    os.kill(int(owner['pid']),0);manual_live=owner['expires']>time.time()
                except ProcessLookupError:pass
            activity,reason=describe(*status_intent(memory,manual_task,manual_live))
            source={'llm':'решил сам (LLM)','fallback':'привычка без LLM','fallback_city':'привычка без LLM'}.get(memory.get('source'),'')
            print(f"{name}: {'онлайн' if connected else 'нет свежего подтверждения'}; {place}; {activity}; {reason}"+(f"; {source}" if source else '')+(f"; думает: {memory['thought']}" if memory.get('thought') else ''))
        except (OSError,RuntimeError,ValueError):print(f'{name}: офлайн; тело недоступно')
    print(brain_line())
REASONS={'NO_TOKEN':'нет токена: впиши RO_WORLD_LLM_API_KEY=... в '+str(ROOT/'.env'),
         'AUTH':'провайдер не принял токен (401/403): проверь ключ в .env',
         'NO_CREDITS':'на счёте провайдера нет денег (402): пополни баланс, мозг сам продолжит в течение 10 минут',
         'RATE_LIMIT':'лимит запросов провайдера (429): мозг ждёт и продолжит сам',
         'DAILY_LIMIT':'дневной лимит daily_llm_usd исчерпан: продолжит после полуночи UTC',
         'OUTAGE':'провайдер недоступен или не ответил вовремя: повтор автоматически',
         'BAD_REQUEST':'провайдер отклонил запрос (модель?): проверь RO_WORLD_LLM_MODEL'}

def brain_line():
    from .llm import Client,LLMUnavailable,load_settings
    c=config()
    try:s=load_settings(ROOT,c);head=f'Мозг: {s.model} через {s.base_url}, токен {s.masked()}'
    except LLMUnavailable:return 'Мозг: '+REASONS['NO_TOKEN']+'; жители живут на привычках.'
    client=Client(ROOT,STATE,c);spent,limit,calls=client.budget();gate=client.gate.state()
    line=f'{head}; сегодня ${spent:.4f} из ${limit} ({calls} вызовов)'
    if gate.get('until',0)>time.time():
        line+='; пауза до '+time.strftime('%H:%M',time.localtime(gate['until']))+': '+REASONS.get(gate.get('reason'),gate.get('reason',''))
    return line

def doctor():
    """One cheap real call: is the token accepted, is there money, does JSON come back."""
    from .llm import Client,LLMUnavailable,load_settings,parse_json_object
    c=config()
    try:s=load_settings(ROOT,c)
    except LLMUnavailable:print('НЕТ: '+REASONS['NO_TOKEN']);return 1
    print(f'Токен {s.masked()}; провайдер {s.base_url}; модель {s.model}; лимит ${s.daily_usd}/сутки')
    client=Client(ROOT,STATE,c);gate=client.gate.state()
    if gate.get('until',0)>time.time():
        if gate.get('reason') in ('RATE_LIMIT','DAILY_LIMIT'):
            print('ЖДЁМ: '+REASONS[gate['reason']]+' (до '+time.strftime('%H:%M',time.localtime(gate['until']))+')');return 2
        client.gate.open()  # the owner is checking a fix right now: try again immediately
    schema={'type':'object','additionalProperties':False,'required':['ok','say'],'properties':{'ok':{'type':'boolean'},'say':{'type':'string'}}}
    try:
        reply=client.complete([{'role':'system','content':'Ответь одним JSON-объектом {"ok": true, "say": "<одно короткое приветствие по-русски>"}.'},
                               {'role':'user','content':'Проверка связи.'}],schema=schema,schema_name='doctor',max_tokens=40,timeout=30)
        answer=parse_json_object(reply.content)
    except LLMUnavailable as e:
        print('НЕТ: '+REASONS.get(e.reason,e.reason)+(f' [{e.status}] {e.detail[:160]}' if e.status or e.detail else ''));return 2
    except ValueError as e:
        print('ОТВЕТ НЕ JSON: модель ответила не по формату ('+str(e)+'). Мозг справится, но лучше выбрать другую модель.');return 3
    print(f"OK: ответ {json.dumps(answer,ensure_ascii=False)[:120]}; режим {reply.mode}; {reply.seconds:.1f} с; ${reply.cost}")
    print(brain_line());return 0

if __name__=='__main__':sys.exit(main())
