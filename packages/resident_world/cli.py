"""Owner CLI for the permanent resident world."""
import json,pathlib,subprocess,sys,time
from .supervisor import STATE,config,online
from packages.body_gateway.cli import request

def describe(activity,reason):
    activities={'hunt':'охота','travel_to':'идёт к цели','rest':'отдых','respawn':'возрождение','sell_loot':'продаёт добычу','buy_potions':'покупает зелья'}
    reasons={'HUNT_INTERVAL':'Охочусь на поле','LIFE_HUNT':'Иду на охоту','LIFE_TOWN':'Иду за снабжением','LOW_POTION_STOCK':'Пополняю зелья','SELL_EARNED_LOOT':'Продаю добычу','LIFE_FIELD':'Иду на поле','LIFE_REST':'Возвращаюсь на охоту'}
    russian_reason=reason if any('А'<=c<='я' or c in 'Ёё' for c in reason) else reasons.get(reason,'Выбираю занятие по состоянию')
    return activities.get(activity,'ожидание'),russian_reason

def main():
    op=sys.argv[1] if len(sys.argv)>1 else 'status'
    if op=='stop':
        (STATE/'STOP').touch(mode=0o600)
        subprocess.run(['systemctl','stop','ro-world.service'],check=True)
        print('Все жители остановлены.');return
    if op=='start':
        (STATE/'STOP').unlink(missing_ok=True)
        subprocess.run(['systemctl','start','ro-world.service'],check=True);return
    if op!='status':raise SystemExit('Доступно: ro status, ro stop, ro start; навыки появятся в P2.')
    for r in config()['residents']:
        d=STATE/r['resident_id'];name=r['name']
        try:
            s=request(str(d/'actions.sock.control'),{'op':'status'});t=s.get('telemetry') or {}
            memory=json.loads((d/'brain.json').read_text()) if (d/'brain.json').exists() else {}
            connected=online(s,time.time());pos=t.get('pos',{})
            place=f"{t.get('map','—')} ({pos.get('x','—')}, {pos.get('y','—')})"
            activity,reason=describe(memory.get('activity',''),memory.get('reason',''))
            print(f"{name}: {'онлайн' if connected else 'нет свежего подтверждения'}; {place}; {activity}; {reason}")
        except (OSError,RuntimeError,ValueError):print(f'{name}: офлайн; тело недоступно')
if __name__=='__main__':main()
