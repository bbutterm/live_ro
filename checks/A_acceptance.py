#!/usr/bin/env python3
"""Веха A: проверка фактических записей изолированного стенда, не генератор данных."""
import json,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).parents[1]
sys.path.insert(0,str(ROOT))
from tools.events import get_events,DEFAULTS,query

def main():
 gates={}
 states=subprocess.check_output(['systemctl','is-active','ro-residents-login','ro-residents-char','ro-residents-map','mariadb'],text=True).splitlines()
 ports=subprocess.check_output(['ss','-ltnH'],text=True)
 gates['01_services_loopback']=states==['active']*4 and all('127.0.0.1:'+str(p) in ports for p in (6900,6121,5121))
 rows=get_events(DEFAULTS,'Tester',5061)
 gates['01b_tester_prontera']=query(DEFAULTS,'SELECT online FROM ro_residents_main.char WHERE char_id=150000').strip()=='1' and any(r['kind']=='loadmap' and r['map']=='prontera' and r['x']==150 and r['y']==150 for r in rows)
 registry=query(DEFAULTS,'SELECT char_id,resident_id,enabled FROM residents.registry WHERE char_id=150001').strip().split('\t')
 gates['02_registry_config']=registry==['150001','resident_a','1'] and 'char_id: 150001' in (ROOT/'residents.yaml').read_text()
 n=int(query(DEFAULTS,"SELECT COUNT(*) FROM ro_residents_logs.picklog WHERE char_id=150000 AND id>2 AND type='S' AND nameid=501 AND amount=1 AND map='prt_in'"))
 gates['03_native_shop_purchase']=n>=1
 gates['04_relog']=any(r['kind']=='logout' and r['id']>5072 for r in rows) and any(r['kind']=='login' and r['id']>5072 for r in rows)
 ordered=[r for r in rows if r['kind'] in ('kill','die','loadmap') and r['id']>5066]
 gates['05_native_field_events']=len(ordered)>=3 and [(r['kind'],r['map']) for r in ordered[:3]]==[('kill','prt_fild08'),('die','prt_fild08'),('loadmap','prontera')] and ordered[0]['a1']==1002
 found=subprocess.run([sys.executable,str(ROOT/'tools/wait_event.py'),'--char','Tester','--kind','kill','--map','prt_fild08','--after','5066','--timeout','0'],capture_output=True).returncode
 missing=subprocess.run([sys.executable,str(ROOT/'tools/wait_event.py'),'--char','Tester','--kind','not_present','--timeout','.2'],capture_output=True).returncode
 gates['06_stream_order_wait']=found==0 and missing==1 and all(a['id']<b['id'] for a,b in zip(rows,rows[1:]))
 resources=pathlib.Path('/root/ragnarok/evidence/07/resources.txt')
 gates['07_resource_snapshot']=resources.is_file() and resources.stat().st_size>100
 print(json.dumps({'gates':gates,'result':'PASS' if all(gates.values()) else 'FAIL'},indent=2))
 return 0 if all(gates.values()) else 1
if __name__=='__main__':raise SystemExit(main())
