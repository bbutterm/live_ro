"""Daily backup of what cannot be recreated: the characters (MariaDB) and the residents'
memory (/var/lib/ro-world/*). Started by the supervisor in a thread (no cron), kept as
7 daily + 4 weekly (Sunday) archives in backup_dir (default /var/backups/ro-world)."""
import datetime,io,json,os,pathlib,subprocess,tarfile,time
DATABASES=('ro_residents_main','residents')
MEMORY=('*.json','*/diary.json','*/plan.json','*/history.json','*/memory.json','*/brain.json')

def due(directory,now=None,every=86400):
    now=time.time() if now is None else now
    found=sorted(pathlib.Path(directory).glob('ro-world-*.tar.gz'))
    return not found or now-found[-1].stat().st_mtime>=every

def make(state,directory,now=None,dump=None):
    """One archive: dump.sql (both schemas, consistent per table) + resident memory files."""
    now=time.time() if now is None else now;directory=pathlib.Path(directory);directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    name=directory/('ro-world-'+datetime.datetime.fromtimestamp(now,datetime.timezone.utc).strftime('%Y%m%d-%H%M')+'.tar.gz')
    sql=(dump or _dump)()
    tmp=name.with_name(name.name+'.tmp')
    with tarfile.open(tmp,'w:gz') as tar:
        info=tarfile.TarInfo('dump.sql');info.size=len(sql);info.mtime=int(now);tar.addfile(info,io.BytesIO(sql))
        for pattern in MEMORY:
            for p in sorted(pathlib.Path(state).glob(pattern)):
                if p.is_file():tar.add(p,arcname='state/'+str(p.relative_to(state)))
    os.chmod(tmp,0o600);os.replace(tmp,name)
    prune(directory,now);return name

def _dump():
    r=subprocess.run(['mariadb-dump','--databases',*DATABASES,'--routines','--events','--add-drop-database'],capture_output=True,timeout=1800)
    if r.returncode:raise RuntimeError('mariadb-dump failed')
    return r.stdout

def prune(directory,now=None):
    """Keep the 7 newest daily archives and one per week (Sunday) for 4 weeks."""
    now=time.time() if now is None else now
    files=sorted(pathlib.Path(directory).glob('ro-world-*.tar.gz'),reverse=True)
    keep=set(files[:7])
    for p in files:
        day=datetime.datetime.strptime(p.name[9:17],'%Y%m%d').replace(tzinfo=datetime.timezone.utc)
        if day.weekday()==6 and now-day.timestamp()<=29*86400:keep.add(p)
    for p in files:
        if p not in keep:p.unlink(missing_ok=True)
