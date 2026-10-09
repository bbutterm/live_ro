"""Private bidirectional body coordinator; bounded Linux runtime, no LLM."""
import argparse,datetime,json,os,pathlib,selectors,signal,socket,struct,time
from .journal import Journal
from .protocol import Session
from .executor import Executor
from tools.events import DEFAULTS,get_events,query

def serve(path,store,resident,char_id,char_name,duration,witness=True):
 path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);store=pathlib.Path(store);store.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 os.umask(0o077);j=Journal(str(store));sel=selectors.DefaultSelector();listeners={};peers={};body=None;outbox=bytearray();deadline=time.monotonic()+duration;last_poll=0;last_ping=0;stopping=False
 def shutdown(*_):
  nonlocal stopping
  stopping=True
 signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
 def send(m):
  if body is None:raise RuntimeError('NO_BODY')
  outbox.extend(json.dumps(m,ensure_ascii=False).encode()+b'\n')
 e=Executor(j,resident,char_id,send)
 def close(c):
  nonlocal body
  if c is body:body=None;outbox.clear()
  try:sel.unregister(c)
  except Exception:pass
  peers.pop(c,None);c.close()
 def control(m):
  op=m.get('op');j.log(op,m)
  if op=='status':return {'resident':resident,'epoch':e.epoch,'telemetry':e.telemetry,'age':time.time()-e.received if e.received else None,'projection':e.projection,'actions':e.active()}
  if op=='run':
   if m.get('resident',resident)!=resident:raise ValueError('RESIDENT')
   return e.run(m['skill'],m['params'],m.get('deadline',60),m.get('idem'))
  if op=='action':return dict(j.action(m['action_id']),trace=j.trace(m['action_id']))
  if op=='actions':return j.actions()
  if op=='trace':return j.trace(m['action_id'])
  if op=='cancel':return e.cancel(m['action_id'])
  if op=='safe_stop':e.safe_stop();return {'queued':True}
  if op=='resend':
   a=j.action(m['action_id']);send({'proto':1,'type':'skill_start','action_id':a['action_id'],'idem_key':a['idem_key'],'skill':a['skill'],'params':json.loads(a['params']),'deadline_ts':a['deadline'],'expect_epoch':a['epoch']});return {'queued':True}
  raise ValueError('OPERATION')
 try:
  for name,p in (('body',path),('control',pathlib.Path(str(path)+'.control'))):
   if p.exists():raise RuntimeError('Socket exists; explicit cleanup required')
   s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.bind(str(p));os.chmod(p,0o600);s.listen(8);s.setblocking(False);listeners[s]=(name,p,p.lstat().st_ino);sel.register(s,selectors.EVENT_READ)
  if witness:
   if not j.cursor('witness'):
    baseline=int(query(DEFAULTS,f'SELECT COALESCE(MAX(id),0) FROM residents.resident_events WHERE char_id={char_id}').strip());j.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?)',('witness',baseline));j.db.commit()
   if not j.cursor('chat'):
    baseline=int(query(DEFAULTS,f'SELECT COALESCE(MAX(id),0) FROM ro_residents_logs.chatlog WHERE src_charid={char_id}').strip());j.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?)',('chat',baseline));j.db.commit()
  print(json.dumps({'status':'ready','resident':resident}),flush=True)
  while not stopping and time.monotonic()<deadline:
   for key,_ in sel.select(.05):
    c=key.fileobj
    if c in listeners:
     new,_=c.accept();uid=struct.unpack('3i',new.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
     if uid!=os.getuid():new.close();continue
     new.setblocking(False);peers[new]={'role':listeners[c][0],'buf':bytearray(),'session':Session(resident,char_id)};sel.register(new,selectors.EVENT_READ);continue
    p=peers[c]
    try:data=c.recv(8192)
    except BlockingIOError:continue
    except OSError:close(c);continue
    if not data:close(c);continue
    p['buf'].extend(data)
    if len(p['buf'])>65536:close(c);continue
    while b'\n' in p['buf']:
     line,_,rest=p['buf'].partition(b'\n');p['buf']=bytearray(rest)
     try:
      m=json.loads(line)
      if p['role']=='control':
       try:reply={'ok':True,'data':control(m)}
       except (ValueError,KeyError,RuntimeError) as err:reply={'ok':False,'code':str(err)}
       c.settimeout(.2);c.sendall(json.dumps(reply,ensure_ascii=False).encode()+b'\n');close(c);break
      p['session'].accept(m)
      if m['type']=='hello':
       if body is not None and body is not c:raise ValueError('BODY_ALREADY_CONNECTED')
       body=c
      j.event('body',m['body_epoch']+':'+str(m['seq']),m['ts'],m);e.body(m)
     except (ValueError,KeyError,TypeError,OSError) as err:
      j.log('peer_rejected',{'reason':type(err).__name__});close(c);break
   now=time.time()
   if body is not None and now-last_ping>=1:send({'proto':1,'type':'heartbeat'});last_ping=now
   if body is not None and outbox:
    try:n=body.send(outbox,socket.MSG_NOSIGNAL|socket.MSG_DONTWAIT);del outbox[:n]
    except BlockingIOError:pass
    except OSError:close(body)
   if witness and now-last_poll>=1:
    last_poll=now
    try:
     for r in get_events(DEFAULTS,char_name,j.cursor('witness')):
      r['event_ts']=datetime.datetime.fromisoformat(r['ts_utc']).timestamp();e.witness(r)
     cursor=j.cursor('chat')
     sql=f"SELECT JSON_OBJECT('id',id,'ts',CAST(time AS CHAR),'type',type,'char_id',src_charid,'to',dst_charname,'text',message) FROM ro_residents_logs.chatlog WHERE src_charid={char_id} AND id>{cursor} ORDER BY id LIMIT 200"
     from zoneinfo import ZoneInfo
     for line in query(DEFAULTS,sql).splitlines():
      r=json.loads(line);ts=datetime.datetime.fromisoformat(r['ts']).replace(tzinfo=ZoneInfo('Europe/Amsterdam')).timestamp();j.event('chat',r['id'],ts,r,r['id'])
     cursor=j.cursor('picklog');sql=f"SELECT JSON_OBJECT('id',id,'ts',CAST(time AS CHAR),'char_id',char_id,'type',type,'item_id',nameid,'amount',amount,'map',map) FROM ro_residents_logs.picklog WHERE char_id={char_id} AND id>{cursor} ORDER BY id LIMIT 200"
     for line in query(DEFAULTS,sql).splitlines():
      r=json.loads(line);ts=datetime.datetime.fromisoformat(r['ts']).replace(tzinfo=ZoneInfo('Europe/Amsterdam')).timestamp();j.event('picklog',r['id'],ts,r,r['id'])
    except (RuntimeError,ValueError) as err:j.log('witness_read_error',{'reason':type(err).__name__})
   e.evaluate()
 finally:
  for c in list(peers):close(c)
  for s,(_,p,inode) in listeners.items():
   s.close()
   if p.exists() and p.lstat().st_ino==inode:p.unlink()
  sel.close();j.close()

def main():
 p=argparse.ArgumentParser();p.add_argument('--socket',required=True);p.add_argument('--store',required=True);p.add_argument('--resident',default='resident_a');p.add_argument('--char-id',type=int,default=150001);p.add_argument('--char-name',default='ResidentA');p.add_argument('--duration',type=float,default=1800);p.add_argument('--no-witness',action='store_true');a=p.parse_args()
 if not 0<a.duration<=28800 or a.char_id<=0:p.error('bounded runtime and positive char id required')
 serve(a.socket,a.store,a.resident,a.char_id,a.char_name,a.duration,not a.no_witness)
if __name__=='__main__':main()
