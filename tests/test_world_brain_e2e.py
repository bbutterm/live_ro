"""End to end: real brain loop + real gateway + plugin-protocol body + scripted LLM.

The model says where to go and what to say; the body does it; the gateway only
confirms what the (fake) server witnessed. A 429 in the middle must not stop
the resident: native habits keep it busy and the model resumes by itself.
"""
import contextlib,json,os,pathlib,subprocess,sys,tempfile,threading,time,unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
R={'resident_id':'resident_a','name':'ResidentA','char_id':150001,'profile':'/nonexistent','enabled':True,'persona':'Мечник. Прямой, смелый, любит охоту.'}
# short_pause_seconds=2: the ~5 s 429 pause below counts as long, so native habits must take over.
C={'daily_llm_usd':2,'llm':{'enabled':True,'max_hunt_minutes':15,'min_decision_seconds':0,'min_speech_seconds':0,'timeout_seconds':10,'short_pause_seconds':2},
   'brain_settle_seconds':.5,'chat_service':'standalone','residents':[R],
   # No 'fountain': the city-walk fallback (5 min idle) would make this test slow.
   'places':{'market':{'map':'prontera','x':156,'y':180},'gate':{'map':'prontera','x':150,'y':40},'field':{'map':'prt_fild08','x':170,'y':240}},
   'place_labels':{'market':'у торговых рядов','gate':'у южных ворот','field':'на поле'}}


def step(action,**kw):
    d={k:None for k in ('text','to','place','map','minutes','until_hp','player','resident','say','remember','plan')}
    d.update(thought='Думаю, что делать',action=action,reason='Сам решил',**kw);return d


@unittest.skipUnless(sys.platform.startswith('linux'),'Unix sockets and SO_PEERCRED')
class BrainEndToEnd(unittest.TestCase):
    def test_model_drives_body_and_survives_a_rate_limit(self):
        from tests.world_sim import FakeWorld,FakeBody,FakeLLM
        from packages.resident_world import supervisor,mind,skills,memory,chat,meeting
        script=[step('walk_to',place='market',say='Пойду к торговым рядам.',remember='Хочу купить меч',plan='Посмотреть товары'),
                step('say',text='Всем доброго вечера!'),
                step('hunt',minutes=5,say='Иду на охоту.'),
                (429,{'error':{'message':'rate limited upstream'}},{'Retry-After':'1'}),
                step('town_service'),
                step('walk_to',place='gate')]
        with tempfile.TemporaryDirectory(dir='/tmp') as tmp,contextlib.ExitStack() as stack:
            state=pathlib.Path(tmp)/'s';world_dir=pathlib.Path(tmp)/'w';d=state/R['resident_id'];d.mkdir(parents=True)
            world=FakeWorld(world_dir,R['char_id'],R['name']);llm=FakeLLM(script);stack.callback(llm.server_close);stack.callback(llm.shutdown)
            gateway=subprocess.Popen([sys.executable,'-m','tests.world_sim','gateway','--world',str(world_dir),'--socket',str(d/'actions.sock'),
                                      '--store',str(d/'journal.sqlite'),'--resident',R['resident_id'],'--char-id',str(R['char_id']),'--char-name',R['name']],
                                     cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            stack.callback(lambda:(gateway.terminate(),gateway.wait(10),gateway.stderr.close()))
            for _ in range(200):
                if (d/'actions.sock.control').exists():break
                time.sleep(.05)
            body=FakeBody(str(d/'actions.sock'),world,R['resident_id'],R['char_id']);body.start();stack.callback(body.stop.set)
            from packages.body_gateway import cli
            for module in (supervisor,mind,skills,memory,chat,meeting):
                if hasattr(module,'STATE'):stack.enter_context(patch.object(module,'STATE',state))
                if hasattr(module,'config'):stack.enter_context(patch.object(module,'config',return_value=C))
                if hasattr(module,'request'):stack.enter_context(patch.object(module,'request',cli.request))
            stack.enter_context(patch.dict(os.environ,{'RO_WORLD_LLM_API_KEY':'sk-e2e-test-token','RO_WORLD_LLM_BASE_URL':llm.url,'RO_WORLD_LLM_MODEL':'test/brain'}))
            brain=threading.Thread(target=supervisor.brain,args=(R,),daemon=True);brain.start()
            def decisions():
                try:return [json.loads(l) for l in (state/'decisions.jsonl').read_text().splitlines()]
                except OSError:return []
            deadline=time.monotonic()+150
            while time.monotonic()<deadline and not ((body.map,body.x,body.y)==('prontera',150,40) and not body.active):
                self.assertIsNone(gateway.poll(),gateway.stderr.read().decode() if gateway.poll() is not None else '')
                time.sleep(.2)
            (state/'STOP').touch();brain.join(30)
            log=decisions();kinds=[r['kind'] for r in log]
            gate=(state/'llm-gate.json').read_text() if (state/'llm-gate.json').exists() else ''
            self.assertEqual((body.map,body.x,body.y),('prontera',150,40),{'log':[(r['kind'],r.get('reason'),r.get('resume_at')) for r in log if r['kind']!='MODEL_CALL'][:12],'llm_requests':len(llm.requests),'gate':gate,'now':time.time(),'body':[(c.get('type'),c.get('skill'),c.get('params')) for c in body.commands][:8],'task':(d/'manual_task.json').read_text()[:600] if (d/'manual_task.json').exists() else ''})
            # Every model decision became a server-confirmed native outcome.
            chosen=[r['decision']['action'] for r in log if r['kind']=='MODEL_DECISION']
            self.assertEqual(chosen,['walk_to','say','hunt','town_service','walk_to'])
            outcomes=[r['outcome'] for r in log if r['kind']=='GOAL_OUTCOME']
            self.assertEqual(outcomes[:5],['done']*5,log)
            # The 429 paused the brain; native habits ran meanwhile; then the model was asked again.
            unavailable=[r for r in log if r['kind']=='MODEL_UNAVAILABLE']
            self.assertEqual(unavailable[0]['reason'],'RATE_LIMIT');self.assertEqual(unavailable[0]['status'],429)
            pause_at=unavailable[0]['ts'];resume_at=next(r['ts'] for r in log if r['kind']=='MODEL_DECISION' and r['decision']['action']=='town_service')
            self.assertGreaterEqual(resume_at-pause_at,5)
            native=[c for c in body.commands if c.get('type')=='skill_start']
            self.assertGreaterEqual(sum(c['skill']=='hunt' for c in native),2,'no native fallback hunt ran during the pause')
            # What the server saw.
            spoken=[r['text'] for r in world.rows('chat')]
            self.assertIn('Пойду к торговым рядам.',spoken);self.assertIn('Всем доброго вечера!',spoken);self.assertIn('Иду на охоту.',spoken)
            self.assertTrue(any(r['kind']=='kill' for r in world.rows('witness')))
            picks=world.rows('picklog')
            self.assertTrue(any(r['amount']<0 for r in picks),'nothing sold');self.assertTrue(any(r['item_id']==501 and r['amount']>0 for r in picks),'no potions bought')
            # Honest money: exactly the provider-reported cost of each answered call, nothing reserved.
            spend=json.loads((state/'llm-spend.json').read_text())['days']
            day=next(iter(spend.values()))
            self.assertEqual(day['calls'],5);self.assertAlmostEqual(float(day['usd']),5*0.0005)
            self.assertEqual(llm.requests[0]['auth'],'Bearer sk-e2e-test-token')
            self.assertEqual(llm.requests[0]['body']['response_format']['type'],'json_schema')
            context=json.loads(llm.requests[1]['body']['messages'][1]['content'])
            self.assertEqual(context['мой_план'],'Посмотреть товары');self.assertIn('Хочу купить меч',context['мой_дневник'])
            self.assertEqual(context['последние_дела'][-1]['итог'],'получилось')


if __name__=='__main__':unittest.main()
