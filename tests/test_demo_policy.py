import importlib.util,unittest
class DemoPolicyTest(unittest.TestCase):
 def policy(self):
  self.assertIsNotNone(importlib.util.find_spec('packages.resident_demo'),'Three-resident policy is missing')
  from packages.resident_demo.policy import validate
  return validate
 def test_valid(self):self.assertEqual(self.policy()({'action':'speak','text':'Привет, Mira!','memory':'Встретил Миру.'},'resident_a')['action'],'speak')
 def test_forbids_commands_and_controls(self):
  f=self.policy()
  for t in ('@die',' #warp prontera','/eval','a\nquit','x'*121):
   with self.assertRaises(ValueError):f({'action':'speak','text':t},'resident_a')
 def test_rejects_arbitrary_actions_and_fields(self):
  f=self.policy()
  for d in ({'action':'sql'},{'action':'wait','shell':'ls'},{'action':'hunt'},{'action':'speak','text':''}):
   with self.assertRaises(ValueError):f(d,'resident_b')
 def test_unknown_persona_and_bad_json_types(self):
  f=self.policy()
  for d,r in (([], 'resident_a'),({'action':'wait'},'gm'),({'action':'wait','memory':{}},'resident_a')):
   with self.assertRaises(ValueError):f(d,r)
