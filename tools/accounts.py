"""Idempotent game-account provisioning shared by the prepare_* tools.

Order matters: the secret is saved (0600) BEFORE the account row exists, so a crash between
the steps never leaves an account whose password is lost (fixing it would need SQL on
accounts, which the project rules forbid). A rerun finishes whatever is missing.
"""
import json,os,pathlib,re,secrets,subprocess

def mariadb(sql,db=None):
    r=subprocess.run(['mariadb','-NB']+([db] if db else []),input=sql,text=True,capture_output=True)
    if r.returncode:raise RuntimeError('MariaDB operation failed (details withheld to protect secrets)')
    return r.stdout

def credentials(path,username):
    """Load the saved secret or create and save a new one first."""
    path=pathlib.Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.exists():
        cred=json.loads(path.read_text())
        if cred.get('username')!=username:raise RuntimeError('CREDENTIALS_FOR_ANOTHER_ACCOUNT')
        return cred,False
    cred={'username':username,'password':secrets.token_hex(10)}
    tmp=path.with_name(path.name+'.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:f.write(json.dumps(cred))
    os.replace(tmp,path);return cred,True

def ensure_account(cred_path,username,sex,email,group_id):
    """Account row exists and its password is the saved one. Returns the credentials."""
    if not re.fullmatch(r'[a-z0-9_]{4,23}',username) or sex not in ('M','F') or type(group_id) is not int:raise ValueError('INVALID_ACCOUNT')
    cred,fresh=credentials(cred_path,username)
    exists=int(mariadb(f"SELECT COUNT(*) FROM ro_residents_main.login WHERE userid='{username}'").strip() or 0)
    if exists:
        if fresh:raise RuntimeError('ACCOUNT_EXISTS_WITHOUT_SAVED_PASSWORD: '+username)
        return cred
    mariadb(f"INSERT INTO login(userid,user_pass,sex,email,group_id) VALUES ('{username}','{cred['password']}','{sex}','{email}',{group_id});",'ro_residents_main')
    return cred

def set_config(path,values):
    """OpenKore control/config.txt: replace or append `key value` lines; keeps 0600."""
    path=pathlib.Path(path);text=path.read_text()
    for key,value in values.items():
        pattern=r'^'+re.escape(key)+r'(?:[ \t]+.*)?$'
        line=(key+' '+str(value)).rstrip()
        text=re.sub(pattern,lambda _:line,text,flags=re.M) if re.search(pattern,text,re.M) else text.rstrip('\n')+'\n'+line+'\n'
    path.write_text(text);path.chmod(0o600)
