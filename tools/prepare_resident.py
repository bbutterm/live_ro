#!/usr/bin/env python3
"""One group0 account/profile for ResidentA (idempotent). No char/game-state SQL edits."""
from pathlib import Path
import secrets,shutil,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.accounts import ensure_account,set_config
r=Path('/root/ragnarok'); p=r/'run/resident'; p.mkdir(mode=0o700,parents=True,exist_ok=True);p.chmod(0o700)
cred=ensure_account(p/'credentials.json','ro_resident_a','M','resident@localhost',0)
for d in ('control','tables'):
 if not (p/d).exists():shutil.copytree(r/'run/tester'/d,p/d)
(p/'plugins').mkdir(exist_ok=True);(r/'logs/resident').mkdir(parents=True,exist_ok=True)
set_config(p/'control/config.txt',{'username':cred['username'],'password':cred['password'],'char':'0','adminPassword':secrets.token_hex(12)})
print('Normal group0 account prepared; character must be created via protocol. Credentials local only.')
