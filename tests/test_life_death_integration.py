"""Synthetic Journal/Executor/LifeCycle integration, NOT live game acceptance."""
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from packages.body_gateway.executor import Executor
from packages.body_gateway.journal import Journal
from packages.resident_demo.life_cycle import LifeCycle


class DeathIntegrationTests(unittest.TestCase):
    def test_respawn_needs_server_loadmap_before_life_can_return_to_town(self):
        with tempfile.TemporaryDirectory() as directory, patch('time.time',return_value=1000) as clock:
            root=pathlib.Path(directory)
            journal=Journal(str(root/'journal.sqlite'))
            self.addCleanup(journal.db.close)
            sent=[]
            executor=Executor(journal,'resident_a',150001,sent.append)
            executor.body(dict(type='hello',body_epoch='fixture-epoch',ts=1000))
            def body(dead,map_name,hp,ts):
                executor.body(dict(type='telemetry',body_epoch='fixture-epoch',ts=ts,
                    map=map_name,pos=dict(x=150,y=150),dead=dead,hp=hp,hp_max=795,
                    inventory={'501':20},zeny=100, running=None))
            def rpc(msg):
                if msg['op']=='status':return dict(age=clock.return_value-executor.received,
                    telemetry=executor.telemetry,projection=executor.projection,actions=executor.active())
                if msg['op']=='run':return executor.run(msg['skill'],msg['params'],msg['deadline'],msg['idem'])
                if msg['op']=='action':return journal.action(msg['action_id'])
                self.fail('Unexpected RPC '+msg['op'])
            body(False,'prt_fild08',795,1000)
            life=LifeCycle(root/'life.json',2000,60);life.state['phase']='hunt'
            hunt=life.tick(rpc,1000)
            body(True,'prt_fild08',0,1000)
            executor.body(dict(type='skill_result',action_id=hunt['action_id'],outcome='error',code='DIED'))
            executor.witness(dict(id=1,kind='die',char_id=150001,map='prt_fild08',x=150,y=150,event_ts=1000))
            self.assertEqual(life.tick(rpc,1000)['code'],'DIED')
            restart=LifeCycle(root/'life.json',2000,60)
            clock.return_value=1001
            respawn=restart.tick(rpc,1001)
            self.assertEqual(sent[-1]['skill'],'respawn')
            executor.body(dict(type='skill_result',action_id=respawn['action_id'],outcome='done',code='RESPAWNED'))
            clock.return_value=1002
            body(False,'prontera',300,1002)
            executor.evaluate()
            self.assertEqual(restart.tick(rpc,1002)['state'],'verifying','Body ACK was mistaken for world proof')
            self.assertFalse(restart.state['death_recovery']['recovered'])
            executor.witness(dict(id=2,kind='loadmap',char_id=150001,map='prontera',x=150,y=150,event_ts=1002))
            executor.evaluate()
            self.assertEqual(restart.tick(rpc,1002)['code'],'RESPAWNED')
            self.assertTrue(restart.state['death_recovery']['recovered'])
            self.assertEqual(restart.state['cycles'],0)
            again=LifeCycle(root/'life.json',2000,60)
            clock.return_value=1003
            again.tick(rpc,1003)
            self.assertEqual(sent[-1]['skill'],'travel_to')
            self.assertEqual(sent[-1]['params']['map'],'prt_in')
            self.assertEqual([m['skill'] for m in sent],['hunt','respawn','travel_to'])


if __name__=='__main__':unittest.main()
