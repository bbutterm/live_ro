#!/usr/bin/env python3
"""SELECT-only server event stream. Raw DB time is Europe/Amsterdam (SYSTEM)."""
import argparse,datetime,json,re,subprocess,time
from zoneinfo import ZoneInfo
DEFAULTS='/root/ragnarok/run/private/reader.cnf'
def query(defaults,sql):
 p=subprocess.run(['mariadb','--defaults-extra-file='+defaults,'-NBr'],input=sql,text=True,capture_output=True,timeout=8)
 if p.returncode: raise RuntimeError('SELECT-only query failed; private details withheld')
 return p.stdout

def get_events(defaults,char,after=0,since=None,kind=None):
 if not re.fullmatch(r'[\w -]{1,24}',char): raise ValueError('Invalid character name')
 if after<0:raise ValueError('Negative cursor')
 cid=query(defaults,"SELECT char_id FROM ro_residents_main.char WHERE name='"+char+"'").strip()
 if not cid.isdigit():raise ValueError('Character not found')
 where='char_id='+cid+' AND id>'+str(after)
 if since:
  dt=datetime.datetime.fromisoformat(since)
  if dt.tzinfo:dt=dt.astimezone(ZoneInfo('Europe/Amsterdam'))
  where+=" AND ts>='"+dt.strftime('%Y-%m-%d %H:%M:%S.%f')+"'"
 if kind:
  if not re.fullmatch(r'[a-z_]{1,16}',kind):raise ValueError('Invalid kind')
  where+=" AND kind='"+kind+"'"
 sql="SELECT JSON_OBJECT('id',id,'ts',CAST(ts AS CHAR),'char_id',char_id,'kind',kind,'map',map,'x',x,'y',y,'a1',a1,'a2',a2) FROM residents.resident_events WHERE "+where+' ORDER BY id LIMIT 200'
 rows=[json.loads(l) for l in query(defaults,sql).splitlines() if l]
 for row in rows:
  row['ts_utc']=datetime.datetime.fromisoformat(row['ts']).replace(tzinfo=ZoneInfo('Europe/Amsterdam')).astimezone(datetime.timezone.utc).isoformat()
  row['db_timezone']='Europe/Amsterdam'
 return rows

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--char',required=True);p.add_argument('--since');p.add_argument('--kind');p.add_argument('--after',type=int,default=0);p.add_argument('--defaults-file',default=DEFAULTS);p.add_argument('--follow',action='store_true');p.add_argument('--duration',type=float,default=30)
 a=p.parse_args()
 if not 0<=a.duration<=300 or a.after<0:p.error('duration0..300 and cursor>=0 required')
 cursor=a.after;deadline=time.monotonic()+a.duration
 while True:
  rows=get_events(a.defaults_file,a.char,cursor,a.since,a.kind)
  for row in rows:print(json.dumps(row,ensure_ascii=False),flush=True);cursor=row['id']
  if not a.follow or time.monotonic()>=deadline:break
  time.sleep(.25)
if __name__=='__main__':main()
