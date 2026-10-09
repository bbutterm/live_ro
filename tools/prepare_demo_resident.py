"""Explicit account/profile provisioning only; characters are created by native client."""
import argparse,json,pathlib,re,secrets,shutil,subprocess
ROOT=pathlib.Path('/root/ragnarok')
def validate(identity,name,sex):
 if identity not in ('resident_b','resident_c') or not re.fullmatch('[A-Za-z][A-Za-z0-9]{2,19}',name) or sex not in ('M','F'):raise ValueError('INVALID_IDENTITY')
def prepare(identity,name,sex):
 validate(identity,name,sex);p=ROOT/'run/Demo'/identity;user='ro_demo_'+identity
 if p.exists():raise RuntimeError('PROFILE_EXISTS')
 q=subprocess.run(['mariadb','-NBe',f"SELECT COUNT(*) FROM ro_residents_main.login WHERE userid='{user}'"],capture_output=True,text=True,check=True)
 if int(q.stdout):raise RuntimeError('ACCOUNT_EXISTS')
 p.mkdir(parents=True,mode=0o700);p.chmod(0o700);pw=secrets.token_hex(10)
 subprocess.run(['mariadb','ro_residents_main'],input=f"INSERT INTO login(userid,user_pass,sex,email,group_id) VALUES ('{user}','{pw}','{sex}','demo@localhost',0);",capture_output=True,text=True,check=True)
 base=ROOT/'run/B/resident';shutil.copytree(base/'control',p/'control');shutil.copytree(base/'tables',p/'tables');(p/'fields').symlink_to(base/'fields',target_is_directory=True)
 f=p/'control/config.txt';s=f.read_text()
 for key,value in {'username':user,'password':pw,'char':'0','adminPassword':secrets.token_hex(12),'attackAuto':'-1','route_randomWalk':'0','lockMap':'','itemsTakeAuto':'0','autoMake':'0'}.items():
  pattern=r'^'+key+r'(?:\s+.*)?$';s=re.sub(pattern,key+' '+value,s,flags=re.M) if re.search(pattern,s,re.M) else s+'\n'+key+' '+value+'\n'
 f.write_text(s);f.chmod(0o600);f=p/'tables/servers.txt';f.write_text(f.read_text().replace('serverEncoding Western','serverEncoding UTF-8'));f.chmod(0o600)
 (p/'identity.json').write_text(json.dumps({'resident':identity,'name':name,'sex':sex}));(p/'identity.json').chmod(0o600)
 print('PREPARED',identity,name,'group0; native character creation required')
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--resident',required=True);a.add_argument('--name',required=True);a.add_argument('--sex',required=True);v=a.parse_args();prepare(v.resident,v.name,v.sex)
