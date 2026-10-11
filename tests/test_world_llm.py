"""Provider client against a local HTTP server: spend, gate, waits and recovery."""
import datetime,http.server,json,os,pathlib,tempfile,threading,unittest
from decimal import Decimal
from unittest.mock import patch
MIDNIGHT=datetime.datetime(2026,10,10,tzinfo=datetime.timezone.utc).timestamp()


class FakeProvider(http.server.ThreadingHTTPServer):
    """Scripted OpenAI-compatible endpoint; each request pops one (status, body, headers)."""
    def __init__(self):
        super().__init__(('127.0.0.1',0),Handler);self.script=[];self.requests=[]
        threading.Thread(target=self.serve_forever,daemon=True).start()
    @property
    def url(self):return f'http://127.0.0.1:{self.server_address[1]}/api/v1'
    def reply(self,status=200,body=None,headers=None):self.script.append((status,body,headers or {}))
    def ok(self,content,cost=None,tokens=(1000,100)):
        usage={'prompt_tokens':tokens[0],'completion_tokens':tokens[1]}
        if cost is not None:usage['cost']=cost
        self.reply(200,{'choices':[{'message':{'content':content}}],'usage':usage})


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append({'path':self.path,'auth':self.headers.get('Authorization'),'body':body})
        status,payload,headers=self.server.script.pop(0) if self.server.script else (500,{'error':'script empty'},{})
        data=json.dumps(payload).encode()
        self.send_response(status)
        for k,v in headers.items():self.send_header(k,v)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.dir=pathlib.Path(self.tmp.name)
        self.server=FakeProvider();self.now=[MIDNIGHT+8*3600.0]
        (self.dir/'.env').write_text(f'RO_WORLD_LLM_API_KEY=sk-test-123456789\nRO_WORLD_LLM_BASE_URL={self.server.url}\nRO_WORLD_LLM_MODEL=test/model\n')
        self.env=patch.dict(os.environ,{k:'' for k in ('RO_WORLD_LLM_API_KEY','LLM_API_KEY','OPENROUTER_API_KEY','OPENAI_API_KEY','DEEPSEEK_API_KEY','RO_WORLD_LLM_BASE_URL','RO_WORLD_LLM_MODEL','RO_WORLD_LLM_DAILY_USD')});self.env.start()
    def tearDown(self):
        self.env.stop();self.server.shutdown();self.server.server_close();self.tmp.cleanup()
    def client(self,**config):
        from packages.resident_world.llm import Client
        return Client(self.dir,self.dir/'state',config or {'daily_llm_usd':2},clock=lambda:self.now[0])
    def test_success_counts_reported_cost_and_sends_strict_schema(self):
        c=self.client();self.server.ok('{"action":"idle"}',cost=0.00042)
        reply=c.complete([{'role':'user','content':'привет'}],schema={'type':'object'},schema_name='decide')
        self.assertEqual(reply.content,'{"action":"idle"}');self.assertEqual(reply.mode,'json_schema')
        req=self.server.requests[0]
        self.assertEqual(req['auth'],'Bearer sk-test-123456789');self.assertTrue(req['path'].endswith('/chat/completions'))
        self.assertEqual(req['body']['response_format']['type'],'json_schema');self.assertTrue(req['body']['response_format']['json_schema']['strict'])
        self.assertEqual(c.ledger.today(self.now[0]),(Decimal('0.00042'),1))
    def test_cost_from_tokens_when_provider_does_not_report_it(self):
        c=self.client();self.server.ok('{}',tokens=(1_000_000,0))
        c.complete([{'role':'user','content':'x'}])
        self.assertEqual(c.ledger.today(self.now[0])[0],Decimal('1.0'))
    def test_unsupported_schema_degrades_to_json_object_and_is_remembered(self):
        c=self.client();self.server.reply(400,{'error':{'message':'response_format json_schema unsupported'}});self.server.ok('{"a":1}',cost=0.0001)
        self.assertEqual(c.complete([{'role':'user','content':'x'}],schema={'type':'object'}).mode,'json_object')
        self.server.ok('{"a":2}',cost=0.0001)
        c.complete([{'role':'user','content':'x'}],schema={'type':'object'})
        modes=[r['body'].get('response_format',{}).get('type') for r in self.server.requests]
        self.assertEqual(modes,['json_schema','json_object','json_object'])
    def test_rate_limit_waits_retry_after_then_resumes_by_itself(self):
        from packages.resident_world.llm import LLMUnavailable
        c=self.client();self.server.reply(429,{'error':{'message':'slow down'}},{'Retry-After':'40'})
        with self.assertRaises(LLMUnavailable) as err:c.complete([{'role':'user','content':'x'}])
        self.assertEqual(err.exception.reason,'RATE_LIMIT');self.assertEqual(err.exception.status,429)
        self.assertGreaterEqual(err.exception.until,self.now[0]+40);self.assertLessEqual(err.exception.until,self.now[0]+48.1)
        # While the gate is closed nobody calls the provider, other processes included.
        self.now[0]+=20
        with self.assertRaises(LLMUnavailable):self.client().complete([{'role':'user','content':'x'}])
        self.assertEqual(len(self.server.requests),1)
        self.now[0]+=30;self.server.ok('{}',cost=0.0001)
        self.client().complete([{'role':'user','content':'x'}])
        self.assertEqual(len(self.server.requests),2);self.assertEqual(c.gate.state(),{})
    def test_no_credits_auth_and_outage_have_their_own_waits(self):
        from packages.resident_world.llm import LLMUnavailable
        for status,reason,low,high in ((402,'NO_CREDITS',600,600),(401,'AUTH',600,600),(503,'OUTAGE',10,12)):
            with self.subTest(status=status):
                c=self.client();self.server.reply(status,{'error':{'message':'x'}})
                with self.assertRaises(LLMUnavailable) as err:c.complete([{'role':'user','content':'x'}])
                self.assertEqual(err.exception.reason,reason)
                self.assertTrue(low<=err.exception.until-self.now[0]<=high,err.exception.until-self.now[0])
                c.gate.open()
    def test_openrouter_style_error_inside_200_is_a_rate_limit(self):
        from packages.resident_world.llm import LLMUnavailable
        c=self.client();self.server.reply(200,{'error':{'code':429,'message':'upstream rate limited'}})
        with self.assertRaises(LLMUnavailable) as err:c.complete([{'role':'user','content':'x'}])
        self.assertEqual(err.exception.reason,'RATE_LIMIT')
    def test_unreachable_provider_is_an_outage_not_a_crash(self):
        from packages.resident_world.llm import LLMUnavailable
        (self.dir/'.env').write_text('RO_WORLD_LLM_API_KEY=sk-test-123456789\nRO_WORLD_LLM_BASE_URL=http://127.0.0.1:9/api/v1\n')
        with self.assertRaises(LLMUnavailable) as err:self.client().complete([{'role':'user','content':'x'}],timeout=2)
        self.assertEqual(err.exception.reason,'OUTAGE')
    def test_daily_limit_waits_for_next_utc_day_without_calling(self):
        from packages.resident_world.llm import LLMUnavailable
        c=self.client(daily_llm_usd=0.001)
        self.server.ok('{}',cost=0.002);c.complete([{'role':'user','content':'x'}])
        with self.assertRaises(LLMUnavailable) as err:c.complete([{'role':'user','content':'x'}])
        self.assertEqual(err.exception.reason,'DAILY_LIMIT');self.assertEqual(err.exception.until,MIDNIGHT+86400+30)
        self.assertEqual(len(self.server.requests),1)
        self.now[0]=MIDNIGHT+86400+31;self.server.ok('{}',cost=0.0001)
        c.complete([{'role':'user','content':'x'}]);self.assertEqual(len(self.server.requests),2)
    def test_inserting_the_token_works_without_restart(self):
        from packages.resident_world.llm import LLMUnavailable
        (self.dir/'.env').write_text(f'RO_WORLD_LLM_BASE_URL={self.server.url}\nRO_WORLD_LLM_API_KEY=\n')
        c=self.client()
        with self.assertRaises(LLMUnavailable) as err:c.complete([{'role':'user','content':'x'}])
        self.assertEqual(err.exception.reason,'NO_TOKEN');self.assertEqual(self.server.requests,[])
        self.assertEqual(c.gate.state(),{})  # not a provider fault: no shared pause
        (self.dir/'.env').write_text(f'RO_WORLD_LLM_BASE_URL={self.server.url}\nRO_WORLD_LLM_API_KEY="sk-new-token-0000"\n')
        self.server.ok('{}',cost=0.0001);c.complete([{'role':'user','content':'x'}])
        self.assertEqual(self.server.requests[0]['auth'],'Bearer sk-new-token-0000')
    def test_environment_wins_and_alias_names_work(self):
        from packages.resident_world.llm import load_settings
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'sk-or-from-env-999','RO_WORLD_LLM_API_KEY':''}):
            (self.dir/'.env').write_text('')
            s=load_settings(self.dir,{'daily_llm_usd':5})
        self.assertEqual(s.key,'sk-or-from-env-999');self.assertEqual(s.base_url,'https://openrouter.ai/api/v1')
        self.assertEqual(s.model,'deepseek/deepseek-v3.2');self.assertEqual(s.daily_usd,Decimal(5));self.assertNotIn('999',s.masked()[:6])
    def test_slow_short_call_does_not_pause_everyone(self):
        # A chat reply with a 2–8 s deadline timing out is not a provider outage.
        from packages.resident_world.llm import LLMUnavailable
        import socket
        def slow(req,timeout):raise socket.timeout('timed out')
        c=self.client();c.urlopen=slow
        with self.assertRaises(LLMUnavailable) as err:c.complete([{'role':'user','content':'x'*300}],timeout=4,shared_pause=False)
        self.assertEqual(err.exception.reason,'TIMEOUT');self.assertEqual(c.gate.state(),{})
        self.assertGreater(c.ledger.today(self.now[0])[0],0)  # an abandoned prompt may still be billed
        with self.assertRaises(LLMUnavailable) as err:c.complete([{'role':'user','content':'x'}],timeout=4)  # a brain's call
        self.assertEqual(err.exception.reason,'OUTAGE');self.assertEqual(c.gate.state()['reason'],'OUTAGE')
    def test_gate_is_not_shortened_or_cleared_by_older_news(self):
        from packages.resident_world.llm import Gate
        g=Gate(self.dir/'gate.json',rng=lambda:0)
        long=g.close('RATE_LIMIT',1000,429,retry_after=600)
        self.assertEqual(g.close('OUTAGE',1001),long)  # a parallel timeout does not cut a 429 pause
        g.open(since=999);self.assertEqual(g.state()['until'],long)  # success of a call started before the 429
        g.open(since=1002);self.assertEqual(g.state(),{})
        g.close('RATE_LIMIT',2000,429,retry_after=300);self.assertEqual(g.close('RATE_LIMIT',2010,429,retry_after=10),2300)
    def test_retry_after_http_date(self):
        from packages.resident_world.llm import _retry_after
        import email.utils
        self.assertAlmostEqual(_retry_after({'Retry-After':email.utils.formatdate(1000+90,usegmt=True)},1000),90,delta=1)
        self.assertEqual(_retry_after({'Retry-After':'7'}),7);self.assertIsNone(_retry_after({'Retry-After':'soon'}))
    def test_pasted_token_with_comment_or_invisible_chars(self):
        from packages.resident_world.llm import load_settings,LLMUnavailable
        for line,key in (('RO_WORLD_LLM_API_KEY=sk-or-v1-abc  # ключ владельца','sk-or-v1-abc'),('RO_WORLD_LLM_API_KEY=sk-or-v1-abc​','sk-or-v1-abc'),('﻿RO_WORLD_LLM_API_KEY="sk-or-v1-abc"','sk-or-v1-abc')):
            with self.subTest(line=line):
                (self.dir/'.env').write_text(line+'\n',encoding='utf-8');self.assertEqual(load_settings(self.dir).key,key)
        (self.dir/'.env').write_text('RO_WORLD_LLM_API_KEY=ключ\n',encoding='utf-8')
        with self.assertRaises(LLMUnavailable) as err:load_settings(self.dir)
        self.assertIn('лишние символы',err.exception.detail)
    def test_broken_bodies_are_outages_not_crashes(self):
        from packages.resident_world.llm import LLMUnavailable
        import http.client
        for body in ([],None,'hi'):
            with self.subTest(body=body):
                c=self.client();self.server.reply(200,body)
                with self.assertRaises(LLMUnavailable) as err:c.complete([{'role':'user','content':'x'}])
                self.assertEqual(err.exception.reason,'OUTAGE');c.gate.open()
        def cut(req,timeout):raise http.client.IncompleteRead(b'{"cho')
        c=self.client();c.urlopen=cut
        with self.assertRaises(LLMUnavailable) as err:c.complete([{'role':'user','content':'x'}])
        self.assertEqual(err.exception.reason,'OUTAGE')
    def test_mandatory_reasoning_keeps_the_strict_schema(self):
        from packages.resident_world.llm import Settings
        c=self.client();c.fixed=Settings(key='sk-test-123456789',base_url=self.server.url.replace('127.0.0.1','127.0.0.1')+'?openrouter.ai')
        self.server.reply(400,{'error':{'message':'Reasoning is mandatory for this endpoint and cannot be disabled.'}});self.server.ok('{}',cost=0.0001)
        self.assertEqual(c.complete([{'role':'user','content':'x'}],schema={'type':'object'}).mode,'json_schema')
        bodies=[r['body'] for r in self.server.requests]
        self.assertIn('reasoning',bodies[0]);self.assertNotIn('reasoning',bodies[1]);self.assertEqual(bodies[1]['response_format']['type'],'json_schema')
    def test_dotenv_quotes_comments_and_fallback_names(self):
        from packages.resident_world.llm import load_settings
        for line,key in (('RO_WORLD_LLM_API_KEY="sk-or-v1-abc123"  # мой ключ','sk-or-v1-abc123'),("RO_WORLD_LLM_API_KEY='sk-or-v1-abc123' # c",'sk-or-v1-abc123'),('export RO_WORLD_LLM_API_KEY=sk-or-v1-abc123','sk-or-v1-abc123')):
            with self.subTest(line=line):
                (self.dir/'.env').write_text(line+'\n');self.assertEqual(load_settings(self.dir).key,key)
        (self.dir/'.env').write_text('RO_WORLD_LLM_API_KEY=   # вставлю позже\n')
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'sk-or-v1-real'}):self.assertEqual(load_settings(self.dir).key,'sk-or-v1-real')
    def test_repeating_rate_limit_escalates_even_with_retry_after(self):
        from packages.resident_world.llm import Gate
        g=Gate(self.dir/'g.json',rng=lambda:0);now=1000.0;waits=[]
        for _ in range(4):
            until=g.close('RATE_LIMIT',now,429,retry_after=30);waits.append(until-now);now=until+1
        self.assertEqual(waits[0],30);self.assertGreater(waits[-1],60)  # a persistent limit is not a short hiccup
    def test_unreached_provider_is_not_charged(self):
        from packages.resident_world.llm import LLMUnavailable
        import socket,urllib.error
        def blackhole(req,timeout):raise urllib.error.URLError(socket.timeout('timed out'))
        c=self.client();c.urlopen=blackhole
        with self.assertRaises(LLMUnavailable):c.complete([{'role':'user','content':'x'*300}],timeout=4,shared_pause=False)
        self.assertEqual(c.ledger.today(self.now[0])[0],0)
    def test_overall_deadline_for_a_chat_reply(self):
        from packages.resident_world.llm import LLMUnavailable
        import time as _t
        def slow(req,timeout):_t.sleep(2);raise OSError('late')
        c=self.client();c.urlopen=slow;c.clock=_t.time;started=_t.monotonic()
        with self.assertRaises(LLMUnavailable) as err:c.complete([{'role':'user','content':'x'}],timeout=8,shared_pause=False,deadline=_t.time()+.5)
        self.assertEqual(err.exception.reason,'TIMEOUT');self.assertLess(_t.monotonic()-started,1.5)
    def test_lenient_json_extraction(self):
        from packages.resident_world.llm import parse_json_object,BadModelOutput
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'),{'a':1})
        self.assertEqual(parse_json_object('Вот ответ: {"a": {"b": 2}} — готово'),{'a':{'b':2}})
        for bad in ('',None,'[1,2]','без json','{"a":'):
            with self.subTest(bad=bad),self.assertRaises(BadModelOutput):parse_json_object(bad)


class DoctorTests(unittest.TestCase):
    """`ro doctor`: one cheap real call with a plain Russian verdict."""
    def run_doctor(self,env_text,script):
        import contextlib,io
        from packages.resident_world import cli,llm
        server=FakeProvider();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        for step in script:server.reply(*step) if isinstance(step,tuple) else server.ok(step,cost=0.00001)
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);(root/'.env').write_text(env_text.replace('{URL}',server.url))
            out=io.StringIO()
            with patch.object(cli,'ROOT',root),patch.object(cli,'STATE',root/'state'),patch.object(cli,'config',return_value={'daily_llm_usd':2}),\
                 patch.dict(os.environ,{**{k:'' for k in llm.KEY_NAMES},'RO_WORLD_LLM_BASE_URL':'','RO_WORLD_LLM_MODEL':'','RO_WORLD_LLM_DAILY_USD':''}),contextlib.redirect_stdout(out):
                code=cli.doctor()
        return code,out.getvalue(),server
    def test_ok(self):
        code,out,server=self.run_doctor('RO_WORLD_LLM_API_KEY=sk-doctor-0000000\nRO_WORLD_LLM_BASE_URL={URL}\n',['{"ok": true, "say": "Привет"}'])
        self.assertEqual(code,0,out);self.assertIn('OK:',out);self.assertIn('Привет',out);self.assertNotIn('sk-doctor-0000000',out)
    def test_missing_token_and_rejected_token_are_explained(self):
        code,out,server=self.run_doctor('RO_WORLD_LLM_BASE_URL={URL}\n',[])
        self.assertEqual(code,1);self.assertIn('впиши RO_WORLD_LLM_API_KEY',out);self.assertEqual(server.requests,[])
        code,out,_=self.run_doctor('RO_WORLD_LLM_API_KEY=sk-bad-000000000\nRO_WORLD_LLM_BASE_URL={URL}\n',[(401,{'error':{'message':'bad key'}})])
        self.assertEqual(code,2);self.assertIn('не принял токен',out)
        code,out,_=self.run_doctor('RO_WORLD_LLM_API_KEY=sk-poor-00000000\nRO_WORLD_LLM_BASE_URL={URL}\n',[(402,{'error':{'message':'no credits'}})])
        self.assertIn('нет денег',out)

if __name__=='__main__':unittest.main()
