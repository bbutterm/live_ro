#!/usr/bin/env python3
"""Prepare one operator-controlled tester account and its OpenKore profile (idempotent)."""
from pathlib import Path
import secrets,shutil,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.accounts import ensure_account,set_config
r=Path('/root/ragnarok');ok=r/'repos/openkore';p=r/'run/tester';p.mkdir(mode=0o700,parents=True,exist_ok=True);p.chmod(0o700)
cred=ensure_account(p/'credentials.json','ro_a_tester','M','tester@localhost',99)
if not (p/'control').exists():shutil.copytree(ok/'control',p/'control')
(p/'tables').mkdir(exist_ok=True);(p/'plugins').mkdir(exist_ok=True);(r/'logs/tester').mkdir(parents=True,exist_ok=True)
f=p/'tables/servers.txt'
if not f.exists():
 f.write_text('[ROResidentsLab]\nip 127.0.0.1\nport 6900\nmaster_version 1\nversion 20180620\nserverType kRO_RagexeRE_2018_06_20e\naddTableFolders kRO/RagexeRE_2018_06_21a;kRO\nserverEncoding UTF-8\ncharBlockSize 155\npinCode 0\n');f.chmod(0o600)
set_config(p/'control/config.txt',{'master':'ROResidentsLab','server':'0','username':cred['username'],'password':cred['password'],'char':'0','attackAuto':'0','route_randomWalk':'0','itemsTakeAuto':'0','itemsGatherAuto':'0','storageAuto':'0','sellAuto':'0','buyAuto':'0','teleportAuto_hp':'0','teleportAuto_portal':'0','secureAdminPassword':'0','adminPassword':secrets.token_hex(12),'autoMake':'0','autoTalkCont':'0','logConsole':'1'})
print('Tester account prepared; group99 only tester; character to be created through normal client protocol; profile secrets local.')
