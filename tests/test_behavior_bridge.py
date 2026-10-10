import unittest
from packages.resident_behavior.bridge import snapshot_from_status

class BehaviorBridgeTests(unittest.TestCase):
    def test_snapshot_uses_observations_not_document_initial_numbers(self):
        status={'telemetry':{'ts':1791626400,'map':'prontera','pos':{'x':156,'y':190},'hp':25,'hp_max':100,'sp':10,'sp_max':20,'weight':84,'weight_max':100,'zeny':17,'dead':False,'sitting':False,'inventory':{'501':1},'base_level':7,'skill_points':0,'nearby_players':[]},'age':1,'actions':[]}
        result=snapshot_from_status(status,'mira',1)
        self.assertEqual(result['zeny'],17)
        self.assertEqual(result['base_level'],7)
        self.assertIsNone(result['job_level'])
        self.assertIsNone(result['near_monsters'])
        self.assertEqual(result['inventory'],{'501':1})
        self.assertIsNone(result['online_minutes'])
        self.assertEqual(result['resident_id'],'mira')
