"""Machine proof for the owner: `ro doctor world` (is the world whole right now) and
`ro accept --window 6h|24h` (TZ §1 criteria over a window, from the server witness,
the chat inbox, decisions.jsonl and the spend ledger). Read-only; never a paid call."""
import datetime,json,os,pathlib,re,shutil,sqlite3,subprocess,time
from .supervisor import ROOT,STATE,config,online,code_version

SERVICES=('mariadb','ro-residents-login','ro-residents-char','ro-residents-map','ro-world','ro-chat')
OCCUPATION={'hunt':'охота','hunt_solo':'охота','hunt_party':'охота','support':'охота','town_service':'снабжение','sell_loot':'снабжение',
            'buy_potions':'снабжение','sell_equipment':'снабжение','rest':'отдых','walk_to':'прогулка','travel_to':'прогулка',
            'say':'разговор','whisper':'разговор','follow':'с игроком','meet':'встреча','idle':'ожидание','find_party':'поиск группы'}


def _systemd():
    return shutil.which('systemctl') is not None and pathlib.Path('/run/systemd/system').is_dir()


def _query(sql):
    from tools.events import query,DEFAULTS
    return query(DEFAULTS,sql)


def _status(r):
    from packages.body_gateway.cli import request
    return request(str(STATE/r['resident_id']/'actions.sock.control'),{'op':'status'})


def doctor_world(now=None):
    """Lines (level, text): level is OK / WARN / FAIL."""
    now=time.time() if now is None else now;c=config();out=[]
    if _systemd():
        for s in SERVICES:
            active=subprocess.run(['systemctl','is-active','--quiet',s+'.service']).returncode==0
            out.append(('OK' if active else 'FAIL',f'служба {s}: '+('работает' if active else 'НЕ работает')))
    else:
        for name,pattern in (('rAthena','map-server'),('мир','packages.resident_world.supervisor'),('чат','packages.resident_world.chat')):
            alive=subprocess.run(['pgrep','-f',pattern],capture_output=True).returncode==0
            out.append(('OK' if alive else 'FAIL',f'процесс {name}: '+('работает' if alive else 'НЕ работает')+' (без systemd)'))
    residents=c['residents'];enabled=[r for r in residents if r.get('enabled',True)]
    out.append(('OK' if len(enabled)==len(residents)==3 else 'FAIL',f'жителей включено {len(enabled)} из {len(residents)} (ТЗ: 3)'))
    for r in residents:
        if not r.get('enabled',True):continue
        try:
            s=_status(r);t=s.get('telemetry') or {};pos=t.get('pos') or {}
            ok=online(s,now)
            out.append(('OK' if ok else 'FAIL',f"{r['name']}: "+(f"онлайн, {t.get('map')} ({pos.get('x')}, {pos.get('y')})" if ok else 'нет свежего подтверждения свидетеля')))
        except (OSError,RuntimeError,ValueError):out.append(('FAIL',f"{r['name']}: тело/шлюз недоступны"))
    try:
        age=float(_query('SELECT COALESCE(UNIX_TIMESTAMP()-UNIX_TIMESTAMP(MAX(ts)),1e9) FROM residents.resident_events').strip())
        out.append(('OK' if age<30 else 'FAIL',f'свидетель: последнее событие {int(age)} с назад'))
    except (RuntimeError,ValueError):out.append(('FAIL','свидетель: БД не читается (reader.cnf?)'))
    from .cli import chat_line,brain_line
    chat=chat_line();out.append(('OK' if 'НЕ' not in chat else 'FAIL',chat))
    version=code_version();out.append(('WARN' if '+правки' in version or version=='неизвестно' else 'OK','код: '+version))
    try:
        overlay_error=(STATE/'world.local.error').read_text().strip()
        out.append(('WARN','ошибка world.local.json: '+overlay_error[:120]+' — действует последняя верная копия'))
    except OSError:pass
    free=shutil.disk_usage(STATE if STATE.exists() else '/').free/2**30
    out.append(('FAIL' if free<1 else 'WARN' if free<5 else 'OK',f'свободно на диске {free:.1f} ГБ'))
    for r in enabled:
        j=STATE/r['resident_id']/'journal.sqlite'
        if j.exists() and j.stat().st_size>200*2**20:out.append(('WARN',f"журнал шлюза {r['name']}: {j.stat().st_size//2**20} МБ"))
    backups=sorted(pathlib.Path(c.get('backup_dir','/var/backups/ro-world')).glob('ro-world-*.tar.gz'))
    if not backups:out.append(('WARN','резервных копий ещё нет'))
    else:
        age=(now-backups[-1].stat().st_mtime)/3600
        out.append(('WARN' if age>36 else 'OK',f'последняя резервная копия {age:.0f} ч назад: {backups[-1].name}'))
    out.append(('OK',brain_line()))
    return out


# ------------------------------------------------------------------ acceptance window
def _decisions(start,end):
    files=sorted(STATE.glob('decisions.jsonl*'),key=lambda p:(len(p.name),p.name),reverse=True)
    for p in files:
        try:lines=p.read_text(encoding='utf8',errors='replace').splitlines()
        except OSError:continue
        for line in lines:
            try:e=json.loads(line)
            except ValueError:continue
            if isinstance(e,dict) and start<=float(e.get('ts',0) or 0)<=end:yield e


def _uptime(residents,start,end):
    ids=','.join(str(int(r['char_id'])) for r in residents)
    rows=_query(f"SELECT char_id,FLOOR(UNIX_TIMESTAMP(ts)/60) FROM residents.resident_events WHERE kind='pos' AND char_id IN ({ids}) "
                f"AND ts>=FROM_UNIXTIME({start:.0f}) AND ts<=FROM_UNIXTIME({end:.0f}) GROUP BY char_id,FLOOR(UNIX_TIMESTAMP(ts)/60)")
    minutes={}
    for line in rows.splitlines():
        cid,m=line.split('\t');minutes.setdefault(int(cid),[]).append(int(float(m)))
    total=max(1,int((end-start)//60));result={}
    for r in residents:
        ms=sorted(minutes.get(int(r['char_id']),[]))
        edges=[int(start//60)]+ms+[int(end//60)]
        gap=max((b-a for a,b in zip(edges,edges[1:])),default=total)
        result[r['name']]={'coverage':round(len(ms)/total,3),'longest_gap_min':gap}
    return result


def _chat(residents,start,end):
    names={r['name'] for r in residents};ids={int(r['char_id']) for r in residents};stats={'messages':0,'answered':0,'late':0,'silent':0,'worst_s':0.0}
    path=STATE/'chat.sqlite'
    if not path.exists():return stats
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=2) as db:
        rows=db.execute('SELECT payload,resident,status,result FROM inbox WHERE resident IS NOT NULL').fetchall()
    for payload,resident,status,result in rows:
        try:e=json.loads(payload);res=json.loads(result) if result else {}
        except ValueError:continue
        if resident not in names or e.get('char_id') in ids or not start<=float(e.get('ts',0) or 0)<=end:continue
        stats['messages']+=1
        if res.get('outcome')=='done':
            delay=float(res.get('ended',e['ts']))-float(e['ts']);stats['worst_s']=max(stats['worst_s'],round(delay,1))
            stats['answered' if delay<=10 else 'late']+=1
        else:stats['silent']+=1
    return stats


def accept(window_seconds,now=None):
    """TZ §1 over [now-window, now]; returns (passed, report dict)."""
    now=time.time() if now is None else now;start=now-window_seconds;c=config()
    residents=[r for r in c['residents'] if r.get('enabled',True)];report={'window_h':round(window_seconds/3600,2),'from':start,'to':now,'version':code_version(),'criteria':{}}
    crit=report['criteria']
    def put(key,ok,text,data=None):crit[key]={'result':ok,'text':text,**({'data':data} if data is not None else {})}
    put('residents',len(residents)==len(c['residents'])==3,f'включено {len(residents)} жителей из {len(c["residents"])} (нужно 3)')
    try:
        up=_uptime(residents,start,now)
        bad=[n for n,u in up.items() if u['coverage']<.95 or u['longest_gap_min']>10]
        put('uptime',not bad,'онлайн по свидетелю ≥95% минут и без перерывов >10 мин'+(': не выполнено у '+', '.join(bad) if bad else ''),up)
    except (RuntimeError,ValueError) as e:put('uptime',None,'свидетель не читается: '+str(e)[:80])
    decided={};done={}
    for e in _decisions(start,now):
        if e.get('kind')=='MODEL_DECISION':decided[e.get('decision_id')]=(e.get('resident'),(e.get('decision') or {}).get('action'),float(e['ts']))
        elif e.get('kind')=='GOAL_OUTCOME' and e.get('outcome')=='done':done[e.get('task_id')]=True
    segments=max(1,int(round(window_seconds/(6*3600))));seg_len=window_seconds/segments;occupations={}
    for did,(who,action,ts) in decided.items():
        if did in done and action:
            seg=min(segments-1,int((ts-start)//seg_len))
            occupations.setdefault(who,[set() for _ in range(segments)])[seg].add(OCCUPATION.get(action,action))
    weak=[r['name'] for r in residents if any(len(s)<3 for s in occupations.get(r['name'],[set()]*segments))]
    put('occupations',not weak,'в каждом 6-часовом окне ≥3 разных занятия по решению LLM, подтверждённых итогом done'+(': мало у '+', '.join(weak) if weak else ''),
        {n:[sorted(s) for s in segs] for n,segs in occupations.items()})
    chat=_chat(residents,start,now)
    put('chat',None if not chat['messages'] else chat['late']==0 and chat['silent']==0,
        'ответ игроку ≤10 с и без молчания'+(' — реплик игроков в окне не было' if not chat['messages'] else ''),chat)
    follows=[e for e in _decisions(start,now) if e.get('kind') in ('CHAT_ACTION_OUTCOME',) and (e.get('task') or {}).get('action')=='follow']
    fdone=sum(1 for e in follows if (e.get('task') or {}).get('outcome')=='done')
    put('follow',None if not follows else fdone>0,f'«пойдём со мной»: follow выполнен {fdone} из {len(follows)}' if follows else '«пойдём со мной» в окне не просили')
    try:ledger=json.loads((STATE/'llm-spend.json').read_text()).get('days',{})
    except (OSError,ValueError,AttributeError):ledger={}
    days={}
    for day,v in (ledger.items() if isinstance(ledger,dict) else []):
        try:d=datetime.date.fromisoformat(day)
        except ValueError:continue
        if datetime.date.fromtimestamp(start)-datetime.timedelta(days=1)<=d<=datetime.date.fromtimestamp(now):days[day]=float((v or {}).get('usd',0))
    limit=float(c.get('daily_llm_usd',2));over=[d for d,u in days.items() if u>limit]
    put('spend',not over,f'траты ≤ ${limit} за UTC-сутки'+(': превышено '+', '.join(over) if over else ''),days)
    restarts={}
    try:restarts={k:v.get('restarts') for k,v in json.loads((STATE/'identity.json').read_text()).get('children',{}).items() if v.get('restarts')}
    except (OSError,ValueError):pass
    report['info']={'model_decisions':len(decided),'goals_done':sum(1 for d in decided if d in done),'process_restarts':restarts}
    results=[v['result'] for v in crit.values()]
    passed=all(r is True for r in results)
    report['passed']=passed;report['verdict']='PASS' if passed else ('НЕТ ДАННЫХ' if None in results and False not in results else 'FAIL')
    return passed,report


def parse_window(text):
    m=re.fullmatch(r'(\d+(?:\.\d+)?)\s*([mhd]?)',text.strip().lower())
    if not m:raise ValueError('окно: например 30m, 6h, 24h')
    return float(m.group(1))*{'m':60,'h':3600,'d':86400,'':3600}[m.group(2)]


def print_accept(window_text):
    passed,report=accept(parse_window(window_text))
    mark={True:'PASS',False:'FAIL',None:'НЕТ ДАННЫХ'}
    for key,v in report['criteria'].items():print(f"{mark[v['result']]:10} {v['text']}")
    print(f"ИТОГ: {report['verdict']} за {report['window_h']} ч; решений LLM {report['info']['model_decisions']}, выполнено {report['info']['goals_done']}")
    STATE.mkdir(parents=True,exist_ok=True)
    out=STATE/('accept-'+window_text.strip().lower()+'.json');out.write_text(json.dumps(report,ensure_ascii=False,default=list))
    print('Подробно:',out);return 0 if passed else 1


def print_doctor_world():
    lines=doctor_world()
    for level,text in lines:print(f'{level:5} {text}')
    fail=sum(1 for l,_ in lines if l=='FAIL')
    print('ИТОГ: '+('PASS' if not fail else f'FAIL ({fail})'));return 0 if not fail else 1
