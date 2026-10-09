"""Opt-in bounded RSS/interval-CPU sampler; no service changes. Raw output private."""
import argparse,json,os,pathlib,subprocess,time
p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--seconds',type=int,default=1800);a=p.parse_args()
if not 1<=a.seconds<=3600:raise SystemExit('bounded duration required')
out=pathlib.Path(a.out);out.parent.mkdir(parents=True,exist_ok=True);start=time.monotonic();previous={};hz=os.sysconf('SC_CLK_TCK')
with out.open('w') as f:
 os.chmod(out,0o600)
 while True:
  now=time.monotonic();rows=[]
  for line in subprocess.check_output(['ps','-eo','pid=,comm=,rss=,args='],text=True).splitlines():
   parts=line.strip().split(None,3)
   if len(parts)!=4:continue
   pid,comm,rss,args=parts;pid=int(pid)
   if not(comm in ('mariadbd','login-server','char-server','map-server') or(comm=='perl' and '--control=/root/ragnarok/run/B/resident/control' in args)):continue
   try:
    fields=pathlib.Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split();ticks=int(fields[11])+int(fields[12]);birth=fields[19]
   except(FileNotFoundError,ProcessLookupError):continue
   old=previous.get((pid,birth));cpu=(ticks-old[0])/hz/(now-old[1])*100 if old else None;previous[(pid,birth)]=(ticks,now)
   rows.append(dict(pid=pid,comm=comm,rss_kib=int(rss),cpu_interval_pct=cpu))
  f.write(json.dumps(dict(elapsed=now-start,ts=time.time(),processes=rows))+'\n');f.flush()
  if now-start>=a.seconds:break
  time.sleep(min(10,a.seconds-(now-start)))
