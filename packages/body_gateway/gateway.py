"""BodyGateway v0: закрытый Unix endpoint, durable телеметрия; действий пока нет."""
import argparse,json,os,selectors,socket,sqlite3,struct,time,pathlib,signal
from .protocol import Session

def serve(path,store,resident,char_id,duration):
 path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True);os.umask(0o077)
 if path.exists():raise RuntimeError('SOCKET_EXISTS: inspect owning process before cleanup')
 db=sqlite3.connect(store);db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE IF NOT EXISTS events(source_key TEXT PRIMARY KEY,received REAL NOT NULL,payload TEXT NOT NULL)');db.commit()
 server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);sel=selectors.DefaultSelector();peers={};stop=False
 def shutdown(*_):
  nonlocal stop
  stop=True
 signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
 owned_inode=None
 try:
  server.bind(str(path));owned_inode=path.lstat().st_ino;os.chmod(path,0o600);server.listen(1);server.setblocking(False);sel.register(server,selectors.EVENT_READ)
  end=time.monotonic()+duration
  print(json.dumps({'gateway':'ready','resident':resident}),flush=True)
  while not stop and time.monotonic()<end:
   for key,_ in sel.select(.1):
    c=key.fileobj
    assert isinstance(c,socket.socket)
    if c is server:
     peer,_=server.accept();uid=struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
     if uid!=os.getuid() or peers:peer.close();continue
     peer.setblocking(False);peers[peer]=[bytearray(),Session(resident,char_id)];sel.register(peer,selectors.EVENT_READ);continue
    buffer,session=peers[c]
    try:
     data=c.recv(8192)
     if not data:raise ConnectionError('EOF')
     buffer.extend(data)
     if len(buffer)>65536:raise ValueError('FRAME_LIMIT')
     while b'\n' in buffer:
      line,_,rest=buffer.partition(b'\n');buffer[:]=rest
      msg=session.accept(json.loads(line));received=time.time();payload=json.dumps(msg,separators=(',',':'),sort_keys=True)
      db.execute('INSERT INTO events VALUES(?,?,?)',(f"{msg['body_epoch']}:{msg['seq']}",received,payload));db.commit()
      print(json.dumps({'received':received,'body':msg},separators=(',',':')),flush=True)
    except (ValueError,TypeError,ConnectionError,OSError,UnicodeError) as error:
     sel.unregister(c);c.close();del peers[c]
     if str(error)!='EOF':print(json.dumps({'gateway':'peer_rejected','reason':type(error).__name__}),flush=True)
 finally:
  for peer in peers:peer.close()
  sel.close();server.close();db.close()
  if owned_inode is not None and path.exists() and path.lstat().st_ino==owned_inode:path.unlink()

def main():
 p=argparse.ArgumentParser();p.add_argument('--socket',required=True);p.add_argument('--store',required=True);p.add_argument('--resident',required=True);p.add_argument('--char-id',type=int,required=True);p.add_argument('--duration',type=float,default=300)
 a=p.parse_args()
 if not 0<a.duration<=1800:p.error('duration must be (0,1800]')
 serve(a.socket,a.store,a.resident,a.char_id,a.duration)
if __name__=='__main__':main()
