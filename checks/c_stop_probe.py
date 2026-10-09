"""Narrow live regression: cancel DURING combat, no kills120s, native Basic3/rest."""
import pathlib,sys,time,json,argparse
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from checks.c_live_cycle import Driver

def main():
 p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--out',required=True);a=p.parse_args()
 if not a.execute:p.error('--execute required')
 d=Driver(a.out)
 try:
  for i in range(60):
   s=d.rpc('status');t=s.get('telemetry',{})
   if s.get('age',100)<3 and s.get('epoch')!='652d1196-7d3e-4a09-9b5c-1ba05090209e' and 'basic_skill'in t:break
   time.sleep(1)
  else:raise RuntimeError('NEW_BODY_NOT_READY')
  d.status();d.recover()
  a=d.rpc('run',skill='hunt',params=dict(map='prt_fild08',duration=180,min_kills=1),deadline=220)
  for i in range(120):
   t=d.status()['telemetry']
   if t['ai_action']=='attack'and t['hp']<t['hp_max']:break
   time.sleep(.5)
  else:raise RuntimeError('NO_ACTIVE_COMBAT_REPRO')
  d.note('STOP_DURING_COMBAT',hp=t['hp'],max_hp=t['hp_max']);d.rpc('cancel',action_id=a['action_id']);a=d.wait(a,10)
  if a['state']!='cancelled':raise RuntimeError('NOT_CANCELLED')
  stop=time.time();end=time.monotonic()+120;seen=[]
  while time.monotonic()<end:
   t=d.status()['telemetry'];seen.append(t);time.sleep(1)
  late=d.kills(stop)
  (d.out/'stop-proof.json').write_text(json.dumps(dict(stop=stop,action=a,late_kills=late,frames=seen),indent=2))
  if late:raise RuntimeError('KILL_AFTER_STOP')
  if any(t['dead']for t in seen):raise RuntimeError('DIED_DURING_STOP_WINDOW')
  # Body may have one pre-cancel telemetry frame in flight. Never excuse a kill.
  if any(t['ai_action']=='attack'for t in seen if t['ts']>=stop+2):raise RuntimeError('ATTACK_CONTINUED')
  d.note('NARROW_STOP_PASS',seconds=120,kills=0)
  t=d.status()['telemetry'];before=dict(t)
  if t['map'] in ('prt_fild08','prt_fild08a'):d.travel('prontera',150,147)
  r=d.run('rest',{'hp_pct':100},240)
  with (d.out/'frames.jsonl').open()as f:rest=[json.loads(l)for l in f if json.loads(l)['ts']>=r['created']]
  if before['hp']<before['hp_max']:
   if not any(t['basic_skill']>=3 and t['sitting']for t in rest):raise RuntimeError('NO_ACTUAL_BASIC3_SITTING')
   d.note('NATIVE_BASIC3_SITTING_PASS',before_basic=before['basic_skill'],after_basic=d.status()['telemetry']['basic_skill'])
  else:d.note('NO_INJURY_BASIC_SITTING_NOT_PROVEN')
  d.summary('NARROW_STOP_PASS_NOT_C20')
 except Exception as e:
  d.summary('FAIL',str(e));raise
 finally:
  try:d.rpc('safe_stop')
  except Exception:pass
if __name__=='__main__':main()
