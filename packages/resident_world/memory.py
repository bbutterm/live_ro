"""UTC-day memory from server witnesses, never from model intentions or invented diary."""
import datetime,json,re,sqlite3,time
from .supervisor import STATE,atomic


def summarize(s):
    if s.get('source')!='witness' or type(s.get('kills')) is not int or s['kills']<0:raise ValueError('UNWITNESSED_MEMORY')
    pieces=['Сегодня: '+str(s['kills'])+' убийств.']
    for key,label in (('hunting','Охота'),('visited','Был на картах'),('met','Встречи'),('contacts','Собеседники')):
        names=[n for n in s.get(key,[]) if isinstance(n,str) and re.fullmatch(r'[\w -]{1,24}',n)][:6]
        if names:pieces.append(label+': '+', '.join(names)+'.')
    return ' '.join(pieces)[:600]


def day_reply(s):
    summarize(s)
    if s['kills'] and s.get('hunting'):
        text='Сегодня: '+str(s['kills'])+' убийств. Охота: '+s['hunting'][0]+'.'
    elif s.get('met'):text='Сегодня встреча с '+s['met'][0]+'.'
    elif s.get('contacts'):text='Сегодня говорил с '+s['contacts'][0]+'.'
    else:text='Сегодня подтверждённых занятий пока нет.'
    if len(text)>80:raise ValueError('MEMORY_REPLY_TOO_LONG')
    return text


def day_decision(r,event,d):
    if not re.search(r'что\s+ты\s+делал[аи]?\s+сегодня|чем\s+ты\s+занимал[а-я]*\s+сегодня',event['text'],re.I):return None
    try:text=day_reply(refresh(r,d))
    except Exception:text='Не могу проверить журнал дня прямо сейчас.'
    args={'text':text}
    if event.get('type')=='W':args['to']=event['name']
    return {'action':'whisper' if event.get('type')=='W' else 'say','args':args,'reason':'Ответ только по подтверждённой памяти дня'}


def read_memory(d,now=None):
    now=time.time() if now is None else now
    day=datetime.datetime.fromtimestamp(now,datetime.timezone.utc).date().isoformat()
    try:
        s=json.loads((d/'memory.json').read_text())
        if s.get('day')==day:return s
    except (OSError,ValueError):pass
    return {'source':'witness','day':day,'kills':0,'hunting':[],'visited':[],'contacts':[],'met':[]}


def refresh(r,d,now=None):
    from tools.events import query,DEFAULTS
    now=time.time() if now is None else now
    old=read_memory(d,now)
    if now-old.get('at',0)<30:return old
    start=int(now)//86400*86400
    cid=int(r['char_id'])
    sql=f"SELECT JSON_OBJECT('kind',kind,'map',map,'count',COUNT(*),'first',MIN(id),'last',MAX(id)) FROM residents.resident_events WHERE char_id={cid} AND ts>=FROM_UNIXTIME({start}) AND ts<=FROM_UNIXTIME({now:.3f}) AND kind IN ('kill','loadmap') GROUP BY kind,map ORDER BY COUNT(*) DESC"
    rows=[json.loads(line) for line in query(DEFAULTS,sql).splitlines() if line]
    s={'source':'witness','day':old['day'],'at':now,'kills':sum(int(row['count']) for row in rows if row['kind']=='kill'),'hunting':[row['map'] for row in rows if row['kind']=='kill' and row['map']],'visited':sorted({row['map'] for row in rows if row['map']}),'contacts':[],'met':[],'evidence':{'server':rows,'chat_ids':[],'meetings':[]}}
    chat=STATE/'chat.sqlite'
    if chat.exists():
        with sqlite3.connect('file:'+str(chat)+'?mode=ro',uri=True) as db:
            messages=db.execute("SELECT id,payload FROM inbox WHERE resident=? AND status='finished' AND json_extract(result,'$.outcome')='done' AND json_extract(payload,'$.ts')>=? AND json_extract(payload,'$.ts')<=? ORDER BY id DESC LIMIT 30",(r['name'],start,now)).fetchall()
        for event_id,payload in messages:
            event=json.loads(payload)
            if event['name'] not in s['contacts']:s['contacts'].append(event['name'])
            s['evidence']['chat_ids'].append(event_id)
    skills=STATE/'skills.jsonl'
    if skills.exists():
        with skills.open() as f:
            for line in f:
                try:task=json.loads(line)
                except ValueError:continue
                proof=task.get('meeting_witness',{});a=proof.get('self',{});b=proof.get('peer',{})
                if task.get('action')!='meet' or task.get('outcome')!='done' or task.get('ended',0)>now:continue
                if a.get('source')!='witness' or b.get('source')!='witness' or not (start<=a.get('ts',0)<=now and start<=b.get('ts',0)<=now):continue
                if task['resident']==r['name']:peer=task['args']['resident']
                elif task['args'].get('resident')==r['name']:peer=task['resident']
                else:continue
                if peer not in s['met']:s['met'].append(peer)
                s['evidence']['meetings'].append({'task':task['id'],'self':a,'peer':b})
    summarize(s);atomic(d/'memory.json',s)
    return s
