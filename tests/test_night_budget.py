import importlib.util
import json
import time
from datetime import datetime
import pathlib
import tempfile
import unittest

class NightBudgetTest(unittest.TestCase):
    def test_reservation_survives_restart_and_cannot_reset_baseline(self):
        self.assertIsNotNone(importlib.util.find_spec('packages.resident_demo.budget'),
                             'Persistent budget guard is missing')
        from packages.resident_demo.budget import Budget, BudgetStop
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'budget.json'
            Budget.initialize(p, 'key-id')
            with Budget(p, 'key-id') as b:
                b.reserve('0.012724245', '1.1', now=1)
            with self.assertRaises(BudgetStop):
                Budget.initialize(p, 'key-id')
            with Budget(p, 'key-id') as b:
                with self.assertRaises(BudgetStop):
                    b.reserve('0.012724245', '1.1', now=1)
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_missing_corrupt_identity_and_concurrent_ledgers_fail_closed(self):
        from packages.resident_demo.budget import Budget, BudgetStop
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'budget.json'
            with self.assertRaises(BudgetStop):
                with Budget(p, 'key-id'): pass
            Budget.initialize(p, 'key-id')
            with self.assertRaises(BudgetStop):
                with Budget(p, 'different-key'): pass
            with Budget(p, 'key-id'):
                with self.assertRaises(BudgetStop):
                    with Budget(p, 'key-id'): pass
            p.write_text('{')
            with self.assertRaises(BudgetStop):
                with Budget(p, 'key-id'): pass

    def test_unknown_decreasing_usage_and_deadline_block_calls(self):
        from packages.resident_demo.budget import Budget, BudgetStop, DEADLINE
        self.assertEqual(DEADLINE, datetime.fromisoformat('2026-10-08T04:00:00+00:00').timestamp())
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'budget.json'
            Budget.initialize(p, 'key-id')
            with Budget(p, 'key-id') as b:
                for usage, cost, now in [('NaN', '.1', 1), ('.01', '.1', 1),
                                         ('.02', 'NaN', 1), ('.02', '.1', DEADLINE),
                                         ('.02', '.1', DEADLINE-1)]:
                    with self.assertRaises(BudgetStop): b.reserve(usage, cost, now)
                b.reserve('.1', '.2', 1)
                with self.assertRaises(BudgetStop): b.reserve('.09', '.1', 1)

    def test_pricing_and_input_bounds_before_reservation(self):
        from packages.resident_demo import budget
        self.assertTrue(hasattr(budget, 'upper_cost'), 'Live pricing bound missing')
        pricing = {'prompt': '0.00000026', 'completion': '0.00000038'}
        messages = [{'role': 'user', 'content': 'Привет'}]
        cost = budget.upper_cost(pricing, messages, 350)
        self.assertGreater(cost, 0)
        self.assertEqual(cost, budget.upper_cost({**pricing, 'input_cache_read': '0.000000135'}, messages, 350))
        for bad in [{}, {'prompt':'NaN','completion':'.1'},
                    {**pricing, 'request': '.1'}]:
            with self.assertRaises(budget.BudgetStop): budget.upper_cost(bad, messages, 350)
        with self.assertRaises(budget.BudgetStop):
            budget.upper_cost(pricing, [{'role':'user','content':'a'*40000}], 350)

    def test_tampered_negative_reservation_cannot_reopen_budget(self):
        from packages.resident_demo.budget import Budget, BudgetStop
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'budget.json'
            Budget.initialize(p, 'key-id')
            data = json.loads(p.read_text())
            data['reservations'] = [{'upper_cost': '-100', 'ts': 1}]
            p.write_text(json.dumps(data))
            with self.assertRaises(BudgetStop):
                with Budget(p, 'key-id') as b: b.reserve('.012724245', '3', 1)

    @__import__('unittest.mock', fromlist=['patch']).patch('packages.resident_demo.budget.time.time', return_value=1)
    def test_paid_boundary_is_called_only_after_durable_reservation(self, fixture_clock):
        from types import SimpleNamespace
        from unittest.mock import patch
        from packages.resident_demo.budget import Budget, BudgetStop, guarded_completion
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'budget.json'
            Budget.initialize(p, 'key-id')
            invocations = []
            def create(**kwargs):
                self.assertEqual(len(json.loads(p.read_text())['reservations']), 1)
                self.assertEqual(kwargs['max_tokens'], 350)
                invocations.append(kwargs)
                raise TimeoutError('fixture ambiguous paid failure')
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            with Budget(p, 'key-id') as b, patch('packages.resident_demo.budget.provider_snapshot',
                    return_value=('.012724245', {'prompt': '.000000259', 'completion': '.00000042'})):
                with self.assertRaisesRegex(BudgetStop, 'PAID_CALL_FAILED_NO_RETRY'):
                    guarded_completion(client, b, {'model':'fixture'}, 'fixture', [{'role':'user','content':'hi'}])
            self.assertEqual(len(invocations), 1)
            self.assertEqual(len(json.loads(p.read_text())['reservations']), 1)
            with Budget(p, 'key-id') as b, patch('packages.resident_demo.budget.provider_snapshot',
                    return_value=('2.012724245', {'prompt': '.000000259', 'completion': '.00000042'})):
                with self.assertRaises(BudgetStop):
                    guarded_completion(client, b, {'model':'fixture'}, 'fixture', [{'role':'user','content':'hi'}])
            self.assertEqual(len(invocations), 1)

    def test_controller_has_no_unguarded_provider_fallback(self):
        src = (pathlib.Path(__file__).parents[1] / 'packages/resident_demo/main.py').read_text()
        self.assertIn('guarded_completion(', src)
        self.assertNotIn('client.chat.completions.create(', src)
        self.assertNotIn('CodexAuxiliaryClient', src)
