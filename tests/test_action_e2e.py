"""Exercise callable model→skills→real Unix gateway→simulated witnesses, offline."""
import contextlib,json,os,pathlib,subprocess,sys,tempfile,time,unittest
from unittest.mock import patch
from tests.world_sim import FakeWorld,FakeBody,FakeLLM
from tests.test_world_brain_e2e import C,R,ROOT
from packages.resident_world import skills,mind,supervisor
from packages.body_gateway.cli import request

@unittest.skipUnless(sys.platform.startswith('linux'),'Unix gateway requires Linux')
class CallableEndToEnd(unittest.TestCase):
    def test_model_native_call_is_confirmed_replay_safe_and_discoverable(self):
        from packages.resident_world.llm import Client
        proposal={'action':'native_control','skill':'travel_to','params':{'map':'prontera','x':156,'y':180,'r':3},'reason':'Прогуляться','thought':'К рынку'}
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            state=pathlib.Path(tmp)/'state';d=state/R['resident_id'];d.mkdir(parents=True)
            world_dir=pathlib.Path(tmp)/'world';world=FakeWorld(world_dir,R['char_id'],R['name'])
            llm=FakeLLM([proposal]);stack.callback(llm.server_close);stack.callback(llm.shutdown)
            gateway=subprocess.Popen([sys.executable,'-m','tests.world_sim','gateway','--world',str(world_dir),'--socket',str(d/'actions.sock'),'--store',str(d/'journal.sqlite'),'--resident',R['resident_id'],'--char-id',str(R['char_id']),'--char-name',R['name']],cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            def stop_gateway():
                gateway.terminate();gateway.wait(10);gateway.stderr.close()
            stack.callback(stop_gateway)
            deadline=time.monotonic()+10
            while not (d/'actions.sock.control').exists() and time.monotonic()<deadline:
                self.assertIsNone(gateway.poll());time.sleep(.05)
            self.assertTrue((d/'actions.sock.control').exists())
            body=FakeBody(str(d/'actions.sock'),world,R['resident_id'],R['char_id']);body.start();stack.callback(body.stop.set)
            for module in (skills,mind,supervisor):
                if hasattr(module,'STATE'):stack.enter_context(patch.object(module,'STATE',state))
                if hasattr(module,'config'):stack.enter_context(patch.object(module,'config',return_value=C))
            stack.enter_context(patch.dict(os.environ,{'RO_WORLD_LLM_API_KEY':'offline-test-only','RO_WORLD_LLM_BASE_URL':llm.url,'RO_WORLD_LLM_MODEL':'test/callable'}))
            deadline=time.monotonic()+10;s={}
            while time.monotonic()<deadline:
                s=request(str(d/'actions.sock.control'),{'op':'status'})
                if s.get('telemetry') and s.get('projection',{}).get('server_pos'):break
                time.sleep(.05)
            brain=mind.Mind(R,d,C,client=Client(ROOT,state,C));chosen=brain.choose(s)
            self.assertIsNotNone(chosen)
            decision=chosen['decision'];self.assertEqual(decision['action'],'native_control')
            result=skills.execute(R['name'],decision['action'],decision['args'],task_id=chosen['id'],origin='brain')
            self.assertEqual(result['outcome'],'done',result)
            self.assertEqual(skills.action_status(R['name'],chosen['id'])['state'],'done')
            native=request(str(d/'actions.sock.control'),{'op':'action','action_id':result['native_action']})
            self.assertEqual(native['state'],'confirmed')
            self.assertTrue(world.rows('witness'))
            before=len([c for c in body.commands if c.get('type')=='skill_start'])
            self.assertEqual(skills.execute(R['name'],decision['action'],decision['args'],task_id=chosen['id']),result)
            self.assertEqual(len([c for c in body.commands if c.get('type')=='skill_start']),before)
            sent=llm.requests[0]['body'];self.assertIn('native_control',sent['response_format']['json_schema']['schema']['properties']['action']['enum'])
            self.assertIn('params',sent['response_format']['json_schema']['schema']['required'])
            self.assertIn('native_control',json.loads(sent['messages'][1]['content'])['can_now'])
