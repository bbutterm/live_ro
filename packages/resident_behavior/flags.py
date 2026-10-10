"""Derived behavioral flags, never fabricated HP/inventory/skills."""
from copy import deepcopy
import re
import random
from .coordinator import stamp, at_place, ratio
from .engine import rng_seed


def enrich(config, rid, snapshot, mental, peers, now, *, plan_habits=True):
    state = deepcopy(mental)
    d = state['derived']
    ts = stamp(snapshot.get('server_time'))
    fresh = ts is not None and 0 <= now-ts <= 10
    inventory = snapshot.get('inventory')
    wallet = snapshot.get('zeny')
    weight, maximum = snapshot.get('weight'), snapshot.get('weight_max')
    potions = inventory.get('501', 0) if isinstance(inventory, dict) else None
    keeps = {}
    for goal in state['goals']:
        if goal['type'] == 'item_count':
            key = str(goal['item_id']); keeps[key] = max(keeps.get(key, 0), goal['target'])
    surplus = any(inventory.get(key, 0) > keeps.get(key, 0) for key in ('909','713','938')) if isinstance(inventory, dict) else None
    stock = {'resident_a':8, 'mira':2, 'borin':3}.get(rid,3)
    d['potions'] = potions
    # Funds alone are not an executable purchase: full supplies + no allowed
    # surplus yields an empty plan and must not drive endless shop round trips.
    purchase = None if potions is None or wallet is None else potions < stock and wallet >= 50
    d['service_possible'] = 1 if surplus is True or purchase is True else (None if surplus is None or purchase is None else 0)
    d['service_need'] = None if weight is None or not maximum or potions is None or wallet is None else int(100*weight/maximum >= 75 or (potions < stock and wallet >= 50))
    d['rest_safe'] = None if snapshot.get('near_monsters') is None else int(not snapshot['near_monsters'])
    d.update(help_request=0,chat_due=0,player_valid=0,whisper_target_valid=0,player=None,whisper_target=None,
             speech_cooldown=int(now-state.get('last_speech_at',float('-inf')) < config['speech'].get('sender_cooldown_s',45)))
    heard=state.get('heard') or {}
    when=stamp(heard.get('server_time'))
    data=heard.get('data') or {}
    peer=data.get('from');text=data.get('text','')
    if when is not None and 0<=now-when<=60 and isinstance(peer,str) and 1<=len(peer)<=24:
        own=config['residents'][rid]['display_name']
        private=data.get('channel') in ('whisper','private')
        addressed=private or bool(re.search(r'(?<!\w)'+re.escape(own)+r'(?!\w)',text,re.I))
        if addressed and peer not in (rid, own):
            d['chat_due']=1
            state['relations'].setdefault(peer,deepcopy(config['relationships']['initial']))
            d['whisper_target']=peer;d['whisper_target_valid']=int(private)
            present=fresh and any(p.get('name')==peer and all(type(p.get(k)) in (int,float) for k in ('x','y')) for p in snapshot.get('near_players') or [])
            d['player']=peer;d['player_valid']=int(present)
            d['help_request']=int(present and bool(re.search(r'помоги|помощь|со мной|за мной|пойд[её]м|пойд[её]шь',text,re.I)))
    # peers maps logical ids to {snapshot, derived, schedule}; absent facts veto.
    # An accepted agreement fixes the target, but never invents peer health.
    d.update(resident_valid=0, meet_available=0)
    accepted = []
    for agreement in snapshot.get('agreements') or []:
        participants = agreement.get('participants', [])
        start, end = stamp(agreement.get('accepted_at')), stamp(agreement.get('deadline'))
        if (agreement.get('status') == 'accepted' and len(participants) == 2
                and len(set(participants)) == 2 and rid in participants
                and agreement.get('place') in config['places']
                and start is not None and end is not None and start <= now < end):
            accepted.append(agreement)
    accepted.sort(key=lambda a: (stamp(a['deadline']), a.get('meeting_id', '')))
    d['meet_due'] = int(bool(accepted))
    current = snapshot.get('current_action') or {}
    d['promise_due'] = int(bool(accepted) or bool(state.get('promise_action_id')
        and state['promise_action_id'] == current.get('action_id') and current.get('result') is None))
    if not d['promise_due']:
        state['drives']['duty'] = 0
    target = next((p for p in accepted[0]['participants'] if p != rid), None) if accepted else None
    place = accepted[0]['place'] if accepted else 'square'
    def available(pid):
        observation = peers.get(pid) or {}
        ps = observation.get('snapshot', observation)
        pd = observation.get('derived', {})
        schedule = observation.get('schedule', {})
        time = stamp(ps.get('server_time'))
        sleep_at = stamp(schedule.get('sleep_at'))
        hp, maximum = ps.get('hp'), ps.get('hp_max')
        own_sleep = stamp(state.get('schedule', {}).get('sleep_at'))
        return (time is not None and 0 <= now-time <= 10 and ps.get('alive') is True
                and isinstance(hp, (int, float)) and isinstance(maximum, (int, float))
                and maximum > 0 and 55 <= 100*hp/maximum <= 100
                and pd.get('sleeping') == 0 and sleep_at is not None and sleep_at-now >= 900
                and d.get('sleeping') == 0 and own_sleep is not None and own_sleep-now >= 900
                and not any(a.get('status') == 'accepted' and a.get('meeting_id') not in {x.get('meeting_id') for x in accepted}
                            for a in ps.get('agreements') or []))
    if target is None:
        target = next((pid for pid in sorted(peers) if pid in config['residents'] and pid != rid and available(pid)), None)
    d['resident'] = target
    d['meet_resident'] = target
    d['meet_place'] = place
    if target in config['residents'] and target != rid:
        d['resident_valid'] = int(place in config['places'])
        d['meet_available'] = int(available(target))
        state['relations'].setdefault(target, deepcopy(config['relationships']['initial']))
    resident = config['residents'][rid]
    ts = stamp(snapshot.get('server_time'))
    fresh = ts is not None and 0 <= now-ts <= 10
    visits = state.setdefault('visits', {})
    observed = [pid for pid in sorted(config['places']) if fresh and at_place(config,snapshot,pid) == 1]
    for pid in observed:
        visits[pid] = now  # Observed proximity, not a fabricated walk outcome.
    d['at_home'] = at_place(config,snapshot,resident['home_place']) if fresh else None
    d.update(habit_due=0,habit_place=None,favorite_place=0,favorite_place_id=None,novelty=0,novelty_place=None)
    habits = state.setdefault('habit_state', {})
    if fresh and current.get('action') == 'hunt' and current.get('result') is None and snapshot.get('map') == 'prt_fild08':
        state['habit_last_hunt_at'] = now
    for habit in resident.get('habits', []):
        record = habits.setdefault(habit['id'], {})
        last_hunt = stamp(state.get('habit_last_hunt_at'))
        if record.get('attempted') and last_hunt is not None and now-last_hunt >= 900 and record.get('reset_hunt_at') != last_hunt:
            record['attempted'] = False
            record['reset_hunt_at'] = last_hunt
        if habit['place'] in observed and record.get('pending'):
            record.update(pending=False, completed_at=now)
        if (plan_habits and not record.get('attempted') and fresh and not d.get('sleeping') and not d.get('break_due')
                and not d.get('meet_due') and now-record.get('completed_at',float('-inf')) >= habit['cooldown_minutes']*60):
            index = state.get('rng_index',0)
            seed = rng_seed(rid,now,index,'selection')
            state['rng_index'] = index+1
            record.update(attempted=True,seed=seed,pending=random.Random(seed).random() < habit['probability'])
            if record['pending'] and habit['place'] in observed:
                record.update(pending=False,completed_at=now)
        if record.get('pending') and habit['place'] in config['places'] and habit['place'] not in observed and not d['habit_due']:
            d.update(habit_due=1,habit_place=habit['place'])
    favorites = [pid for pid in resident.get('favorite_places',[]) if pid in config['places'] and pid not in observed
                 and (stamp(visits.get(pid)) is None or now-stamp(visits[pid]) >= 7*86400)]
    if favorites:
        d.update(favorite_place=1,favorite_place_id=min(favorites,key=lambda p:(visits.get(p,float('-inf')),p)))
    novel = [pid for pid in sorted(config['places']) if config['places'][pid].get('kind') != 'home'
             and pid not in observed and (stamp(visits.get(pid)) is None or now-stamp(visits[pid]) >= 7*86400)]
    if novel:
        d.update(novelty=1,novelty_place=novel[0])
    place = ('home' if d.get('sleeping') or d.get('break_due') else
             'meet' if d.get('meet_due') else 'habit' if d['habit_due'] else
             'favorite' if d['favorite_place'] else 'novelty' if d['novelty'] else 'default')
    target = {'home':resident['home_place'],'meet':d.get('meet_place'),'habit':d['habit_place'],
              'favorite':d['favorite_place_id'],'novelty':d['novelty_place'],'default':'square'}[place]
    d['place'] = target
    d['place_valid'] = int(target in config['places'])
    d['already_at_place'] = at_place(config,snapshot,target) if fresh else None
    d['hp_pct'] = ratio(snapshot,'hp')
    d['hp_need'] = None if d['hp_pct'] is None else 100-d['hp_pct']
    return state
