import importlib.util,json,pathlib,tempfile,unittest

class SessionBudgetTest(unittest.TestCase):
    def test_continuation_keeps_prior_spend_and_cannot_reset_baseline(self):
        from packages.resident_demo.session_budget import SessionBudget
        from packages.resident_demo.budget import BudgetStop
        with tempfile.TemporaryDirectory() as directory:
            old=pathlib.Path(directory)/'old.json';new=pathlib.Path(directory)/'new.json'
            policy=dict(key_id='test',baseline='7',deadline=1000)
            SessionBudget.initialize(old,**policy)
            with SessionBudget(old,**policy) as budget:budget.reserve('7','.8',now=1)
            before=old.read_bytes()
            SessionBudget.initialize(new,**{**policy,'deadline':2000},prior=old)
            with SessionBudget(new,**{**policy,'deadline':2000}) as budget:
                with self.assertRaises(BudgetStop):budget.reserve('7','.3',now=1)
            self.assertEqual(old.read_bytes(),before)
            with self.assertRaises(BudgetStop):SessionBudget.initialize(pathlib.Path(directory)/'bad.json',**{**policy,'baseline':'8'},prior=old)

    def test_fresh_one_dollar_budget_preserves_reservations_and_deadline(self):
        self.assertIsNotNone(importlib.util.find_spec('packages.resident_demo.session_budget'))
        from packages.resident_demo.session_budget import SessionBudget
        from packages.resident_demo.budget import BudgetStop
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'budget.json'
            policy=dict(key_id='test',baseline='7',deadline=1000)
            SessionBudget.initialize(path,**policy)
            with SessionBudget(path,**policy) as budget:
                budget.reserve('7','.6',now=1)
            with SessionBudget(path,**policy) as budget:
                with self.assertRaises(BudgetStop):budget.reserve('7','.5',now=1)
                with self.assertRaises(BudgetStop):budget.reserve('7','.1',now=950)
            with self.assertRaises(BudgetStop):SessionBudget.initialize(path,**policy)
            with self.assertRaises(BudgetStop):
                with SessionBudget(path,**{**policy,'deadline':2000}):pass
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(len(json.loads(path.read_text())['reservations']),1)
