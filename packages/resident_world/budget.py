"""World-wide UTC daily reservations, fsynced before every paid request."""
import datetime,fcntl,json,os,pathlib,urllib.request
from decimal import Decimal
from packages.resident_demo.budget import BudgetStop,upper_cost,provider_snapshot

class DailyBudget:
    def __init__(self,path,key_id,limit):
        self.path=pathlib.Path(path);self.key_id=key_id;self.limit=Decimal(str(limit))
        if not self.limit.is_finite() or not 0<self.limit<=2:raise BudgetStop('INVALID_DAILY_LIMIT')
    def reserve(self,usage,cost,now,already_spent=0):
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with self.path.with_suffix('.lock').open('a') as lock:
            os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX)
            try:
                usage,cost,already_spent=Decimal(str(usage)),Decimal(str(cost)),Decimal(str(already_spent))
                if not already_spent.is_finite() or not 0<=already_spent<=usage:raise ValueError('INVALID_DAILY_USAGE')
                if not usage.is_finite() or usage<0 or not cost.is_finite() or cost<=0:raise ValueError('INVALID_ACCOUNTING')
                day=datetime.datetime.fromtimestamp(now,datetime.timezone.utc).date().isoformat()
                state=json.loads(self.path.read_text()) if self.path.exists() else {'key_id':self.key_id,'days':{}}
                if state['key_id']!=self.key_id or state['days'] and day<max(state['days']):raise ValueError('ACCOUNT_IDENTITY_OR_CLOCK')
                old=state['days'].get(day,{'baseline':str(usage-already_spent),'last_usage':str(usage),'reserved':'0','reservations':[]})
                reservations=[Decimal(r['cost']) for r in old['reservations']];reserved=sum(reservations,Decimal(0))
                baseline,last=Decimal(old['baseline']),Decimal(old['last_usage'])
                if any(not x.is_finite() or x<=0 for x in reservations) or not baseline.is_finite() or not last.is_finite() or not 0<=baseline<=last<=usage or reserved!=Decimal(old['reserved']):raise ValueError('INVALID_PERSISTED_ACCOUNTING')
                if usage-baseline+reserved+cost>self.limit:raise BudgetStop('DAILY_LIMIT')
                old['last_usage']=str(usage);old['reserved']=str(reserved+cost);old['reservations'].append({'cost':str(cost),'ts':now});state['days'][day]=old
                tmp=self.path.with_suffix('.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                with os.fdopen(fd,'w') as f:json.dump(state,f);f.flush();os.fsync(f.fileno())
                os.replace(tmp,self.path);fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
                return len(old['reservations'])
            except (OSError,ValueError,KeyError,TypeError,ArithmeticError) as error:raise BudgetStop('DAILY_RESERVATION_DENIED') from error

def world_snapshot(cfg,key,timeout=8):
    if cfg['base_url'].rstrip('/')!='https://openrouter.ai/api/v1':raise BudgetStop('UNAPPROVED_ENDPOINT')
    def get(route):
        req=urllib.request.Request(cfg['base_url'].rstrip('/')+route,headers={'Authorization':'Bearer '+key})
        with urllib.request.urlopen(req,timeout=timeout) as response:return json.load(response)['data']
    try:
        account=get('/key');model=next(m for m in get('/models') if m['id']==cfg['model'])
        return account['usage'],model['pricing'],account['usage_daily']
    except Exception as error:raise BudgetStop('ACCOUNTING_UNAVAILABLE') from error
