import json
import tempfile
import unittest
from pathlib import Path
from packages.resident_behavior.ownership import read_lease, lease_active, dispatch_allowed, create_lease, set_phase

class OwnershipTests(unittest.TestCase):
    def test_deadline_survives_respawn_and_cannot_be_renewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            first=create_lease(d,now=100,duration=900,token='canary')
            self.assertTrue(dispatch_allowed(set_phase(d,'controlled','canary'),'canary',101))
            self.assertFalse(dispatch_allowed(read_lease(d),'canary',1000))
            self.assertTrue(lease_active(read_lease(d)))
            with self.assertRaises(ValueError):create_lease(d,now=500,duration=900,token='other')
            self.assertEqual(read_lease(d)['deadline'],first['deadline'])
    def test_corrupt_or_overlong_lease_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); p=d/'behavior-lease.json'
            self.assertIsNone(read_lease(d))
            for data in ('broken',json.dumps({'token':'x','started_at':0,'deadline':1201,'phase':'controlled'})):
                p.write_text(data)
                lease=read_lease(d)
                self.assertTrue(lease_active(lease))
                self.assertFalse(dispatch_allowed(lease,'x',1))
    def test_legacy_promises_are_not_spoken_as_accepted_v1_intents(self):
        from packages.resident_behavior.ownership import neutral_speech
        lease={'phase':'controlled'}
        self.assertEqual(neutral_speech(lease,'Пойдём, буду рядом.'),'Слышу тебя. Пока не обещаю движение.')
        self.assertEqual(neutral_speech(lease,'Договорились: встреча у фонтана.'),'Слышу тебя. Встречу пока не подтверждаю.')
        self.assertEqual(neutral_speech(lease,'Привет! Как твои дела?'),'Привет! Как твои дела?')
        self.assertEqual(neutral_speech(None,'Иду к фонтану.'),'Иду к фонтану.')

    def test_release_is_explicit_and_owner_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); create_lease(d,now=100,duration=900,token='one')
            self.assertFalse(dispatch_allowed(read_lease(d),'one',101))
            with self.assertRaises(ValueError):set_phase(d,'controlled','wrong')
            set_phase(d,'controlled','one')
            self.assertFalse(dispatch_allowed(read_lease(d),'wrong',101))
            self.assertFalse(lease_active(set_phase(d,'released','one')))
