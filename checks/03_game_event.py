#!/usr/bin/env python3
"""Real laboratory DB evidence gate, not an offline unit test."""
import argparse,subprocess

def main():
 p=argparse.ArgumentParser();p.add_argument('kind',choices=['loadmap','kill','die','purchase']);p.add_argument('--after',type=int,required=True);a=p.parse_args()
 assert a.after>=0
 if a.kind=='purchase':
  q=f"SELECT COUNT(*) FROM ro_residents_logs.picklog WHERE char_id=150001 AND id>{a.after} AND type='S' AND nameid=501 AND amount=1"
 else:
  detail=" AND map='guild_vs1'" if a.kind=='loadmap' else " AND a1=1002" if a.kind=='kill' else " AND a1>0"
  q=f"SELECT COUNT(*) FROM residents.resident_events WHERE char_id=150001 AND kind='{a.kind}' AND id>{a.after}"+detail
 n=int(subprocess.check_output(['mariadb','-NBe',q],text=True))
 assert n>0,f'FAIL: no fresh real {a.kind} evidence for ResidentA'
 print(f'PASS: fresh {a.kind} evidence rows={n}')

if __name__=='__main__':main()
