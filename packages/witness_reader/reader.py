#!/usr/bin/env python3
"""Witness reader v0: SELECT-only, raw DB timestamps, bounded observation."""
import argparse,json,subprocess,time

def read_events(defaults_file,after):
    after=int(after)
    if after<0: raise ValueError('Negative cursor')
    sql="SELECT JSON_OBJECT('id',id,'ts',CAST(ts AS CHAR),'char_id',char_id,'kind',kind,'map',map,'x',x,'y',y,'a1',a1,'a2',a2) FROM residents.resident_events WHERE id > %d ORDER BY id LIMIT 200" % after
    p=subprocess.run(['mariadb','--defaults-extra-file='+defaults_file,'--batch','--raw','--skip-column-names'],input=sql,text=True,capture_output=True,timeout=8)
    if p.returncode: raise RuntimeError('Reader database connection/query failed (credential-bearing details withheld)')
    return [json.loads(l) for l in p.stdout.splitlines() if l]

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--defaults-file',required=True)
    ap.add_argument('--after',type=int,default=0)
    ap.add_argument('--duration',type=float,default=0,help='Seconds of observation; 0 = one batch')
    args=ap.parse_args()
    if args.after<0 or not 0<=args.duration<=300: ap.error('cursor >=0; duration 0..300')
    cursor=args.after;deadline=time.monotonic()+args.duration
    while True:
        rows=read_events(args.defaults_file,cursor)
        for row in rows:
            if row['id']<=cursor: raise RuntimeError('Nonmonotonic source cursor')
            print(json.dumps(row,ensure_ascii=False),flush=True);cursor=row['id']
        if time.monotonic()>=deadline:break
        time.sleep(min(.5,max(0,deadline-time.monotonic())))

if __name__=='__main__':main()
