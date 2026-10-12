#!/usr/bin/env python3
"""Новый мир RO «живые жители» на чистом Ubuntu 24.04 — одной командой.

    sudo python3 /root/ragnarok/repos/live_ro/tools/deploy_world.py

Повторяет docs/DEPLOY_NEW_WORLD.md, но каждая фаза сначала проверяет, сделана ли она,
поэтому запуск можно повторять после любой ошибки: готовое не трогается.
Секреты не печатаются. Ключ LLM вписывает владелец в .env; без ключа мир живёт на привычках.

    --only ФАЗА / --from ФАЗА   выполнить одну фазу / начиная с фазы
    --dev                       без systemd (контейнер разработчика): службы запускаются процессами
    --inspect                   только посмотреть, что уже есть (ничего не меняет)
    --adopt                     мир уже работает: игровой сервер не трогать, проверить жителей,
                                добавить реестр/пользователя follow и перезапустить мир на новом коде
"""
import argparse,datetime,json,os,pathlib,re,shutil,subprocess,sys,time

ROOT=pathlib.Path(__file__).resolve().parents[1]
BASE=pathlib.Path('/root/ragnarok');RATHENA=BASE/'repos/rathena';OPENKORE=BASE/'repos/openkore'
PRIVATE=BASE/'run/private';LOGS=BASE/'logs';STATE=pathlib.Path('/var/lib/ro-world')
PINS={'rathena':('https://github.com/rathena/rathena.git','e985006171d2eb320ee512a653f4c83aea3d81b6'),
      'openkore':('https://github.com/OpenKore/openkore.git','51de1ddfc4449ae5217f6886de702f87ca934030')}
PACKAGES=['git','build-essential','make','zlib1g-dev','libmariadb-dev','libmariadb-dev-compat','mariadb-server','mariadb-client','perl',
          'libperl-dev','libreadline-dev','libncurses-dev','libcurl4-openssl-dev','python3','python3-venv','python3-yaml','tzdata',
          'ca-certificates','tmux','acl','iproute2','openssl','sqlite3','jq']
CHARS=[('tester','Tester','M',150000,BASE/'run/tester'),('resident_a','ResidentA','M',150001,BASE/'run/Demo/resident_a'),
       ('resident_b','Mira','F',150002,BASE/'run/Demo/resident_b'),('resident_c','Borin','M',150003,BASE/'run/Demo/resident_c')]
PORTS={'login':6900,'char':6121,'map':5121}
DEV=False


class Stop(Exception):
    """Шаг не может продолжаться: причина и что сделать — по-русски."""


def log(msg):
    line=time.strftime('%H:%M:%S ')+msg;print(line,flush=True)
    try:
        LOGS.mkdir(parents=True,exist_ok=True)
        with (LOGS/'deploy.log').open('a') as f:f.write(line+'\n')
    except OSError:pass


def run(cmd,check=True,input=None,cwd=None,quiet=False,timeout=None,env=None):
    r=subprocess.run(cmd,input=input,cwd=cwd,text=True,capture_output=True,timeout=timeout,env=env)
    if check and r.returncode:
        tail=(r.stderr or r.stdout or '').strip().splitlines()[-5:]
        raise Stop('команда не прошла: '+' '.join(map(str,cmd))[:160]+('\n    '+'\n    '.join(tail) if tail and not quiet else ''))
    return r


def sql(text,db=None):
    r=subprocess.run(['mariadb','-NB']+([db] if db else []),input=text,text=True,capture_output=True)
    if r.returncode:raise Stop('MariaDB: запрос не прошёл (подробности скрыты, чтобы не показать секреты)')
    return r.stdout.strip()


def service(action,*units):
    if DEV:return
    run(['systemctl',action,*units])


def wait_port(port,seconds=90):
    end=time.time()+seconds
    while time.time()<end:
        if f'127.0.0.1:{port} ' in run(['ss','-ltnH'],check=False).stdout:return True
        time.sleep(1)
    return False


# ---------------------------------------------------------------- фазы
def phase_system():
    """Часовой пояс UTC, пакеты, swap, пользователь, каталоги."""
    if os.geteuid()!=0:raise Stop('нужен root: sudo python3 tools/deploy_world.py')
    if ROOT!=BASE/'repos/live_ro':raise Stop(f'репозиторий должен лежать в {BASE}/repos/live_ro (сейчас {ROOT}): службы и пути зашиты')
    if pathlib.Path('/etc/localtime').resolve().name not in ('UTC','Etc/UTC') and not str(pathlib.Path('/etc/localtime').resolve()).endswith('/UTC'):
        pathlib.Path('/etc/localtime').unlink(missing_ok=True);pathlib.Path('/etc/localtime').symlink_to('/usr/share/zoneinfo/UTC')
        pathlib.Path('/etc/timezone').write_text('UTC\n');log('часовой пояс: UTC (время свидетеля без перехода на зимнее время)')
    missing=[p for p in PACKAGES if run(['dpkg-query','-W','-f=${Status}',p],check=False).stdout!='install ok installed']
    if missing:
        log('ставлю пакеты: '+' '.join(missing))
        run(['apt-get','update','-qq'],env=dict(os.environ,DEBIAN_FRONTEND='noninteractive'))
        run(['apt-get','install','-y','-qq',*missing],env=dict(os.environ,DEBIAN_FRONTEND='noninteractive'),timeout=1800)
    mem=int(re.search(r'MemTotal:\s+(\d+)',pathlib.Path('/proc/meminfo').read_text()).group(1))//1024
    if not DEV and mem<6000 and not run(['swapon','--show','--noheadings'],check=False).stdout.strip():
        log('добавляю swap 2 ГБ (RAM %d МБ)'%mem)
        run(['fallocate','-l','2G','/swapfile']);os.chmod('/swapfile',0o600);run(['mkswap','/swapfile']);run(['swapon','/swapfile'])
        with open('/etc/fstab','a') as f:f.write('/swapfile none swap sw 0 0\n')
    if run(['id','ro-residents'],check=False).returncode:
        run(['useradd','--system','--user-group','--no-create-home','--shell','/usr/sbin/nologin','ro-residents'])
    for d,mode in ((BASE/'repos',0o755),(LOGS,0o755),(BASE/'run',0o700),(PRIVATE,0o700),(BASE/'run/B/resident',0o700),(BASE/'run/Demo',0o700),(pathlib.Path('/root/.hermes'),0o700)):
        d.mkdir(parents=True,exist_ok=True);d.chmod(mode)


def phase_sources():
    """rAthena и OpenKore на закреплённых коммитах (настоящие каталоги, не симлинки)."""
    for name,path in (('rathena',RATHENA),('openkore',OPENKORE)):
        url,pin=PINS[name]
        if path.is_symlink():raise Stop(f'{path} — симлинк; нужен настоящий каталог')
        if (path/'.git').exists() and run(['git','-C',str(path),'rev-parse','HEAD'],check=False).stdout.strip()==pin:continue
        if not (path/'.git').exists():
            run(['git','init','-q',str(path)]);run(['git','-C',str(path),'remote','add','origin',url])
        log(f'загружаю {name} {pin[:7]}')
        run(['git','-C',str(path),'fetch','-q','--depth','1','origin',pin],timeout=1800)
        run(['git','-C',str(path),'checkout','-q','--detach','FETCH_HEAD'])
        if run(['git','-C',str(path),'rev-parse','HEAD']).stdout.strip()!=pin:raise Stop(f'{name}: не тот коммит')


def phase_rathena_build():
    """Патчи и сборка renewal, PACKETVER 20180620."""
    patches=ROOT/'server/patches'
    char_patch=patches/'char-null-session.patch'
    if run(['git','apply','-R','--check',str(char_patch)],cwd=RATHENA,check=False).returncode:
        run(['git','apply',str(char_patch)],cwd=RATHENA);log('патч char-null-session применён')
    if 'residents_witness' not in (RATHENA/'npc/scripts_custom.conf').read_text():
        run(['git','apply',str(patches/'witness-autoload.patch')],cwd=RATHENA);log('патч witness-autoload применён')
    stamp=RATHENA/'.live_ro_build'
    want=PINS['rathena'][1]+':20180620:'+run(['sha256sum',str(char_patch)]).stdout.split()[0]
    bins=[RATHENA/b for b in ('login-server','char-server','map-server')]
    if all(b.exists() for b in bins) and stamp.exists() and stamp.read_text()==want:return
    log('собираю rAthena (10–30 минут)')
    run(['./configure','--enable-packetver=20180620','--disable-lto'],cwd=RATHENA,timeout=600)
    jobs=str(max(1,min(4,os.cpu_count() or 1)))
    run(['make','-j'+jobs,'login','char','map'],cwd=RATHENA,timeout=7200)
    run(['make','import'],cwd=RATHENA)
    if not all(b.exists() for b in bins):raise Stop('rAthena не собрался: '+str(LOGS))
    stamp.write_text(want)


def phase_database():
    """MariaDB в UTC, схемы rAthena, свидетель, читатель и узкий пользователь регистрации гостей."""
    conf=pathlib.Path('/etc/mysql/mariadb.conf.d/60-ro-residents.cnf')
    text="[mysqld]\ndefault-time-zone='+00:00'\nmyisam_recover_options=BACKUP,FORCE\n"
    global RATHENA_CHANGED
    changed=not conf.exists() or conf.read_text()!=text
    if changed:conf.write_text(text);RATHENA_CHANGED=True  # a running rAthena reconnects after the restart below
    if DEV:run(['service','mariadb','restart' if changed else 'start'],check=False)
    else:
        run(['systemctl','enable','--now','mariadb'])
        if changed:run(['systemctl','restart','mariadb'])
    for _ in range(30):
        if run(['mariadb','-NBe','SELECT 1'],check=False).returncode==0:break
        time.sleep(1)
    else:raise Stop('MariaDB не запускается')
    if sql('SELECT @@global.time_zone')!='+00:00':raise Stop('MariaDB не в UTC')
    dbs=sql('SHOW DATABASES').split()
    if 'ro_residents_main' not in dbs:
        log('создаю схемы rAthena');run([sys.executable,str(ROOT/'tools/provision_a1.py')])
    elif not (PRIVATE/'server.json').exists():raise Stop('БД есть, а секретов в run/private/server.json нет: восстановите их, пересоздавать нельзя')
    if 'residents' not in dbs:
        log('схема свидетеля');run(['mariadb'],input=(ROOT/'server/schema/001_witness.sql').read_text())
    reader=PRIVATE/'reader.cnf'
    if not reader.exists():
        import secrets
        pw=secrets.token_hex(20);u="'ro_residents_reader'@'127.0.0.1'"
        tables=['residents.registry','residents.resident_events','ro_residents_main.`char`','ro_residents_logs.chatlog','ro_residents_logs.picklog','ro_residents_logs.zenylog','ro_residents_logs.atcommandlog']
        sql(f"DROP USER IF EXISTS {u};CREATE USER {u} IDENTIFIED BY '{pw}';"+''.join(f'GRANT SELECT ON {t} TO {u};' for t in tables))
        write_secret(reader,f'[client]\nhost=127.0.0.1\nuser=ro_residents_reader\npassword={pw}\n');log('читатель свидетеля (только SELECT)')
    registry=PRIVATE/'registry.cnf'
    if not registry.exists():
        import secrets
        pw=secrets.token_hex(20);u="'ro_residents_registry'@'127.0.0.1'"
        sql(f"DROP USER IF EXISTS {u};CREATE USER {u} IDENTIFIED BY '{pw}';GRANT SELECT,INSERT,DELETE ON residents.registry TO {u};")
        write_secret(registry,f'[client]\nhost=127.0.0.1\nuser=ro_residents_registry\npassword={pw}\n');log('пользователь регистрации гостя для follow (только residents.registry)')


def write_secret(path,text):
    tmp=path.with_name(path.name+'.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:f.write(text)
    os.replace(tmp,path)


RATHENA_CHANGED=False

def _put(path,text):
    """Write only when different; remember that a running rAthena must reload it."""
    global RATHENA_CHANGED
    path=pathlib.Path(path)
    if path.exists() and path.read_text(errors='replace')==text:return
    path.write_text(text);RATHENA_CHANGED=True

def phase_rathena_config():
    """Без PIN, старт в Пронтере, журналы, NPC свидетеля, права для ro-residents."""
    char=RATHENA/'conf/import/char_conf.txt';text=char.read_text()
    for line in ('pincode_enabled: no','start_point: prontera,156,191'):
        if line not in text:text=text.rstrip('\n')+'\n'+line+'\n'
    _put(char,text)
    _put(RATHENA/'conf/import/log_conf.txt',(ROOT/'server/witness/log_conf.txt').read_text())
    _put(RATHENA/'npc/custom/residents_witness.txt',(ROOT/'server/witness/residents_witness.txt').read_text())
    _put(RATHENA/'npc/custom/residents_maps.txt',(ROOT/'server/witness/maps.txt').read_text())
    custom=RATHENA/'npc/scripts_custom.conf'
    if 'npc/custom/residents_maps.txt' not in custom.read_text():
        _put(custom,custom.read_text().rstrip('\n')+'\n\n// live_ro witness loadevent mapflags\nnpc: npc/custom/residents_maps.txt\n')
    run(['setfacl','-m','u:ro-residents:x','/root',str(BASE),str(BASE/'repos')])
    (RATHENA/'log').mkdir(exist_ok=True);shutil.chown(RATHENA/'log','ro-residents','ro-residents');(RATHENA/'log').chmod(0o750)
    for f in (RATHENA/'conf/import').glob('*.txt'):
        shutil.chown(f,'root','ro-residents');f.chmod(0o640)
    if run(['runuser','-u','ro-residents','--','cat',str(RATHENA/'conf/import/inter_conf.txt')],check=False).returncode:
        raise Stop('ro-residents не читает conf/import (права)')


def phase_rathena_services():
    """login/char/map: юниты с автоперезапуском, только 127.0.0.1."""
    if DEV:
        for name in ('login','char','map'):
            if f'127.0.0.1:{PORTS[name]} ' in run(['ss','-ltnH']).stdout:continue
            out=open(LOGS/f'{name}-server.log','ab')
            subprocess.Popen(['runuser','-u','ro-residents','--','./'+name+'-server'],cwd=RATHENA,stdout=out,stderr=out,start_new_session=True)
            time.sleep(3)
    else:
        for name in ('login','char','map'):
            unit=f'ro-residents-{name}.service'
            shutil.copyfile(ROOT/'ops/units'/unit,'/etc/systemd/system/'+unit)
            drop=pathlib.Path(f'/etc/systemd/system/{unit}.d');drop.mkdir(exist_ok=True)
            (drop/'10-restart.conf').write_text('[Service]\nRestart=on-failure\nRestartSec=10\n')
        run(['systemctl','daemon-reload'])
        if RATHENA_CHANGED:
            log('настройки rAthena изменились: перезапускаю серверы (жители переподключатся сами)')
            run(['systemctl','restart','ro-residents-login','ro-residents-char','ro-residents-map'])
        else:run(['systemctl','start','ro-residents-login','ro-residents-char','ro-residents-map'])
        pathlib.Path('/etc/logrotate.d/ro-residents').write_text(  # rAthena and OpenKore logs of a permanent world
            f'{LOGS}/*.log {LOGS}/*/*.txt {{\n daily\n rotate 7\n maxsize 100M\n compress\n missingok\n notifempty\n copytruncate\n}}\n')
    for port in (6900,6121,5121):
        if not wait_port(port,180):raise Stop(f'порт {port} не открылся: смотри {LOGS}/*-server.log')
    listen=run(['ss','-ltnH']).stdout
    if re.search(r'(0\.0\.0\.0|\*|\[::\]):(6900|6121|5121)\b',listen):raise Stop('серверы слушают не только 127.0.0.1')


def phase_openkore():
    """XSTools, проверка плагина, поля карт из кэша rAthena."""
    if not (OPENKORE/'src/auto/XSTools/XSTools.so').exists():
        log('собираю XSTools');run([sys.executable,'src/scons-local-3.1.2/scons.py','-j'+str(max(1,min(4,os.cpu_count() or 1)))],cwd=OPENKORE,timeout=3600)
    run(['perl','-I'+str(OPENKORE/'src'),'-I'+str(OPENKORE/'src/deps'),'-c',str(ROOT/'plugins/residentBody/residentBody.pl')],cwd=OPENKORE)
    fields=BASE/'run/B/resident/fields'
    if not (fields/'provenance.json').exists():
        log('строю поля карт')
        run([sys.executable,str(ROOT/'checks/b_prepare_fields.py'),'--cache',str(RATHENA/'db/map_cache.dat'),'--cache',str(RATHENA/'db/re/map_cache.dat'),
             '--out',str(fields),'--fallback',str(OPENKORE/'fields')],timeout=1800)


def phase_profiles():
    """Аккаунты (пароль сохраняется до создания) и профили OpenKore."""
    run([sys.executable,str(ROOT/'tools/prepare_tester.py')],quiet=True)
    run([sys.executable,str(ROOT/'tools/prepare_resident.py')],quiet=True)
    b=BASE/'run/B/resident'
    for d in ('control','tables'):
        if not (b/d).exists():shutil.copytree(BASE/'run/resident'/d,b/d)
    sys_txt=b/'control/sys.txt';t=sys_txt.read_text()
    t=re.sub(r'^loadPlugins .*$','loadPlugins 2',t,flags=re.M);t=re.sub(r'^loadPlugins_list .*$','loadPlugins_list residentBody',t,flags=re.M)
    sys_txt.write_text(t)
    a=BASE/'run/Demo/resident_a';a.mkdir(mode=0o700,parents=True,exist_ok=True)
    for d in ('control','tables'):
        if not (a/d).exists():shutil.copytree(b/d,a/d)
    if not (a/'fields').exists():(a/'fields').symlink_to(b/'fields',target_is_directory=True)
    sys.path.insert(0,str(ROOT));from tools.accounts import set_config
    set_config(a/'control/config.txt',{'attackAuto':'-1','logConsole':'0'})
    for rid,name,sex in (('resident_b','Mira','F'),('resident_c','Borin','M')):
        run([sys.executable,str(ROOT/'tools/prepare_demo_resident.py'),'--resident',rid,'--name',name,'--sex',sex],quiet=True)
        set_config(BASE/'run/Demo'/rid/'control/config.txt',{'logConsole':'0'})
    for _,name,_,_,p in CHARS:
        cfg=(p/'control/config.txt').read_text()
        if not re.search(r'^master ROResidentsLab$',cfg,re.M) or not re.search(r'^char 0$',cfg,re.M):raise Stop(f'профиль {p} не настроен')
        if (p/'tables/servers.txt').exists() and 'kRO_RagexeRE_2018_06_20e' not in (p/'tables/servers.txt').read_text():raise Stop(f'{p}: не тот serverType')


def char_id(name):
    out=sql(f"SELECT char_id FROM ro_residents_main.`char` WHERE name='{name}'")
    return int(out) if out.isdigit() else None


def create_character(session,profile,name,sex,fields):
    """Через обычный протокол клиента OpenKore (никакого SQL по персонажам)."""
    tmux=['tmux','-L','ro-residents']
    run(tmux+['kill-session','-t',session],check=False)
    cmd=(f'perl openkore.pl --control={profile}/control --tables={profile}/tables:tables --fields={fields} '
         f'--plugins={BASE}/run/tester/plugins --logs={LOGS}/{session} --ai=manual')
    (LOGS/session).mkdir(parents=True,exist_ok=True)
    run(tmux+['new-session','-d','-x','200','-y','50','-s',session,'-c',str(OPENKORE),cmd])
    def screen():return run(tmux+['capture-pane','-p','-S','-3000','-t',session],check=False).stdout
    def key(text):run(tmux+['send-keys','-t',session,'-l','--',text]);run(tmux+['send-keys','-t',session,'Enter'])
    def wait(patterns,seconds):
        end=time.time()+seconds
        while time.time()<end:
            s=screen()
            for p in patterns:
                if re.search(p,s,re.I):return p
            time.sleep(1)
        return None
    try:
        hit=wait([r'Create a new character',r'You are now in the game',r'Please enter your .*password',r'pin ?code'],240)
        if hit is None:raise Stop(f'{name}: OpenKore не дошёл до выбора персонажа за 4 минуты; tmux -L ro-residents attach -t {session}')
        if 'password' in hit or 'pin' in hit:raise Stop(f'{name}: OpenKore просит пароль/PIN — профиль сломан')
        if hit!=r'You are now in the game':
            key('0');time.sleep(2)
            key(f'0 "{name}" 1 1 novice {sex}')
            # The server is the witness: the character row exists and is online (the screen scrolls away).
            end=time.time()+180
            while time.time()<end and sql(f"SELECT COUNT(*) FROM ro_residents_main.`char` WHERE name='{name}' AND online=1")!='1':time.sleep(2)
            if char_id(name) is None:raise Stop(f'{name}: персонаж не создан; tmux -L ro-residents attach -t {session}')
        key('quit');time.sleep(5)
    finally:
        run(tmux+['kill-session','-t',session],check=False)


def phase_characters():
    """Tester, ResidentA, Mira, Borin — строго по одному, со сверкой char_id; регистрация в свидетеле."""
    fields=BASE/'run/B/resident/fields'
    for rid,name,sex,expected,profile in CHARS:
        cid=char_id(name)
        if cid is None:
            log(f'создаю персонажа {name}')
            create_character('mk_'+rid,profile,name,sex,fields);cid=char_id(name)
            if cid is None:raise Stop(f'{name}: персонажа нет в БД после создания')
        if cid!=expected:raise Stop(f'{name}: char_id {cid}, а мир ждёт {expected}. DELETE/UPDATE персонажей запрещены: впишите {cid} в config/world.yaml и residents.yaml коммитом')
    sql("INSERT IGNORE INTO residents.registry(char_id,resident_id,enabled) VALUES (150000,'tester',1),(150001,'resident_a',1),(150002,'resident_b',1),(150003,'resident_c',1)")


def phase_world():
    """venv мозга, .env, службы мира и чата."""
    venv=pathlib.Path('/opt/ro-residents/world-venv')
    if not (venv/'bin/python').exists():
        venv.parent.mkdir(parents=True,exist_ok=True);run([sys.executable,'-m','venv','--system-site-packages',str(venv)])
    run([str(venv/'bin/python'),'-c','import yaml'])
    env=ROOT/'.env'
    if not env.exists():
        shutil.copyfile(ROOT/'.env.example',env);env.chmod(0o600)
    env.chmod(0o600)
    has_key=bool(re.search(r'^\s*(export\s+)?RO_WORLD_LLM_API_KEY\s*=\s*["\']?[A-Za-z0-9]',env.read_text(errors='replace'),re.M))
    if not has_key:log('ВНИМАНИЕ: в .env нет RO_WORLD_LLM_API_KEY — жители будут жить на привычках, пока владелец не впишет ключ (перезапуск не нужен)')
    stale=[p for p in STATE.glob('*/BEHAVIOR_V1')]+[p for p in STATE.glob('*/behavior-lease.json')]
    if stale:raise Stop('остались файлы V1: '+', '.join(map(str,stale))+' — новый мозг с ними не работает')
    (STATE/'STOP').unlink(missing_ok=True)
    if DEV:
        if run(['pgrep','-f','packages.resident_world.supervisor'],check=False).returncode:
            out=open(LOGS/'ro-world.log','ab')
            subprocess.Popen([sys.executable,'-m','packages.resident_world.supervisor'],cwd=ROOT,stdout=out,stderr=out,start_new_session=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        time.sleep(5)
        if run(['pgrep','-f','packages.resident_world.chat'],check=False).returncode:
            out=open(LOGS/'ro-chat.log','ab')
            subprocess.Popen([str(venv/'bin/python'),'-m','packages.resident_world.chat'],cwd=ROOT,stdout=out,stderr=out,start_new_session=True)
        return
    for unit,src in (('ro-world.service',ROOT/'ops/units/ro-world.service'),('ro-chat.service',ROOT/'deploy/ro-chat.service')):
        shutil.copyfile(src,'/etc/systemd/system/'+unit)
    drop=pathlib.Path('/etc/systemd/system/ro-world.service.d');drop.mkdir(exist_ok=True)
    (drop/'10-rathena.conf').write_text('[Unit]\nWants=ro-residents-login.service ro-residents-char.service ro-residents-map.service ro-chat.service\n')
    run(['systemctl','daemon-reload'])
    run(['systemctl','enable','ro-residents-login','ro-residents-char','ro-residents-map'],check=False)  # no [Install]: started by ro-world's Wants
    sys.path.insert(0,str(ROOT));from packages.resident_world.supervisor import code_version
    try:running=json.loads((STATE/'identity.json').read_text()).get('version')
    except (OSError,ValueError):running=None
    active=run(['systemctl','is-active','ro-world.service'],check=False).stdout.strip()=='active'
    if active and running!=code_version():
        # An existing world runs older code: load the new one (bodies relog by themselves).
        log(f'мир работает на коде {running}, в checkout {code_version()}: перезапускаю ro-world и ro-chat')
        run(['systemctl','reset-failed','ro-chat.service'],check=False);run(['systemctl','restart','ro-world.service','ro-chat.service'])
    run(['systemctl','enable','--now','ro-world.service']);time.sleep(5)
    run(['systemctl','reset-failed','ro-chat.service'],check=False);run(['systemctl','enable','--now','ro-chat.service'])


def phase_check():
    """Ждём жителей онлайн и печатаем ro status и ro doctor world."""
    end=time.time()+300
    while time.time()<end:
        out=run([sys.executable,'-m','packages.resident_world.cli','status'],cwd=ROOT,check=False).stdout
        if out.count(': онлайн')>=3:break
        time.sleep(10)
    print(out,flush=True)
    doctor=run([sys.executable,'-m','packages.resident_world.cli','doctor','world'],cwd=ROOT,check=False)
    print(doctor.stdout,flush=True)
    if out.count(': онлайн')<3:raise Stop('за 5 минут онлайн не все трое: смотри /var/lib/ro-world/*-body.log и world.log')


# ---------------------------------------------------------------- существующий мир
def _unit_dir(unit):
    """WorkingDirectory of an installed unit (resolved), or None."""
    out=run(['systemctl','show','-p','WorkingDirectory','--value',unit],check=False).stdout.strip()
    return pathlib.Path(out).resolve() if out and out!='[not set]' else None


def adopt_check():
    """Только проверка: службы запускают этот checkout, персонажи, аккаунты и профили жителей на месте."""
    if os.geteuid()!=0:raise Stop('нужен root')
    sys.path.insert(0,str(ROOT));from packages.resident_world.supervisor import config
    c=config()
    if not DEV:
        for unit in ('ro-world.service','ro-chat.service'):
            d=_unit_dir(unit)
            if d is None:raise Stop(f'служба {unit} не установлена — это не существующий мир, нужен обычный запуск без --adopt')
            if d!=ROOT.resolve():raise Stop(f'служба {unit} запускает код из {d}, а этот checkout — {ROOT.resolve()}. Обновите тот checkout (git pull) и запустите скрипт оттуда')
        mapdir=_unit_dir('ro-residents-map.service')
        if mapdir and 'residents_witness' not in (mapdir/'npc/scripts_custom.conf').read_text(errors='replace'):raise Stop(f'NPC свидетеля не подключён в {mapdir}')
    if not pathlib.Path('/root/ragnarok/repos/openkore/openkore.pl').exists():raise Stop('нет /root/ragnarok/repos/openkore/openkore.pl — тела жителей запускаются оттуда')
    dbs=sql('SHOW DATABASES').split()
    for db in ('ro_residents_main','ro_residents_logs','residents'):
        if db not in dbs:raise Stop(f'нет БД {db}')
    if not (PRIVATE/'reader.cnf').exists():raise Stop(f'нет {PRIVATE}/reader.cnf — свидетель не читается')
    for r in c['residents']:
        name=sql(f"SELECT name FROM ro_residents_main.`char` WHERE char_id={int(r['char_id'])}")
        if name!=r['name']:raise Stop(f"персонаж {r['char_id']}: в БД «{name or 'нет'}», а в config/world.yaml «{r['name']}»")
        cfg=pathlib.Path(r['profile'])/'control/config.txt'
        if not cfg.exists():raise Stop(f'нет профиля {cfg}')
        text=cfg.read_text(errors='replace');user=re.search(r'^username\s+(\S+)',text,re.M)
        if not user or not re.search(r'^password\s+\S+',text,re.M):raise Stop(f'в {cfg} нет username/password')
        if sql(f"SELECT COUNT(*) FROM ro_residents_main.login WHERE userid='{user.group(1)}'")!='1':raise Stop(f"аккаунт профиля {r['name']} не найден в БД")
        if not re.search(r'^char\s+\d+\s*$',text,re.M):raise Stop(f'в {cfg} не выбран слот персонажа (char)')
        log(f"{r['name']}: персонаж {r['char_id']}, профиль и аккаунт на месте"+('' if r.get('enabled',True) else ' (выключен в config)'))
    tz=sql('SELECT @@global.time_zone,@@system_time_zone').split()
    if tz[:1]!=['+00:00'] and tz[-1:]!=['UTC']:log('ВНИМАНИЕ: MariaDB в местном времени ('+' / '.join(tz)+'): 25.10 с 01:00 до 02:00 UTC жители могут час жить на привычках. Это не исправляется на живом мире автоматически.')


def adopt_witness():
    """Реестр свидетеля для жителей и узкий пользователь регистрации гостя (follow)."""
    sys.path.insert(0,str(ROOT));from packages.resident_world.supervisor import config
    for r in config()['residents']:
        sql(f"INSERT IGNORE INTO residents.registry(char_id,resident_id,enabled) VALUES ({int(r['char_id'])},'{r['resident_id']}',1)")
    registry=PRIVATE/'registry.cnf'
    if not registry.exists():
        import secrets
        pw=secrets.token_hex(20);u="'ro_residents_registry'@'127.0.0.1'"
        sql(f"DROP USER IF EXISTS {u};CREATE USER {u} IDENTIFIED BY '{pw}';GRANT SELECT,INSERT,DELETE ON residents.registry TO {u};")
        write_secret(registry,f'[client]\nhost=127.0.0.1\nuser=ro_residents_registry\npassword={pw}\n');log('пользователь регистрации гостя для follow (только residents.registry)')


def adopt_units():
    """Добавки к юнитам (сами юниты не перезаписываются): чат не сдаётся и следует за миром."""
    if DEV:return
    for unit,text in (('ro-chat.service','[Unit]\nPartOf=ro-world.service\nStartLimitIntervalSec=0\n'),('ro-world.service','[Unit]\nWants=ro-chat.service\n')):
        d=pathlib.Path(f'/etc/systemd/system/{unit}.d');d.mkdir(exist_ok=True)
        (d/'20-live-ro.conf').write_text(text)
    run(['systemctl','daemon-reload'])


def adopt_restart():
    """Мир на старом коде — перезапуск ro-world и ro-chat на новый (тела переподключатся сами)."""
    sys.path.insert(0,str(ROOT));from packages.resident_world.supervisor import code_version
    try:running=json.loads((STATE/'identity.json').read_text()).get('version')
    except (OSError,ValueError):running=None
    if running==code_version():log(f'мир уже работает на коде {running}');return
    log(f'мир работает на коде {running or "без версии"}, в checkout {code_version()}: перезапускаю ro-world и ro-chat')
    if DEV:
        (STATE/'STOP').touch()
        for _ in range(30):
            if run(['pgrep','-f','packages.resident_world.supervisor'],check=False).returncode:break
            time.sleep(1)
        phase_world();return
    run(['systemctl','reset-failed','ro-chat.service'],check=False)
    (STATE/'STOP').unlink(missing_ok=True)
    run(['systemctl','restart','ro-world.service','ro-chat.service'])


def inspect():
    """Только чтение: что уже есть на сервере. Ничего не меняет и не печатает секреты."""
    def line(ok,text):print(('OK    ' if ok is True else 'НЕТ   ' if ok is False else 'ВНИМ  ')+text,flush=True)
    import platform
    line(None,'ОС: '+platform.platform()+'; часовой пояс: '+str(pathlib.Path('/etc/localtime').resolve()).split('zoneinfo/')[-1])
    for name,path in (('rathena',RATHENA),('openkore',OPENKORE)):
        head=run(['git','-C',str(path),'rev-parse','HEAD'],check=False).stdout.strip() if path.exists() else ''
        line(head==PINS[name][1] if head else (None if path.exists() else False),f'{name}: {path} '+(f'коммит {head[:7]}'+('' if head==PINS[name][1] else f' (ожидается {PINS[name][1][:7]})') if head else 'есть, но не git-копия' if path.exists() else 'нет'))
    line(ROOT==BASE/'repos/live_ro',f'live_ro: {ROOT} коммит '+run(['git','-C',str(ROOT),'rev-parse','--short','HEAD'],check=False).stdout.strip())
    line(all((RATHENA/b).exists() for b in ('login-server','char-server','map-server')),'rAthena собран (login/char/map-server)')
    others=run(['bash','-c','find / -xdev \\( -path /proc -o -path /sys \\) -prune -o -name map-server -type f -print 2>/dev/null | head -5'],check=False).stdout.split()
    stray=[p for p in others if not p.startswith(str(RATHENA))]
    if stray:line(None,'другие map-server на диске: '+', '.join(stray))
    listen=run(['ss','-ltnH'],check=False).stdout
    for port in (6900,6121,5121,3306):
        bound=[l.split()[3] for l in listen.splitlines() if l.split()[3].endswith(':'+str(port))] if listen else []
        line(bool(bound) and all(b.startswith('127.0.0.1:') for b in bound) if port!=3306 else bool(bound),f'порт {port}: '+(', '.join(bound) or 'никто не слушает'))
    if shutil.which('systemctl') and pathlib.Path('/run/systemd/system').is_dir():
        for unit in ('mariadb','ro-residents-login','ro-residents-char','ro-residents-map','ro-world','ro-chat'):
            state=run(['systemctl','is-active',unit+'.service'],check=False).stdout.strip()
            line(state=='active',f'служба {unit}: {state}')
    dbs=run(['mariadb','-NBe','SHOW DATABASES'],check=False).stdout.split()
    line('ro_residents_main' in dbs and 'residents' in dbs,'БД: '+', '.join(d for d in dbs if d not in ('information_schema','mysql','performance_schema','sys')))
    if 'ro_residents_main' in dbs:
        tz=run(['mariadb','-NBe','SELECT @@global.time_zone,@@system_time_zone'],check=False).stdout.split()
        line(None,'MariaDB time_zone: '+' / '.join(tz))
        accounts=run(['mariadb','-NBe',"SELECT userid FROM ro_residents_main.login WHERE userid LIKE 'ro\\_%' ORDER BY account_id"],check=False).stdout.split()
        line(len(accounts)>=4,'аккаунты жителей: '+(', '.join(accounts) or 'нет'))
        chars=run(['mariadb','-NBe',"SELECT CONCAT(char_id,' ',name,' online=',online,' класс=',class,' ур=',base_level) FROM ro_residents_main.`char` ORDER BY char_id"],check=False).stdout.strip().splitlines()
        line(len(chars)>=4,'персонажи: '+('; '.join(chars) or 'нет'))
    if 'residents' in dbs:
        reg=run(['mariadb','-NBe','SELECT CONCAT(char_id,"=",resident_id) FROM residents.registry'],check=False).stdout.split()
        line(len(reg)>=4,'реестр свидетеля: '+(', '.join(reg) or 'пусто'))
        last=run(['mariadb','-NBe','SELECT COALESCE(TIMESTAMPDIFF(SECOND,MAX(ts),NOW()),-1) FROM residents.resident_events'],check=False).stdout.strip()
        line(last not in ('','-1') and int(last)<60,'свидетель: последнее событие '+(last+' с назад' if last not in ('','-1') else 'нет событий'))
    for p in ('run/private/server.json','run/private/reader.cnf','run/private/registry.cnf','run/tester/credentials.json','run/resident/credentials.json',
              'run/Demo/resident_a/control/config.txt','run/Demo/resident_b/credentials.json','run/Demo/resident_c/credentials.json','run/B/resident/fields/provenance.json'):
        line((BASE/p).exists(),str(BASE/p))
    witness=(RATHENA/'npc/scripts_custom.conf').exists() and 'residents_witness' in (RATHENA/'npc/scripts_custom.conf').read_text()
    line(witness,'NPC свидетеля подключён в rAthena')
    env=ROOT/'.env';has=env.exists() and bool(re.search(r'^\s*(export\s+)?RO_WORLD_LLM_API_KEY\s*=\s*["\']?[A-Za-z0-9]',env.read_text(errors='replace'),re.M))
    line(has,'.env: '+('ключ LLM есть' if has else 'ключа LLM нет'))
    line(STATE.exists(),f'{STATE}: '+(', '.join(sorted(p.name for p in STATE.iterdir()))[:200] if STATE.exists() else 'нет'))
    if shutil.which('systemctl') and pathlib.Path('/run/systemd/system').is_dir():
        for unit in ('ro-residents-map.service','ro-world.service','ro-chat.service'):
            d=_unit_dir(unit)
            if d:line(unit!='ro-residents-map.service' and d!=ROOT.resolve() and None or True,f'{unit} запускается из {d}')
        if run(['systemctl','is-active','ro-world.service'],check=False).stdout.strip()=='active':
            print('Мир уже работает: python3 tools/deploy_world.py --adopt — игровой сервер не трогается, жители проверяются и мир перезапускается на новом коде.',flush=True)
            return 0
    print('Чистый сервер: python3 tools/deploy_world.py — готовое он пропустит.',flush=True)
    return 0


PHASES=[('system',phase_system),('sources',phase_sources),('rathena_build',phase_rathena_build),('database',phase_database),
        ('rathena_config',phase_rathena_config),('rathena_services',phase_rathena_services),('openkore',phase_openkore),
        ('profiles',phase_profiles),('characters',phase_characters),('world',phase_world),('check',phase_check)]


ADOPT=[('adopt_check',adopt_check),('adopt_witness',adopt_witness),('adopt_units',adopt_units),('adopt_restart',adopt_restart),('check',phase_check)]


def main():
    global DEV
    a=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument('--only');a.add_argument('--from',dest='start');a.add_argument('--dev',action='store_true')
    a.add_argument('--inspect',action='store_true',help='только показать, что уже есть на сервере')
    a.add_argument('--adopt',action='store_true',help='мир уже работает: не трогать игровой сервер, только жители и перезапуск на новом коде')
    v=a.parse_args();DEV=v.dev
    if v.inspect:return inspect()
    phases=ADOPT if v.adopt else PHASES;names=[n for n,_ in phases]
    for n in (v.only,v.start):
        if n and n not in names:a.error('фазы: '+', '.join(names))
    todo=[p for p in phases if (not v.only or p[0]==v.only)]
    if v.start:todo=todo[names.index(v.start):]
    started=time.time()
    for name,fn in todo:
        log(f'== {name}: {fn.__doc__.strip().splitlines()[0]}')
        try:fn()
        except Stop as e:
            log(f'СТОП на фазе {name}: {e}');log(f'исправьте и запустите снова: sudo python3 {ROOT}/tools/deploy_world.py'+(' --adopt' if v.adopt else '')+f' --from {name}');return 2
        except subprocess.TimeoutExpired as e:
            log(f'СТОП на фазе {name}: слишком долго ({e.cmd})');return 2
    log('готово за %d мин'%((time.time()-started)//60));return 0


if __name__=='__main__':sys.exit(main())
