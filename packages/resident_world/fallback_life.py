"""Durable city visits only when the model has declined a goal; no paid calls."""
from .skills import witness_at,has_sellable
from .supervisor import regeneration_blocked

def city_goal(status,state,now,c):
    t=status.get('telemetry') or {}
    # Nothing the shop buys and still overweight: do not alternate shop/field
    # forever or request rest with regeneration disabled.
    if (not t.get('dead') and (t.get('weight',0)>t.get('weight_max',100000)*.8
        or (regeneration_blocked(t) and t.get('hp',0)*100<max(1,t.get('hp_max',0))*80))
        and not has_sellable(t)):
        point=c.get('places',{}).get('fountain')
        if not point:return None
        if not witness_at(status,point,now):
            return {'action':'walk_to','args':{'place':'fountain'},'reason':'Иду в безопасный город: магазин не снимает этот перевес'}
        return {'action':'idle','args':{'minutes':5},'reason':'Безопасная пауза: перевес без разрешённой добычи для продажи'}
    if (t.get('dead') or t.get('hp',0)*100<max(1,t.get('hp_max',0))*60
        or t.get('map')=='prt_in' or t.get('inventory',{}).get('501',0)<3
        or t.get('weight',0)>t.get('weight_max',100000)*.8):return None
    if now<state.get('next_city_at',0):return None
    point=c.get('places',{}).get('fountain')
    if not point:return None
    if not witness_at(status,point,now):
        return {'action':'walk_to','args':{'place':'fountain'},'reason':'Иду в город передохнуть без модели'}
    if state.get('stage')=='rest':
        return {'action':'idle','args':{'minutes':5},'reason':'Отдыхаю в городе без модели'}
    return {'action':'say','args':{'text':'Заглянул в город передохнуть. Как у вас дела?'},'reason':'Общаюсь в городе без модели'}

def completed(state,goal,outcome,now):
    if outcome=='failed(CHAT_INTERRUPT)':
        state['next_city_at']=now+5
    elif outcome!='done':
        state.clear();state['next_city_at']=now+300
    elif goal['action']=='walk_to':state['stage']='say'
    elif goal['action']=='say':state['stage']='rest'
    elif goal['action']=='idle':
        state.clear();state['next_city_at']=now+900
