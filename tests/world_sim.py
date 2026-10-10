"""Offline stand-in for the game around the real gateway and brain.

FakeWorld   server-side truth: witness/chatlog/picklog rows the real gateway reads.
FakeBody    speaks the residentBody plugin protocol on the gateway body socket.
FakeLLM     OpenAI-compatible HTTP endpoint with a scripted list of answers.
`python -m tests.world_sim gateway ...` runs the REAL body_gateway.service with
its SQL reads redirected to the FakeWorld files; nothing else is replaced.
Used by tests/test_world_brain_e2e.py. Not a proof of play: a proof of wiring.
"""
import datetime,http.server,json,pathlib,re,socket,sys,threading,time,uuid
from zoneinfo import ZoneInfo

AMS=ZoneInfo('Europe/Amsterdam')
SELLABLE={909:3,1201:1}  # what one fake hunt drops (Jellopy x3, Knife x1)


def local(ts):return datetime.datetime.fromtimestamp(ts,AMS).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


class FakeWorld:
    def __init__(self,root,char_id,name):
        self.root=pathlib.Path(root);self.root.mkdir(parents=True,exist_ok=True);self.char_id=char_id;self.name=name
        self.lock=threading.Lock();self.ids={'witness':0,'chat':0,'picklog':0}
        for k in self.ids:(self.root/(k+'.jsonl')).touch()
    def _add(self,kind,row):
        with self.lock:
            self.ids[kind]+=1;row=dict(row,id=self.ids[kind])
            with (self.root/(kind+'.jsonl')).open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
            return row
    def witness(self,kind,m,x,y,a1=0,a2=0):
        now=time.time()
        return self._add('witness',{'ts':local(now),'ts_utc':datetime.datetime.fromtimestamp(now,datetime.timezone.utc).isoformat(),'char_id':self.char_id,'kind':kind,'map':m,'x':x,'y':y,'a1':a1,'a2':a2,'db_timezone':'Europe/Amsterdam'})
    def chat(self,kind,text,to=''):return self._add('chat',{'ts':local(time.time()),'type':kind,'char_id':self.char_id,'to':to,'text':text})
    def say_as(self,name,char_id,text,m,x,y,kind='O',to=''):
        """A player (not the resident) speaks: the same chatlog the chat service reads."""
        return self._add('chat',{'ts':local(time.time()),'type':kind,'char_id':char_id,'name':name,'to':to,'text':text,'map':m,'x':x,'y':y})
    def chatlog_query(self,residents):
        """tools.events.query stand-in for chat.ingest (JOIN char → name, UNIX_TIMESTAMP)."""
        names={r['char_id']:r['name'] for r in residents}
        def query(defaults,sql):
            if 'MAX(id)' in sql:return '0\n'
            cursor=int(re.search(r'l\.id>(\d+)',sql).group(1));out=[]
            for r in self.rows('chat'):
                if r['id']<=cursor or r['type'] not in ('O','W'):continue
                ts=datetime.datetime.fromisoformat(r['ts']).replace(tzinfo=AMS).timestamp()
                out.append({'id':r['id'],'ts':ts,'name':r.get('name',names.get(r['char_id'],'?')),'char_id':r['char_id'],'type':r['type'],'to':r.get('to',''),
                            'map':r.get('map',''),'x':r.get('x',0),'y':r.get('y',0),'text':r['text']})
            return ''.join(json.dumps(o,ensure_ascii=False)+'\n' for o in out)
        return query
    def pick(self,item,amount,m):return self._add('picklog',{'ts':local(time.time()),'char_id':self.char_id,'type':'S','item_id':item,'amount':amount,'map':m})
    def rows(self,kind):
        return [json.loads(l) for l in (self.root/(kind+'.jsonl')).read_text().splitlines() if l]


class FakeBody(threading.Thread):
    """One OpenKore body: honest telemetry, slow-enough skills, server rows on success."""
    def __init__(self,socket_path,world,resident,char_id,start=('prontera',150,190)):
        super().__init__(daemon=True);self.path=socket_path;self.world=world;self.resident=resident;self.char_id=char_id
        self.epoch=str(uuid.uuid4());self.seq=0;self.stop=threading.Event();self.commands=[];self.active=None
        self.map,self.x,self.y=start;self.hp,self.hp_max=180,200;self.zeny=300;self.inv={'501':10};self.sitting=False;self.weight,self.weight_max=400,3000
        self.nearby=[{'name':'Tester','pos':{'x':start[1]+2,'y':start[2]}}]
    def frame(self,kind,**data):
        self.seq+=1;return {'proto':1,'type':kind,'body_epoch':self.epoch,'seq':self.seq,'ts':time.time(),**data}
    def send(self,m):self.sock.sendall(json.dumps(m,ensure_ascii=False).encode()+b'\n')
    def telemetry(self):
        sellable={str(k):self.inv.get(str(k),0) for k in SELLABLE if self.inv.get(str(k),0)}
        return self.frame('telemetry',map=self.map,pos={'x':self.x,'y':self.y},dest={'x':self.x,'y':self.y},hp=self.hp,hp_max=self.hp_max,sp=20,sp_max=40,
                          zeny=self.zeny,weight=self.weight,weight_max=self.weight_max,dead=False,sitting=self.sitting,inventory=dict(self.inv),sellable=sellable,
                          nearby_players=self.nearby if self.map=='prontera' else [],near_monsters=[],mode='IDLE_SAFE' if not self.active else 'BUSY',
                          running={'action_id':self.active['action_id'],'phase':'running'} if self.active and self.active['skill'] not in ('say','whisper') else None,
                          behavior_v1=False,base_level=12)
    def run(self):
        for _ in range(200):
            try:
                self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.connect(self.path);break
            except OSError:self.sock.close();time.sleep(.05)
        self.world.witness('login',self.map,self.x,self.y)
        self.send(self.frame('hello',resident=self.resident,char_id=self.char_id,account_id=1,plugin_version='sim',ledger=[]))
        self.sock.settimeout(.1);buf=b'';last_t=last_pos=0
        while not self.stop.is_set():
            try:
                data=self.sock.recv(65536)
                if not data:break
                buf+=data
            except socket.timeout:pass
            while b'\n' in buf:
                line,buf=buf.split(b'\n',1);self.command(json.loads(line))
            now=time.time()
            if self.active and now>=self.active['due']:self.finish()
            if now-last_t>=.2:self.send(self.telemetry());last_t=now
            if now-last_pos>=1:self.world.witness('pos',self.map,self.x,self.y);last_pos=now
        self.sock.close()
    def command(self,m):
        if m.get('type')=='heartbeat':return
        self.commands.append(m)
        if m.get('type')=='safe_stop':self.active=None;return
        if m.get('type')=='skill_cancel':
            if self.active and self.active['action_id']==m.get('action_id'):
                self.send(self.frame('skill_result',action_id=m['action_id'],outcome='cancelled',code='CANCELLED'));self.active=None
            return
        if m.get('type')!='skill_start':return
        if m.get('expect_epoch')!=self.epoch:return self.send(self.frame('skill_rejected',action_id=m['action_id'],code='EPOCH',reason='epoch'))
        if self.active and (self.active['skill'] in ('say','whisper'))==(m['skill'] in ('say','whisper')):
            return self.send(self.frame('skill_rejected',action_id=m['action_id'],code='BUSY',reason='busy'))
        self.send(self.frame('skill_accepted',action_id=m['action_id'],code='ACCEPTED'))
        self.send(self.frame('skill_progress',action_id=m['action_id'],phase='running',detail='sim'))
        self.active=dict(m,due=time.time()+(.1 if m['skill'] in ('say','whisper') else .4))
    def finish(self):
        a=self.active;self.active=None;p=a.get('params') or {};skill=a['skill'];outcome,code='done','OK'
        if skill=='travel_to':
            changed=p['map']!=self.map;self.map=p['map'];self.x=p.get('x',self.x);self.y=p.get('y',self.y);self.sitting=False
            if changed:self.world.witness('loadmap',self.map,self.x,self.y)
            self.world.witness('pos',self.map,self.x,self.y);code='ARRIVED'
        elif skill=='say':self.world.chat('O',p['text']);code='SPOKEN'
        elif skill=='whisper':self.world.chat('W',p['text'],p['to_name']);code='SENT'
        elif skill=='hunt':
            if self.map!=p['map']:outcome,code='error','WRONG_MAP'
            else:
                for _ in range(2):self.world.witness('kill',self.map,self.x,self.y,1002,0)
                for k,n in SELLABLE.items():self.inv[str(k)]=self.inv.get(str(k),0)+n
                self.inv['501']=max(0,self.inv.get('501',0)-1);self.weight+=60;code='HUNTED'
        elif skill=='rest':self.hp=self.hp_max;self.sitting=True;self.world.witness('pos',self.map,self.x,self.y);code='RESTED'
        elif skill=='sell_loot':
            if self.map!='prt_in':outcome,code='error','WRONG_MAP'
            else:
                for k in list(SELLABLE):
                    n=self.inv.pop(str(k),0)
                    if n:self.world.pick(k,-n,self.map);self.zeny+=5*n;self.weight-=15*n
                code='SOLD'
        elif skill=='buy_potions':
            n=p['stock_goal']-self.inv.get('501',0)
            if self.map!='prt_in' or n<=0:outcome,code='error','NO_PURCHASE_NEEDED'
            else:self.world.pick(501,n,self.map);self.inv['501']=p['stock_goal'];self.zeny-=10*n;code='BOUGHT'
        else:outcome,code='error','UNSUPPORTED'
        self.send(self.frame('skill_result',action_id=a['action_id'],outcome=outcome,code=code,detail='sim'))


class FakeLLM(http.server.ThreadingHTTPServer):
    """Each POST pops one scripted step: a dict decision, (status, body, headers), or None = 500."""
    def __init__(self,script):
        super().__init__(('127.0.0.1',0),_LLMHandler);self.script=list(script);self.requests=[]
        threading.Thread(target=self.serve_forever,daemon=True).start()
    @property
    def url(self):return f'http://127.0.0.1:{self.server_address[1]}/api/v1'


class _LLMHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append({'at':time.time(),'auth':self.headers.get('Authorization'),'body':body})
        step=self.server.script.pop(0) if self.server.script else None
        headers={}
        if isinstance(step,dict):status,payload=200,{'choices':[{'message':{'content':json.dumps(step,ensure_ascii=False)}}],'usage':{'prompt_tokens':1500,'completion_tokens':120,'cost':0.0005}}
        elif isinstance(step,tuple):status,payload,headers=step
        else:status,payload=500,{'error':{'message':'script finished'}}
        data=json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(status)
        for k,v in headers.items():self.send_header(k,v)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)


def _gateway(argv):
    """Real gateway, SQL replaced by FakeWorld files (same row shapes as tools/events.py)."""
    import argparse
    from packages.body_gateway import service
    a=argparse.ArgumentParser();a.add_argument('--world');a.add_argument('--socket');a.add_argument('--store');a.add_argument('--resident');a.add_argument('--char-id',type=int);a.add_argument('--char-name')
    a=a.parse_args(argv);root=pathlib.Path(a.world)
    def rows(kind):return [json.loads(l) for l in (root/(kind+'.jsonl')).read_text().splitlines() if l]
    def get_events(defaults,char,after=0,since=None,kind=None):return [r for r in rows('witness') if r['id']>after][:200]
    def query(defaults,sql):
        if 'MAX(id)' in sql:return '0\n'
        cursor=int(re.search(r'id>(\d+)',sql).group(1))
        source='chat' if 'chatlog' in sql else 'picklog'
        return ''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows(source) if r['id']>cursor)
    service.get_events=get_events;service.query=query
    service.serve(a.socket,a.store,a.resident,a.char_id,a.char_name,None,True)


if __name__=='__main__' and len(sys.argv)>1 and sys.argv[1]=='gateway':_gateway(sys.argv[2:])
