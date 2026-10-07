#!/usr/bin/env python3
"""Prepare one operator-controlled tester account and immutable test profile."""
from pathlib import Path
import subprocess,secrets,json,shutil,re
r=Path('/root/ragnarok');ok=r/'repos/openkore';p=r/'run/tester';p.mkdir(mode=0o700,exist_ok=True);p.chmod(0o700)
cred={'username':'ro_a_tester','password':secrets.token_hex(10)}
q=subprocess.run(['mariadb','-NBe',"SELECT COUNT(*) FROM ro_residents_main.login WHERE userid='ro_a_tester'"],capture_output=True,text=True,check=True)
if int(q.stdout):raise RuntimeError('Tester already exists; refusing reinitialization')
sql=f"INSERT INTO login (userid,user_pass,sex,email,group_id) VALUES ('{cred['username']}','{cred['password']}','M','tester@localhost',99);"
subprocess.run(['mariadb','ro_residents_main'],input=sql,text=True,check=True,capture_output=True)
f=p/'credentials.json';f.write_text(json.dumps(cred));f.chmod(0o600)
shutil.copytree(ok/'control',p/'control',dirs_exist_ok=True)
(p/'tables').mkdir(exist_ok=True);(p/'plugins').mkdir(exist_ok=True);(r/'logs/tester').mkdir(exist_ok=True)
f=p/'tables/servers.txt';f.write_text('[ROResidentsLab]\nip 127.0.0.1\nport 6900\nmaster_version 1\nversion 20180620\nserverType kRO_RagexeRE_2018_06_20e\naddTableFolders kRO/RagexeRE_2018_06_21a;kRO\nserverEncoding Western\ncharBlockSize 155\npinCode 0\n');f.chmod(0o600)
f=p/'control/config.txt';text=f.read_text();vals={'master':'ROResidentsLab','server':'0','username':cred['username'],'password':cred['password'],'char':'0','attackAuto':'0','route_randomWalk':'0','itemsTakeAuto':'0','itemsGatherAuto':'0','storageAuto':'0','sellAuto':'0','buyAuto':'0','teleportAuto_hp':'0','teleportAuto_portal':'0','secureAdminPassword':'0','adminPassword':secrets.token_hex(12),'autoMake':'0','autoTalkCont':'0','logConsole':'1'}
for key,value in vals.items():
 pattern=r'^'+re.escape(key)+r'(?:\s+.*)?$'
 if re.search(pattern,text,re.M):text=re.sub(pattern,key+' '+value,text,flags=re.M)
 else:text+='\n'+key+' '+value+'\n'
f.write_text(text);f.chmod(0o600)
print('Tester account prepared; group99 only tester; character to be created through normal client protocol; profile secrets local.')
