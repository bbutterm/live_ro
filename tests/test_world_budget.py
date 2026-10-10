import unittest,tempfile,pathlib,json

class DailyBudgetTests(unittest.TestCase):
    def test_confirmed_receipt_replaces_reserve_without_trusting_usage_lag(self):
        from packages.resident_world.budget import DailyBudget, BudgetStop
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp)/'budget.json'
            budget = DailyBudget(p, 'key', 2)
            call = budget.reserve(10, 1, 1791532000)
            self.assertTrue(callable(getattr(budget, 'settle', None)), 'missing receipt settlement')
            budget.settle(call, '.01', 'gen-confirmed')
            budget.settle(call, '.01', 'gen-confirmed')  # retry is idempotent
            with self.assertRaises(BudgetStop):
                budget.reserve(10, '1.995', 1791532001)
            budget.reserve(10, '1.90', 1791532002)
            day = json.loads(p.read_text())['days']['2026-10-09']
            self.assertEqual(day['reserved'], '1.90')
            self.assertEqual(day['reservations'][0]['settled_cost'], '0.01')
            with self.assertRaises(BudgetStop):
                budget.settle('unidentified-old-call', '0', 'gen-unknown')

    def test_explicit_temporary_override_keeps_old_reserves(self):
        from packages.resident_world.budget import DailyBudget,BudgetStop
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'budget.json';budget=DailyBudget(p,'key',2)
            budget.reserve(10,1.9,1791532000)
            budget.reserve(10,.2,1791532001,limit_override=3)
            with self.assertRaises(BudgetStop):budget.reserve(10,.01,1791532002)
            self.assertEqual(len(json.loads(p.read_text())['days']['2026-10-09']['reservations']),2)

    def test_provider_daily_spend_is_included_in_first_reservation(self):
        from packages.resident_world.budget import DailyBudget,BudgetStop
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BudgetStop):DailyBudget(pathlib.Path(tmp)/'budget.json','key',2).reserve(10,.75,1791532000,already_spent=1.5)
    def test_shared_reservations_survive_restart_and_block_over_cap(self):
        from packages.resident_world.budget import DailyBudget,BudgetStop
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'budget.json'
            DailyBudget(p,'key',2).reserve(10,.75,1791532000)
            DailyBudget(p,'key',2).reserve(10,.75,1791532001)
            with self.assertRaises(BudgetStop):DailyBudget(p,'key',2).reserve(10,.75,1791532002)
            state=json.loads(p.read_text());day=state['days']['2026-10-09']
            self.assertEqual(len(day['reservations']),2)
            self.assertEqual(day['reserved'],'1.50')
