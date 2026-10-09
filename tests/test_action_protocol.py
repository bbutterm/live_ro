import unittest,uuid,time
from packages.body_gateway.protocol import Session
class ActionProtocolTest(unittest.TestCase):
 def test_action_envelope_requires_matching_epoch(self):
  epoch=str(uuid.uuid4());s=Session('resident_a',150001)
  s.accept({'proto':1,'type':'hello','resident':'resident_a','char_id':150001,'body_epoch':epoch,'seq':1,'ts':time.time()})
  m={'proto':1,'type':'skill_accepted','action_id':'action1','body_epoch':epoch,'seq':2,'ts':time.time()}
  self.assertEqual(s.accept(m)['type'],'skill_accepted')
  with self.assertRaises(ValueError):s.accept(dict(m,seq=3,body_epoch=str(uuid.uuid4())))
