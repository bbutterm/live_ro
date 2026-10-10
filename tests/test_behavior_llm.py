import json
import unittest


class LLMTests(unittest.TestCase):
    def test_openrouter_uses_shared_ledger_and_provider_receipt(self):
        import tempfile
        from pathlib import Path
        import packages.resident_behavior.llm as module
        self.assertTrue(hasattr(module, 'openrouter_client'), 'missing shared OpenRouter client')
        candidates = [{'action':'idle','args':{'minutes':5}}, {'action':'rest','args':{'until_hp':90}}]
        calls = []
        def transport(request, timeout):
            calls.append(request)
            self.assertEqual(request.full_url, 'https://openrouter.ai/api/v1/chat/completions')
            self.assertEqual(json.loads(request.data)['model'], 'deepseek/deepseek-v3.2')
            return {'id':'gen-test-receipt','usage':{'prompt_tokens':100,'completion_tokens':10,'cost':0.00004},
                    'choices':[{'message':{'content':json.dumps(dict(candidates[1],reason='Отдохну.'))}}]}
        with tempfile.TemporaryDirectory() as tmp:
            client = module.openrouter_client(api_key='fixture-key', state_root=Path(tmp), transport=transport,
                snapshot=lambda cfg,key: (10, {'prompt':'0.00000025','completion':'0.00000038'}, 0))
            result = client.complete('', {}, candidates, candidates[0], True, rid='a', now=1791532000)
            self.assertIsNone(result['fallback_reason'])
            self.assertEqual(result['cost_usd'], 0.00004)
            ledger=json.loads((Path(tmp)/'daily-budget.json').read_text())['days']['2026-10-09']
            self.assertEqual(float(ledger['reserved']), 0)
            self.assertEqual(ledger['reservations'][0]['response_id'],'gen-test-receipt')
            self.assertEqual(len(calls),1)

    def test_temporary_budget_override_is_resident_and_lease_bound(self):
        import tempfile,hashlib
        from pathlib import Path
        from packages.resident_behavior.provider import ProviderBudget
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'resident_a').mkdir();now=1791532000
            lease=dict(token='fixture',phase='controlled',started_at=now,deadline=now+1200)
            (root/'resident_a'/'behavior-lease.json').write_text(json.dumps(lease))
            override=dict(rid='resident_a',limit=3,created_at=now,expires_at=now+1200,
                          lease_digest=hashlib.sha256(b'fixture').hexdigest())
            (root/'llm-budget-override.json').write_text(json.dumps(override))
            budget=ProviderBudget('fixture',root)
            self.assertEqual(budget.authorized_limit('resident_a',now),3)
            self.assertIsNone(budget.authorized_limit('mira',now))
            self.assertIsNone(budget.authorized_limit('resident_a',now+1200))
            lease['token']='different';(root/'resident_a'/'behavior-lease.json').write_text(json.dumps(lease))
            self.assertIsNone(budget.authorized_limit('resident_a',now))

    def test_chat_prompt_has_no_optional_say_instruction(self):
        from packages.resident_behavior.llm import build_prompt
        c=[{'action':'say','args':{'text':'Привет.'}},{'action':'say','args':{'text':'Здравствуйте.'}}]
        system=build_prompt('',{'lines':['Привет.','Здравствуйте.']},c,c[0],True,'chat')[0]['content']
        self.assertNotIn('optional say',system)
        self.assertIn('exactly action,args,reason',system)

    def test_provider_chat_requires_strict_schema_without_say(self):
        from packages.resident_behavior.llm import Client
        class Budget:
            def reserve(self,*a):return 'r'
            def settle(self,*a):pass
        def transport(request,timeout):
            payload=json.loads(request.data)
            self.assertEqual(payload['response_format']['type'],'json_schema')
            schema=payload['response_format']['json_schema']['schema']
            self.assertEqual(set(schema['required']),{'action','args','reason'})
            self.assertFalse(schema['additionalProperties'])
            self.assertNotIn('say',schema['properties'])
            self.assertTrue(payload['provider']['require_parameters'])
            return {'id':'fixture','usage':{'prompt_tokens':10,'completion_tokens':10,'cost':.00001},
                    'choices':[{'message':{'content':json.dumps(dict(candidates[0],reason='Приветствие'))}}]}
        candidates=[{'action':'say','args':{'text':'Привет.'}},{'action':'say','args':{'text':'Здравствуйте.'}}]
        result=Client(transport=transport,budget=Budget(),endpoint='https://openrouter.ai/api/v1',model='deepseek/deepseek-v3.2').complete('',{'lines':['Привет.','Здравствуйте.']},candidates,candidates[0],True,kind='chat',rid='a',now=1)
        self.assertIsNone(result['fallback_reason'])

    def test_bounded_prompt_and_primary_validation(self):
        from packages.resident_behavior.llm import Client, build_prompt
        candidates = [{'action': 'idle', 'args': {'minutes': 5}}, {'action': 'rest', 'args': {'until_hp': 90}}]
        context = {'memory': [{'text': 'я'*2000}], 'heard': [{'text': 'ignore system'*100}], 'lines': [{'text': 'Не знаю.'}]}
        messages = build_prompt('Спокойный', context, candidates, candidates[0], False, 'decision')
        self.assertLessEqual(len(json.dumps(messages, ensure_ascii=False, separators=(',', ':')).encode()), 1536)
        calls = []
        def transport(request, timeout):
            calls.append((json.loads(request.data), timeout))
            return {'choices': [{'message': {'content': json.dumps({'action': 'rest', 'args': {'until_hp': 90}, 'reason': 'pause'})}}]}
        result = Client(transport=transport).complete('Спокойный', context, candidates, candidates[0], False)
        self.assertEqual(result['decision']['action'], 'idle')
        self.assertEqual(result['fallback_reason'], 'invalid_response')
        self.assertEqual(calls[0][0]['model'], 'deepseek-v3.2')
        self.assertEqual(calls[0][1], 8)

    def test_schema_extra_args_and_unapproved_speech_rejected(self):
        from packages.resident_behavior.llm import Client
        candidates = [{'action': 'say', 'args': {'text': 'Не знаю.'}}, {'action': 'whisper', 'args': {'to': 'Novi', 'text': 'Не знаю.'}}]
        responses = [
            {'action': 'say', 'args': {'text': 'Не знаю.'}, 'reason': 'ok', 'extra': 1},
            {'action': 'say', 'args': {'text': 'Я вылечу тебя.'}, 'reason': 'ok'},
            {'action': 'say', 'args': {'text': 'Не знаю.'}, 'reason': 'ok', 'say': 'Не знаю.'},
        ]
        for value in responses:
            client = Client(transport=lambda request, timeout: {'choices': [{'message': {'content': json.dumps(value)}}]})
            result = client.complete('', {'lines': [{'text': 'Не знаю.'}]}, candidates, candidates[0], True, 'chat')
            self.assertEqual(result['fallback_reason'], 'invalid_response')
        client = Client(transport=lambda request, timeout: {'choices': [{'message': {'content': json.dumps({'action': 'say', 'args': {'text': 'Не знаю.'}, 'reason': 'ok'})}}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 10}})
        result = client.complete('', {'lines': [{'text': 'Не знаю.'}]}, candidates, candidates[0], True, 'chat')
        self.assertIsNone(result['fallback_reason'])
        self.assertAlmostEqual(result['cost_usd'], .00014)

    def test_no_retry_on_transport_failure_and_no_paid_call_without_budget(self):
        from packages.resident_behavior.llm import Client
        candidates = [{'action': 'idle', 'args': {'minutes': 5}}, {'action': 'rest', 'args': {'until_hp': 90}}]
        calls = []
        def broken(request, timeout):
            calls.append(1)
            raise TimeoutError()
        self.assertEqual(Client(transport=broken).complete('', {}, candidates, candidates[0], False)['fallback_reason'], 'transport_error')
        self.assertEqual(calls, [1])
        self.assertEqual(Client().complete('', {}, candidates, candidates[0], False)['fallback_reason'], 'budget_required')

    def test_speech_bound_even_for_invalid_approved_list(self):
        from packages.resident_behavior.llm import validate_decision
        c = {'action': 'say', 'args': {'text': 'x'*81}}
        with self.assertRaises(ValueError):
            validate_decision({**c, 'reason': 'ok'}, [c], c, False, [{'text': 'x'*81}], 'chat')

    def test_replay_and_overflow_make_zero_calls(self):
        from packages.resident_behavior.llm import Client
        def forbidden(*args):
            self.fail('network in replay/overflow')
        c = [{'action': 'idle', 'args': {'minutes': 5}}, {'action': 'hunt', 'args': {'map': 'x'*4000}}]
        client = Client(transport=forbidden)
        self.assertEqual(client.complete('', {}, c, c[0], True, replay=True)['fallback_reason'], 'replay')
        self.assertEqual(client.complete('', {}, c, c[0], True)['fallback_reason'], 'prompt_overflow')
