"""ro accept / ro doctor world: verdicts from witness, chat inbox, decisions and spend."""
import json,os,pathlib,sqlite3,tempfile,time,unittest
from unittest.mock import patch

C={'daily_llm_usd':2,'residents':[{'name':n,'resident_id':'r'+str(i),'char_id':150001+i,'enabled':True} for i,n in enumerate(('ResidentA','Mira','Borin'))]}


class AcceptTests(unittest.TestCase):
    def setUp(self):
        from packages.resident_world import proof
        self.proof=proof;self.tmp=tempfile.TemporaryDirectory();self.state=pathlib.Path(self.tmp.name);self.now=1_800_000_000.0
        self.stack=[patch.object(proof,'STATE',self.state),patch.object(proof,'config',return_value=C),patch.object(proof,'code_version',return_value='abc1234')]
        for p in self.stack:p.start()
    def tearDown(self):
        for p in self.stack:p.stop()
        self.tmp.cleanup()
    def witness(self,gap_from=None):
        start=int((self.now-6*3600)//60);end=int(self.now//60)
        rows=[f'{150001+i}\t{m}' for i in range(3) for m in range(start,end+1) if not (gap_from and gap_from<=m<gap_from+15 and i==1)]
        return lambda sql:'\n'.join(rows)+'\n'
    def decide(self,resident,action,ts,done=True):
        did=f'{resident}-{action}-{ts}'
        lines=[{'ts':ts,'kind':'MODEL_DECISION','resident':resident,'decision_id':did,'decision':{'action':action,'args':{},'reason':'x'}}]
        if done:lines.append({'ts':ts+60,'kind':'GOAL_OUTCOME','resident':resident,'task_id':did,'outcome':'done','action':action})
        with (self.state/'decisions.jsonl').open('a') as f:f.write(''.join(json.dumps(l,ensure_ascii=False)+'\n' for l in lines))
    def chat(self,rows):
        db=sqlite3.connect(self.state/'chat.sqlite')
        db.execute('CREATE TABLE inbox(id INTEGER PRIMARY KEY,payload TEXT,resident TEXT,status TEXT,claimed REAL,result TEXT)')
        for i,(char_id,ts,outcome,ended) in enumerate(rows,1):
            db.execute('INSERT INTO inbox VALUES(?,?,?,?,?,?)',(i,json.dumps({'char_id':char_id,'ts':ts,'name':'Vanya'}),'Mira','finished',ts,json.dumps({'outcome':outcome,'ended':ended})))
        db.commit();db.close()
    def test_a_good_six_hours_pass_every_criterion(self):
        for who in ('ResidentA','Mira','Borin'):
            for k,action in enumerate(('hunt','town_service','walk_to','say')):self.decide(who,action,self.now-5*3600+k*600)
        self.chat([(9,self.now-3600,'done',self.now-3600+4),(9,self.now-1800,'done',self.now-1800+7)])
        (self.state/'llm-spend.json').write_text(json.dumps({'days':{time.strftime('%Y-%m-%d',time.gmtime(self.now)):{'usd':'0.80','calls':300}}}))
        with patch.object(self.proof,'_query',side_effect=self.witness()):
            passed,report=self.proof.accept(6*3600,now=self.now)
        crit=report['criteria']
        self.assertTrue(crit['uptime']['result'] and crit['occupations']['result'] and crit['chat']['result'] and crit['spend']['result'],crit)
        self.assertIsNone(crit['follow']['result']);self.assertFalse(passed);self.assertEqual(report['verdict'],'НЕТ ДАННЫХ')  # nobody asked to follow
    def test_failures_are_named(self):
        for who in ('ResidentA','Mira','Borin'):
            self.decide(who,'hunt',self.now-3600);self.decide(who,'walk_to',self.now-3000,done=False);self.decide(who,'say',self.now-2000)
        self.chat([(9,self.now-600,'done',self.now-600+14),(9,self.now-300,'silent(NO_REPLY)',None),(150002,self.now-200,'done',self.now-199)])
        (self.state/'llm-spend.json').write_text(json.dumps({'days':{time.strftime('%Y-%m-%d',time.gmtime(self.now)):{'usd':'2.40'}}}))
        with patch.object(self.proof,'_query',side_effect=self.witness(gap_from=int(self.now//60)-100)):
            passed,report=self.proof.accept(6*3600,now=self.now)
        crit=report['criteria']
        self.assertFalse(passed);self.assertEqual(report['verdict'],'FAIL')
        self.assertIn('Mira',crit['uptime']['text'])                       # a 15-minute hole in her witness positions
        self.assertFalse(crit['occupations']['result'])                     # only 2 done occupations each
        self.assertEqual((crit['chat']['data']['late'],crit['chat']['data']['silent'],crit['chat']['data']['messages']),(1,1,2))  # resident lines excluded
        self.assertFalse(crit['spend']['result'])
    def test_a_window_shorter_than_6h_does_not_fail_occupations(self):
        self.decide('ResidentA','hunt',self.now-1500);self.decide('ResidentA','town_service',self.now-900)
        for who in ('Mira','Borin'):
            for k,action in enumerate(('hunt','town_service','walk_to')):self.decide(who,action,self.now-1500+k*300)
        with patch.object(self.proof,'_query',side_effect=self.witness()):
            _,report=self.proof.accept(1800,now=self.now)
        occ=report['criteria']['occupations']
        self.assertIsNone(occ['result']);self.assertIn('ResidentA 2',occ['text'])  # 2 occupations in 30 min is not a 6-hour verdict
        for who in ('ResidentA',):self.decide(who,'rest',self.now-300)
        with patch.object(self.proof,'_query',side_effect=self.witness()):
            _,report=self.proof.accept(1800,now=self.now)
        self.assertTrue(report['criteria']['occupations']['result'])        # 3 already reached: a short window may pass
    def test_a_9h_window_is_one_segment_not_two_short_ones(self):
        for who in ('ResidentA','Mira','Borin'):
            for k,action in enumerate(('hunt','town_service','walk_to')):self.decide(who,action,self.now-8*3600+k*3*3600)
        with patch.object(self.proof,'_query',side_effect=self.witness()):
            _,report=self.proof.accept(9*3600,now=self.now)
        self.assertTrue(report['criteria']['occupations']['result'],report['criteria']['occupations'])
    def test_lines_the_server_could_not_log_are_counted_from_the_map_server_log(self):
        # A Windows client in cp1251: MariaDB refuses the line, so no resident can ever hear it.
        log=self.state/'map-server.log'
        log.write_text("[Info]: 'Owner' logged in.\n[SQL]: DB error - Incorrect string value: '\\xCF\\xF0...' for column `ro_residents_logs`.`chatlog`.`message` at row 1\n"*2)
        with patch.object(self.proof,'_systemd',return_value=False):
            self.assertEqual(self.proof.rejected_chat(dev_log=str(log)),2)
            self.assertIsNone(self.proof.rejected_chat(dev_log=str(self.state/'no-such.log')))  # unknown is not «0 lost»
    def test_chat_report_says_how_many_answers_came_from_the_model(self):
        db=sqlite3.connect(self.state/'chat.sqlite')
        db.execute('CREATE TABLE inbox(id INTEGER PRIMARY KEY,payload TEXT,resident TEXT,status TEXT,claimed REAL,result TEXT)')
        rows=[(1,{'char_id':9,'ts':self.now-60,'name':'Owner','text':'привет','type':'W','to':'Mira'},'Mira','finished',{'outcome':'done','ended':self.now-58,'by':'LLM','decision':{'action':'whisper','args':{'to':'Owner','text':'Привет! Я тут.'}}}),
              (2,{'char_id':9,'ts':self.now-30,'name':'Owner','text':'иди ко мне','type':'W','to':'Mira'},'Mira','finished',{'outcome':'done','ended':self.now-27,'by':'правила','decision':{'action':'follow','args':{'player':'Owner','minutes':3},'say':'Иду к тебе.'}}),
              (3,{'char_id':9,'ts':self.now-10,'name':'Owner','text':'mira?','type':'W','to':'mira'},None,'ignored',None)]
        for i,e,r,st,res in rows:db.execute('INSERT INTO inbox VALUES(?,?,?,?,NULL,?)',(i,json.dumps(e,ensure_ascii=False),r,st,json.dumps(res,ensure_ascii=False) if res else None))
        db.commit();db.close()
        stats=self.proof._chat(C['residents'],self.now-3600,self.now)
        self.assertEqual((stats['messages'],stats['answered'],stats['by_llm']),(2,2,1))
        from packages.resident_world import cli
        with patch.object(cli,'STATE',self.state),patch.object(cli,'config',return_value=C):lines=cli.chat_lines(10)
        self.assertEqual(len(lines),6);self.assertIn('Owner → Mira (шёпот): привет',lines[0]);self.assertIn('LLM: Привет! Я тут.',lines[1])
        self.assertIn('Иду к тебе. [follow]',lines[3]);self.assertIn('регистр важен',lines[5])
    def test_window_parsing(self):
        self.assertEqual([self.proof.parse_window(x) for x in ('30m','6h','24h','1d','2')],[1800,21600,86400,86400,7200])
        with self.assertRaises(ValueError):self.proof.parse_window('вчера')


if __name__=='__main__':unittest.main()


class BackupTests(unittest.TestCase):
    def test_daily_archive_holds_db_and_memory_and_keeps_7_daily_plus_4_weekly(self):
        import tarfile,datetime
        from packages.resident_world import backup
        with tempfile.TemporaryDirectory() as tmp:
            state=pathlib.Path(tmp)/'state';(state/'resident_a').mkdir(parents=True);out=pathlib.Path(tmp)/'b'
            (state/'resident_a'/'diary.json').write_text('[{"text":"встретила Тестера"}]');(state/'resident_a'/'journal.sqlite').write_text('big')
            (state/'world.local.json').write_text('{}')
            start=datetime.datetime(2026,9,1,3,0,tzinfo=datetime.timezone.utc).timestamp()
            for day in range(40):
                p=backup.make(state,out,now=start+day*86400,dump=lambda:b'-- dump')
                os.utime(p,(start+day*86400,start+day*86400))
            names=sorted(p.name for p in out.glob('*.tar.gz'))
            self.assertEqual(len([n for n in names if n>='ro-world-20261004']),7)       # 7 newest days
            sundays=[n for n in names if datetime.date(int(n[9:13]),int(n[13:15]),int(n[15:17])).weekday()==6]
            self.assertTrue(4<=len(sundays)<=5)
            with tarfile.open(out/names[-1]) as tar:
                members=set(tar.getnames())
                self.assertEqual(tar.extractfile('dump.sql').read(),b'-- dump')
            self.assertIn('state/resident_a/diary.json',members);self.assertIn('state/world.local.json',members)
            self.assertNotIn('state/resident_a/journal.sqlite',members)
            self.assertFalse(backup.due(out,now=start+39*86400+3600));self.assertTrue(backup.due(out,now=start+40*86400+1))
