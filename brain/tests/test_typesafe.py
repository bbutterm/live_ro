import json
import unittest
from unittest.mock import patch
from live_brain.config import Provider
from live_brain import llm
from live_brain.typesafe import decide

class Response:
    def __init__(self, answer): self.answer=answer
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self): return json.dumps(self.answer).encode()

class TypeSafeTests(unittest.TestCase):
    def provider(self):
        return Provider('jev','https://api.typesafe.ai/v1/systemone','fixture-secret','jev-latest',5,200,300)
    def test_native_contract(self):
        def http(req,timeout):
            self.assertEqual(req.full_url,'https://api.typesafe.ai/v1/systemone')
            body=json.loads(req.data)
            self.assertNotIn('messages',body)
            self.assertEqual(set(body['questions']['route']['criteria']),{'deepseek','ignore'})
            return Response({'answers':{'route':{'type':'choice','choice':'deepseek','confidence':0.9}},'usage':{'input_tokens':10}})
        with patch('urllib.request.urlopen',side_effect=http):
            result,usage,latency=decide(self.provider(),[{'role':'user','content':'hello'}])
        self.assertTrue(result['call_llm'])
        self.assertIsNone(result['quick'])
    def test_ignore(self):
        with patch('urllib.request.urlopen',return_value=Response({'answers':{'route':{'type':'choice','choice':'ignore','confidence':0.9}}})):
            result,_,_=decide(self.provider(),[])
        self.assertFalse(result['call_llm'])
    def test_unknown_choice_rejected(self):
        with patch('urllib.request.urlopen',return_value=Response({'answers':{'route':{'type':'choice','choice':'shell','confidence':0.9}}})):
            with self.assertRaises(llm.LLMError): decide(self.provider(),[])
    def test_low_confidence_falls_back(self):
        with patch('urllib.request.urlopen',return_value=Response({'answers':{'route':{'type':'choice','choice':'ignore','confidence':0.1}}})):
            with self.assertRaises(llm.LLMError): decide(self.provider(),[])
