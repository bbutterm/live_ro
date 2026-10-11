"""Pinned provider bridge: global USD cap plus durable behavior quotas.

Read-only provider accounting precedes every reservation. Unidentified requests
remain reserved, including after process restart; no automatic historical refund.
"""
import hashlib
import threading
from pathlib import Path
from .budget import Budget
from packages.resident_world.budget import DailyBudget, BudgetStop, world_snapshot, upper_cost

BASE_URL = 'https://openrouter.ai/api/v1'
MODEL = 'deepseek/deepseek-v3.2'


class ProviderBudget:
    def __init__(self, api_key, state_root, snapshot=None):
        self.key = api_key
        self.config = {'base_url': BASE_URL, 'model': MODEL}
        self.snapshot = snapshot or world_snapshot
        self.shared = DailyBudget(Path(state_root)/'daily-budget.json', hashlib.sha256(api_key.encode()).hexdigest(), 2)
        self.quota = Budget(Path(state_root)/'behavior-llm-budget.sqlite', global_cap=self._global_reserve)
        self.references = {}
        self.lock = threading.RLock()
        self.account = None
        self.last_error = None
        self.state_root = Path(state_root)
        self.limit_override = None

    def authorized_limit(self, rid, now):
        """Owner-approved one-resident override; expires with the 20-minute lease."""
        import json
        from .ownership import read_lease
        if rid not in ('resident_a','mira','borin'):
            return None
        try:
            approval=json.loads((self.state_root/'llm-budget-override.json').read_text())
            lease=read_lease(self.state_root/rid)
            if (approval['rid']==rid and approval['limit']==3
                    and approval['created_at']<=now<approval['expires_at']
                    and 0<approval['expires_at']-approval['created_at']<=1200
                    and lease and lease['phase']=='controlled' and now<lease['deadline']
                    and approval['lease_digest']==hashlib.sha256(lease['token'].encode()).hexdigest()):
                return 3
        except (OSError,ValueError,KeyError,TypeError):
            pass
        return None

    def _global_reserve(self, amount, now, identifier):
        usage, _, daily = self.account
        common_id = self.shared.reserve(usage, amount, now, already_spent=daily,limit_override=self.limit_override)
        self.references[identifier] = common_id
        return True

    def reserve(self, rid, kind, max_tokens, now):
        with self.lock:
            try:
                self.account = self.snapshot(self.config, self.key)
                # The prompt is capped at 1536 UTF-8 bytes. Reserve the existing
                # pricing guard's larger 350-token response bound, not a guess.
                upper = upper_cost(self.account[1], [{'role':'user','content':'x'*1536}], 350)
                self.last_error = None
                self.limit_override = self.authorized_limit(rid,now)
                return self.quota.reserve(rid, kind, max_tokens, now, upper_cost_usd=upper)
            except (BudgetStop, OSError, ValueError, KeyError, TypeError, ArithmeticError):
                self.last_error = 'provider_or_shared_budget_denied'
                return None
            finally:
                self.account = None

    def settle(self, identifier, cost, response_id):
        with self.lock:
            try:
                common_id = self.references.get(identifier)
                if common_id is None or cost is None:
                    return False
                self.shared.settle(common_id, cost, response_id)
                self.quota.settle(identifier, cost)
                # The same key also feeds the resident_world brain: one spend journal sees both.
                from packages.resident_world.llm import SpendLedger
                import time
                SpendLedger(self.state_root/'llm-spend.json').add(cost, time.time())
                return True
            except (BudgetStop, OSError, ValueError, KeyError, TypeError, ArithmeticError):
                self.last_error = 'settlement_denied'
                return False


def from_environment(env_path, state_root='/var/lib/ro-world'):
    """Read credentials locally; never expose them in repr, logs or exceptions."""
    from .llm import openrouter_client
    values = {}
    for line in Path(env_path).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('\"\'')
    if values.get('RO_WORLD_LLM_BASE_URL', '').rstrip('/') != BASE_URL or values.get('RO_WORLD_LLM_MODEL') != MODEL:
        raise ValueError('unapproved provider/model')
    key = values.get('RO_WORLD_LLM_API_KEY')
    if not key:
        raise ValueError('missing provider credential')
    return openrouter_client(api_key=key, state_root=state_root)
