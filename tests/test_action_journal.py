import importlib.util,pathlib,tempfile,unittest
class JournalTest(unittest.TestCase):
 def test_time_range_index_migrates_existing_events_without_changing_facts(self):
  from packages.body_gateway.journal import Journal
  with tempfile.TemporaryDirectory() as d:
   path=d+'/world.sqlite';j=Journal(path)
   j.event('witness',1,10,{'id':1},1);j.event('witness',2,20,{'id':2},2)
   j.db.execute('DROP INDEX IF EXISTS events_source_ts');j.close()
   j=Journal(path)
   plan=' '.join(r[3] for r in j.db.execute("EXPLAIN QUERY PLAN SELECT payload FROM events WHERE source='witness' AND ts>=? ORDER BY ts",(15,)))
   self.assertIn('events_source_ts',plan);self.assertNotIn('TEMP B-TREE',plan)
   self.assertEqual([r[0] for r in j.db.execute("SELECT source_key FROM events WHERE source='witness' AND ts>=? ORDER BY ts",(15,))],['2'])
   self.assertEqual(j.cursor('witness'),2);self.assertEqual(j.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],2);j.close()
 def test_transition_chain_is_durable_and_duplicate_does_not_create_action(self):
  module=pathlib.Path(__file__).parents[1]/'packages/body_gateway/journal.py'
  self.assertTrue(module.exists(),'Action journal missing')
  from packages.body_gateway.journal import Journal
  with tempfile.TemporaryDirectory() as d:
   j=Journal(d+'/world.sqlite');a=j.plan('resident_a','travel_to',{'map':'prontera','x':150,'y':150,'r':3},'key1',123,'epoch1',42)
   self.assertEqual(j.plan('resident_a','travel_to',{},'key1',123,'epoch1',42)['action_id'],a['action_id'])
   for s in ('dispatched','accepted','running','verifying','confirmed'):j.transition(a['action_id'],s,'ARRIVED' if s=='confirmed' else '',{})
   self.assertEqual([r['state'] for r in j.trace(a['action_id'])],['planned','dispatched','accepted','running','verifying','confirmed']);j.close()
   j=Journal(d+'/world.sqlite');self.assertEqual(j.action(a['action_id'])['state'],'confirmed');self.assertEqual(len(j.actions()),1);j.close()


class JournalGrowthTests(unittest.TestCase):
    def test_purge_keeps_open_evidence_and_drops_old_history(self):
        import tempfile,time,pathlib
        from packages.body_gateway.journal import Journal
        with tempfile.TemporaryDirectory() as tmp:
            j=Journal(str(pathlib.Path(tmp)/'j.sqlite'));now=time.time()
            for i in range(50):j.event('witness',str(i),now-4*86400+i,{'i':i})
            j.event('witness','fresh',now-60,{'i':'fresh'})
            old=j.plan('r','hunt',{},'old',now-31*86400,'e',0);j.transition(old['action_id'],'rejected_local','X')
            j.db.execute('UPDATE actions SET updated=? WHERE action_id=?',(now-31*86400,old['action_id']));j.db.commit()
            open_=j.plan('r','hunt',{},'open',now+600,'e',0)
            j.db.execute('UPDATE actions SET created=? WHERE action_id=?',(now-3*86400,open_['action_id']));j.db.commit()
            j.event('witness','needed',now-3*86400+30,{'i':'needed'})  # inside the open action's window
            for _ in range(20):j.log('cancel',{})
            j.purge(now)
            keys={r[0] for r in j.db.execute("SELECT source_key FROM events")}
            self.assertEqual(keys,{'fresh','needed'})                       # 4-day-old facts gone, open evidence kept
            self.assertEqual([a['idem_key'] for a in j.open_actions('r')],['open'])
            self.assertEqual(j.db.execute('SELECT COUNT(*) FROM actions').fetchone()[0],1)  # month-old finished action gone
            j.close()
