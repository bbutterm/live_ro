"""LLM chooses a goal; trusted code compiles a feasible native plan, never shell."""
from packages.resident_demo.life_cycle import FIELD,LOOT

def decision_context(t,previous)->dict:
    available=options(t)
    state={k:t.get(k) for k in ('map','pos','dead','hp','hp_max','zeny')}
    state['resources']={'red_potions':t['inventory'].get('501',0),'blue_potions':t['inventory'].get('505',0)}
    state['items']=[dict(item_id=k,count=v) for k,v in sorted(t['inventory'].items())]
    feedback=[dict(goal=e.get('decision',{}).get('goal'),outcomes=[{k:o.get(k) for k in ('skill','state','code')} for o in e.get('outcomes',[])]) for e in previous[-8:]]
    last=next((e for e in reversed(feedback) if e['goal']!='wait'),None)
    if last and last['goal']=='hunt' and any(o['state']=='failed' and o['code']=='NO_TARGET' for o in last['outcomes']):available.pop('hunt',None)
    return dict(self=state,available_goals=available,previous_results=feedback,requirements={'hunt':{'min_hp_percent':80,'min_red_potions':3,'blue_potions_required':False,'mechanic':'Обычная ближняя атака; отсутствие синих зелий не мешает охоте'}})

def options(t):
    if (t.get('dead') is not False or any(type(t.get(k)) is not int for k in ('hp','hp_max','zeny'))
            or not 0<t['hp']<=t['hp_max'] or t['zeny']<0 or not isinstance(t.get('inventory'),dict)
            or any(type(n) is not int or n<0 for n in t['inventory'].values())
            or t.get('map') not in (FIELD,'prontera','prt_in')):raise ValueError('INVALID_DECISION_STATE')
    choices={'wait':'Подождать; только если другие занятия сейчас неуместны'}
    if t['map']==FIELD:choices['return_town']='Вернуться в город и сменить занятие'
    if t['hp']*100>=t['hp_max']*80 and t['inventory'].get('501',0)>=3:choices['hunt']='Охотиться на доступных Poring ради добычи'
    if t['hp']*100<t['hp_max']*80:choices['rest']='Восстановить здоровье в безопасном месте'
    if t['inventory'].get('501',0)<17 and t['zeny']>=30:choices['buy_supplies']='Купить недостающие зелья за собственные деньги'
    if any(t['inventory'].get(k,0)>0 for k in LOOT):choices['sell_loot']='Продать добытый обычный лут'
    return choices

def plan(decision,t,previous=()):
    available=decision_context(t,previous)['available_goals']
    if (not isinstance(decision,dict) or set(decision)!={'goal','reason'}
            or not isinstance(decision['goal'],str) or decision['goal'] not in available
            or not isinstance(decision['reason'],str) or not 1<=len(decision['reason'])<=300
            or any(ord(c)<32 for c in decision['reason'])):raise ValueError('INVALID_MODEL_DECISION')
    goal=decision['goal']
    def travel(map_name,x,y):return dict(skill='travel_to',params=dict(map=map_name,x=x,y=y,r=3),timeout=240)
    if goal=='wait':return []
    if goal=='return_town':return [travel('prontera',150,150)]
    if goal=='hunt':
        approach=[] if t['map']==FIELD else [travel(FIELD,170,240)]
        return approach+[dict(skill='hunt',params=dict(map=FIELD,duration=60,min_kills=1),timeout=100)]
    approach=[travel('prt_in',126,76)]
    if goal=='rest':return approach+[dict(skill='rest',params=dict(hp_pct=80),timeout=240)]
    if goal=='sell_loot':return approach+[dict(skill='sell_loot',params={},timeout=40)]
    stock=t['inventory'].get('501',0);goal_stock=min(20,stock+min(t['zeny'],300)//10)
    return approach+[dict(skill='buy_potions',params=dict(item_id=501,stock_goal=goal_stock,max_zeny=300),timeout=40)]
