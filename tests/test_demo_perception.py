import unittest
from packages.resident_demo import policy
class PerceptionTest(unittest.TestCase):
 def test_only_nearby_same_map_public_speech_enters_context(self):
  f=getattr(policy,'perceive',None);self.assertTrue(callable(f),'Perception missing')
  t={'map':'prontera','pos':{'x':150,'y':150}}
  rows=[{'id':i,'map':m,'x':x,'y':150,'text':'hello'}for i,m,x in [(1,'prontera',155),(2,'prontera',200),(3,'prt_in',150)]]
  self.assertEqual([r['id']for r in f(t,rows)],[1])
