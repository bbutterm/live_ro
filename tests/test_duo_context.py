import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from packages.resident_world import mind

class DuoContextTests(unittest.TestCase):
    def test_compact_state_exposes_only_observed_job_party_and_skills(self):
        r={'name':'Mira','char_id':150002}
        s={'telemetry':{'class_id':4,'job_level':1,'learned_skills':{'AL_HEAL':0},'party':{'name':'Duo','source':'native_client','members':[{'name':'Borin','hp':70,'hp_max':100}]}}}
        with tempfile.TemporaryDirectory() as d, patch.object(mind,'witness_rows',return_value=[]):
            result=json.loads(mind.compact_state(r,s,Path(d),[],limit=10000))
        self.assertEqual(result['class_id'],4)
        self.assertEqual(result['learned_skills']['AL_HEAL'],0)
        self.assertEqual(result['party']['members'][0]['hp'],70)
        self.assertNotIn('healed',result)

    def test_autonomous_context_has_observed_party_and_job(self):
        from tests import test_world_mind_brain as base
        harness=base.ChooseTests();harness.setUp()
        try:
            s={**base.HEALTHY,'telemetry':{**base.HEALTHY['telemetry'],'class_id':4,'learned_skills':{'AL_HEAL':1},'party':{'name':'Duo','source':'native_client'}}}
            result=harness.m.perceive(s,1000)
            self.assertEqual(result['я']['class_id'],4)
            self.assertEqual(result['я']['learned_skills']['AL_HEAL'],1)
            self.assertEqual(result['я']['party']['source'],'native_client')
        finally:harness.tearDown()
