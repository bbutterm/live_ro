import importlib.util,pathlib,tempfile,unittest
s=importlib.util.spec_from_file_location('reader',pathlib.Path(__file__).parents[1]/'packages/witness_reader/reader.py')
assert s is not None and s.loader is not None
r=importlib.util.module_from_spec(s);s.loader.exec_module(r)

class StoreTests(unittest.TestCase):
 def test_restart_preserves_cursor_and_deduplicates(self):
  self.assertTrue(callable(getattr(r,'EventStore',None)),'persistent EventStore missing')
  with tempfile.TemporaryDirectory() as d:
   p=str(pathlib.Path(d)/'events.sqlite');x=r.EventStore(p)
   row={'id':4,'kind':'unit_fixture','ts':'raw_fixture_time'}
   self.assertEqual(x.append('test-source',[row]),[row]);x.close()
   y=r.EventStore(p);self.assertEqual(y.cursor('test-source'),4)
   self.assertEqual(y.append('test-source',[row]),[]);self.assertEqual(y.cursor('other-source'),0);y.close()
 def test_transient_fetch_failure_is_retried(self):
  from unittest.mock import patch
  self.assertTrue(callable(getattr(r,'fetch_with_retry',None)),'bounded retry missing')
  with patch.object(r,'read_events',side_effect=[RuntimeError('unit fixture'),[{'id':1}]]),patch.object(r.time,'sleep'):
   self.assertEqual(r.fetch_with_retry('unit-fixture',0),[{'id':1}])
 def test_bad_batch_rolls_back_events_and_cursor(self):
  with tempfile.TemporaryDirectory() as d:
   x=r.EventStore(str(pathlib.Path(d)/'events.sqlite'))
   with self.assertRaises(ValueError):x.append('s',[{'id':1},{'id':0}])
   self.assertEqual(x.cursor('s'),0)
   self.assertEqual(x.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0);x.close()
 def test_conflicting_same_id_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   x=r.EventStore(str(pathlib.Path(d)/'events.sqlite'));x.append('s',[{'id':1,'kind':'fixture_a'}])
   with self.assertRaises(ValueError):x.append('s',[{'id':1,'kind':'fixture_b'}])
   self.assertEqual(x.cursor('s'),1);x.close()
