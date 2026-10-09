import unittest

class WorldMemoryTests(unittest.TestCase):
    def test_snapshot_survives_reload_and_resets_at_utc_day_boundary(self):
        from packages.resident_world import memory
        from unittest.mock import patch
        from pathlib import Path
        import tempfile,json
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            raw=json.dumps({'kind':'kill','map':'prt_fild08','count':'17','first':1,'last':17})
            with patch.object(memory,'STATE',d),patch('tools.events.query',return_value=raw) as query:
                s=memory.refresh({'char_id':2,'name':'Mira'},d,now=100)
                self.assertEqual(s['kills'],17)
                self.assertEqual(memory.read_memory(d,110)['kills'],17)
                self.assertEqual(memory.refresh({'char_id':2,'name':'Mira'},d,110)['kills'],17)
                self.assertEqual(query.call_count,1)
            self.assertEqual(memory.read_memory(d,86401)['kills'],0)

    def test_day_question_has_a_factual_decision_without_calling_model(self):
        from packages.resident_world.memory import day_decision
        from unittest.mock import patch
        from pathlib import Path
        with patch('packages.resident_world.memory.refresh',return_value={'source':'witness','kills':17,'hunting':['prt_fild08']}):
            d=day_decision({'name':'Mira'},{'name':'Tester','type':'W','text':'Что ты делала сегодня?'},Path('/not-used'))
            self.assertEqual(d['action'],'whisper');self.assertEqual(d['args']['to'],'Tester');self.assertIn('17',d['args']['text'])
        with patch('packages.resident_world.memory.refresh',side_effect=RuntimeError('unavailable')):
            d=day_decision({}, {'name':'Tester','type':'O','text':'что ты делал сегодня?'},Path('/not-used'))
            self.assertIn('Не могу',d['args']['text'])
        self.assertIsNone(day_decision({}, {'text':'привет'},Path('/not-used')))

    def test_daily_answer_uses_only_witness_facts_and_fits_speech(self):
        from packages.resident_world.memory import summarize,day_reply
        s={'source':'witness','day':'2026-10-09','kills':17,'hunting':['prt_fild08'],'visited':['prontera','prt_fild08'],'contacts':['Tester'],'met':['ResidentA']}
        text=summarize(s)
        self.assertIn('17',text);self.assertIn('Tester',text);self.assertIn('ResidentA',text)
        self.assertLessEqual(len(text),600)
        reply=day_reply(s);self.assertIn('17',reply);self.assertIn('prt_fild08',reply);self.assertLessEqual(len(reply),80)
        self.assertNotIn('купил',reply)
        with self.assertRaises(ValueError):summarize(dict(s,source='model'))
        self.assertIn('нет',day_reply({'source':'witness','kills':0,'hunting':[],'visited':[],'contacts':[],'met':[]}))
