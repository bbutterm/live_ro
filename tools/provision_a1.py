#!/usr/bin/env python3
"""One-shot isolated initial schema/config provisioning. No existing DB mutation."""
from pathlib import Path
import subprocess, secrets, json, os
root=Path('/root/ragnarok'); repo=root/'repos/rathena'
names=['ro_residents_main','ro_residents_logs']
def sql(text, db=None):
    cmd=['mariadb','-N']+([db] if db else [])
    r=subprocess.run(cmd,input=text,text=True,capture_output=True)
    if r.returncode: raise RuntimeError('MariaDB operation failed (details withheld to protect secrets)')
    return r.stdout
existing=sql('SHOW DATABASES').splitlines()
if any(n in existing for n in names): raise RuntimeError('Dedicated schema already exists; refusing overwrite')
if sql("SELECT user FROM mysql.user WHERE user='ro_residents_srv'").strip(): raise RuntimeError('Dedicated user already exists')
private=root/'run/private'; private.mkdir(mode=0o700,parents=True,exist_ok=True); private.chmod(0o700)
secrets_data={'db_password':secrets.token_hex(20),'inter_user':'ro_internal','inter_password':secrets.token_hex(10)}
p=private/'server.json'; p.write_text(json.dumps(secrets_data)); p.chmod(0o600)
for n in names: sql(f'CREATE DATABASE `{n}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;')
for f in ['main.sql','web.sql','roulette_default_data.sql']: sql((repo/'sql-files'/f).read_text(),names[0])
sql((repo/'sql-files/logs.sql').read_text(),names[1])
pw=secrets_data['db_password']
sql(f"CREATE USER 'ro_residents_srv'@'127.0.0.1' IDENTIFIED BY '{pw}';"+''.join(f"GRANT ALL PRIVILEGES ON `{n}`.* TO 'ro_residents_srv'@'127.0.0.1';" for n in names))
sql(f"UPDATE login SET userid='{secrets_data['inter_user']}', user_pass='{secrets_data['inter_password']}' WHERE account_id=1 AND sex='S';",names[0])
imp=repo/'conf/import';imp.mkdir(exist_ok=True)
def conf(name,text):
    p=imp/name
    if p.exists() and p.stat().st_size: raise RuntimeError('Nonempty config exists: '+name)
    p.write_text(text);p.chmod(0o600)
text=''
for prefix in ['login_server','ipban_db','char_server','map_server','web_server','log_db']:
    is_log=prefix=='log_db'; db=names[1] if is_log else names[0]
    key='db' if prefix in ['ipban_db','log_db'] else 'db'
    text+=f'{prefix}_ip: 127.0.0.1\n{prefix}_port: 3306\n{prefix}_id: ro_residents_srv\n{prefix}_pw: {pw}\n{prefix}_db: {db}\n'
conf('inter_conf.txt',text)
conf('login_conf.txt','bind_ip: 127.0.0.1\nlogin_port: 6900\nnew_account: no\n')
common=f"userid: {secrets_data['inter_user']}\npasswd: {secrets_data['inter_password']}\nbind_ip: 127.0.0.1\n"
conf('char_conf.txt',common+'login_ip: 127.0.0.1\nchar_ip: 127.0.0.1\nlogin_port: 6900\nchar_port: 6121\nserver_name: ROResidentsLab\n')
conf('map_conf.txt',common+'char_ip: 127.0.0.1\nmap_ip: 127.0.0.1\nchar_port: 6121\nmap_port: 5121\n')
print('Created dedicated main/log schemas; pinned SQL imported; dedicated server user; loopback-only configuration; secrets local 0600.')
print('MAIN_TABLES',sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='ro_residents_main'").strip())
