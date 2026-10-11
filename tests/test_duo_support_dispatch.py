import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
from packages.resident_world import skills
class SupportDispatchTests(unittest.TestCase):
    def test_only_operator_opted_profile_dispatches_support(self):
        c={'residents':[{'resident_id':'resident_b','support_target':'Borin'}]}
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'resident_b'
            with patch.object(skills,'request',return_value={'telemetry':{'map':'prt_fild08'}}),patch.object(skills,'run_native',return_value='done') as run:
                skills.execute_native_menu('hunt',{'map':'prt_fild08','minutes':5},c,'sock','key',{},state)
                self.assertEqual(run.call_args.args[1]['params']['support_target'],'Borin')
