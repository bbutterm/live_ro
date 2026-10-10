"""Offline gateway fences and native parallel speech regression evidence."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from packages.body_gateway import service
from packages.body_gateway.executor import Executor
from packages.body_gateway.journal import Journal
from tests import test_behavior_native


class FencingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.j = Journal(str(self.directory / 'test.sqlite'))
        self.addCleanup(self.j.close)
        self.sent = []
        self.e = Executor(self.j, 'resident_a', 150001, self.sent.append)
        self.e.body(dict(type='hello', body_epoch='ep'))
        self.e.body(dict(type='telemetry', body_epoch='ep', ts=time.time(),
                         map='prontera', pos={'x': 1, 'y': 2}, dead=False,
                         running=None, behavior_v1=True))

    def lease(self, phase='controlled', **updates):
        now = time.time()
        data = dict(token='owner', started_at=now-1, deadline=now+60, phase=phase)
        data.update(updates)
        (self.directory / 'behavior-lease.json').write_text(json.dumps(data))

    def call(self, **message):
        return service.control_request(self.e, self.j, self.sent.append, self.directory, message)

    def motor(self):
        return self.call(op='run', skill='travel_to', params={'map':'prontera','r':0}, behavior_owner='owner')

    def test_status_hides_owned_motor_but_keeps_speech(self):
        self.lease()
        motor = self.motor()
        speech = self.call(op='run', skill='whisper', params={'to_name':'Tester','text':'Hello'})
        self.e.body(dict(type='telemetry',body_epoch='ep',ts=time.time(),map='prontera',pos={'x':1,'y':2},dead=False,running={'action_id':motor['action_id']},behavior_v1=True))
        status = self.call(op='status')
        self.assertIsNone(status['telemetry']['running'])
        self.assertIsNone(status['projection']['running']['value'])
        self.assertEqual(self.e.telemetry['running']['action_id'], motor['action_id'])
        self.assertEqual([a['action_id'] for a in status['actions']], [speech['action_id']])
        self.assertEqual(status['ownership']['phase'], 'controlled')
        self.assertNotIn('token', status['ownership'])
        self.assertEqual(len(self.call(op='status', behavior_owner='owner')['actions']), 2)
        with self.assertRaisesRegex(ValueError, 'BEHAVIOR_OWNER'):
            self.call(op='cancel', action_id=motor['action_id'])
        self.assertEqual(len(self.sent), 2)

    def test_expired_lease_blocks_dispatch_but_owner_can_cleanup(self):
        self.lease()
        motor = self.motor()
        self.lease(deadline=time.time()-0.1)
        for message in (dict(op='run',skill='rest',params={'hp_pct':85}),
                        dict(op='resend',action_id=motor['action_id'])):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, 'BEHAVIOR_OWNER'):
                    self.call(**message, behavior_owner='owner')
        with self.assertRaisesRegex(ValueError, 'BEHAVIOR_OWNER'):
            self.call(op='safe_stop')
        self.call(op='cancel', action_id=motor['action_id'], behavior_owner='owner')
        self.call(op='safe_stop', behavior_owner='owner')
        self.assertEqual([m['type'] for m in self.sent], ['skill_start','skill_cancel','safe_stop'])

    def test_dispatch_requires_owner_and_controlled_phase(self):
        for phase in ('acquiring','draining','blocked','controlled'):
            self.lease(phase)
            for token in (None, 'wrong'):
                with self.assertRaisesRegex(ValueError, 'BEHAVIOR_OWNER'):
                    self.call(op='run',skill='rest',params={'hp_pct':85},behavior_owner=token)
            if phase != 'controlled':
                with self.assertRaisesRegex(ValueError, 'BEHAVIOR_OWNER'):
                    self.motor()
        self.assertEqual(self.sent, [])

    def test_corrupt_lease_fails_closed_and_speech_is_independent(self):
        for data in ('broken', json.dumps(dict(token='owner',started_at=0,deadline=1201,phase='controlled'))):
            (self.directory / 'behavior-lease.json').write_text(data)
            with self.assertRaisesRegex(ValueError, 'BEHAVIOR_OWNER'):
                self.motor()
            with self.assertRaisesRegex(ValueError, 'BEHAVIOR_OWNER'):
                self.call(op='safe_stop')
        speech = self.call(op='run',skill='say',params={'text':'Hello'})
        self.assertEqual(speech['state'], 'dispatched')
        with self.assertRaisesRegex(ValueError, 'INVALID_TEXT'):
            self.call(op='run',skill='say',params={'text':'@die'})

    def test_owned_cancel_remains_protected_after_release_and_restart(self):
        self.lease()
        motor = self.motor()
        self.lease('released')
        self.e = Executor(self.j,'resident_a',150001,self.sent.append)
        with self.assertRaisesRegex(ValueError, 'BEHAVIOR_OWNER'):
            self.call(op='cancel', action_id=motor['action_id'])
        with self.assertRaisesRegex(ValueError, 'BEHAVIOR_OWNER'):
            self.call(op='safe_stop')
        self.call(op='cancel',action_id=motor['action_id'],behavior_owner='owner')

    def test_motor_trades_and_resend_fenced_without_owner(self):
        self.lease()
        for skill in ('travel_to','rest','hunt','respawn','buy_potions','sell_loot'):
            with self.subTest(skill=skill):
                with self.assertRaisesRegex(ValueError, 'BEHAVIOR_OWNER'):
                    self.call(op='run',skill=skill,params={})
        motor=self.motor()
        with self.assertRaisesRegex(ValueError, 'BEHAVIOR_OWNER'):
            self.call(op='resend',action_id=motor['action_id'])
        self.call(op='resend',action_id=motor['action_id'],behavior_owner='owner')
        self.lease('released')
        with self.assertRaisesRegex(ValueError, 'BEHAVIOR_OWNER'):
            self.call(op='resend',action_id=motor['action_id'],behavior_owner='owner')

    def test_no_lease_legacy_semantics(self):
        motor = self.call(op='run',skill='rest',params={'hp_pct':85})
        self.assertEqual(self.call(op='status')['actions'][0]['action_id'], motor['action_id'])
        self.call(op='cancel',action_id=motor['action_id'])
        self.call(op='resend',action_id=motor['action_id'])
        self.call(op='safe_stop')
        self.assertEqual(len(self.sent), 4)


class NativeSpeechTests(unittest.TestCase):
    native = test_behavior_native.BehaviorNativeTests.native

    def scenario(self, enabled=True):
        return self.native(r'''
BEGIN {package Commands; our @sent; sub run {push @sent,$_[0]} $INC{'Commands.pm'}=1;}
sub speech {my($id,$skill)=@_; residentBody::command({proto=>1,type=>'skill_start',action_id=>$id,idem_key=>$id,expect_epoch=>$epoch,skill=>$skill,params=>{text=>'Hello',to_name=>'Tester'},deadline_ts=>residentBody::time()+100});}
start('rest',{hp_pct=>90}); @AI::cleared=();
speech('say','say'); my $after_say=residentBody::telemetryFrame();
speech('pm','whisper'); speech('busy','say');
residentBody::command({proto=>1,type=>'skill_cancel',action_id=>'say'});
residentBody::privateMessageResult('packet_pre/private_message_sent',{type=>0});
my $after_pm=residentBody::telemetryFrame(); speech('pm','whisper');
open(my $ledger,'<',"$ARGV[1]/ledger.jsonl") or die $!; my @durable=map{JSON::PP::decode_json($_)}<$ledger>; close $ledger;
print JSON::PP::encode_json({frames=>\@frames,sent=>\@Commands::sent,after_say=>$after_say,after_pm=>$after_pm,cleared=>\@AI::cleared,ledger=>\@durable});
''', enabled=enabled)

    def test_v1_say_and_pm_preserve_motor_and_durable_results(self):
        result = self.scenario()
        self.assertEqual(result['sent'], ['c Hello','pm "Tester" Hello'])
        self.assertEqual(result['cleared'], [])
        for key in ('after_say','after_pm'):
            self.assertEqual(result[key]['running']['action_id'], 'test')
            self.assertEqual(result[key]['mode'], 'REST')
        results = {f[1]['action_id']:f[1] for f in result['frames'] if f[0]=='skill_result'}
        self.assertEqual(results['say']['code'], 'SPOKEN')
        self.assertEqual(results['pm']['code'], 'SENT')
        ledger = {a['action_id']:a['result'] for a in result['ledger']}
        self.assertEqual(ledger['pm']['action_id'], 'pm')
        self.assertEqual(ledger['say']['code'], 'SPOKEN')
        self.assertEqual(ledger['test']['code'], 'IN_PROGRESS')
        rejects = {f[1]['action_id']:f[1]['code'] for f in result['frames'] if f[0]=='skill_rejected'}
        self.assertEqual(rejects['busy'], 'BUSY')
        self.assertEqual(rejects['pm'], 'DUPLICATE')

    def test_pending_speech_cancel_and_timeout_never_stop_motor(self):
        result = self.native(r'''
BEGIN {package Commands; sub run {} $INC{'Commands.pm'}=1;}
my $clock=residentBody::time(); {no warnings 'redefine'; *residentBody::time=sub{$clock};}
my $pm={proto=>1,type=>'skill_start',action_id=>'pm',idem_key=>'pm',expect_epoch=>$epoch,skill=>'whisper',params=>{to_name=>'Tester',text=>'Hello'},deadline_ts=>$clock+10};
residentBody::command($pm); start('rest',{hp_pct=>90}); @AI::cleared=();
residentBody::command({proto=>1,type=>'skill_cancel',action_id=>'pm'});
residentBody::command({%$pm,action_id=>'timeout',idem_key=>'timeout'});
$clock+=11; residentBody::pollSkill();
print JSON::PP::encode_json({frames=>\@frames,cleared=>\@AI::cleared,telemetry=>residentBody::telemetryFrame(),ledger=>residentBody::helloLedger()});
''')
        self.assertEqual(result['cleared'], [])
        self.assertEqual(result['telemetry']['running']['action_id'], 'test')
        outcomes = {f[1]['action_id']: f[1]['code'] for f in result['frames'] if f[0]=='skill_result'}
        self.assertEqual(outcomes, {'pm':'CANCELLED','timeout':'TIMEOUT'})

    def test_legacy_motor_keeps_global_busy_semantics(self):
        result = self.scenario(enabled=False)
        self.assertEqual(result['sent'], [])
        self.assertEqual(result['after_pm']['running']['action_id'], 'test')
