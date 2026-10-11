import unittest,tempfile,time,contextlib
from pathlib import Path
from unittest.mock import Mock,patch
from packages.resident_world import chat,supervisor,skills
class PeerSpeechTests(unittest.TestCase):
    def test_party_peer_speech_uses_independent_slot_and_preserves_hunt(self):
        with tempfile.TemporaryDirectory() as td:
            state=Path(td);d=state/'resident_b';d.mkdir()
            r={'resident_id':'resident_b','char_id':150002,'name':'Mira','duo_partner':'Borin'}
            event={'id':99,'char_id':150003,'name':'Borin','type':'O','text':'Готова?','ts':time.time()}
            s={'telemetry':{'party':{'share':1,'members':[{'name':'Borin'}]},'duo_speech':True}}
            decision={'action':'say','args':{'text':'Держусь рядом, слежу за твоим здоровьем.'},'reason':'Тактика'}
            store=Mock();store.suppress_repeat.side_effect=lambda e,x,*args:x
            def model(e,s,default):
                self.assertFalse((d/'CHAT_PENDING').exists())
                return decision
            def run(*args):
                self.assertFalse((d/'CHAT_PENDING').exists())
                self.assertEqual(args[1]['skill'],'say')
                self.assertNotEqual(args[-1],d) # a speech task must not overwrite the motor journal
                return 'done'
            with patch.object(supervisor,'STATE',state),patch('packages.body_gateway.cli.request',return_value=s),patch('packages.resident_world.memory.day_decision',return_value=None),patch.object(skills,'manual',side_effect=AssertionError('must not cancel hunt')),patch.object(skills,'run_native',side_effect=run),patch('packages.resident_world.continuity.confirm_chat_reply') as confirm,patch('packages.resident_world.mind.record'):
                chat.handle(event,r,{},store,model)
                confirm.assert_called_once_with(d,event,decision,'done')
            self.assertFalse((d/'CHAT_PENDING').exists())
            self.assertFalse((d/'PEER_CHAT_PENDING').exists())
