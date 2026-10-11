"""Duo opt-in native seams; live evidence is a separate acceptance gate."""
import json
import unittest
from tests.test_behavior_native import BehaviorNativeTests

class DuoNativeTests(unittest.TestCase):
    native = BehaviorNativeTests.native
    setup = "$Globals::config{residentSupportTarget}='Borin'; $Globals::config{followTarget}='Borin'; $Globals::char->{party}={joined=>1,name=>'Duo',users=>{b=>{name=>'Borin',online=>1,hp=>80,hp_max=>100}}};"

    def test_support_follows_only_during_hunt_and_does_not_attack(self):
        result=self.native(self.setup+" start('hunt',{map=>'prt_fild08',duration=>30}); residentBody::applyMode(); residentBody::pollSkill(); my %during=%Globals::config; residentBody::command({proto=>1,type=>'safe_stop'}); print JSON::PP::encode_json({during=>\\%during,after=>\\%Globals::config,t=>residentBody::telemetryFrame()});",enabled=False)
        self.assertEqual(result['during']['follow'],1)
        self.assertEqual(result['during']['attackAuto'],-1)
        self.assertEqual(result['during']['route_randomWalk'],0)
        self.assertEqual(result['after']['follow'],0)

    def test_opt_in_needs_real_party_membership(self):
        result=self.native(self.setup+" $Globals::char->{party}{joined}=0; start('hunt',{map=>'prt_fild08',duration=>30}); print JSON::PP::encode_json(\\%Globals::config);",enabled=False)
        self.assertEqual(result['follow'],0)

    def test_behavior_v1_still_disables_cooperation(self):
        result=self.native(self.setup+" start('hunt',{map=>'prt_fild08',duration=>30}); print JSON::PP::encode_json(\\%Globals::config);",enabled=True)
        self.assertEqual(result['follow'],0)
        self.assertEqual(result['attackAuto'],-1)

    def test_duo_speech_does_not_cancel_native_support_motor(self):
        result=self.native(self.setup+" $Globals::config{residentDuo}=1;start('hunt',{map=>'prt_fild08',duration=>30});residentBody::command({proto=>1,type=>'skill_start',action_id=>'duo-speech',idem_key=>'duo-speech',expect_epoch=>$epoch,skill=>'say',params=>{text=>'Tactic'},deadline_ts=>residentBody::time()+10});print JSON::PP::encode_json({t=>residentBody::telemetryFrame(),sent=>\\@Commands::sent});",enabled=False)
        self.assertEqual(result['sent'],['c Tactic'])
        self.assertEqual(result['t']['mode'],'HUNT')
        self.assertEqual(result['t']['running']['action_id'],'test')

    def test_hunt_never_uses_cross_map_shortcuts(self):
        result=self.native("start('hunt',{map=>'prt_fild08',duration=>30}); print JSON::PP::encode_json(\\%Globals::config);",enabled=False)
        self.assertEqual(result['route_randomWalk'],2)

    def test_native_party_and_class_frames_are_truthful(self):
        result=self.native(self.setup+" $Globals::char->{jobID}=4; print JSON::PP::encode_json(residentBody::telemetryFrame());",enabled=False)
        self.assertEqual(result['class_id'],4)
        self.assertEqual(result['party']['name'],'Duo')
        self.assertEqual(result['party']['members'][0]['hp'],80)
        self.assertNotIn('healed',result['party'])
