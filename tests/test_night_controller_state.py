import importlib.util
import json
import pathlib
import tempfile
import unittest


class ControllerStateTests(unittest.TestCase):
    def test_chat_replay_survives_restart_without_duplicate(self):
        self.assertIsNotNone(importlib.util.find_spec('packages.resident_demo.state'), 'Persistent chat state missing')
        from packages.resident_demo.state import ControllerState
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'state.json'
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
            s = ControllerState(p, mem, 63)
            t = {'map': 'prontera', 'pos': {'x': 150, 'y': 150}}
            chats = [{'id': 61, 'map': 'prontera', 'x': 149, 'y': 152, 'text': 'Привет', 'char_id': 150004}]
            s.hear('resident_a', t, chats, fresh=False)
            self.assertEqual(s.actors['resident_a']['cursor'], 0)
            s.hear('resident_a', t, chats, fresh=True)
            self.assertEqual(s.actors['resident_a']['mem']['heard'], chats)
            restart = ControllerState(p, mem, 10000)
            restart.hear('resident_a', t, chats, fresh=True)
            self.assertEqual(restart.actors['resident_a']['mem']['heard'], chats)
            self.assertEqual(restart.actors['resident_a']['cursor'], 61)
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_stale_actor_does_not_block_fresh_actor_chat_pages(self):
        from packages.resident_demo.state import ControllerState
        self.assertTrue(hasattr(ControllerState, 'ingest'), 'Per-actor paginated ingestion missing')
        with tempfile.TemporaryDirectory() as d:
            mem = {rid: {'resident': rid, 'notes': '', 'heard': [], 'outcomes': []} for rid in ('resident_a', 'resident_b')}
            s = ControllerState(pathlib.Path(d)/'state.json', mem, 63)
            telemetry = {'map': 'prontera', 'pos': {'x': 150, 'y': 150}}
            rows = [{'id': i, 'map': 'prontera', 'x': 150, 'y': 150, 'text': str(i), 'char_id': 150004} for i in range(1, 206)]
            fetched = []
            def fetch(cursor):
                fetched.append(cursor)
                return [r for r in rows if r['id'] > cursor][:100]
            states = {'resident_a': {'age': 10, 'telemetry': telemetry}, 'resident_b': {'age': .1, 'telemetry': telemetry}}
            for _ in range(3):
                s.ingest(states, fetch)
            self.assertEqual(fetched, [0, 100, 200])
            self.assertEqual(s.actors['resident_a']['cursor'], 0, 'Stale actor lost unread backlog')
            self.assertEqual(s.actors['resident_b']['cursor'], 205, 'Fresh actor stuck on stale consumer page')
            states['resident_a']['age'] = .1
            restart = ControllerState(s.path, mem, 205)
            for _ in range(3):
                restart.ingest(states, fetch)
            self.assertEqual(restart.actors['resident_a']['cursor'], 205)
            self.assertEqual([r['id'] for r in restart.actors['resident_a']['mem']['heard']], list(range(198, 206)))

    def test_bootstrap_replay_deduplicates_imported_memory(self):
        from packages.resident_demo.state import ControllerState
        with tempfile.TemporaryDirectory() as d:
            chat = {'id': 61, 'map': 'prontera', 'x': 149, 'y': 152, 'text': 'Привет', 'char_id': 150004}
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [chat], 'outcomes': []}}
            s = ControllerState(pathlib.Path(d)/'state.json', mem, 63)
            s.hear('resident_a', {'map': 'prontera', 'pos': {'x': 150, 'y': 150}}, [chat, chat], True)
            self.assertEqual(s.actors['resident_a']['mem']['heard'], [chat], 'Replay duplicated historical heard event')
            self.assertEqual(s.actors['resident_a']['cursor'], 61)
            self.assertEqual(mem['resident_a']['heard'], [chat], 'Caller memory was mutated')

    def test_missing_initialized_checkpoint_fails_closed(self):
        from packages.resident_demo.state import ControllerState
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)/'state.json'
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
            s = ControllerState(p, mem, 63)
            s.reserve_decision('resident_a', 100, 20, {150001, 150002, 150003})
            p.unlink()
            with self.assertRaisesRegex(ValueError, 'CONTROLLER_STATE_MISSING'):
                ControllerState(p, mem, 63)
            self.assertFalse(p.exists(), 'Lost checkpoint was silently reinitialized')

    def test_silence_gate_survives_restart_and_ignores_resident_chatter(self):
        from packages.resident_demo.state import ControllerState
        self.assertTrue(hasattr(ControllerState, 'decision_due'), 'Durable silence/cooldown gate missing')
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)/'state.json'
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
            s = ControllerState(p, mem, 63)
            residents = {150001, 150002, 150003}
            self.assertTrue(s.decision_due('resident_a', 100, residents))
            s.reserve_decision('resident_a', 100, 20, residents)
            restart = ControllerState(p, mem, 63)
            self.assertFalse(restart.decision_due('resident_a', 10000, residents), 'Silence must not pay again after restart')
            restart.actors['resident_a']['mem']['heard'] = [{'id': 62, 'char_id': 150002}]
            self.assertFalse(restart.decision_due('resident_a', 10000, residents), 'Residents must not fuel endless chatter')
            restart.actors['resident_a']['mem']['heard'].append({'id': 63, 'char_id': 150004})
            self.assertFalse(restart.decision_due('resident_a', 119, residents), 'Human chat cannot bypass minimum cooldown')
            self.assertTrue(restart.decision_due('resident_a', 120, residents))
            restart.reserve_decision('resident_a', 120, 20, residents)
            self.assertFalse(ControllerState(p, mem, 63).decision_due('resident_a', 10000, residents), 'Same human event must not trigger twice')

    def test_legacy_decisions_adopt_consumed_input_without_reset(self):
        from packages.resident_demo.state import ControllerState
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)/'state.json'
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
            s = ControllerState(p, mem, 63)
            actor = s.actors['resident_a']
            actor['decisions'] = 1
            actor['mem']['heard'] = [{'id': 61, 'char_id': 150004}]
            s.save()
            restart = ControllerState(p, mem, 63)
            self.assertFalse(restart.decision_due('resident_a', 10000, {150001, 150002, 150003}))
            restart.actors['resident_a']['mem']['heard'].append({'id': 62, 'char_id': 150004})
            self.assertTrue(restart.decision_due('resident_a', 10000, {150001, 150002, 150003}), 'Legacy actor never responds to new human input')
            persisted = json.loads(p.read_text())['actors']['resident_a']['model_gate']
            self.assertEqual(persisted['human_id'], 61)
            self.assertEqual(restart.actors['resident_a']['decisions'], 1)

    def test_checkpoint_rejects_malformed_model_accounting(self):
        from packages.resident_demo.state import ControllerState
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)/'state.json'
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
            s = ControllerState(p, mem, 63)
            valid = json.loads(p.read_text())
            corruptions = [
                {'model_gate': {'next_at': float('nan'), 'human_id': 0}},
                {'model_gate': {'next_at': float('inf'), 'human_id': 0}},
                {'model_gate': {'next_at': -1, 'human_id': 0}},
                {'model_gate': {'next_at': True, 'human_id': 0}},
                {'model_gate': {'next_at': 100, 'human_id': -1}},
                {'model_gate': {'next_at': 100, 'human_id': '61'}},
                {'model_gate': None}, {'model_gate': {}},
                {'decisions': -1}, {'decisions': True}, {'decisions': '1'},
                {'pending': {'expires_at': float('nan'), 'action_id': 'known'}},
                {'pending': {'deadline': 30, 'action_id': 'legacy-without-expiry'}},
                {'mem': {'resident': 'resident_a', 'heard': [{'id': -1, 'char_id': 150004}]}},
            ]
            for corruption in corruptions:
                with self.subTest(corruption=corruption):
                    data = json.loads(json.dumps(valid))
                    data['actors']['resident_a'].update(corruption)
                    p.write_text(json.dumps(data))
                    before = p.read_bytes()
                    with self.assertRaisesRegex(ValueError, 'CONTROLLER_STATE_SCHEMA'):
                        ControllerState(p, mem, 63)
                    self.assertEqual(p.read_bytes(), before, 'Invalid checkpoint was rewritten')

    def test_recovered_intent_does_not_renew_absolute_deadline(self):
        from packages.resident_demo.state import ControllerState
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)/'state.json'
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
            s = ControllerState(p, mem, 63)
            s.actors['resident_a']['steps'] = [('travel_to', {'map': 'prontera', 'x': 150, 'y': 150, 'r': 1}, 30)]
            def lost(msg):
                raise TimeoutError('acceptance unknown')
            from unittest.mock import patch
            with patch('time.time', return_value=100), self.assertRaises(TimeoutError):
                s.dispatch('resident_a', lost)
            restart = ControllerState(p, mem, 63)
            messages = []
            def gateway(msg):
                messages.append(msg)
                if msg['op'] == 'actions':
                    return []
                return {'action_id': 'would-dispatch'}
            with patch('time.time', return_value=131), self.assertRaisesRegex(ValueError, 'CONTROLLER_INTENT_EXPIRED'):
                restart.dispatch('resident_a', gateway)
            self.assertFalse(any(m['op']=='run' for m in messages), 'Expired intent dispatched afresh')
            idem = restart.actors['resident_a']['pending']['idem']
            accepted = {'action_id': 'original', 'idem_key': idem, 'resident': 'resident_a', 'state': 'confirmed'}
            with patch('time.time', return_value=132):
                row = restart.dispatch('resident_a', lambda msg: [accepted] if msg['op']=='actions' else self.fail('Expired run renewed'))
            self.assertEqual(row['action_id'], 'original')
            self.assertEqual(restart.actors['resident_a']['pending']['expires_at'], 130)

    def test_lost_dispatch_reply_reuses_persisted_key(self):
        from packages.resident_demo.state import ControllerState
        from packages.body_gateway.journal import Journal
        self.assertTrue(hasattr(ControllerState, 'dispatch'), 'Persist-before-dispatch missing')
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'state.json'
            mem = {'resident_a': {'resident': 'resident_a', 'notes': '', 'heard': [], 'outcomes': []}}
            s = ControllerState(p, mem, 63)
            s.actors['resident_a']['steps'] = [('travel_to', {'map': 'prontera', 'x': 150, 'y': 150, 'r': 3}, 30)]
            s.save()
            j = Journal(d+'/gateway.sqlite')
            def gateway(msg):
                on_disk = json.loads(p.read_text())['actors']['resident_a']['pending']
                self.assertEqual(on_disk['idem'], msg['idem'])
                row = j.plan('resident_a', msg['skill'], msg['params'], msg['idem'], 123, 'epoch', 0)
                raise TimeoutError('reply lost after durable gateway acceptance')
            with self.assertRaises(TimeoutError):
                s.dispatch('resident_a', gateway)
            aid = j.actions()[0]['action_id']
            restart = ControllerState(p, mem, 63)
            def recover(msg):
                return j.plan('resident_a', msg['skill'], msg['params'], msg['idem'], 123, 'epoch', 0)
            row = restart.dispatch('resident_a', recover)
            self.assertEqual(row['action_id'], aid)
            self.assertEqual(len(j.actions()), 1)
            reread = ControllerState(p, mem, 63)
            self.assertEqual(reread.actors['resident_a']['pending']['action_id'], aid)
            self.assertEqual(reread.actors['resident_a']['steps'], [])
            j.close()

    def test_last_paid_decision_executes_native_step_before_cap_exit(self):
        self.run_main_fixture(1, speak=True)
        self.assertTrue(any(m.get('skill') == 'say' for m in self.native_messages), 'Final paid decision never dispatched')

    def test_main_native_failure_dispatches_automatic_alternative(self):
        self.run_main_fixture(1, native_failure=True)
        self.assertTrue(any(m.get('skill') == 'hunt' for m in self.native_messages), 'Main discarded failure without production recovery')

    def run_main_fixture(self, max_calls, human_near_b_only=False, speak=False, native_failure=False):
        import sys
        import types
        from unittest.mock import patch, MagicMock
        from packages.resident_demo import main as controller
        from packages.resident_demo.policy import ROLES
        contexts = []
        self.native_messages = []
        chat = {'id': 61, 'map': 'prontera', 'x': 149, 'y': 152, 'text': 'Привет', 'char_id': 150004, 'name': 'Vanya'}
        with tempfile.TemporaryDirectory() as d:
            base = pathlib.Path(d)
            for rid in ROLES:
                (base/rid).mkdir()
            def sql(config, statement):
                import re
                if 'MAX(id)' in statement:
                    return '63'
                cursor = int(re.search(r'l.id>(\d+)', statement)[1])
                return json.dumps(chat) if cursor < 61 else ''
            def native(socket, msg):
                self.native_messages.append(msg)
                if msg['op'] == 'status':
                    x = 180 if human_near_b_only and 'resident_b' not in socket else 150
                    return {'age': 0.1, 'actions': [], 'telemetry': {'dead': False, 'map': 'prt_fild08' if native_failure else 'prontera', 'pos': {'x': x, 'y': 150}, 'hp': 10, 'hp_max': 10, 'zeny': 0}}
                if msg['op'] in ('run', 'action'):
                    failure = native_failure and not any(m.get('skill') == 'hunt' for m in self.native_messages)
                    return {'action_id': 'fixture-action', 'state': 'failed' if failure else 'confirmed', 'skill': 'travel_to', 'code': 'NO_ROUTE' if failure else 'ARRIVED'}
                return {}
            def fixture_completion(client, guard, cfg, key, messages):
                contexts.append(json.loads(messages[1]['content']))
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps({'action': 'speak' if speak else 'wait', 'memory': 'fixture', 'text': 'Привет!' if speak else '', 'reason': ''})))])
            original = pathlib.Path.read_text
            def private_read(path, *args, **kwargs):
                if str(path).endswith('resident_llm.json'):
                    return json.dumps({'model': 'fixture', 'key_file': '/fixture-key', 'base_url': 'https://fixture.invalid'})
                if str(path) == '/fixture-key':
                    return 'UNIT-TEST-NOT-A-SECRET'
                return original(path, *args, **kwargs)
            import itertools
            with patch('packages.resident_demo.budget.DEADLINE', __import__('time').time() + 1000), patch.object(controller, 'BASE', base), patch.object(controller, 'query', sql), patch.object(controller, 'request', native), patch.object(controller.time, 'sleep'), patch.object(controller.time, 'monotonic', side_effect=itertools.count()), patch.object(pathlib.Path, 'read_text', private_read), patch.dict(sys.modules, {'openai': types.SimpleNamespace(OpenAI=MagicMock())}), patch('packages.resident_demo.budget.Budget', MagicMock()), patch('packages.resident_demo.budget.guarded_completion', fixture_completion), patch.object(sys, 'argv', ['controller', '--duration', '180' if native_failure else '60', '--max-calls', str(max_calls), '--interval', '20']):
                controller.main()
            if not human_near_b_only and not native_failure:
                self.assertEqual([r['id'] for r in contexts[0]['private_memory']['heard']], [61], 'Startup MAX cursor dropped owner greeting')
            self.assertTrue((base/'ai/controller-state.json').exists(), 'Main lacks durable checkpoint')
            return contexts

    def test_main_delivers_existing_chat_to_model_context(self):
        self.assertEqual(len(self.run_main_fixture(1)), 1, 'Inner actor loop exceeded max_calls=1')

    def test_main_prioritizes_local_human_over_unprompted_introduction(self):
        contexts = self.run_main_fixture(1, human_near_b_only=True)
        self.assertEqual(contexts[0]['self']['name'], 'Mira', 'Unprompted introduction spent last call before local human response')
        self.assertEqual(contexts[0]['private_memory']['heard'][0]['id'], 61)

    def test_main_silence_does_not_spend_after_first_decisions(self):
        self.assertEqual(len(self.run_main_fixture(6)), 3, 'Silence caused repeated paid decisions')

if __name__ == '__main__':
    unittest.main()
