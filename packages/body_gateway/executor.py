"""Bounded skill executor: body ACK never counts as world evidence."""
import json,time,uuid,math
from .journal import TERMINAL
class Executor:
 def __init__(self,journal,resident,char_id,send):
  self.j=journal;self.resident=resident;self.char_id=char_id;self.send=send;self.epoch=None;self.telemetry=None;self.received=0;self.projection={}
  self.support_seen={}
  for row in self.j.db.execute("SELECT payload FROM events WHERE source='witness' AND ts>=? ORDER BY ts",(time.time()-7200,)):  # recent facts only
   self.witness(json.loads(row[0]),persist=False)
  for a in self.active():
   if a['state']=='planned':self.j.transition(a['action_id'],'rejected_local','RECOVERY_UNDISPATCHED',{'reason':'plan never dispatched; no replay'})
   elif a['state']!='unknown':self.j.transition(a['action_id'],'unknown','COORDINATOR_RESTART',{'reason':'no replay of dispatched action'})
 def active(self):return self.j.open_actions(self.resident)
 def body(self,m):
  kind=m['type'];now=time.time()
  if kind=='hello':
   epoch=m['body_epoch']
   for a in self.active():
    if a['epoch']!=epoch and a['state']!='unknown':self.j.transition(a['action_id'],'unknown','BODY_RESTARTED',{})
   self.epoch=epoch
   for item in m.get('ledger',[]):
    r=item.get('result',{})
    if r.get('outcome') in ('done','error','cancelled'):
     try:self.body(dict(r,type='skill_result'))
     except ValueError:pass
  elif kind=='telemetry':
   self.telemetry=m;self.received=now
   if m.get('cooperative_support') is True:
    for a in self.active():
     p=json.loads(a['params']);party=m.get('party') or {}
     if a['skill']=='hunt' and p.get('support_target') in {u.get('name') for u in party.get('members',[])} and party.get('share')==1 and (m.get('running') or {}).get('action_id')==a['action_id']:
      self.support_seen[a['action_id']]={'body_epoch':m.get('body_epoch'),'ts':m['ts'],'party':party}
   for field in ('map','pos','dead','hp','sp','zeny','weight','mode','running'):
    self.projection[field]={'value':m.get(field),'source':'body','ts':m['ts']}
  elif kind in ('skill_accepted','skill_progress','skill_result','skill_rejected'):
   try:a=self.j.action(m.get('action_id',''))
   except ValueError:return
   if a['state'] in TERMINAL:return
   aid=a['action_id']
   if kind=='skill_accepted' and a['state']=='dispatched':self.j.transition(aid,'accepted',m.get('code',''),m)
   elif kind=='skill_progress' and a['state']=='accepted':self.j.transition(aid,'running','',m)
   elif kind=='skill_rejected':
    if m.get('code')!='DUPLICATE':self.j.transition(aid,'failed',m.get('code','REJECTED'),m)
   elif kind=='skill_result':
    outcome=m.get('outcome');code=m.get('code','ERROR')
    if outcome=='done':
     if a['state'] in ('dispatched','accepted','running','unknown'):self.j.transition(aid,'verifying',code,m)
    elif outcome=='cancelled':self.j.transition(aid,'cancelled',code,m)
    else:self.j.transition(aid,'timed_out' if code=='TIMEOUT' and a['state']!='unknown' else 'failed',code,m)
 def witness(self,r,persist=True):
  if r.get('char_id')!=self.char_id:return
  ts=r['event_ts']
  if persist:
   r=dict(r,received_ts=time.time());self.j.event('witness',r['id'],ts,r,r['id'])
  values={'map':r.get('map')}
  if r['kind'] in ('pos','loadmap','login'):values['pos']={'x':r['x'],'y':r['y']}
  if r['kind']=='die':values['dead']=True
  if r['kind'] in ('login','loadmap'):values.update(dead=False,online=True)
  if r['kind']=='logout':values['online']=False
  for field,value in values.items():
   self.projection['server_'+field]={'value':value,'source':'witness','ts':ts}
  if self.telemetry and ts>=self.telemetry['ts']-2:
   conflict=(r['kind']=='die' and not self.telemetry.get('dead')) or (r['kind'] in ('pos','loadmap') and r['map']!=self.telemetry['map'])
   if conflict:
    self.j.event('divergence',str(uuid.uuid4()),time.time(),{'body':self.telemetry,'server':r})
    for a in self.active():
     if a['state'] not in ('unknown','planned'):self.j.transition(a['action_id'],'unknown','DIVERGENCE',r)
 def run(self,skill,params,deadline,idem=None):
  if skill not in ('travel_to','whisper','say','hunt','rest','respawn','buy_potions','sell_loot'):raise ValueError('UNSUPPORTED')
  if not isinstance(deadline,(int,float)) or not math.isfinite(deadline) or not 0<deadline<=(3660 if skill=='hunt' else 600):raise ValueError('DEADLINE')
  if skill in ('say','whisper') and (not isinstance(params,dict) or not isinstance(params.get('text'),str) or not 0<len(params['text'])<=120 or (skill=='say' and params['text'].lstrip().startswith(('@','#','/'))) or any(ord(c)<32 or ord(c)==127 for c in params['text']) or ';;' in params['text'] or '\\' in params['text']):raise ValueError('INVALID_TEXT')
  key=idem or str(uuid.uuid4());old=[a for a in self.j.actions() if a['resident']==self.resident and a['idem_key']==key]
  if old:return old[0]
  if skill=='sell_loot' and isinstance(params,dict) and 'category' in params:
   if params['category'] not in ('loot','equipment') or 'items' in params:raise ValueError('INVALID_SALE_CATEGORY')
   category=((self.telemetry or {}).get('sellable_by_category') or {}).get(params['category'])
   if not isinstance(category,dict) or not category:raise ValueError('NO_CATEGORY_TELEMETRY')
   params={**params,'_sellable_before':dict(category)}
  if skill=='sell_loot' and (isinstance(params,dict) and 'items' in params or (self.telemetry or {}).get('behavior_v1') is True):
   items=params.get('items') if isinstance(params,dict) else None
   bag=(self.telemetry or {}).get('inventory')
   if not isinstance(items,dict) or not items or any(k not in ('909','713','938') or type(n) is not int or not 1<=n<=1000 for k,n in items.items()):raise ValueError('INVALID_ITEMS')
   if not isinstance(bag,dict) or any(type(bag.get(k)) is not int or n>bag[k] for k,n in items.items()):raise ValueError('INVALID_ITEMS')
   params={**params,'items':dict(items)}
  if skill=='hunt' and params.get('party_target'):
   party=(self.telemetry or {}).get('party') or {}
   if party.get('source')!='native_client' or party.get('share')!=1 or not any(u.get('name')==params['party_target'] and u.get('online') is True for u in party.get('members',[])):raise ValueError('INVALID_PARTY')
  if skill=='hunt' and params.get('support_target'):
   t=self.telemetry or {};party=t.get('party') or {}
   if params['support_target'] not in {u.get('name') for u in party.get('members',[])} or party.get('share')!=1 or type(t.get('job_level')) is not int:raise ValueError('INVALID_SUPPORT_PARTY')
   params={**params,'_job_level_before':t['job_level']}
  if skill in ('buy_potions','sell_loot'):params={**params,'_inventory_before':dict((self.telemetry or {}).get('inventory',{})),'_picklog_baseline':self.j.cursor('picklog')}
  if skill=='sell_loot' and 'items' not in params and 'category' not in params and isinstance((self.telemetry or {}).get('sellable'),dict):params['_sellable_before']=dict(self.telemetry['sellable'])
  now=time.time();a=self.j.plan(self.resident,skill,params,key,now+deadline,self.epoch,self.j.cursor('witness'));aid=a['action_id']
  if not self.telemetry or now-self.telemetry['ts']>3 or now-self.received>3:reason='STALE_TELEMETRY'
  elif self.telemetry.get('dead') and skill!='respawn':reason='DIED'
  elif any(a['action_id']!=aid and ((a['skill'] in ('say','whisper')) == (skill in ('say','whisper'))) for a in self.active()):reason='BUSY'
  else:reason=None
  if reason:self.j.transition(aid,'rejected_local',reason,{});return self.j.action(aid)
  m={'proto':1,'type':'skill_start','action_id':aid,'idem_key':key,'skill':skill,'params':params,'deadline_ts':a['deadline'],'expect_epoch':self.epoch}
  self.j.transition(aid,'dispatched','',m)
  try:self.send(m)
  except (OSError,RuntimeError):self.j.transition(aid,'unknown','DISPATCH_UNCERTAIN',{})
  return self.j.action(aid)
 def evaluate(self):
  now=time.time()
  for a in self.active():
   aid=a['action_id'];p=json.loads(a['params']);state=a['state'];t=self.telemetry
   rows=[json.loads(r[0]) for r in self.j.db.execute("SELECT payload FROM events WHERE source='witness' AND ts>=? ORDER BY ts",(a['created'],))]
   rows=[r for r in rows if r['id']>a['baseline'] and r['char_id']==self.char_id]
   server_alive=self.projection.get('server_dead',{}).get('value') is not True and self.projection.get('server_online',{}).get('value') is not False
   server_map_ok=not t or self.projection.get('server_map',{}).get('value',t['map'])==t['map']
   if state in ('verifying','unknown') and t and now-t['ts']<=3 and now-self.received<=3 and not t.get('dead') and (a['skill'] in ('say','whisper') or not t.get('running')) and server_alive and server_map_ok:
    if a['skill']=='travel_to':
     def near(x,y,r):return 'x' not in p or max(abs(x-p['x']),abs(y-p['y']))<=r
     hit=next((r for r in reversed(rows) if r['kind'] in ('pos','loadmap') and r['map']==p['map'] and near(r['x'],r['y'],p['r'])),None)
     arrived=t['map']==p['map'] and near(t['pos']['x'],t['pos']['y'],p['r'])
     if hit and arrived:
      self.j.transition(aid,'confirmed','ARRIVED',{'server':hit,'body':t});continue
    elif a['skill'] in ('buy_potions','sell_loot'):
     trades=[json.loads(r[0]) for r in self.j.db.execute("SELECT payload FROM events WHERE source='picklog' AND ts>=?",(a['created']-1,))]
     trades=[r for r in trades if r['id']>p['_picklog_baseline'] and r['char_id']==self.char_id and r['type']=='S' and r['map']==t['map']]
     before=p['_inventory_before'];bag=t.get('inventory',{})
     if a['skill']=='buy_potions':
      need=p['stock_goal']-before.get('501',0);hits=[r for r in trades if r['item_id']==501 and r['amount']>0];ok=need>0 and sum(r['amount'] for r in hits)>=need and bag.get('501',0)>=p['stock_goal']
     elif 'items' in p:
      # Explicit plans are quantities, never an instruction to liquidate stacks.
      expected={int(k):n for k,n in p['items'].items()}
      hits=[r for r in trades if r['amount']<0]
      ok=self.epoch==a['epoch']==t.get('body_epoch') and bool(expected) and all(r['item_id'] in expected for r in hits) and all(sum(-r['amount'] for r in hits if r['item_id']==iid)==n and bag.get(str(iid),0)<=before.get(str(iid),0)-n for iid,n in expected.items())
     else:
      # Legacy whole-stack sale: the fixed list plus the junk the body reported
      # sellable by item type when the action was planned; all of it must be sold.
      ids=set() if 'category' in p else {909,914,705,935,919,908,916}
      junk=p.get('_sellable_before') if isinstance(p.get('_sellable_before'),dict) else {}
      required={int(k):v for k,v in (junk if '_sellable_before' in p else before).items() if int(k) in ids and v>0}
      # The dealer may refuse some junk (NoSell, bound): it needs proof only where it was sold.
      optional={int(k):v for k,v in junk.items() if type(v) is int and v>0 and int(k) not in required}
      hits=[r for r in trades if (r['item_id'] in required or r['item_id'] in optional) and r['amount']<0];sold={}
      for r in hits:sold[r['item_id']]=sold.get(r['item_id'],0)-r['amount']
      ok=bool(hits) and all(sold.get(iid,0)>=n and bag.get(str(iid),0)<=before.get(str(iid),0)-n for iid,n in required.items()) and all(bag.get(str(iid),0)<=before.get(str(iid),0)-n for iid,n in sold.items() if iid in optional)
     if a['skill']=='sell_loot' and 'category' in p:
      ok=ok and self.epoch==a['epoch']==t.get('body_epoch') and all(r['item_id'] in optional for r in trades if r['amount']<0)
     if ok:self.j.transition(aid,'confirmed','BOUGHT' if a['skill']=='buy_potions' else 'SOLD',{'server_picklog':hits,'body':t});continue
    elif a['skill'] in ('rest','respawn'):
     hit=next((r for r in reversed(rows) if r['kind'] in ('pos','loadmap','login') and r['map']==t['map']),None)
     hp_ok=a['skill']=='respawn' or (t.get('hp_max',0)>0 and t.get('hp',0)*100/t['hp_max']>=p['hp_pct'])
     sitting_ok=a['skill']!='rest' or t.get('behavior_v1') is not True or t.get('sitting') is True
     if hit and hp_ok and sitting_ok:self.j.transition(aid,'confirmed','RESPAWNED' if a['skill']=='respawn' else 'RESTED',{'server':hit,'body':t});continue
    elif a['skill']=='say':
     spoken=any(r['state']=='verifying' and r['code']=='SPOKEN' for r in self.j.trace(aid))
     hits=[json.loads(r[0]) for r in self.j.db.execute("SELECT payload FROM events WHERE source='chat' AND ts BETWEEN ? AND ?",(a['created']-1,a['deadline']))]
     parts=[r for r in hits if r.get('char_id')==self.char_id and r.get('type')=='O']
     wanted=' '.join(p['text'].split());hit=None
     for i in range(len(parts)):
      for end in range(i+1,min(i+8,len(parts))+1):
       fragment=parts[i:end]
       if ' '.join(' '.join(r['text'].split()) for r in fragment)==wanted:hit=fragment;break
      if hit:break
     if spoken and hit:self.j.transition(aid,'confirmed','SPOKEN',{'server_parts':hit});continue
    elif a['skill']=='hunt' and (not p.get('party_target') or (t.get('party') or {}).get('source')=='native_client' and (t.get('party') or {}).get('share')==1 and any(u.get('name')==p['party_target'] and u.get('online') is True for u in (t.get('party') or {}).get('members',[]))):
     if p.get('support_target'):
      seen=self.support_seen.get(aid);party=t.get('party') or {}
      progress=[r for r in rows if r['kind']=='job_level' and r['map']==p['map'] and r.get('a1',0)>p['_job_level_before'] and t.get('job_level',0)>=r['a1']]
      if seen and seen['body_epoch']==a['epoch']==t.get('body_epoch') and progress and party.get('share')==1 and p['support_target'] in {u.get('name') for u in party.get('members',[])}:
       self.j.transition(aid,'confirmed','SUPPORT_PROGRESS',{'server_earned_levels':progress,'native_support':seen,'body':t});continue
     else:
      kills=[r for r in rows if r['kind']=='kill' and r['map']==p['map']]
      if len(kills)>=p.get('min_kills',1):self.j.transition(aid,'confirmed','HUNTED',{'server_kills':kills,'body':t});continue
    elif a['skill']=='whisper':
     history=self.j.trace(aid);sent=any(r['state']=='verifying' and r['code']=='SENT' for r in history)
     hits=[json.loads(r[0]) for r in self.j.db.execute("SELECT payload FROM events WHERE source='chat' AND ts BETWEEN ? AND ?",(a['created']-1,a['created']+10))]
     hit=next((r for r in hits if r.get('char_id')==self.char_id and r.get('to')==p['to_name'] and r.get('text')==p['text'] and r.get('type','').lower()=='w'),None)
     if sent and hit:self.j.transition(aid,'confirmed','SENT',{'server':hit});continue
   if state=='unknown' and self.epoch and self.epoch!=a['epoch'] and t and now-t['ts']<3:
    self.j.transition(aid,'failed','BODY_RESTARTED',{'reason':'new body, no server arrival evidence'});continue
   if now>a['deadline']:
    if state not in ('unknown','planned'):
     try:self.send({'proto':1,'type':'skill_cancel','action_id':aid,'reason':'deadline'})
     except (OSError,RuntimeError):pass
     self.j.transition(aid,'unknown','VERIFY_TIMEOUT',{'reason':'no complete server evidence'})
    elif state=='unknown' and now>a['deadline']+60:self.j.transition(aid,'escalated','NO_EVIDENCE',{})
 def cancel(self,aid):
  a=self.j.action(aid)
  if a['state'] in TERMINAL:return a
  self.send({'proto':1,'type':'skill_cancel','action_id':aid,'reason':'operator'});self.j.log('cancel',{'action_id':aid});return self.j.action(aid)
 def safe_stop(self):
  self.send({'proto':1,'type':'safe_stop'});self.j.log('safe_stop',{})
