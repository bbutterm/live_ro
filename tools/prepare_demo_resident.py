"""Explicit account/profile provisioning only; characters are created by native client."""
import argparse,json,pathlib,re,secrets,shutil,subprocess
ROOT=pathlib.Path('/root/ragnarok')
def validate(identity,name,sex):
 if identity not in ('resident_b','resident_c') or not re.fullmatch('[A-Za-z][A-Za-z0-9]{2,19}',name) or sex not in ('M','F'):raise ValueError('INVALID_IDENTITY')
def prepare(identity,name,sex):
 """Idempotent: the password is saved before the account row; a rerun finishes missing steps."""
 import sys;sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
 from tools.accounts import ensure_account,set_config
 validate(identity,name,sex);p=ROOT/'run/Demo'/identity;user='ro_demo_'+identity
 p.mkdir(parents=True,mode=0o700,exist_ok=True);p.chmod(0o700)
 cred=ensure_account(p/'credentials.json',user,sex,'demo@localhost',0)
 base=ROOT/'run/B/resident'
 for d in ('control','tables'):
  if not (p/d).exists():shutil.copytree(base/d,p/d)
 if not (p/'fields').exists():(p/'fields').symlink_to(base/'fields',target_is_directory=True)
 set_config(p/'control/config.txt',{'username':user,'password':cred['password'],'char':'0','adminPassword':secrets.token_hex(12),'attackAuto':'-1','route_randomWalk':'0','lockMap':'','itemsTakeAuto':'0','autoMake':'0'})
 f=p/'tables/servers.txt';f.write_text(f.read_text().replace('serverEncoding Western','serverEncoding UTF-8'));f.chmod(0o600)
 (p/'identity.json').write_text(json.dumps({'resident':identity,'name':name,'sex':sex}));(p/'identity.json').chmod(0o600)
 print('PREPARED',identity,name,'group0; native character creation required')
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--resident',required=True);a.add_argument('--name',required=True);a.add_argument('--sex',required=True);v=a.parse_args();prepare(v.resident,v.name,v.sex)
