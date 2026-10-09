import unittest,tempfile,pathlib,json

class DailyBudgetTests(unittest.TestCase):
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
