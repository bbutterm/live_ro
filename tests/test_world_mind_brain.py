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
        self.assertFalse(self.m.waiting(1001));self.assertEqual(self.m.next_call,1030)
    def test_rate_limit_pauses_until_gate_reopens(self):
        from packages.resident_world.llm import LLMUnavailable
        self.reply(LLMUnavailable('RATE_LIMIT',1600,429,'slow down'))
        with patch.object(self.mind_module.time,'time',return_value=1000):self.assertIsNone(self.m.choose(HEALTHY))
        self.assertEqual(json.loads((self.d/'llm-pause.json').read_text())['until'],1600)
        self.assertFalse(self.m.waiting(1100))
        with patch.object(self.mind_module.time,'time',return_value=1100):self.assertIsNone(self.m.choose(HEALTHY))
        self.assertEqual(self.client.complete.call_count,1)
        self.reply({'action':'idle','minutes':2,'reason':'Отдышаться'})
        with patch.object(self.mind_module.time,'time',return_value=1601):self.assertEqual(self.m.choose(HEALTHY)['decision']['action'],'idle')
        self.assertFalse((self.d/'llm-pause.json').exists())
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
