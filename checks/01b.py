#!/usr/bin/env python3
import json,subprocess
from pathlib import Path

def sql(q):
    p=subprocess.run(['mariadb','-NBe',q],capture_output=True,text=True,check=True,timeout=10)
    return p.stdout.strip()
checks={
 'tester_created_by_protocol': sql("SELECT COUNT(*) FROM ro_residents_main.char WHERE char_id=150000 AND name='Tester' AND class=0 AND base_level=1")=='1',
 'server_witness_logout_then_login': sql("SELECT COUNT(*) FROM residents.resident_events a JOIN residents.resident_events b ON b.char_id=a.char_id AND b.id>a.id WHERE a.kind='logout' AND b.kind='login' AND a.char_id=150000")!='0',
 'server_confirmed_two_positions': int(sql("SELECT COUNT(DISTINCT CONCAT(map,':',x,':',y)) FROM residents.resident_events WHERE char_id=150000 AND kind='pos'"))>=2,
 'reader_real_events': bool(Path('/root/ragnarok/evidence/01b/reader.jsonl').read_text().strip()),
}
print(json.dumps(checks,ensure_ascii=False,indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
