"""End to end: a player speaks near a resident; the LLM answers; the server hears it.

Real chat service (ingest → responder → Mind.respond → skills) and real gateway;
the body speaks the plugin protocol and the (fake) server chatlog proves delivery.
"""
import contextlib,json,os,pathlib,subprocess,sys,tempfile,threading,time,unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
R={'resident_id':'resident_a','name':'ResidentA','char_id':150001,'profile':'/nonexistent','enabled':True,'persona':'Мечник. Прямой, смелый, любит охоту.'}
C={'daily_llm_usd':2,'llm':{'enabled':True,'max_hunt_minutes':15},'chat_service':'standalone','chat_radius':14,'residents':[R],
   'places':{'fountain':{'map':'prontera','x':156,'y':191},'field':{'map':'prt_fild08','x':170,'y':240}}}


def reply(action,**kw):
    d={k:None for k in ('text','to','place','map','minutes','until_hp','player','resident','say','remember','plan')}
    d.update(thought='Отвечу по-человечески',action=action,reason='Ответ',**kw);return d


@unittest.skipUnless(sys.platform.startswith('linux'),'Unix sockets and SO_PEERCRED')
class ChatEndToEnd(unittest.TestCase):
    def test_player_hears_a_model_reply_aloud_and_in_a_whisper(self):
        from tests.world_sim import FakeWorld,FakeBody,FakeLLM
        from packages.resident_world import supervisor,mind,skills,memory,chat,meeting
        from packages.body_gateway import cli
        with tempfile.TemporaryDirectory(dir='/tmp') as tmp,contextlib.ExitStack() as stack:
            state=pathlib.Path(tmp)/'s';world_dir=pathlib.Path(tmp)/'w';d=state/R['resident_id'];d.mkdir(parents=True)
            world=FakeWorld(world_dir,R['char_id'],R['name'])
            llm=FakeLLM([reply('say',text='Привет! Охота идёт отлично, порингов полно.'),reply('whisper',to='Tester',text='Тише: у южных ворот спокойнее.')])
            stack.callback(llm.server_close);stack.callback(llm.shutdown)
            gateway=subprocess.Popen([sys.executable,'-m','tests.world_sim','gateway','--world',str(world_dir),'--socket',str(d/'actions.sock'),
                                      '--store',str(d/'journal.sqlite'),'--resident',R['resident_id'],'--char-id',str(R['char_id']),'--char-name',R['name']],
                                     cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            stack.callback(lambda:(gateway.terminate(),gateway.wait(10)))
            for _ in range(200):
                if (d/'actions.sock.control').exists():break
                time.sleep(.05)
            body=FakeBody(str(d/'actions.sock'),world,R['resident_id'],R['char_id']);body.start();stack.callback(body.stop.set)
            for module in (supervisor,mind,skills,memory,chat,meeting):
                if hasattr(module,'STATE'):stack.enter_context(patch.object(module,'STATE',state))
                if hasattr(module,'config'):stack.enter_context(patch.object(module,'config',return_value=C))
                if hasattr(module,'request'):stack.enter_context(patch.object(module,'request',cli.request))
            stack.enter_context(patch('tools.events.query',world.chatlog_query([R])))
            stack.enter_context(patch.dict(os.environ,{'RO_WORLD_LLM_API_KEY':'sk-e2e-chat','RO_WORLD_LLM_BASE_URL':llm.url,'RO_WORLD_LLM_MODEL':'test/brain'}))
            for _ in range(100):  # resident is online once the gateway has seen login + a fresh position
                try:
                    if supervisor.online(cli.request(str(d/'actions.sock.control'),{'op':'status'}),time.time()):break
                except (OSError,RuntimeError,ValueError):pass
                time.sleep(.1)
            listener=chat.start_listener(R)
            # Stop and join before patches/tmp go away: an orphan would reach the real chatlog.
            stack.callback(lambda:((state/'STOP').touch(),listener.join(10)))
            def resident_lines(kind):return [r for r in world.rows('chat') if r['char_id']==R['char_id'] and r['type']==kind]
            time.sleep(1)
            world.say_as('Tester',150000,'Привет, ResidentA! Как охота?','prontera',152,190)
            deadline=time.monotonic()+30
            while time.monotonic()<deadline and not resident_lines('O'):time.sleep(.2)
            self.assertEqual([r['text'] for r in resident_lines('O')],['Привет! Охота идёт отлично, порингов полно.'])
            world.say_as('Tester',150000,'Подскажи по секрету, где спокойно?','prontera',152,190,kind='W',to='ResidentA')
            deadline=time.monotonic()+30
            while time.monotonic()<deadline and not resident_lines('W'):time.sleep(.2)
            self.assertEqual([(r['to'],r['text']) for r in resident_lines('W')],[('Tester','Тише: у южных ворот спокойнее.')])
            # The model heard the player's actual words; delivery is the server's chatlog, not an ACK.
            heard=[json.loads(r['body']['messages'][1]['content'])['incoming']['text'] for r in llm.requests]
            self.assertEqual(heard,['Привет, ResidentA! Как охота?','Подскажи по секрету, где спокойно?'])
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                log=[json.loads(l) for l in (state/'decisions.jsonl').read_text().splitlines()]
                replies=[r for r in log if r['kind']=='CHAT_REPLY']
                if len(replies)==2:break
                time.sleep(.2)
            self.assertEqual([r['outcome'] for r in replies],['done','done'])
            self.assertTrue(all(r['seconds']<10 for r in replies),replies)


if __name__=='__main__':unittest.main()
