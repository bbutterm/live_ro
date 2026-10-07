import importlib.util,pathlib,sys,unittest
ROOT=pathlib.Path(__file__).parents[1];sys.path.insert(0,str(ROOT))
class ProtocolTest(unittest.TestCase):
 def test_real_session_requires_matching_hello_before_telemetry(self):
  p=ROOT/'packages/body_gateway/protocol.py';self.assertTrue(p.exists(),'Body session not implemented')
  s=importlib.util.spec_from_file_location('body_protocol',p);assert s and s.loader;m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
  session=m.Session('resident_a',150001)
  epoch='cf844d0a-3d79-4b52-a327-e4ff0adf5090'
  telemetry={'proto':1,'type':'telemetry','seq':2,'body_epoch':epoch,'ts':1,'map':'prontera','pos':{'x':150,'y':150},'dest':{'x':160,'y':150},'hp':40,'hp_max':40,'dead':False,'mode':'IDLE_SAFE'}
  with self.assertRaises(ValueError):session.accept(telemetry)
  wrong={'proto':1,'type':'hello','resident':'resident_a','char_id':150000,'body_epoch':epoch,'seq':1,'ts':1}
  with self.assertRaises(ValueError):session.accept(wrong)
  try:session.accept(dict(wrong,char_id=150001,body_epoch=None))
  except ValueError:pass
  except Exception as e:self.fail('Malformed UUID crashes instead of peer rejection: '+type(e).__name__)
  else:self.fail('Malformed UUID accepted')
  session.accept(dict(wrong,char_id=150001))
  self.assertEqual(session.accept(telemetry)['pos'],{'x':150,'y':150})
  with self.assertRaises(ValueError):session.accept(telemetry)
  with self.assertRaises(ValueError):session.accept(dict(telemetry,seq=3,body_epoch='other'))
  with self.assertRaises(ValueError):session.accept(dict(telemetry,seq=3,proto=2))
