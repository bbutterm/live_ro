import unittest
from unittest.mock import patch
from packages.resident_behavior.config import load_behavior
from packages.resident_behavior.reducer import initial_state
from packages.resident_behavior.bridge import snapshot_from_status
from packages.resident_behavior.runtime import observe
from tests.test_behavior_motor import Body
from tests.test_behavior_adapter import Clock

class BehaviorRuntimeTests(unittest.TestCase):
    def test_observer_enriches_flags_and_keeps_before_state(self):
        c=load_behavior('config/behavior.yaml');clock=Clock();body=Body(clock)
        status=body('explicit',{'op':'status'});now=clock().timestamp()
        before=initial_state(c,'mira',snapshot_from_status(status,'mira',1),now)
        def enrich(config,rid,snapshot,mental,peers,when):
            mental['derived']['observer_enriched']=1
            return mental
        with patch('packages.resident_behavior.runtime.enrich',side_effect=enrich) as called:
            snapshot,state,record=observe(c,{'name':'Mira'},status,before,2,now)
        called.assert_called_once()
        self.assertEqual(state['derived']['observer_enriched'],1)
        self.assertEqual(record['drives_before'],before['drives'])
        self.assertEqual(record['relations_before'],before['relations'])
        self.assertNotIn('observer_enriched',before['derived'])
        self.assertEqual(record['state']['rollout_mode'],'read_only_shadow')
