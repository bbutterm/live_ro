"""Operator CLI; bounded commands, no arbitrary OpenKore console execution."""
import argparse,json,socket,time,sys
from .journal import TERMINAL
DEFAULT_SOCKET='/root/ragnarok/run/B/actions.sock.control'
def request(path,msg):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(3);s.connect(path);s.sendall(json.dumps(msg,ensure_ascii=False).encode()+b'\n');data=bytearray()
  while b'\n' not in data:
   chunk=s.recv(8192)
   if not chunk:raise RuntimeError('CONTROL_CLOSED')
   data.extend(chunk)
   if len(data)>4*1024*1024:raise RuntimeError('CONTROL_TOO_LARGE')
 r=json.loads(data)
 if not r.get('ok'):raise RuntimeError(r.get('code','CONTROL_ERROR'))
 return r['data']
def main():
 p=argparse.ArgumentParser(prog='residents');p.add_argument('--socket',default=DEFAULT_SOCKET);sp=p.add_subparsers(dest='cmd',required=True)
 sp.add_parser('status');sp.add_parser('actions');sp.add_parser('safe_stop')
 for op in ('action','trace','cancel','resend'):
  q=sp.add_parser(op);q.add_argument('action_id')
 q=sp.add_parser('run');q.add_argument('resident');q.add_argument('skill',choices=('travel_to','whisper'));q.add_argument('--map');q.add_argument('--x',type=int);q.add_argument('--y',type=int);q.add_argument('--r',type=int,default=3);q.add_argument('--to-name');q.add_argument('--text');q.add_argument('--deadline',type=float,default=60);q.add_argument('--idem');q.add_argument('--wait',type=float,default=0)
 a=p.parse_args();m={'op':a.cmd}
 if a.cmd in ('action','trace','cancel','resend'):m['action_id']=a.action_id
 if a.cmd=='run':
  if a.skill=='travel_to':
   if not a.map:p.error('--map required')
   params={'map':a.map,'r':a.r}
   if a.x is not None:params['x']=a.x
   if a.y is not None:params['y']=a.y
  else:
   if not a.to_name or not a.text:p.error('--to-name and --text required')
   params={'to_name':a.to_name,'text':a.text}
  if not 0<=a.wait<=600:p.error('--wait 0..600')
  m.update(resident=a.resident,skill=a.skill,params=params,deadline=a.deadline,idem=a.idem)
 try:
  data=request(a.socket,m)
  if a.cmd=='run' and a.wait:
   end=time.monotonic()+a.wait
   while data['state'] not in TERMINAL and time.monotonic()<end:time.sleep(.25);data=request(a.socket,{'op':'action','action_id':data['action_id']})
  print(json.dumps(data,ensure_ascii=False,indent=2))
 except (OSError,RuntimeError,ValueError) as err:print(json.dumps({'error':str(err)}),file=sys.stderr);return 2
 return 0
if __name__=='__main__':sys.exit(main())
