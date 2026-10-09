"""Opt-in real-server acceptance probe. No GM commands or gameplay SQL writes.
Run from repo: python3 checks/b_live_acceptance.py --execute
Raw evidence stays outside Git. Stops on false confirmation/config mutation.
"""
import argparse,datetime,hashlib,json,os,pathlib,signal,sqlite3,subprocess,sys,time,uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from packages.body_gateway.cli import request,DEFAULT_SOCKET
from packages.body_gateway.journal import TERMINAL
from tools.events import query,DEFAULTS
ROOT=pathlib.Path(__file__).resolve().parents[1];RUN=pathlib.Path('/root/ragnarok/run/B');DB=pathlib.Path('/root/ragnarok/evidence/B/world.sqlite');CONFIG=RUN/'resident/control/config.txt';ESTOP=pathlib.Path('/root/ragnarok/run/ESTOP')
OUT=pathlib.Path('/root/ragnarok/evidence/B/acceptance-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
results=[];samples=[];children=[];started=time.time();tag=str(uuid.uuid4());owned_estop=False

def rpc(op,**kw):return request(DEFAULT_SOCKET,dict(op=op,**kw))
def checksum():return hashlib.sha256(CONFIG.read_bytes()).hexdigest()
def emit(row):
 results.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
 with (OUT/'results.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
def tick():
 s=rpc('status');samples.append({'ts':time.time(),'age':s['age'],'telemetry':s['telemetry']});return s

def ready(timeout=40):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  try:
   s=tick()
   if s['age'] is not None and s['age']<3 and s['telemetry'] and not s['telemetry']['dead'] and not s['telemetry'].get('running') and not s['actions']:return s
  except (OSError,RuntimeError):pass
  time.sleep(.2)
 raise RuntimeError('NO_FRESH_IDLE_BODY')

def start(label,skill='travel_to',params=None,deadline=240.0):
 ready();return rpc('run',resident='resident_a',skill=skill,params=params,deadline=deadline,idem=tag+'-'+label)
def wait(a,seconds=650):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  tick();a=rpc('action',action_id=a['action_id'])
  if a['state'] in TERMINAL:return a
  time.sleep(.2)
 raise RuntimeError('BOUNDED_ACTION_WAIT_EXPIRED '+a['action_id'])
def check(label,a,states=('confirmed',),codes=('ARRIVED',),loadmap=False):
 trace=rpc('trace',action_id=a['action_id']);ok=a['state'] in states and a['code'] in codes
 if a['state']=='confirmed':
  ev=json.loads(trace[-1]['evidence']);server=ev.get('server');p=json.loads(a['params'])
  truth=bool(server and server['char_id']==150001)
  if a['skill']=='travel_to':
   truth=truth and server['map']==p['map'] and server['id']>a['baseline'] and server['event_ts']>=a['created']
   if 'x' in p:truth=truth and max(abs(server['x']-p['x']),abs(server['y']-p['y']))<=p['r']+3
  else:truth=truth and server['to']==p['to_name'] and server['text']==p['text'] and server['type']=='W'
  if not truth:rpc('safe_stop');raise RuntimeError('FALSE_CONFIRMED '+a['action_id'])
 if loadmap:
  with sqlite3.connect(DB) as db:
   ev=[json.loads(r[0])for r in db.execute("SELECT payload FROM events WHERE source='witness' AND ts>=?",(a['created'],))]
  ok=ok and any(r['kind']=='loadmap' and r['map']==json.loads(a['params'])['map'] and r['id']>a['baseline'] for r in ev)
 if checksum()!=before:rpc('safe_stop');raise RuntimeError('CONFIG_MUTATION')
 (OUT/('trace_'+label+'.json')).write_text(json.dumps(trace,ensure_ascii=False,indent=2))
 emit({'test':label,'pass':bool(ok),'action_id':a['action_id'],'state':a['state'],'code':a['code'],'trace_states':[t['state']for t in trace]});return ok

def travel(label,map,x=None,y=None,r=0,deadline=360):
 p={'map':map,'r':r}
 if x is not None:p.update(x=x,y=y)
 return wait(start(label,params=p,deadline=deadline),deadline+70)
def town(label):
 ready();t=rpc('status')['telemetry']
 if t['map']=='prontera' and max(abs(t['pos']['x']-150),abs(t['pos']['y']-147))<=3:return
 a=travel(label,'prontera',150,147,3,600)
 if a['state']!='confirmed':raise RuntimeError('PRECONDITION_TOWN '+a['code'])
def scope_pid(needle,comm):
 rows=subprocess.check_output(['ps','-eo','pid=,comm=,args='],text=True).splitlines();matches=[]
 for row in rows:
  p,c,args=row.strip().split(None,2)
  if c==comm and needle in args:matches.append(int(p))
 if len(matches)!=1:raise RuntimeError('PROCESS_SCOPE_NOT_UNIQUE '+str(matches))
 return matches[0]
def restart_coordinator():
 for p in (RUN/'actions.sock',pathlib.Path(str(RUN/'actions.sock')+'.control')):
  if p.exists():p.unlink()
 f=(OUT/('coordinator-restart-'+str(len(children))+'.log')).open('w')
 p=subprocess.Popen([sys.executable,'-m','packages.body_gateway.service','--socket',str(RUN/'actions.sock'),'--store',str(DB),'--duration','6000'],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT);children.append(p);f.close()
def restart_body():
 env=dict(os.environ,TERM='dumb',RESIDENT_SOCKET=str(RUN/'actions.sock'),RESIDENT_ID='resident_a',RESIDENT_RUN_DIR=str(RUN/'resident'))
 f=(OUT/'body-restart.log').open('w')
 p=subprocess.Popen(['timeout','6000','perl','openkore.pl','--control='+str(RUN/'resident/control'),'--fields='+str(RUN/'resident/fields'),'--tables='+str(RUN/'resident/tables')+':tables','--plugins='+str(ROOT/'plugins'),'--logs=/root/ragnarok/logs/B-resident','--ai=auto'],cwd='/root/ragnarok/repos/openkore',env=env,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT);children.append(p);f.close()
def exports():
 with sqlite3.connect(DB) as db:
  db.row_factory=sqlite3.Row
  for table,col in [('actions','created'),('action_transitions','ts'),('operator_log','ts')]:
   rows=[dict(r)for r in db.execute('SELECT * FROM '+table+' WHERE '+col+'>=?',(started,))]
   (OUT/(table+'.jsonl')).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n'for r in rows))
  ev=[dict(r)for r in db.execute('SELECT * FROM events WHERE ts>=?',(started,))]
  (OUT/'witness_events.jsonl').write_text(''.join(r['payload']+'\n'for r in ev if r['source']=='witness'))
  body=[json.loads(r['payload'])for r in ev if r['source']=='body' and json.loads(r['payload'])['type']=='telemetry'];native=[json.loads(r['payload'])for r in ev if r['source']=='witness']
  diffs=[]
  for n in native:
   if n['kind']!='pos':continue
   nearby=[b for b in body if b['map']==n['map'] and abs(b['ts']-n['event_ts'])<=2]
   if nearby:
    b=min(nearby,key=lambda b:abs(b['ts']-n['event_ts']));diffs.append({'id':n['id'],'delta':max(abs(n['x']-b['pos']['x']),abs(n['y']-b['pos']['y'])),'dt':abs(b['ts']-n['event_ts'])})
  (OUT/'moving_position_pairs.json').write_text(json.dumps(diffs,indent=2))
  checkpoints=[]
  for row in db.execute("SELECT t.action_id,t.evidence FROM action_transitions t JOIN actions a USING(action_id) WHERE t.state='confirmed' AND a.skill='travel_to' AND a.created>=? ORDER BY t.id",(started,)):
   e=json.loads(row[1]);b=e['body'];n=e['server'];checkpoints.append({'action_id':row[0],'server_id':n['id'],'delta':max(abs(b['pos']['x']-n['x']),abs(b['pos']['y']-n['y'])),'body_running':b.get('running'),'map':b['map']})
  (OUT/'position_checkpoints.json').write_text(json.dumps(checkpoints,indent=2));emit({'test':'check10','pass':len(checkpoints)>=10 and all(r['delta']<=3 and not r['body_running'] for r in checkpoints),'pairs':len(checkpoints),'max_delta':max((r['delta']for r in checkpoints),default=None),'moving_diagnostic_max_delta':max((r['delta']for r in diffs),default=None)})
  lags=[r['received_ts']-r['event_ts']for r in native if 'received_ts'in r]
 def pct(xs,p):return sorted(xs)[max(0,min(len(xs)-1,int((len(xs)-1)*p)))] if xs else None
 from zoneinfo import ZoneInfo
 started_local=datetime.datetime.fromtimestamp(started,ZoneInfo('Europe/Amsterdam')).strftime('%Y-%m-%d %H:%M:%S')
 commands=query(DEFAULTS,"SELECT JSON_OBJECT('id',atcommand_id,'ts',atcommand_date,'char_id',char_id,'map',map,'command',command) FROM ro_residents_logs.atcommandlog WHERE char_id=150001 AND atcommand_date>='"+started_local+"' ORDER BY atcommand_id")
 (OUT/'atcommandlog.jsonl').write_text(commands);identity=subprocess.check_output(['mariadb','-NBe',"SELECT JSON_OBJECT('char_id',c.char_id,'group_id',l.group_id) FROM ro_residents_main.char c JOIN ro_residents_main.login l ON c.account_id=l.account_id WHERE c.char_id=150001"],text=True,timeout=8);(OUT/'identity.json').write_text(identity)
 emit({'test':'ordinary_no_GM','pass':json.loads(identity)['group_id']==0 and not commands.strip()})
 ages=[s['age']for s in samples if s['age']is not None];summary={'run':tag,'elapsed':time.time()-started,'results':results,'telemetry_age_p95':pct(ages,.95),'witness_lag_p95':pct(lags,.95),'config_before':before,'config_after':checksum()}
 (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));(OUT/'samples.jsonl').write_text(''.join(json.dumps(s)+'\n'for s in samples));print('EVIDENCE_DIRECTORY='+str(OUT),flush=True)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
 if not args.execute:parser.error('--execute required: this moves the dedicated lab resident and kills its two scoped processes')
 OUT.mkdir(parents=True,mode=0o700);before=checksum();(OUT/'config-before.sha256').write_text(before+'\n')
 if ESTOP.exists():raise SystemExit('Existing operator ESTOP: refuse to remove it')
 try:
  ready();town('initial-town');seq=[];until=time.monotonic()+30
  while time.monotonic()<until:
   t=tick()['telemetry'];seq.append((t['body_epoch'],t['seq'],t['map'],t['pos'],t['hp']));time.sleep(.25)
  unique=list(dict.fromkeys((e,s)for e,s,*_ in seq));emit({'test':'check9','pass':len(unique)>=25 and all(unique[i][0]==unique[i-1][0]and unique[i][1]>unique[i-1][1]for i in range(1,len(unique))),'messages':len(unique)})
  for i,(x,y)in enumerate([(156,80),(156,100),(156,120),(156,140),(156,160)]):check('T1-'+str(i),travel('T1-'+str(i),'prontera',x,y))
  for i in range(3):check('T10-online-'+str(i),wait(start('T10-online-'+str(i),'whisper',{'to_name':'Tester','text':'BF_B_'+tag[:8]+'_'+str(i)},30)),codes=('SENT',))
  check('T10-offline',wait(start('T10-offline','whisper',{'to_name':'BF_NoSuch_'+tag[:8],'text':'BF_B_OFFLINE'},30)),('failed',),('OFFLINE',))
  for i,p in enumerate([{'map':'prontera','x':0,'y':0,'r':0},{'map':'no_such_b_map','r':0}]):check('T4-'+str(i),wait(start('T4-'+str(i),params=p)),('failed','rejected_local'),('UNWALKABLE','NO_ROUTE'))
  check('T5',wait(start('T5',params={'map':'prontera','x':250,'y':177,'r':0},deadline=2)),('failed','timed_out'),('TIMEOUT',));ready()
  a=start('T6',params={'map':'prontera','x':250,'y':177,'r':0});time.sleep(1);rpc('cancel',action_id=a['action_id']);check('T6',wait(a),('cancelled',),('CANCELLED',));ready()
  a=start('T11',params={'map':'prontera','x':156,'y':80,'r':0});deadline=time.monotonic()+5
  while time.monotonic()<deadline:
   s=tick()
   if s['telemetry']['running'] and s['telemetry']['running']['action_id']==a['action_id']:break
   time.sleep(.1)
  else:raise RuntimeError('T11_NO_ACTIVE_MOTION')
  at=time.monotonic();ESTOP.write_text('B_ACCEPTANCE '+tag);owned_estop=True
  while time.monotonic()-at<2 and tick()['telemetry']['mode']!='IDLE_SAFE':time.sleep(.05)
  stopped=time.monotonic()-at;positions=[];until=time.monotonic()+10
  while time.monotonic()<until:positions.append(tick()['telemetry']['pos']);time.sleep(.25)
  check('T11',wait(a),('cancelled',),('CANCELLED',));emit({'test':'T11-stop-window','pass':stopped<=2 and all(p==positions[0]for p in positions),'stop_seconds':stopped});ESTOP.unlink();owned_estop=False
  for i in range(5):
   town('T2-pre-'+str(i));check('T2-'+str(i),travel('T2-'+str(i),'prt_fild08'),loadmap=True)
  for i in range(5):
   town('T3-pre-'+str(i));check('T3-'+str(i),travel('T3-'+str(i),'izlude'),loadmap=True)
  for i in range(2):
   town('T9-pre-'+str(i));a=start('T9-'+str(i),params={'map':'prt_fild08','r':0});time.sleep(.5);rpc('resend',action_id=a['action_id']);a=wait(a);check('T9-'+str(i),a,loadmap=True)
   with sqlite3.connect(DB)as db:
    events=[json.loads(r[0])for r in db.execute("SELECT payload FROM events WHERE source='body' AND ts>=?",(a['created'],))];n=[json.loads(r[0])for r in db.execute("SELECT payload FROM events WHERE source='witness' AND ts>=?",(a['created'],))]
   emit({'test':'T9-duplicate-'+str(i),'pass':any(r.get('action_id')==a['action_id']and r.get('code')=='DUPLICATE'for r in events)and sum(r['kind']=='loadmap'and r['map']=='prt_fild08'for r in n)==1})
  for i in range(2):
   town('T7-pre-'+str(i));a=start('T7-'+str(i),params={'map':'prontera','x':250,'y':177,'r':0},deadline=120);time.sleep(1);os.kill(scope_pid('packages.body_gateway.service','python3'),signal.SIGKILL);time.sleep(30);restart_coordinator();time.sleep(2);check('T7-'+str(i),wait(a,180),('confirmed','failed'),('ARRIVED','NO_ROUTE','TIMEOUT'))
   a=travel('T7-return-'+str(i),'prontera',150,147,0);check('T7-return-'+str(i),a)
  old=ready()['epoch'];a=start('T8',params={'map':'prontera','x':250,'y':177,'r':0});time.sleep(1);os.kill(scope_pid('--control='+str(RUN/'resident/control'),'perl'),signal.SIGKILL);time.sleep(2);restart_body();time.sleep(5);a=wait(a,80);check('T8',a,('failed',),('BODY_RESTARTED',));emit({'test':'T8-epoch','pass':rpc('status')['epoch']!=old})
  for i in range(2):
   town('T13-pre-'+str(i));check('T13-'+str(i),travel('T13-'+str(i),'gef_fild03',200,200,3,600),('confirmed','failed','timed_out'),('ARRIVED','DIED','STUCK'))
  emit({'test':'T12','pass':before==checksum()});exports()
  if not all(r['pass']for r in results):raise SystemExit(1)
 except BaseException as err:
  emit({'test':'RUN_ABORT','pass':False,'reason':str(err)})
  try:rpc('safe_stop')
  except Exception:pass
  exports();raise
 finally:
  if owned_estop and ESTOP.exists() and tag in ESTOP.read_text():ESTOP.unlink()
