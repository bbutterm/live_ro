"""Reusable unattended acceptance window for the permanent world (no new runs)."""
import argparse,json,pathlib,subprocess,time
from .supervisor import ROOT,STATE,config,online,atomic
from packages.body_gateway.cli import request
from tools.events import query,DEFAULTS

def observe(seconds,phase):
    if seconds<3600:raise ValueError('P1 needs a full hour; short windows are not acceptance')
    started=time.time();end=time.monotonic()+seconds;failures=0;samples=0
    baseline=int(query(DEFAULTS,'SELECT COALESCE(MAX(id),0) FROM residents.resident_events').strip())
    rlist=[r for r in config()['residents'] if r.get('enabled',True)]
    report={'phase':phase,'started':started,'required_seconds':seconds,'residents':[r['name'] for r in rlist],'baseline':baseline,'samples':0,'failed_samples':0,'complete':False}
    while time.monotonic()<end:
        service=subprocess.run(['systemctl','is-active','--quiet','ro-world.service']).returncode==0
        healthy=service
        for r in rlist:
            try:healthy=online(request(str(STATE/r['resident_id']/'actions.sock.control'),{'op':'status'}),time.time()) and healthy
            except (OSError,RuntimeError,ValueError):healthy=False
        failures+=int(not healthy);samples+=1
        report.update(samples=samples,failed_samples=failures,observed_seconds=time.time()-started)
        atomic(STATE/'acceptance.json',report)
        time.sleep(min(5,max(0,end-time.monotonic())))
    ids=','.join(str(r['char_id']) for r in rlist)
    evidence=query(DEFAULTS,f"SELECT char_id,kind,COUNT(*) FROM residents.resident_events WHERE id>{baseline} AND char_id IN ({ids}) GROUP BY char_id,kind")
    report.update(complete=True,ended=time.time(),witness_counts=evidence,passed=failures==0)
    atomic(STATE/'acceptance.json',report)
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1

def main():
    p=argparse.ArgumentParser();p.add_argument('--seconds',type=float,default=3600);p.add_argument('--phase',default='P1');a=p.parse_args()
    raise SystemExit(observe(a.seconds,a.phase))
if __name__=='__main__':main()
