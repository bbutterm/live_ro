"""The LLM brain: lenient answers, honest availability, one repair, pauses, diary."""
import json,pathlib,tempfile,unittest
from unittest.mock import Mock,patch

C={'places':{'fountain':{'map':'prontera','x':156,'y':191},'market':{'map':'prontera','x':156,'y':180},'field':{'map':'prt_fild08','x':170,'y':240}},
   'place_labels':{'market':'у торговых рядов'},
   'residents':[{'name':'ResidentA','resident_id':'resident_a','char_id':1},{'name':'Mira','resident_id':'resident_b','char_id':2}],
   'llm':{'max_hunt_minutes':15,'min_decision_seconds':20,'min_speech_seconds':45}}
HEALTHY={'telemetry':{'hp':100,'hp_max':100,'weight':100,'weight_max':1000,'zeny':200,'inventory':{'501':10},'sellable':{},
                      'nearby_players':[{'name':'Tester','pos':{'x':150,'y':190}}],'map':'prontera','pos':{'x':150,'y':190}},
         'projection':{'server_map':{'value':'prontera'},'server_pos':{'value':{'x':150,'y':190}}}}


def norm(obj,s=HEALTHY,**kw):
    from packages.resident_world.mind import normalize_decision
    return normalize_decision(obj,C,s,**kw)


class NormalizeTests(unittest.TestCase):
    def test_flat_strict_schema_answer(self):
        d,n=norm({'thought':'Хочу размяться','action':'hunt','text':None,'to':None,'place':None,'map':None,'minutes':12,'until_hp':None,'player':None,'resident':None,
                  'say':'Пойду на поле, разомнусь.','remember':'Тестер стоит у фонтана','plan':'Набить зени на зелья','reason':'Нужны деньги'})
        self.assertEqual(d,{'action':'hunt','args':{'map':'prt_fild08','minutes':12},'reason':'Нужны деньги','say':'Пойду на поле, разомнусь.'})
        self.assertEqual(n['plan'],'Набить зени на зелья');self.assertEqual(n['remember'],'Тестер стоит у фонтана')
    def test_nested_args_aliases_and_loose_numbers(self):
        self.assertEqual(norm({'action':'Walk','args':{'place':'Market'},'reason':'Глянуть цены'})[0]['args'],{'place':'market'})
        self.assertEqual(norm({'action':'farm','args':{'minutes':'20 минут'},'reason':'x'})[0]['args'],{'map':'prt_fild08','minutes':15})
        self.assertEqual(norm({'action':'hunt','minutes':2.6,'reason':'x'})[0]['args']['minutes'],5)
        self.assertEqual(norm({'action':'sleep','minutes':30,'reason':'x'})[0],{'action':'idle','args':{'minutes':10},'reason':'x'})
        self.assertEqual(norm({'action':'shop','reason':'x','extra':'rm -rf'})[0],{'action':'town_service','args':{},'reason':'x'})
    def test_one_speech_channel_and_no_lies(self):
        d,_=norm({'action':'say','text':None,'say':'Всем привет!','reason':'Поздороваться'})
        self.assertEqual(d,{'action':'say','args':{'text':'Всем привет!'},'reason':'Поздороваться'})
        d,_=norm({'action':'idle','minutes':2,'say':'Иду к тебе!','reason':'x'})
        self.assertNotIn('say',d)  # a promise without movement is dropped, not spoken
        d,_=norm({'action':'idle','minutes':2,'say':'Hello there','reason':'x'})
        self.assertNotIn('say',d)
        d,_=norm({'action':'say','text':'Очень длинная фраза '*10,'reason':'x'})
        self.assertLessEqual(len(d['args']['text']),80)
    def test_whisper_and_follow_targets_resolve_case_insensitively(self):
        self.assertEqual(norm({'action':'whisper','to':'tester','text':'Привет!','reason':'x'})[0]['args'],{'text':'Привет!','to':'Tester'})
        self.assertEqual(norm({'action':'follow','player':'TESTER','minutes':40,'reason':'x'})[0]['args'],{'player':'Tester','minutes':15})
        with self.assertRaises(ValueError):norm({'action':'follow','player':'Nobody','reason':'x'})
    def test_chat_reply_to_whisper_stays_private_and_targets_sender(self):
        d,_=norm({'action':'say','text':'Да, слышу.','reason':'x'},allowed=('say','whisper','follow','walk_to','meet'),event={'name':'Tester','type':'W','text':'эй'})
        self.assertEqual(d['action'],'whisper');self.assertEqual(d['args']['to'],'Tester')
    def test_invalid_choices_raise_for_one_repair(self):
        for bad in ({'action':'teleport','reason':'x'},{'action':'walk_to','place':'луна','reason':'x'},{'action':'say','text':'Hello','reason':'x'},
                    {'action':'meet','resident':'Гость','place':'fountain','reason':'x'},{'action':'say','text':'Иду к фонтану','reason':'x'}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):norm(bad)
    def test_whisper_only_to_people_near_known_or_neighbours(self):
        with self.assertRaisesRegex(ValueError,'WHISPER_TARGET'):norm({'action':'whisper','to':'RandomStranger','text':'Привет!','reason':'x'})
        self.assertEqual(norm({'action':'whisper','to':'mira','text':'Привет!','reason':'x'})[0]['args']['to'],'Mira')
        self.assertEqual(norm({'action':'whisper','to':'Guest','text':'Спасибо!','reason':'x'},known={'Guest'})[0]['args']['to'],'Guest')
    def test_honesty_promises_and_console_separators(self):
        from packages.resident_world.skills import speech
        for promise in ('Пойду к фонтану','Скоро буду у фонтана','Подойду к тебе','Приду через минуту','Уже бегу к тебе!','Следую за тобой','Иду!'):
            with self.subTest(promise=promise),self.assertRaisesRegex(ValueError,'PROMISE'):speech(promise)
        for fine in ('Приятно познакомиться','В следующий раз поболтаем','Буду рад помочь','Люди идут на рынок','Не держи обиду'):
            with self.subTest(fine=fine):self.assertEqual(speech(fine),fine)
        for bad in ('Привет;;quit','Привет\\nquit'):
            with self.subTest(bad=bad),self.assertRaisesRegex(ValueError,'INVALID_TEXT'):speech(bad)
        self.assertNotIn('say',norm({'action':'idle','minutes':2,'say':'Подойду позже','reason':'x'})[0])
        self.assertEqual(norm({'action':'rest','until_hp':90,'say':'Приятно отдохнуть','reason':'x'})[0]['say'],'Приятно отдохнуть')
    def test_more_promise_forms_and_sanitised_preludes(self):
        from packages.resident_world.skills import speech
        for promise in ('Пойдёмте к фонтану!','Идём к фонтану','Идёмте','Уже в пути','Выхожу к тебе'):
            with self.subTest(promise=promise),self.assertRaisesRegex(ValueError,'PROMISE'):speech(promise)
        d,_=norm({'action':'walk_to','place':'market','say':'Ура \\o/ к рядам;;вперёд','reason':'x'})
        self.assertEqual(d['say'],'Ура /o/ к рядам;вперёд')
        from packages.resident_world.mind import validate_decision
        with self.assertRaisesRegex(ValueError,'INVALID_SAY'):validate_decision({'action':'walk_to','args':{'place':'market'},'say':'Ура;;да','reason':'x'},C,HEALTHY)
    def test_never_whisper_to_self(self):
        from packages.resident_world.mind import availability
        with self.assertRaisesRegex(ValueError,'WHISPER_TARGET'):norm({'action':'whisper','to':'Mira','text':'Привет!','reason':'x'},me='Mira')
        self.assertNotIn('Mira',availability(C,dict(HEALTHY,_me='Mira'),())['whisper'])
        self.assertIn('ResidentA',availability(C,dict(HEALTHY,_me='Mira'),())['whisper'])
    def test_whisper_repair_hint_names_the_rule(self):
        from packages.resident_world.mind import explain
        self.assertIn('can_now.whisper',explain(ValueError('WHISPER_TARGET')))
    def test_absurd_numbers_do_not_crash(self):
        self.assertEqual(norm({'action':'hunt','minutes':float('inf'),'reason':'x'})[0]['args']['minutes'],10)
        self.assertEqual(norm({'action':'idle','minutes':'9'*500,'reason':'x'})[0]['args']['minutes'],10)
    def test_overweight_blocks_hunt_and_rest_but_offers_selling(self):
        from packages.resident_world.mind import availability
        heavy={'telemetry':dict(HEALTHY['telemetry'],weight=850,weight_max=1000,hp=40,sellable={'909':40})}
        with self.assertRaisesRegex(ValueError,'OVERWEIGHT'):norm({'action':'hunt','minutes':5,'reason':'x'},heavy)
        with self.assertRaisesRegex(ValueError,'OVERWEIGHT'):norm({'action':'rest','until_hp':90,'reason':'x'},heavy)
        can=availability(C,heavy)
        self.assertTrue(can['hunt'].startswith('нельзя'));self.assertTrue(can['rest'].startswith('нельзя'));self.assertIn('срочно',can['town_service'])
        self.assertEqual(norm({'action':'town_service','reason':'Продать добычу'},heavy)[0]['action'],'town_service')


class ChooseTests(unittest.TestCase):
    def setUp(self):
        from packages.resident_world import mind
        self.mind_module=mind;self.tmp=tempfile.TemporaryDirectory();self.state=pathlib.Path(self.tmp.name)
        self.d=self.state/'resident_b';self.d.mkdir()
        self.patches=[patch.object(mind,'STATE',self.state),patch('packages.resident_world.memory.refresh')]
        for p in self.patches:p.start()
        self.client=Mock();self.client.budget.return_value=(0,2,0)
        self.m=mind.Mind({'name':'Mira','char_id':2,'resident_id':'resident_b','persona':'Послушница, добрая.'},self.d,C,client=self.client)
    def tearDown(self):
        for p in self.patches:p.stop()
        self.tmp.cleanup()
    def reply(self,*contents):
        from packages.resident_world.llm import Reply
        from decimal import Decimal
        self.client.complete.side_effect=[c if isinstance(c,Exception) else Reply(json.dumps(c,ensure_ascii=False) if isinstance(c,dict) else c,Decimal('0.0003'),900,120,'json_schema',.4) for c in contents]
    def test_brain_is_asked_with_facts_and_remembers_its_plan(self):
        self.reply({'thought':'Тестер рядом, поздороваюсь','action':'say','text':'Привет, Тестер!','say':None,'remember':'Встретила Тестера в Пронтере','plan':'Потом на охоту','reason':'Вежливость'})
        with patch.object(self.mind_module.time,'time',return_value=1000):p=self.m.choose(HEALTHY)
        self.assertEqual(p['decision'],{'action':'say','args':{'text':'Привет, Тестер!'},'reason':'Вежливость'})
        self.assertEqual(p['thought'],'Тестер рядом, поздороваюсь')
        system,user=self.client.complete.call_args.args[0]
        self.assertIn('Ты — Mira',system['content']);self.assertIn('у торговых рядов',system['content'])
        context=json.loads(user['content'])
        self.assertEqual(context['я']['красных_зелий'],10);self.assertEqual(context['рядом'][0]['имя'],'Tester');self.assertEqual(context['can_now']['hunt'],'да')
        self.assertEqual(self.client.complete.call_args.kwargs['schema']['additionalProperties'],False)
        self.assertEqual(json.loads((self.d/'diary.json').read_text())[-1]['text'],'Встретила Тестера в Пронтере')
        self.assertEqual(self.m.plan(),'Потом на охоту')
        # Healthy pause between thoughts: the body is left alone, not handed to fallback.
        self.assertTrue(self.m.waiting(1010));self.assertFalse(self.m.waiting(1046))
        records=[json.loads(l) for l in (self.state/'decisions.jsonl').read_text().splitlines()]
        self.assertEqual(records[-1]['kind'],'MODEL_DECISION')
    def test_one_repair_with_the_exact_reason(self):
        self.reply('{"action":"walk_to","place":"луна","reason":"x"}',{'action':'walk_to','place':'fountain','reason':'К фонтану'})
        with patch.object(self.mind_module.time,'time',return_value=1000):p=self.m.choose(HEALTHY)
        self.assertEqual(p['decision']['args'],{'place':'fountain'})
        repair=self.client.complete.call_args_list[1].args[0][-1]['content']
        self.assertIn('place только из списка мест',repair)
    def test_garbage_twice_pauses_briefly_then_lets_native_habits_work(self):
        self.reply('не знаю','тоже не знаю')
        with patch.object(self.mind_module.time,'time',return_value=1000):self.assertIsNone(self.m.choose(HEALTHY))
        self.assertTrue(self.m.waiting(1001));self.assertEqual(self.m.next_call,1030)  # short: wait
        self.m.errors=3;self.reply('мимо','мимо')
        with patch.object(self.mind_module.time,'time',return_value=1031):self.assertIsNone(self.m.choose(HEALTHY))
        self.assertFalse(self.m.waiting(1032))  # repeated garbage: native habits keep the body busy
    def test_rate_limit_pauses_until_gate_reopens(self):
        from packages.resident_world.llm import LLMUnavailable
        self.reply(LLMUnavailable('RATE_LIMIT',1600,429,'slow down'))
        with patch.object(self.mind_module.time,'time',return_value=1000):self.assertIsNone(self.m.choose(HEALTHY))
        self.assertTrue(1600<=json.loads((self.d/'llm-pause.json').read_text())['until']<=1630)  # jittered per caller
        self.assertFalse(self.m.waiting(1100))
        with patch.object(self.mind_module.time,'time',return_value=1100):self.assertIsNone(self.m.choose(HEALTHY))
        self.assertEqual(self.client.complete.call_count,1)
        self.reply({'action':'idle','minutes':2,'reason':'Отдышаться'})
        with patch.object(self.mind_module.time,'time',return_value=1631):self.assertEqual(self.m.choose(HEALTHY)['decision']['action'],'idle')
        self.assertFalse((self.d/'llm-pause.json').exists())
    def test_old_pause_file_from_previous_code_does_not_mute_the_brain(self):
        from packages.resident_world import mind
        (self.d/'llm-pause.json').write_text(json.dumps({'until':10**10,'error':'BudgetStop'}))
        m=mind.Mind({'name':'Mira','char_id':2,'resident_id':'resident_b','persona':'x'},self.d,C,client=self.client)
        self.reply({'action':'idle','minutes':2,'reason':'Осмотреться'})
        with patch.object(self.mind_module.time,'time',return_value=1000):self.assertEqual(m.choose(HEALTHY)['decision']['action'],'idle')
    def test_daily_limit_is_rechecked_every_few_minutes_not_slept_until_midnight(self):
        from packages.resident_world.llm import LLMUnavailable
        self.reply(LLMUnavailable('DAILY_LIMIT',86400+30,detail='лимит'))
        with patch.object(self.mind_module.time,'time',return_value=1000):self.assertIsNone(self.m.choose(HEALTHY))
        self.assertTrue(1300<=self.m.unavailable_until<=1330)
    def test_stale_intent_is_dropped_and_a_fresh_one_asked(self):
        (self.d/'model-intent.json').write_text(json.dumps({'id':'old','at':100,'decision':{'action':'say','args':{'text':'Доброе утро!'},'reason':'x'}}))
        self.reply({'action':'idle','minutes':2,'reason':'Сейчас вечер'})
        with patch.object(self.mind_module.time,'time',return_value=1000):p=self.m.choose(HEALTHY)
        self.assertEqual(p['decision']['action'],'idle')
    def test_chat_is_paced_per_player_and_late_messages_get_the_quick_default(self):
        self.reply(*[{'action':'say','text':'Привет!','reason':'Ответ'} for _ in range(5)])
        default={'action':'say','args':{'text':'Слышу тебя.'},'reason':'Ответ'}
        s=dict(HEALTHY)
        with patch.object(self.mind_module.time,'time',return_value=1000):
            late=self.m.respond({'name':'Tester','type':'O','text':'эй','ts':994},s,default)
            self.assertIs(late,default);self.client.complete.assert_not_called()
            answers=[self.m.respond({'name':'Tester','type':'O','text':'привет','ts':1000},s,default) for _ in range(5)]
        self.assertEqual(self.client.complete.call_count,3)  # a chatty player gets the quick default afterwards
        self.assertIs(answers[-1],default)
        with patch.object(self.mind_module.time,'time',return_value=1000):self.m.respond({'name':'Other','type':'O','text':'привет','ts':1000},s,default)
        self.assertEqual(self.client.complete.call_count,4)
    def test_vote_place_is_the_offered_key(self):
        self.reply({'choice':'accept','place':'у фонтана','reason':'Давно не виделись'},{'choice':'counter','place':'Market','reason':'Там удобнее'})
        with patch.object(self.mind_module.time,'time',return_value=1000):
            self.assertEqual(self.m.negotiate({'resident':'ResidentA','place':'fountain','meeting_id':'m1'},HEALTHY,True)['place'],'fountain')
            self.assertEqual(self.m.negotiate({'resident':'ResidentA','place':'fountain','meeting_id':'m2'},HEALTHY,True)['place'],'market')
    def test_long_or_repeating_pauses_hand_the_body_to_habits(self):
        from packages.resident_world.llm import LLMUnavailable
        for reason in ('NO_TOKEN','AUTH','NO_CREDITS','DAILY_LIMIT'):
            with self.subTest(reason=reason):
                self.m.next_call=self.m.unavailable_until=0;self.m.streak=0
                self.reply(LLMUnavailable(reason,1030))
                with patch.object(self.mind_module.time,'time',return_value=1000):self.assertIsNone(self.m.choose(HEALTHY))
                self.assertFalse(self.m.waiting(1001))
        self.m.next_call=self.m.unavailable_until=0;self.m.streak=0
        for i in range(3):
            self.reply(LLMUnavailable('RATE_LIMIT',2000+i*40+30,429))
            with patch.object(self.mind_module.time,'time',return_value=2000+i*40):self.assertIsNone(self.m.choose(HEALTHY))
            self.assertEqual(self.m.waiting(2001+i*40),i<2)  # the third 429 in a row: stop standing still
    def test_chat_rechecks_a_raised_daily_limit_within_minutes(self):
        from packages.resident_world.llm import LLMUnavailable
        self.client.complete.side_effect=LLMUnavailable('DAILY_LIMIT',86400+30)
        default={'action':'say','args':{'text':'Слышу.'},'reason':'x'}
        with patch.object(self.mind_module.time,'time',return_value=1000):self.m.respond({'name':'Tester','type':'O','text':'эй','ts':1000},HEALTHY,default)
        self.assertLessEqual(self.m.chat_next,1300)
    def test_unreadable_intent_does_not_wedge_the_brain(self):
        (self.d/'model-intent.json').write_text('{broken')
        self.reply({'action':'idle','minutes':2,'reason':'Осмотреться'})
        with patch.object(self.mind_module.time,'time',return_value=1000):self.assertEqual(self.m.choose(HEALTHY)['decision']['action'],'idle')
    def test_only_delivered_replies_count_as_answered(self):
        # A failed reply must not let the model believe it already answered (or promised).
        import sqlite3,contextlib,time
        with contextlib.closing(sqlite3.connect(self.state/'chat.sqlite')) as db:
            db.execute('CREATE TABLE inbox(id INTEGER PRIMARY KEY,payload TEXT,resident TEXT,status TEXT,claimed REAL,result TEXT)')
            for text,outcome in (('привет','done'),('пойдём к фонтану?','failed(BODY_OFFLINE)')):
                db.execute('INSERT INTO inbox(payload,resident,status,result) VALUES(?,?,?,?)',(json.dumps({'name':'Tester','type':'C','text':text,'ts':time.time()}),'Mira','done',
                           json.dumps({'outcome':outcome,'decision':{'action':'say','args':{'text':'Привет, '+text}}})))
            db.commit()
        done,failed=self.m.heard()
        self.assertEqual(done['я ответил'],'Привет, привет');self.assertNotIn('я ответил',failed);self.assertIn('не смог ответить',failed)
    def test_budget_is_paced_over_the_day(self):
        self.client.budget.return_value=(1,2,100)  # half the money spent by 03:00 UTC
        self.assertGreater(self.m.gap(3*3600,{'action':'hunt'}),20*3)
        self.client.budget.return_value=(.1,2,10)
        self.assertEqual(self.m.gap(12*3600,{'action':'hunt'}),20)



class WorldConfigTests(unittest.TestCase):
    ROOT=pathlib.Path(__file__).resolve().parents[1]
    def config(self):return json.loads((self.ROOT/'config/world.yaml').read_text())
    def test_every_place_has_a_russian_label_and_a_brain_menu(self):
        c=self.config()
        self.assertEqual(set(c['places']),set(c['place_labels']))
        self.assertEqual((c['place_labels']['fountain'],c['place_labels']['field']),('у фонтана','на поле'))  # meeting protocol phrases
        self.assertTrue(c['llm']['enabled']);self.assertGreaterEqual(c['llm']['max_hunt_minutes'],5)
    def test_places_are_walkable_on_the_pinned_renewal_map_cache(self):
        from packages.body_gateway.fields import read_cache
        cache=next((p for p in (self.ROOT/'vendor/rathena/db/re/map_cache.dat',pathlib.Path('/root/ragnarok/repos/rathena/db/re/map_cache.dat')) if p.exists()),None)
        if cache is None:self.skipTest('pinned rAthena map cache not checked out')
        maps=read_cache(cache.read_bytes())
        for name,p in self.config()['places'].items():
            w,h,cells=maps[p['map']]
            with self.subTest(place=name):
                self.assertTrue(all(cells[(p['x']+a)+(p['y']+b)*w] in (0,3) for a in (-1,0,1) for b in (-1,0,1)))

if __name__=='__main__':unittest.main()
