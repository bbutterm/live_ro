#!/usr/bin/env python3
"""Lab-only wire regression: reject char-select without authentication, survive it."""
import socket,struct,subprocess,time

def state():
 p=subprocess.run(['systemctl','show','ro-residents-char','-p','MainPID','-p','ActiveState'],capture_output=True,text=True,check=True)
 return dict(l.split('=',1) for l in p.stdout.splitlines())

def main():
 before=state();assert before['ActiveState']=='active' and before['MainPID']!='0'
 with socket.create_connection(('127.0.0.1',6121),timeout=3) as s:
  s.sendall(struct.pack('<HB',0x66,0))
  try: closed=s.recv(32)==b''
  except ConnectionResetError: closed=True
 deadline=time.monotonic()+2;after=state()
 while time.monotonic()<deadline:
  after=state()
  if after!=before:break
  time.sleep(.1)
 assert after==before, 'FAIL: unauthenticated char-select crashed/restarted character server'
 assert closed,'FAIL: unauthenticated session was not rejected'
 print('PASS: unauthenticated char-select rejected; exact server PID survived')

if __name__=='__main__':main()
