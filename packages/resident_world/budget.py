"""World-wide UTC daily reservations, fsynced before every paid request."""
import datetime,fcntl,json,os,pathlib,urllib.request,uuid
from decimal import Decimal
from packages.resident_demo.budget import BudgetStop,upper_cost,provider_snapshot

class DailyBudget:
    def __init__(self,path,key_id,limit):
        self.path=pathlib.Path(path);self.key_id=key_id;self.limit=Decimal(str(limit))
        if not self.limit.is_finite() or not 0<self.limit<=2:raise BudgetStop('INVALID_DAILY_LIMIT')
    def reserve(self,usage,cost,now,already_spent=0,*,limit_override=None):
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
                reservations=[Decimal(r['cost']) for r in old['reservations']]
                settlements=[Decimal(r['settled_cost']) for r in old['reservations'] if 'settled_cost' in r]
                reserved=sum((Decimal(r['cost']) for r in old['reservations'] if 'settled_cost' not in r),Decimal(0))
                baseline,last=Decimal(old['baseline']),Decimal(old['last_usage'])
                seed=Decimal(old.get('settlement_baseline',str(usage-baseline)))
                if any(not x.is_finite() or x<=0 for x in reservations) or any(not x.is_finite() or x<0 for x in settlements) or not seed.is_finite() or seed<0 or not baseline.is_finite() or not last.is_finite() or not 0<=baseline<=last<=usage or reserved!=Decimal(old['reserved']):raise ValueError('INVALID_PERSISTED_ACCOUNTING')
                # Response costs remain charged even while provider counters lag.
                charged=max(usage-baseline,already_spent,seed+sum(settlements,Decimal(0)))
                effective=self.limit if limit_override is None else Decimal(str(limit_override))
                if not effective.is_finite() or not self.limit<=effective<=3:raise ValueError('INVALID_TEMPORARY_LIMIT')
                if charged+reserved+cost>effective:raise BudgetStop('DAILY_LIMIT')
                identifier=uuid.uuid4().hex
                old['settlement_baseline']=str(seed)
                old['last_usage']=str(usage);old['reserved']=str(reserved+cost);old['reservations'].append({'id':identifier,'cost':str(cost),'ts':now});state['days'][day]=old
                tmp=self.path.with_suffix('.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                with os.fdopen(fd,'w') as f:json.dump(state,f);f.flush();os.fsync(f.fileno())
                os.replace(tmp,self.path);fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
                return identifier
            except (OSError,ValueError,KeyError,TypeError,ArithmeticError) as error:raise BudgetStop('DAILY_RESERVATION_DENIED') from error

    def settle(self,identifier,actual_cost,response_id):
        """Caller supplies cost from the identified provider response, never an estimate.

        Missing receipts and pre-migration reservations remain fully reserved.
        This does not reconcile unidentified historical calls from aggregate usage.
        """
        if actual_cost is None:return False
        with self.path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            try:
                amount=Decimal(str(actual_cost))
                if not amount.is_finite() or amount<0 or not isinstance(response_id,str) or not response_id:raise ValueError('INVALID_RECEIPT')
                state=json.loads(self.path.read_text())
                if state['key_id']!=self.key_id:raise ValueError('ACCOUNT_IDENTITY')
                found=[(day,r) for day in state['days'].values() for r in day['reservations'] if r.get('id')==identifier and identifier]
                if len(found)!=1:raise ValueError('UNKNOWN_RESERVATION')
                day,entry=found[0]
                if 'settled_cost' in entry:
                    if entry.get('response_id')!=response_id or Decimal(entry['settled_cost'])!=amount:raise ValueError('CONFLICTING_RECEIPT')
                    return True
                if any(r.get('response_id')==response_id for d in state['days'].values() for r in d['reservations']):raise ValueError('DUPLICATE_RECEIPT')
                pending=sum((Decimal(r['cost']) for r in day['reservations'] if 'settled_cost' not in r),Decimal(0))
                upper=Decimal(entry['cost'])
                if pending!=Decimal(day['reserved']) or not pending.is_finite() or not upper.is_finite() or upper<=0 or pending<upper:raise ValueError('INVALID_PERSISTED_ACCOUNTING')
                # Only reservations created with an identified settlement epoch qualify.
                seed=Decimal(day['settlement_baseline'])
                if not seed.is_finite() or seed<0:raise ValueError('INVALID_SETTLEMENT_BASELINE')
                entry.update(settled_cost=str(amount),response_id=response_id)
                day['reserved']=str(pending-upper)
                tmp=self.path.with_suffix('.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                with os.fdopen(fd,'w') as f:json.dump(state,f);f.flush();os.fsync(f.fileno())
                os.replace(tmp,self.path);fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
                return True
            except (OSError,ValueError,KeyError,TypeError,ArithmeticError) as error:raise BudgetStop('DAILY_SETTLEMENT_DENIED') from error

def world_snapshot(cfg,key,timeout=8):
    if cfg['base_url'].rstrip('/')!='https://openrouter.ai/api/v1':raise BudgetStop('UNAPPROVED_ENDPOINT')
    def get(route):
        req=urllib.request.Request(cfg['base_url'].rstrip('/')+route,headers={'Authorization':'Bearer '+key})
        with urllib.request.urlopen(req,timeout=timeout) as response:return json.load(response)['data']
    try:
        account=get('/key');model=next(m for m in get('/models') if m['id']==cfg['model'])
        return account['usage'],model['pricing'],account['usage_daily']
    except Exception as error:raise BudgetStop('ACCOUNTING_UNAVAILABLE') from error
