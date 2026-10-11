import unittest,tempfile
from pathlib import Path
from packages.resident_world.chat import ChatStore
class DuoCadenceTests(unittest.TestCase):
    def test_bot_chain_ends_after_quiet_but_stays_bounded_within_exchange(self):
        with tempfile.TemporaryDirectory() as d:
            store=ChatStore(Path(d)/'chat.sqlite')
            outcomes=[]
            for i in range(5):
                e={'id':i+1,'name':'Borin','text':'tactic '+str(i),'ts':100+i}
                outcomes.append(store.enqueue(e,'Mira',True,100+i))
            self.assertEqual(outcomes,[True,True,True,True,False])
            self.assertTrue(store.enqueue({'id':6,'name':'Borin','text':'a new observed tactic','ts':225},'Mira',True,225))
            self.assertEqual(store.get('chain'),1)

    def test_no_quiet_reset_for_fast_echo_loop(self):
        with tempfile.TemporaryDirectory() as d:
            store=ChatStore(Path(d)/'chat.sqlite')
            for i in range(8):store.enqueue({'id':i+1,'name':'Borin','text':'reply '+str(i),'ts':100+i},'Mira',True,100+i)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM inbox WHERE status='queued'").fetchone()[0],4)
