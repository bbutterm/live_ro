"""Owner CLI for the permanent resident world."""
import json,pathlib,subprocess,sys,time
from .supervisor import STATE,config,online
from packages.body_gateway.cli import request

def describe(activity,reason):
    activities={'walk_to':'идёт к цели','follow':'следует за игроком','meet':'встреча','idle':'ожидание','say':'говорит','whisper':'шёпот','town_service':'снабжение','hunt':'охота','travel_to':'идёт к цели','rest':'отдых','respawn':'возрождение','sell_loot':'продаёт добычу','buy_potions':'покупает зелья'}
    reasons={'HUNT_INTERVAL':'Охочусь на поле','LIFE_HUNT':'Иду на охоту','LIFE_TOWN':'Иду за снабжением','LOW_POTION_STOCK':'Пополняю зелья','SELL_EARNED_LOOT':'Продаю добычу','LIFE_FIELD':'Иду на поле','LIFE_REST':'Возвращаюсь на охоту'}
    russian_reason=reason if any('А'<=c<='я' or c in 'Ёё' for c in reason) else reasons.get(reason,'Выбираю занятие по состоянию')
    return activities.get(activity,'ожидание'),russian_reason

def status_intent(memory,manual_task,manual_live):
    if manual_live and manual_task and not manual_task.get('ended'):return manual_task['action'],'Ручная проверка навыка'
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
    if op!='status':raise SystemExit('Доступно: ro status, ro stop, ro start, ro do.')
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
            print(f"{name}: {'онлайн' if connected else 'нет свежего подтверждения'}; {place}; {activity}; {reason}")
        except (OSError,RuntimeError,ValueError):print(f'{name}: офлайн; тело недоступно')
if __name__=='__main__':main()
