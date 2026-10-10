"""Repeatable offline acceptance diagnostics. Scenario evidence is NOT live proof."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from .bridge import iso
from .config import load_behavior, config_hash
from .coordinator import coordinate
from .engine import select
from .flags import enrich
from .reducer import initial_state, advance


def _scenario(config, hours):
    start=datetime(2026,10,10,tzinfo=timezone.utc).timestamp()
    count={rid:0 for rid in config['residents']};motors={rid:set() for rid in count}
    digest=hashlib.sha256();states={};snapshots={};active={};violations=[]
    levels={'resident_a':35,'mira':20,'borin':20};wallet={'resident_a':870,'mira':0,'borin':420}
    bags={'resident_a':{'501':8,'909':2},'mira':{},'borin':{'501':3,'909':3}}
    for rid,resident in config['residents'].items():
        home=config['places'][resident['home_place']]
        snapshots[rid]={'version':1,'resident_id':rid,'seq':0,'server_time':iso(start),
                        'map':home['map'],'x':home['x'],'y':home['y'],'hp':100,'hp_max':100,
                        'sp':10,'sp_max':10,'weight':0,'weight_max':100,'zeny':wallet.get(rid,0),
                        'inventory':deepcopy(bags.get(rid,{})),'base_level':levels.get(rid,1),'job_level':10,
                        'free_skill_points':0,'alive':True,'sitting':False,'online_minutes':0,
                        'current_action':None,'near_players':[],'near_monsters':[],'agreements':[]}
        states[rid]=initial_state(config,rid,snapshots[rid],start)
    for minute in range(int(hours*60)+1):
        now=start+minute*60
        for rid in sorted(snapshots):
            s=snapshots[rid];s.update(server_time=iso(now),seq=minute+1,online_minutes=minute)
            a=active.get(rid)
            if a and now>=a['ends_at']:
                if a['action']=='walk_to':
                    place=config['places'][a['args']['place']];s.update(map=place['map'],x=place['x'],y=place['y'])
                elif a['action']=='hunt':s.update(map='prt_fild08',x=170,y=355)
                elif a['action']=='rest':s.update(sitting=True)
                elif a['action']=='town_service':
                    place=config['places']['kafra'];s.update(map=place['map'],x=place['x'],y=place['y'])
                s['current_action']=dict(action_id=a['action_id'],action=a['action'],args=a['args'],started_at=iso(a['starts_at']),result='done',error_code=None)
                active.pop(rid,None)
            states[rid]=advance(config,rid,states[rid],s,now)
            states[rid]=enrich(config,rid,s,states[rid],{},now)
            if minute%10:continue
            m=states[rid];ranking=select(config,rid,s,m,now);decision=coordinate(config,rid,s,m,ranking,now)
            m['rng_index']=ranking['next_rng_index'];m['decision_index']=m.get('decision_index',0)+1
            count[rid]+=1;motors[rid].add(decision['action'])
            row={'resident_id':rid,'at':iso(now),'decision':decision,'ranking':ranking,'drives':m['drives'],'derived':m['derived']}
            digest.update(json.dumps(row,sort_keys=True,ensure_ascii=False,allow_nan=False).encode())
            if decision['action']=='hunt' and (s['hp']*100/s['hp_max']<55 or s['weight']*100/s['weight_max']>=85 or m['drives']['fear']>=80 or m['derived']['recovery_lock']):violations.append({'rid':rid,'at':now,'reason':'unsafe_hunt'})
            if not decision['continuation']:
                action=decision['action'];args=decision['args'];duration=args.get('minutes',config['actions'][action]['min_minutes'] or 1)*60
                active[rid]=dict(decision,starts_at=now,ends_at=now+duration)
                s['current_action']=dict(action_id=decision['action_id'],action=action,args=args,started_at=iso(now),result=None,error_code=None)
    return {'digest':digest.hexdigest(),'decisions':count,'motor_actions':{rid:sorted(v) for rid,v in motors.items()},'violations':violations}


def replay_gate(config,hours=72):
    if not 0<hours<=168:raise ValueError('bounded replay hours 0..168')
    first=_scenario(config,hours);second=_scenario(config,hours)
    return {'evidence_kind':'generated_replay_scenario','scope':'deterministic_core_only',
            'hours':hours,'deterministic':first==second,'llm_calls':0,'live_motor_commands':0,
            'rng_runtime_requires':'Python 3.12 for formal v1 compatibility','config_hash':config_hash(config),
            'full_v1_acceptance':False,**first}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',default='config/behavior.yaml');p.add_argument('--hours',type=float,default=72);p.add_argument('--output',required=True)
    args=p.parse_args();report=replay_gate(load_behavior(args.config),args.hours)
    Path(args.output).parent.mkdir(parents=True,exist_ok=True);Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))
    return int(not report['deterministic'] or bool(report['violations']))

if __name__=='__main__':raise SystemExit(main())
