import json
import tempfile
import unittest
from pathlib import Path


class BudgetTests(unittest.TestCase):
    def test_reserve_persist_rates_and_unknown_usage(self):
        from packages.resident_behavior.budget import Budget
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'budget.sqlite'
            b = Budget(path)
            ids = [b.reserve('a', 'decision', 192, 10000+i) for i in range(6)]
            self.assertTrue(all(ids))
            self.assertIsNone(b.reserve('a', 'decision', 192, 10010))
            b.settle(ids[0], None)
            self.assertIsNone(Budget(path).reserve('a', 'decision', 192, 10010))
            self.assertIsNotNone(b.reserve('a', 'chat', 128, 10010))
            self.assertIsNotNone(b.reserve('a', 'decision', 192, 13601))

    def test_quotas_no_borrow_and_concurrent_rate_limit(self):
        from concurrent.futures import ThreadPoolExecutor
        from packages.resident_behavior.budget import Budget
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'budget.sqlite'
            b = Budget(path)
            rid = b.reserve('a', 'chat', 128, 10000)
            b.settle(rid, .549)
            self.assertIsNone(b.reserve('b', 'chat', 128, 10000))
            self.assertIsNotNone(b.reserve('b', 'decision', 192, 10000))
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: Budget(path).reserve('c', 'decision', 192, 10000), range(20)))
            self.assertEqual(sum(x is not None for x in results), 6)
            self.assertIsNotNone(b.reserve('b', 'chat', 128, 90000))

    def test_chat_daily_and_legacy_days_lock_protocol(self):
        from packages.resident_behavior.budget import Budget
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'budget.sqlite'
            b = Budget(path)
            for i in range(180):
                self.assertIsNotNone(b.reserve(str(i), 'chat', 128, 10000))
            self.assertIsNone(b.reserve('extra', 'chat', 128, 10000))
            old = Path(d)/'old.json'
            old.write_text(json.dumps({'key_id': 'x', 'days': {'1970-01-01': {'baseline': '10', 'last_usage': '10', 'reserved': '0.1', 'reservations': [{'cost': '0.1', 'ts': 1}]}}}))
            b = Budget(Path(d)/'other.sqlite', old)
            rid = b.reserve('a', 'chat', 128, 10000)
            self.assertIsNotNone(rid)
            b.settle(rid, 0)
            state = json.loads(old.read_text())['days']['1970-01-01']
            self.assertEqual(state['reserved'], '0.10208')
            self.assertEqual(len(state['reservations']), 2)

    def test_legacy_cap_preserves_reserved(self):
        from packages.resident_behavior.budget import Budget
        with tempfile.TemporaryDirectory() as d:
            old = Path(d)/'daily-budget.json'
            state = {'day': '1970-01-01', 'baseline': 10, 'provider_usage': 11.9, 'reserved': .1, 'calls': 2}
            old.write_text(json.dumps(state))
            self.assertIsNone(Budget(Path(d)/'new.sqlite', old).reserve('a', 'chat', 128, 10000))
            self.assertEqual(json.loads(old.read_text()), state)
