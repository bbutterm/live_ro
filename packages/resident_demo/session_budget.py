"""Explicit $1 trial authorization; never resets the historical night ledger."""
import fcntl,json,math,os,pathlib,tempfile
from decimal import Decimal
from packages.resident_demo.budget import BudgetStop

class SessionBudget:
    def __init__(self,path,*,key_id,baseline,deadline):
        self.path=pathlib.Path(path)
        try:
            value=Decimal(str(baseline))
            if not value.is_finite() or value<0 or not key_id or type(deadline) not in (float,int) or not math.isfinite(deadline) or deadline<=0:
                raise ValueError('invalid authorization')
        except Exception as error:raise BudgetStop('INVALID_SESSION_POLICY') from error
        self.policy=dict(key_id=key_id,baseline=str(value),deadline=deadline,limit='1')
        self.lock=None

    @classmethod
    def initialize(cls,path,*,prior=None,**policy):
        budget=cls(path,**policy);budget.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        state=dict(budget.policy,last_usage=budget.policy['baseline'],reservations=[])
        if prior is not None:
            old=json.loads(pathlib.Path(prior).read_text())
            if any(old.get(k)!=budget.policy[k] for k in ('key_id','baseline','limit')):raise BudgetStop('PRIOR_AUTHORIZATION_MISMATCH')
            with cls(prior,key_id=old['key_id'],baseline=old['baseline'],deadline=old['deadline']) as parent:
                try:claim=os.open(str(prior)+'.successor',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                except FileExistsError as error:raise BudgetStop('PRIOR_ALREADY_CONTINUED') from error
                with os.fdopen(claim,'w') as stream:
                    stream.write(str(budget.path.resolve()));stream.flush();os.fsync(stream.fileno())
                state.update(last_usage=parent.state['last_usage'],reservations=parent.state['reservations'],prior=str(prior))
        try:fd=os.open(budget.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError as error:raise BudgetStop('SESSION_ALREADY_EXISTS') from error
        with os.fdopen(fd,'w') as stream:
            json.dump(state,stream)
            stream.flush();os.fsync(stream.fileno())

    def __enter__(self):
        self.lock=open(str(self.path)+'.lock','a');os.chmod(self.lock.name,0o600)
        try:
            fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.state=json.loads(self.path.read_text())
            if self.path.stat().st_mode&0o077 or any(self.state.get(k)!=v for k,v in self.policy.items()):raise ValueError('policy mismatch')
        except Exception as error:
            self.lock.close();self.lock=None;raise BudgetStop('SESSION_LEDGER_UNAVAILABLE') from error
        return self

    def __exit__(self,*_):
        if self.lock is not None:self.lock.close();self.lock=None

    def reserve(self,usage,upper_cost,now):
        try:
            usage,cost=Decimal(str(usage)),Decimal(str(upper_cost));baseline=Decimal(self.policy['baseline'])
            previous=Decimal(self.state['last_usage']);reservations=[Decimal(r['upper_cost']) for r in self.state['reservations']]
            if (self.lock is None or not all(v.is_finite() for v in [usage,cost,previous,*reservations])
                    or cost<=0 or any(v<=0 for v in reservations) or usage<previous or previous<baseline
                    or type(now) not in (float,int) or not math.isfinite(now) or now+60>=self.policy['deadline']
                    or usage-baseline+sum(reservations,Decimal(0))+cost>Decimal('1')):raise ValueError('authorization exhausted')
        except Exception as error:raise BudgetStop('CALL_NOT_AUTHORIZED') from error
        self.state['last_usage']=str(usage)
        self.state['reservations'].append(dict(upper_cost=str(cost),ts=now))
        fd,name=tempfile.mkstemp(prefix='.budget-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'w') as stream:
                json.dump(self.state,stream);stream.flush();os.fsync(stream.fileno())
            os.replace(name,self.path)
        finally:
            if os.path.exists(name):os.unlink(name)
        return len(self.state['reservations'])
