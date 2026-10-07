#!/usr/bin/env python3
"""Exit0 on actual event, exit1 on timeout, exit2 on reader failure."""
import argparse,json,time,subprocess
from events import DEFAULTS,get_events

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--char',required=True);p.add_argument('--kind',required=True);p.add_argument('--map');p.add_argument('--after',type=int,default=0);p.add_argument('--timeout',type=float,default=10);p.add_argument('--defaults-file',default=DEFAULTS)
 a=p.parse_args()
 if not 0<=a.timeout<=300 or a.after<0:p.error('timeout0..300 and cursor>=0 required')
 deadline=time.monotonic()+a.timeout
 while True:
  try:rows=get_events(a.defaults_file,a.char,a.after,kind=a.kind)
  except (RuntimeError,ValueError,subprocess.TimeoutExpired):return 2
  for r in rows:
   if not a.map or r['map']==a.map:print(json.dumps(r));return 0
  if time.monotonic()>=deadline:return 1
  time.sleep(.1)
if __name__=='__main__':raise SystemExit(main())
