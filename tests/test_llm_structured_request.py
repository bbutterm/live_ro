import json,pathlib,tempfile,time,types,unittest
from unittest.mock import patch

class StructuredRequestTest(unittest.TestCase):
    def test_schema_and_required_provider_parameters_are_sent_after_reservation(self):
        from packages.resident_demo.budget import guarded_completion
        from packages.resident_demo.session_budget import SessionBudget
        schema={'type':'object','properties':{'goal':{'type':'string','enum':['wait']}},'required':['goal'],'additionalProperties':False}
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'budget.json';policy=dict(key_id='unit',baseline='7',deadline=time.time()+300)
            SessionBudget.initialize(path,**policy)
            with SessionBudget(path,**policy) as guard:
                captured={}
                def create(**kwargs):
                    self.assertEqual(len(json.loads(path.read_text())['reservations']),1)
                    captured.update(kwargs);return object()
                client=types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
                with patch('packages.resident_demo.budget.provider_snapshot',return_value=(7,{'prompt':'0.000001','completion':'0.000001'})):
                    guarded_completion(client,guard,{'model':'fixture'},'fixture',[{'role':'user','content':'test'}],response_schema=schema)
                self.assertEqual(captured['response_format']['type'],'json_schema')
                self.assertTrue(captured['response_format']['json_schema']['strict'])
                self.assertEqual(captured['response_format']['json_schema']['schema'],schema)
                self.assertTrue(captured['extra_body']['provider']['require_parameters'])
                self.assertEqual(captured['extra_body']['provider']['only'],['DeepInfra'])
                self.assertFalse(captured['extra_body']['provider']['allow_fallbacks'])
                self.assertEqual(captured['max_tokens'],350)
