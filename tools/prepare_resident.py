#!/usr/bin/env python3
"""One new group0 account/profile; refuses existing account. No char/game-state SQL edits."""
from pathlib import Path
import json,re,secrets,shutil,subprocess
r=Path('/root/ragnarok'); p=r/'run/resident'; p.mkdir(mode=0o700,exist_ok=True);p.chmod(0o700)
q=subprocess.run(['mariadb','-NBe',"SELECT COUNT(*) FROM ro_residents_main.login WHERE userid='ro_resident_a'"],capture_output=True,text=True,check=True)
if int(q.stdout):raise RuntimeError('Resident exists; refusing overwrite')
cred={'username':'ro_resident_a','password':secrets.token_hex(10)}
subprocess.run(['mariadb','ro_residents_main'],input=f"INSERT INTO login(userid,user_pass,sex,email,group_id) VALUES ('{cred['username']}','{cred['password']}','M','resident@localhost',0)",text=True,capture_output=True,check=True)
shutil.copytree(r/'run/tester/control',p/'control',dirs_exist_ok=False)
shutil.copytree(r/'run/tester/tables',p/'tables',dirs_exist_ok=False)
(p/'plugins').mkdir();(r/'logs/resident').mkdir(exist_ok=True)
f=p/'control/config.txt';s=f.read_text()
for key,value in {'username':cred['username'],'password':cred['password'],'char':'0','adminPassword':secrets.token_hex(12)}.items():s=re.sub(r'^'+key+r'\s+.*$',key+' '+value,s,flags=re.M)
f.write_text(s);f.chmod(0o600)
f=p/'credentials.json';f.write_text(json.dumps(cred));f.chmod(0o600)
print('Normal group0 account prepared; character must be created via protocol. Credentials local only.')
