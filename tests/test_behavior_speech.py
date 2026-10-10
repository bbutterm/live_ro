import copy
import unittest

CONFIG = {'speech': {'defaults': {'reply_delay_s': [4, 14], 'skip_reply_in_combat_rate': 1, 'emoji_rate': 1, 'slang_rate': 1, 'typo_rate': 1}}, 'residents': {'mira': {'traits': {'talk': 1, 'humor': 1}}}}


class SpeechTests(unittest.TestCase):
    def test_observed_facts_only_and_deterministic_rng(self):
        from packages.resident_behavior.speech import build_lines
        c = {'now': 10000, 'mental': {'rng_index': 0, 'drives': {'tired': 70}}, 'events': [{'event_id': 'e1', 'type': 'level_up', 'data': {}}]}
        other = copy.deepcopy(c)
        a = build_lines(CONFIG, 'mira', c)
        self.assertEqual(a, build_lines(CONFIG, 'mira', other))
        self.assertEqual(c['mental']['rng_index'], 1)
        self.assertTrue(any(line['fact_refs'] == ['e1'] for line in a))
        self.assertTrue(all(len(line['text']) <= 35 for line in a))
        empty = build_lines(CONFIG, 'mira', {'now': 10000, 'mental': {'rng_index': 0}})
        self.assertFalse(any('ап)' in line['text'] or 'народ' in line['text'] or 'Heal' in line['text'] for line in empty))
        recent = [x['text'] for x in a][:5]
        c['mental']['recent_lines'] = recent
        self.assertFalse(set(recent) & {x['text'] for x in build_lines(CONFIG, 'mira', c)})

    def test_public_sender_cooldown_and_no_styled_repeat(self):
        from packages.resident_behavior.speech import apply_style
        c = {'now': 10000, 'mental': {'rng_index': 0, 'speech_history': [{'sender': 'Novi', 'server_time': 9990}], 'recent_lines': []}, 'events': [{'event_id': 'h', 'type': 'heard', 'data': {'from': 'Novi', 'channel': 'public', 'text': 'привет'}}]}
        line = {'text': 'Не знаю.', 'template_id': 'unknown', 'fact_refs': [], 'channel': 'public', 'to': None, 'promise_action_id': None}
        self.assertEqual(apply_style(CONFIG, 'mira', c, line)['skip_reason'], 'sender_cooldown')
        c['mental']['speech_history'] = []
        c['mental']['rng_index'] = 0
        record = apply_style(CONFIG, 'mira', c, line)
        c['mental']['recent_lines'] = [record['text']]
        c['mental']['rng_index'] = 0
        self.assertEqual(apply_style(CONFIG, 'mira', c, line)['skip_reason'], 'recent_repeat')

    def test_delay_skip_distribution_and_factual_numbers_unchanged(self):
        from packages.resident_behavior.speech import apply_style, build_lines
        config = copy.deepcopy(CONFIG)
        config['speech']['defaults']['skip_reply_in_combat_rate'] = .5
        c = {'now': 10000, 'mental': {'rng_index': 0, 'drives': {'tired': 100}}, 'state': {'current_action': {'action': 'hunt'}, 'near_monsters': [{}]}, 'events': [{'event_id': 'h', 'type': 'heard', 'server_time': 9999, 'data': {'from': 'Novi', 'channel': 'public', 'text': '80 зени?'}}]}
        line = next(x for x in build_lines(config, 'mira', c) if x['template_id'] == 'price')
        skipped = 0
        for _ in range(200):
            record = apply_style(config, 'mira', c, line)
            skipped += record['skipped']
            if not record['skipped']:
                self.assertGreaterEqual(record['delay_s'], 4*1.8*1.5)
                self.assertLessEqual(record['delay_s'], min(40, 14*1.8*1.5))
                self.assertIn('80', record['text'])
                self.assertLessEqual(len(record['text']), 35)
        self.assertLess(abs(skipped/200-.5), .15)

    def test_bot_reply_no_combat_skip_and_ttl(self):
        from packages.resident_behavior.speech import build_lines, apply_style
        c = {'now': 10000, 'mental': {'rng_index': 0, 'drives': {'tired': 100}, 'active_mood': 'scared'}, 'state': {'current_action': {'action': 'hunt'}, 'near_monsters': [{}]}, 'events': [{'type': 'heard', 'event_id': 'h', 'server_time': 9999, 'data': {'from': 'Novi', 'channel': 'whisper', 'text': 'ты бот?'}}]}
        line = build_lines(CONFIG, 'mira', c)[0]
        self.assertIn('ИИ', line['text'])
        record = apply_style(CONFIG, 'mira', c, line)
        self.assertFalse(record['skipped'])
        self.assertLessEqual(record['delay_s'], 40)
        self.assertEqual(record['to'], 'Novi')
        c['now'] = 10061
        record = apply_style(CONFIG, 'mira', c, line)
        self.assertTrue(record['skipped'])
        self.assertEqual(record['skip_reason'], 'expired')
