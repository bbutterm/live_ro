#!/usr/bin/env python3
"""Milestone A, step 1: running isolated servers, ports and schema."""
import pathlib, subprocess, sys, socket
root = pathlib.Path('/root/ragnarok/repos/rathena')
errors=[]
for name,port in [('login-server',6900),('char-server',6121),('map-server',5121)]:
    pids=[]
    for p in pathlib.Path('/proc').iterdir():
        if not p.name.isdigit(): continue
        try:
            if (p/'comm').read_text().strip()==name and (p/'cwd').resolve()==root: pids.append(p.name)
        except OSError: pass
    print(name,'project_pids',pids)
    if not pids: errors.append(name+' absent')
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=2): pass
        print('PORT',port,'PASS')
    except OSError: errors.append('port '+str(port)+' closed')
r=subprocess.run(['mariadb','-NBe',"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='ro_residents_main';"],capture_output=True,text=True)
print('MAIN_TABLES',r.stdout.strip())
if r.returncode or not r.stdout.strip().isdigit() or int(r.stdout)<30: errors.append('main schema absent/incomplete')
print('RESULT: FAIL: '+'; '.join(errors) if errors else 'RESULT: PASS')
sys.exit(bool(errors))
