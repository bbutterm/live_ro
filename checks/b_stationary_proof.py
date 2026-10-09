"""Additional real B proof: fresh server probes during ESTOP. Requires prepared live stand."""
import sys,pathlib,json,time,uuid,sqlite3,argparse
p=argparse.ArgumentParser();p.add_argument("--execute",action="store_true");args=p.parse_args()
if not args.execute:raise SystemExit("Opt-in --execute required")
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from packages.body_gateway.cli import request,DEFAULT_SOCKET
P=pathlib.Path('/root/ragnarok/evidence/B');STOP=pathlib.Path('/root/ragnarok/run/ESTOP')
if STOP.exists():raise SystemExit('Operator ESTOP already present: leave it untouched')
TOKEN='B_PROOF_'+str(uuid.uuid4())
def rpc(**m):return request(DEFAULT_SOCKET,m)
def wait(aid,limit):
 until=time.monotonic()+limit
 while time.monotonic()<until:
  a=rpc(op='action',action_id=aid)
  if a['state'] in ('confirmed','failed','timed_out','cancelled'):return a
  time.sleep(.3)
 raise RuntimeError('action wait exceeded')
a=rpc(op='run',resident='resident_a',skill='travel_to',params={'map':'prontera','x':150,'y':147,'r':0},deadline=240,idem_key=str(uuid.uuid4()));r=wait(a['action_id'],260);assert r['state']=='confirmed',r
c=sqlite3.connect(str(P/'world.sqlite'));c.row_factory=sqlite3.Row
start=rpc(op='status')['telemetry']['pos'];a=rpc(op='run',resident='resident_a',skill='travel_to',params={'map':'prontera','x':250,'y':177,'r':0},deadline=120,idem_key=str(uuid.uuid4()))
end=time.monotonic()+8
while time.monotonic()<end:
 s=rpc(op='status');t=s['telemetry']
 if t.get('running') and t['pos']!=start:break
 time.sleep(.2)
else:raise RuntimeError('No physical movement before ESTOP')
stamp=time.time();STOP.open('x').write(TOKEN);positions=[];stopped=None
try:
 end=time.monotonic()+18
 while time.monotonic()<end:
  s=rpc(op='status');t=s['telemetry']
  if t['mode']=='IDLE_SAFE' and not t.get('running'):
   if stopped is None:stopped=time.time()
   positions.append((time.time(),t['map'],t['pos']['x'],t['pos']['y']))
  time.sleep(.2)
finally:
 if STOP.exists() and STOP.read_text()==TOKEN:STOP.unlink()
r=rpc(op='action',action_id=a['action_id']);assert r['state']=='cancelled',r
assert stopped and stopped-stamp<=2
assert len({p[1:]for p in positions})==1,positions
rows=[json.loads(r[0])for r in c.execute("SELECT payload FROM events WHERE source='witness' AND ts>=? ORDER BY ts",(stopped+1,))];rows=[r for r in rows if r['kind']=='pos' and int(r['char_id'])==150001]
assert len(rows)>=3,rows
assert rows[-1]['event_ts']-rows[0]['event_ts']>=10,rows
assert len({(r['map'],r['x'],r['y'])for r in rows})==1,rows
out={'pass':True,'action_id':a['action_id'],'estop_seconds':stopped-stamp,'body_samples':len(positions),'body_span':positions[-1][0]-positions[0][0],'server_samples':len(rows),'server_span':rows[-1]['event_ts']-rows[0]['event_ts'],'server_positions':[(r['event_ts'],r['map'],r['x'],r['y'])for r in rows]}
(P/'stationary-proof.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
