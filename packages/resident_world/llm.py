"""LLM provider for the residents' brains: one token, honest spend, waits out limits.

Only the API token is required (RO_WORLD_LLM_API_KEY in the repository .env).
Any OpenAI-compatible endpoint works; OpenRouter + deepseek/deepseek-v3.2 is the
default. Money is counted from each response's own usage, never from upfront
reservations, so estimates cannot eat the daily cap (root cause of E04 and of
the night "без API"). Every key-level problem (429, 402, 401/403, daily cap,
outage) closes one shared gate with a resume time; brains fall back to native
habits meanwhile and the gate reopens by itself, so the world keeps living.
"""
import datetime,email.utils,fcntl,http.client,json,os,pathlib,random,re,socket,time,urllib.error,urllib.request
from dataclasses import dataclass
from decimal import Decimal,InvalidOperation

DEFAULT_BASE_URL='https://openrouter.ai/api/v1'
DEFAULT_MODEL='deepseek/deepseek-v3.2'
KEY_NAMES=('RO_WORLD_LLM_API_KEY','LLM_API_KEY','OPENROUTER_API_KEY','OPENAI_API_KEY','DEEPSEEK_API_KEY')
# Used only when the provider does not report the price itself (USD per 1M tokens).
DEFAULT_PRICE_IN,DEFAULT_PRICE_OUT='1.0','3.0'
# Seconds the shared gate stays closed for each kind of key-level failure.
WAITS={'NO_TOKEN':60,'AUTH':600,'NO_CREDITS':600,'BAD_REQUEST':300}


class LLMUnavailable(RuntimeError):
    """The provider cannot be used before `until` (epoch seconds); fall back meanwhile."""
    def __init__(self,reason,until,status=None,detail=''):
        super().__init__(reason+(': '+detail if detail else ''))
        self.reason,self.until,self.status,self.detail=reason,until,status,detail


class BadModelOutput(ValueError):
    """The model answered, but not with a usable JSON object."""


@dataclass
class Settings:
    key:str
    base_url:str=DEFAULT_BASE_URL
    model:str=DEFAULT_MODEL
    daily_usd:Decimal=Decimal(2)
    price_in:Decimal=Decimal(DEFAULT_PRICE_IN)
    price_out:Decimal=Decimal(DEFAULT_PRICE_OUT)
    source:str='.env'
    @property
    def openrouter(self):return 'openrouter.ai' in self.base_url
    def masked(self):return (self.key[:6]+'…'+self.key[-4:]) if len(self.key)>12 else '***'


def read_env(path):
    values={}
    try:raw=pathlib.Path(path).read_bytes()
    except OSError:return values
    # Windows editors save UTF-16 or cp1251: the key is ASCII, broken comments are harmless.
    lines=(raw.decode('utf-16','replace') if raw[:2] in (b'\xff\xfe',b'\xfe\xff') else raw.decode('utf-8-sig','replace')).splitlines()
    for line in lines:
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:continue
        k,v=line.split('=',1);k=k.strip();v=v.strip()
        if k.startswith('export '):k=k[7:].strip()
        if v[:1] in ('"',"'") and v[0] in v[1:]:v=v[1:v.index(v[0],1)]  # "value"  # comment
        else:v=re.split(r'\s+#',v,maxsplit=1)[0].strip().strip('"\'')
        values[k]=v
    return values


def _decimal(value,default):
    try:
        d=Decimal(str(value))
        return d if d.is_finite() and d>=0 else Decimal(default)
    except (InvalidOperation,ValueError,TypeError):return Decimal(default)


def load_settings(root,config=None):
    """Process environment wins over .env; only the key is mandatory."""
    env=read_env(pathlib.Path(root)/'.env')
    def get(name,default=None):
        v=os.environ.get(name)
        return v if v not in (None,'') else env.get(name) or default
    def clean(v):
        # Pasted from a browser/messenger: drop invisible characters and a trailing "# comment".
        v=re.sub('[\u200b-\u200f\u2060\ufeff\xa0]','',v or '').strip()
        v=v.split()[0] if v.split() else ''
        return '' if v.startswith('#') else v
    base_url=get('RO_WORLD_LLM_BASE_URL',DEFAULT_BASE_URL).rstrip('/')
    # A provider-specific name is only used for that provider: never send an OpenAI key to OpenRouter.
    owners={'OPENROUTER_API_KEY':'openrouter.ai','OPENAI_API_KEY':'api.openai.com','DEEPSEEK_API_KEY':'api.deepseek.com'}
    names=[n for n in KEY_NAMES if n not in owners or owners[n] in base_url]
    name=next((n for n in names if clean(get(n))),None);key=clean(get(name)) if name else ''
    source='окружение процесса' if name and clean(os.environ.get(name)) else '.env'
    if not key or key.lower() in ('changeme','your-token-here','вставь-токен-сюда'):
        raise LLMUnavailable('NO_TOKEN',time.time()+WAITS['NO_TOKEN'],detail='в .env нет RO_WORLD_LLM_API_KEY')
    if not (key.isascii() and key.isprintable()) or any(ch in key for ch in '"\'#'):
        raise LLMUnavailable('NO_TOKEN',time.time()+WAITS['NO_TOKEN'],detail='в токене лишние символы — вставь только сам ключ')
    daily=get('RO_WORLD_LLM_DAILY_USD',(config or {}).get('daily_llm_usd',2))
    return Settings(key=key,base_url=base_url,model=get('RO_WORLD_LLM_MODEL',DEFAULT_MODEL),source=source+' ('+name+')',
                    daily_usd=_decimal(daily,2),price_in=_decimal(get('RO_WORLD_LLM_PRICE_IN',DEFAULT_PRICE_IN),DEFAULT_PRICE_IN),
                    price_out=_decimal(get('RO_WORLD_LLM_PRICE_OUT',DEFAULT_PRICE_OUT),DEFAULT_PRICE_OUT))


def utc_day(now):return datetime.datetime.fromtimestamp(now,datetime.timezone.utc).date().isoformat()
def next_utc_midnight(now):return (int(now)//86400+1)*86400


class _Locked:
    """Read-modify-write of one small JSON file shared by brains and the chat service."""
    def __init__(self,path,default):
        self.path=pathlib.Path(path);self.default=default
    def __enter__(self):
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.lock=self.path.with_suffix('.lock').open('a');fcntl.flock(self.lock,fcntl.LOCK_EX)
        try:self.data=json.loads(self.path.read_text())
        except (OSError,ValueError):self.data=json.loads(json.dumps(self.default))
        if not isinstance(self.data,dict):self.data=json.loads(json.dumps(self.default))
        return self
    def save(self):
        tmp=self.path.with_suffix('.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:json.dump(self.data,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        os.replace(tmp,self.path)
    def __exit__(self,*_):
        self.lock.close()


class SpendLedger:
    """Actual USD per UTC day. No reservations, nothing to leak."""
    def __init__(self,path):self.path=pathlib.Path(path)
    def today(self,now):
        with _Locked(self.path,{'days':{}}) as s:
            day=s.data.get('days',{}).get(utc_day(now),{})
            return Decimal(str(day.get('usd','0'))),int(day.get('calls',0))
    def check(self,limit,now):
        spent,_=self.today(now)
        if spent>=limit:
            raise LLMUnavailable('DAILY_LIMIT',next_utc_midnight(now)+30,detail=f'потрачено ${spent:.4f} из ${limit}')
        return spent
    def add(self,usd,now,tokens_in=0,tokens_out=0):
        with _Locked(self.path,{'days':{}}) as s:
            days=s.data.setdefault('days',{});day=days.setdefault(utc_day(now),{'usd':'0','calls':0,'tokens_in':0,'tokens_out':0})
            day['usd']=str(Decimal(str(day.get('usd','0')))+Decimal(str(usd)));day['calls']=int(day.get('calls',0))+1
            day['tokens_in']=int(day.get('tokens_in',0))+int(tokens_in or 0);day['tokens_out']=int(day.get('tokens_out',0))+int(tokens_out or 0)
            for old in sorted(days)[:-30]:del days[old]
            s.save()


class Gate:
    """Shared provider gate: closed with a resume time, reopened by the first success."""
    def __init__(self,path,rng=None):self.path=pathlib.Path(path);self.rng=rng or (lambda:random.random())
    def state(self):
        with _Locked(self.path,{}) as s:return dict(s.data)
    def check(self,now):
        s=self.state()
        if s.get('until',0)>now:raise LLMUnavailable(s.get('reason','PAUSED'),s['until'],s.get('status'),s.get('detail',''))
    def close(self,reason,now,status=None,retry_after=None,detail='',since=None):
        """since: when the failed request started. Requests already in flight when the gate
        closed belong to the same burst and do not count as a repetition."""
        with _Locked(self.path,{}) as s:
            same=s.data.get('reason')==reason
            burst=same and since is not None and s.data.get('at',0)>=since
            failures=int(s.data.get('failures',0))+(0 if burst else 1) if same else 1
            if burst:return max(s.data.get('until',0),now)
            if s.data.get('until',0)>now and reason=='OUTAGE' and s.data.get('reason')!='OUTAGE':
                return s.data['until']  # an outage never overrides a provider-imposed pause
            if reason=='DAILY_LIMIT':until=next_utc_midnight(now)+30
            elif reason=='RATE_LIMIT':
                expo=min(900,20*2**min(failures-1,6))
                wait=retry_after if retry_after is not None and failures<=1 else max(retry_after or 0,expo)
                until=now+max(5,min(3600,wait))*(1+.2*self.rng())
            elif reason=='OUTAGE':until=now+min(300,10*2**min(failures-1,5))*(1+.2*self.rng())
            else:until=now+WAITS.get(reason,60)
            until=max(until,s.data.get('until',0)) if s.data.get('until',0)>now else until
            s.data={'reason':reason,'until':until,'status':status,'failures':failures,'detail':detail[:300],'at':now};s.save()
            return until
    def open(self,since=None):
        """A success clears only pauses set before that request started (not newer 429s)."""
        with _Locked(self.path,{}) as s:
            if s.data and (since is None or s.data.get('at',0)<=since):s.data={};s.save()


@dataclass
class Reply:
    content:str
    cost:Decimal
    tokens_in:int
    tokens_out:int
    mode:str
    seconds:float


def parse_json_object(text):
    """Lenient: tolerates ```json fences and prose around exactly one JSON object."""
    if not isinstance(text,str) or not text.strip():raise BadModelOutput('EMPTY_REPLY')
    t=text.strip()
    fence=re.search(r'```(?:json)?\s*(.*?)```',t,re.S)
    if fence:t=fence.group(1).strip()
    try:value=json.loads(t)
    except ValueError:
        start,end=t.find('{'),t.rfind('}')
        if start<0 or end<=start:raise BadModelOutput('NO_JSON_OBJECT') from None
        try:value=json.loads(t[start:end+1])
        except ValueError:raise BadModelOutput('INVALID_JSON') from None
    if not isinstance(value,dict):raise BadModelOutput('JSON_NOT_OBJECT')
    return value


def _retry_after(headers,now=None):
    v=headers.get('Retry-After') if headers else None
    if v is None:return None
    try:return float(v)
    except (TypeError,ValueError):pass
    try:return max(0.0,email.utils.parsedate_to_datetime(v).timestamp()-(time.time() if now is None else now))
    except (TypeError,ValueError,IndexError,OverflowError):return None


class Client:
    """Chat completions over plain HTTPS; the response format degrades gracefully."""
    MODES=('json_schema','json_object','plain')
    def __init__(self,root,state_dir,config=None,clock=None,urlopen=None,settings=None):
        # Resolved per call, never at import: a module first imported while a test
        # froze time.time must not keep that frozen clock (gate would never reopen).
        self.root=pathlib.Path(root);self.state_dir=pathlib.Path(state_dir);self.config=config or {}
        self.clock=clock or (lambda:time.time());self.urlopen=urlopen or (lambda *a,**k:urllib.request.urlopen(*a,**k))
        self.fixed=settings;self.ledger=SpendLedger(self.state_dir/'llm-spend.json');self.gate=Gate(self.state_dir/'llm-gate.json')
        self.mode_floor={};self.no_reasoning={}
    def settings(self):
        # Re-read every call: inserting or replacing the token needs no restart.
        return self.fixed or load_settings(self.root,self.config)
    def budget(self,now=None):
        now=self.clock() if now is None else now
        try:s=self.settings();limit=s.daily_usd
        except LLMUnavailable:limit=_decimal(self.config.get('daily_llm_usd',2),2)
        spent,calls=self.ledger.today(now);return spent,limit,calls
    def complete(self,messages,*,schema=None,schema_name='answer',max_tokens=400,timeout=20,temperature=.8,shared_pause=True,deadline=None):
        """shared_pause=False: a slow answer only fails this caller (a chat reply), it does not
        pause everybody. deadline (epoch s): give up waiting by then, the request may finish later."""
        if deadline is None:return self._complete(messages,schema,schema_name,max_tokens,timeout,temperature,shared_pause)
        import threading
        box={}
        def run():
            try:box['reply']=self._complete(messages,schema,schema_name,max_tokens,timeout,temperature,shared_pause)
            except BaseException as e:box['error']=e
        worker=threading.Thread(target=run,daemon=True,name='llm-call');worker.start();worker.join(max(.1,deadline-self.clock()))
        if worker.is_alive():raise LLMUnavailable('TIMEOUT',self.clock()+5,detail='не успели к сроку ответа')
        if 'error' in box:raise box['error']
        return box['reply']
    def _complete(self,messages,schema,schema_name,max_tokens,timeout,temperature,shared_pause):
        now=self.clock()
        try:s=self.settings()
        except LLMUnavailable as e:
            # Not a provider problem: no shared gate, the next call re-reads .env.
            raise LLMUnavailable('NO_TOKEN',now+WAITS['NO_TOKEN'],detail=e.detail) from None
        self.gate.check(now)
        self.ledger.check(s.daily_usd,now)
        start=self.mode_floor.get(s.model,0) if schema else 1
        last=None;index=start
        while index<len(self.MODES):
            mode=self.MODES[index]
            try:
                reply=self._post(s,messages,mode,schema,schema_name,max_tokens,timeout,temperature,shared_pause,now)
                if schema and index!=start:self.mode_floor[s.model]=index
                self.gate.open(since=now);return reply
            except _Rejected as e:
                last=e
                # Models with mandatory reasoning refuse the toggle, not the format.
                if s.openrouter and 'reasoning' in e.detail.lower() and not self.no_reasoning.get(s.model):
                    self.no_reasoning[s.model]=True;continue
                index+=1
        until=self.gate.close('BAD_REQUEST',self.clock(),last.status if last else None,detail=last.detail if last else '')
        raise LLMUnavailable('BAD_REQUEST',until,last.status if last else None,last.detail if last else '')
    def _post(self,s,messages,mode,schema,schema_name,max_tokens,timeout,temperature,shared_pause=True,since=None):
        body={'model':s.model,'messages':messages,'max_tokens':max_tokens,'temperature':temperature}
        if mode=='json_schema':body['response_format']={'type':'json_schema','json_schema':{'name':schema_name,'strict':True,'schema':schema}}
        elif mode=='json_object':body['response_format']={'type':'json_object'}
        if s.openrouter:
            body['usage']={'include':True}
            if mode!='plain' and not self.no_reasoning.get(s.model):body['reasoning']={'enabled':False}
        req=urllib.request.Request(s.base_url+'/chat/completions',data=json.dumps(body,ensure_ascii=False).encode('utf-8'),method='POST',
                                   headers={'Authorization':'Bearer '+s.key,'Content-Type':'application/json','X-Title':'RO residents','HTTP-Referer':'https://github.com/bbutterm/live_ro'})
        started=time.monotonic()
        try:
            with self.urlopen(req,timeout=timeout) as response:raw=response.read(2_000_000)
        except urllib.error.HTTPError as e:
            try:detail=e.read(2000).decode('utf-8','replace')
            except Exception:detail=''
            self._http_failure(e.code,detail,_retry_after(e.headers,self.clock()),mode,since)
        except UnicodeEncodeError:
            raise LLMUnavailable('AUTH',self.clock()+WAITS['AUTH'],detail='токен содержит не-ASCII символы') from None
        except (urllib.error.URLError,TimeoutError,OSError,http.client.HTTPException) as e:
            delivered=isinstance(e,(TimeoutError,socket.timeout))  # sent, then no answer in time
            timed_out=delivered or isinstance(getattr(e,'reason',None),(TimeoutError,socket.timeout))
            if delivered:
                # The provider may still bill an abandoned request: count its prompt conservatively.
                self.ledger.add(Decimal(len(req.data)//3)*s.price_in/Decimal(1_000_000),self.clock(),len(req.data)//3,0)
            if timed_out and not shared_pause:raise LLMUnavailable('TIMEOUT',self.clock()+5,detail='не дождались ответа за '+str(round(timeout,1))+' с') from None
            why=type(e).__name__+': '+str(getattr(e,'reason',None) or e)[:200]
            until=self.gate.close('OUTAGE',self.clock(),detail=why,since=since)
            raise LLMUnavailable('OUTAGE',until,detail=why) from None
        try:payload=json.loads(raw)
        except ValueError:payload=None
        if not isinstance(payload,dict):
            until=self.gate.close('OUTAGE',self.clock(),detail='HTTP body is not a JSON object')
            raise LLMUnavailable('OUTAGE',until,detail='HTTP body is not a JSON object') from None
        if payload.get('error') and not payload.get('choices'):
            err=payload['error'] if isinstance(payload['error'],dict) else {'message':str(payload['error'])}
            code=err.get('code');code=int(code) if isinstance(code,int) or (isinstance(code,str) and code.isdigit()) else 502
            self._http_failure(code,json.dumps(err,ensure_ascii=False)[:2000],None,mode,since)
        usage=payload.get('usage') if isinstance(payload.get('usage'),dict) else {}
        tin,tout=int(usage.get('prompt_tokens') or 0),int(usage.get('completion_tokens') or 0)
        cost=usage.get('cost')
        cost=_decimal(cost,'0') if cost is not None else (tin*s.price_in+tout*s.price_out)/Decimal(1_000_000)
        self.ledger.add(cost,self.clock(),tin,tout)
        try:content=payload['choices'][0]['message'].get('content') or ''
        except (KeyError,IndexError,TypeError,AttributeError):content=''
        if isinstance(content,list):content=''.join(part.get('text','') for part in content if isinstance(part,dict))
        return Reply(content=content,cost=cost,tokens_in=tin,tokens_out=tout,mode=mode,seconds=time.monotonic()-started)
    def _http_failure(self,status,detail,retry_after,mode,since=None):
        now=self.clock()
        if status==429:reason='RATE_LIMIT'
        elif status==402:reason='NO_CREDITS'
        elif status in (401,403):reason='AUTH'
        elif status in (400,404,405,415,422) and mode!='plain':raise _Rejected(status,detail)
        elif status in (400,404,405,415,422):reason='BAD_REQUEST'
        else:reason='OUTAGE'
        until=self.gate.close(reason,now,status,retry_after,detail,since=since)
        raise LLMUnavailable(reason,until,status,detail[:300])


class _Rejected(Exception):
    """Request shape refused (e.g. json_schema unsupported): retry in a simpler mode."""
    def __init__(self,status,detail):super().__init__(status);self.status=status;self.detail=detail[:300]
