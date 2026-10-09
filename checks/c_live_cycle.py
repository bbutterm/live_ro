"""Opt-in bounded C driver. Real RPC + server journal; no LLM, grants or SQL writes.
Smoke is not C20/C21. C27 stays gated on explicit live C26 evidence.
"""
import sys,pathlib,time,json,sqlite3,argparse,os,hashlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from packages.body_gateway.cli import request
from packages.body_gateway.journal import TERMINAL
SOCKET='/root/ragnarok/run/C/actions.sock.control'
DB='/root/ragnarok/evidence/C/world.sqlite'
PROFILE='/root/ragnarok/run/B/resident/control/config.txt'
LOOT={'909','914','705','935','919','908','916'}
class Driver:
 def __init__(self,out):
  self.out=pathlib.Path(out);self.out.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(self.out,0o700);self.steps={};self.cycles=0;self.frames=[];self.epoch=None;self.last_move=time.monotonic();self.last_pos=None;self.rest_from_injury=False;self.profile=hashlib.sha256(pathlib.Path(PROFILE).read_bytes()).hexdigest()
 def rpc(self,op,**kwargs):return request(SOCKET,dict(op=op,caller='c_live_cycle',**kwargs))
 def note(self,kind,**data):
  row=dict(ts=time.time(),kind=kind,**data)
  with (self.out/'driver.jsonl').open('a')as f:f.write(json.dumps(row)+'\n')
  print(json.dumps(row),flush=True)
 def status(self):
  s=self.rpc('status');t=s.get('telemetry')
  if not t or s['age']>3:raise RuntimeError('STALE_BODY')
  if self.epoch is None:self.epoch=s['epoch']
  if s['epoch']!=self.epoch:raise RuntimeError('UNEXPECTED_BODY_RESTART')
  key=(t['map'],t['pos']['x'],t['pos']['y'])
  if key!=self.last_pos:self.last_move=time.monotonic();self.last_pos=key
  if time.monotonic()-self.last_move>300:raise RuntimeError('STATIONARY_OVER_5_MIN')
  self.frames.append(dict(at=time.time(),**t))
  with (self.out/'frames.jsonl').open('a')as f:f.write(json.dumps(self.frames[-1])+'\n')
  self.frames=self.frames[-10:]
  return s
 def kills(self,since):
  with sqlite3.connect(DB)as db:
   rows=[json.loads(r[0])for r in db.execute("SELECT payload FROM events WHERE source='witness' AND ts>=?",(since,))]
  return [r for r in rows if r['kind']=='kill' and r['char_id']==150001]
 def wait(self,a,limit):
  end=time.monotonic()+limit
  while a['state']not in TERMINAL:
   if time.monotonic()>end:raise RuntimeError('UNRESOLVED_ACTION:'+a['action_id']+':'+a['state'])
   self.status();time.sleep(1);a=self.rpc('action',action_id=a['action_id'])
  (self.out/(a['action_id']+'.json')).write_text(json.dumps(a,indent=2));self.note('ACTION',skill=a['skill'],state=a['state'],code=a['code'],action_id=a['action_id']);return a
 def run(self,skill,params,deadline=180,allow=()):
  self.status();a=self.rpc('run',skill=skill,params=params,deadline=deadline);a=self.wait(a,deadline+8)
  if a['state']!='confirmed' and a['code']not in allow:raise RuntimeError(skill+':'+a['state']+':'+a['code'])
  return a
 def travel(self,map,x,y):return self.run('travel_to',dict(map=map,x=x,y=y,r=3),240)
 def rest(self):
  t=self.status()['telemetry'];injured=t['hp']*100<t['hp_max']*80
  if injured:
   if t['map'] in ('prt_fild08','prt_fild08a'):self.travel('prontera',150,147)
   a=self.run('rest',{'hp_pct':80},240);self.rest_from_injury=True;self.steps['22']=True;self.note('REST_FROM_INJURY',before_hp=t['hp'],before_max=t['hp_max'],action_id=a['action_id'])
 def recover(self):
  t=self.status()['telemetry']
  if t['dead']:
   self.run('respawn',{},60);self.note('NATIVE_RESPAWN')
  self.rest()
 def cycle(self,hunt_seconds=180):
  self.recover();t=self.status()['telemetry']
  if t['map']!='prt_fild08':self.travel('prt_fild08',170,240)
  start=time.time();a=self.run('hunt',dict(map='prt_fild08',duration=hunt_seconds,min_kills=1),hunt_seconds+40,allow=('NEEDS_REST','DIED'))
  self.recover();kills=self.kills(start)
  if not kills:raise RuntimeError('CYCLE_NO_SERVER_KILLS')
  self.travel('prt_in',126,76);self.rest();t=self.status()['telemetry'];sold=False;bought=False
  if any(t['inventory'].get(k,0)>0 for k in LOOT):self.run('sell_loot',{},40);sold=True
  t=self.status()['telemetry'];stock=t['inventory'].get('501',0)
  if stock<17:
   goal=min(20,stock+int(t['zeny']//10))
   if goal<=stock or goal<3:raise RuntimeError('INSUFFICIENT_EARNED_ZENY')
   self.run('buy_potions',dict(item_id=501,stock_goal=goal,max_zeny=300),40);bought=True;self.steps['24']=True
  self.travel('prt_fild08',170,240);returned=time.time()
  tail=self.run('hunt',dict(map='prt_fild08',duration=180,min_kills=1),220,allow=('NEEDS_REST','DIED'));self.recover()
  if not self.kills(returned):raise RuntimeError('NO_KILL_AFTER_FIELD_RETURN')
  self.cycles+=1
  if sold and bought and a['state']=='confirmed' and tail['state']=='confirmed':self.steps['25']=True
  self.note('CYCLE_COMPLETE',cycle=self.cycles,kills=len(kills),sold=sold,bought=bought)
 def hunt_stop(self):
  self.recover();t=self.status()['telemetry']
  if t['map']!='prt_fild08':self.travel('prt_fild08',170,240)
  self.last_move=time.monotonic();start=time.time();mono=time.monotonic();a=self.rpc('run',skill='hunt',params=dict(map='prt_fild08',duration=1200,min_kills=5),deadline=1240)
  self.note('C20_STARTED',action_id=a['action_id'])
  while time.monotonic()-mono<900:
   self.status();cur=self.rpc('action',action_id=a['action_id'])
   if cur['state']in TERMINAL or cur['state']=='unknown':raise RuntimeError('C20_EARLY_END:'+cur['state']+':'+cur['code'])
   time.sleep(2)
  # Stop immediately by command, not by skill deadline or a move-to-town workaround.
  self.rpc('cancel',action_id=a['action_id']);a=self.wait(a,10)
  if a['state']!='cancelled':raise RuntimeError('C20_NOT_CANCELLED')
  stop=time.time();kills=self.kills(start)
  if len(kills)<5:raise RuntimeError('C20_FEWER_THAN_5_KILLS')
  end=time.monotonic()+120
  while time.monotonic()<end:
   if self.status()['telemetry']['dead']:raise RuntimeError('C20_DIED_AFTER_STOP')
   time.sleep(2)
  late=self.kills(stop)
  if late:raise RuntimeError('C20_KILL_AFTER_STOP')
  self.steps['20']=True;self.note('C20_PASS',kills=len(kills),stop_action=a['action_id'],quiet_seconds=120)
 def summary(self,status,error=None):
  ok=hashlib.sha256(pathlib.Path(PROFILE).read_bytes()).hexdigest()==self.profile
  r=dict(status=status,error=error,steps=self.steps,cycles=self.cycles,profile_unchanged=ok,rest_from_injury=self.rest_from_injury)
  (self.out/'summary.json').write_text(json.dumps(r,indent=2));self.note('SUMMARY',**r)
  if not ok:raise RuntimeError('PROFILE_CHANGED')
def main():
 p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--smoke',action='store_true');p.add_argument('--out',required=True);a=p.parse_args()
 if not a.execute:p.error('live game actions require --execute')
 os.umask(0o077);d=Driver(a.out)
 try:
  if a.smoke:d.cycle(60);d.summary('SMOKE_PASS_NOT_ACCEPTANCE');return
  d.hunt_stop();start=time.monotonic()
  while time.monotonic()-start<3600:d.cycle(180)
  d.note('C21_CANDIDATE_REQUIRES_FINAL_AUDIT',elapsed=time.monotonic()-start,cycles=d.cycles)
  # Never promote missing live death/stuck/no-route checks to PASS.
  gate=pathlib.Path('/root/ragnarok/evidence/C/reactions.json')
  if not gate.exists() or json.loads(gate.read_text()).get('verdict')!='PASS':d.summary('WAIT_C26_BEFORE_6H');return
  start=time.monotonic();base=d.cycles
  while time.monotonic()-start<21600:d.cycle(300)
  if d.cycles-base<5:raise RuntimeError('C27_FEWER_THAN_5_CYCLES')
  d.steps['27']=True;d.summary('C27_CANDIDATE_REQUIRES_FINAL_AUDIT')
 except Exception as e:
  try:d.rpc('safe_stop')
  except Exception:pass
  d.summary('FAIL',str(e));raise
 finally:
  try:d.rpc('safe_stop')
  except Exception:pass
if __name__=='__main__':main()
