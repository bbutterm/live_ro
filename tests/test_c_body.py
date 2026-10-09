import pathlib,subprocess,tempfile,unittest,json
ROOT=pathlib.Path(__file__).parents[1]
class CBodyTest(unittest.TestCase):
 def test_hunt_memory_only_mode_and_cancel(self):
  with tempfile.TemporaryDirectory() as d:
   code=r'''BEGIN{
package Globals;use Exporter 'import';our @EXPORT=qw($char $charID $accountID $field $net $messageSender %config);our($char,$charID,$accountID,$field,$net,$messageSender);our%config;$INC{'Globals.pm'}=1;
package Plugins;sub register{}sub addHooks{return 1}sub delHooks{};$INC{'Plugins.pm'}=1;
package Utils;use Exporter 'import';our @EXPORT_OK=qw(calcPosition);sub calcPosition{{x=>20,y=>25}};$INC{'Utils.pm'}=1;
package Network;sub IN_GAME(){5};$INC{'Network.pm'}=1;
package AI;sub clear{}sub action{''}sub inQueue{0};$INC{'AI.pm'}=1;
package FakeList;sub getItems{[{ID=>'m1',nameID=>1002}]}
package FakeChar;sub attack{$_[0]{attacks}++}
}
require $ARGV[0];$ENV{RESIDENT_RUN_DIR}=$ARGV[1];$ENV{RESIDENT_ESTOP}=$ARGV[1].'/ESTOP';$Globals::char=bless{hp=>40,hp_max=>40,pos_to=>{x=>20,y=>25}},'FakeChar';$Globals::monstersList=bless{},'FakeList';$Globals::field={baseName=>'prt_fild08'};
my@frames;{no warnings 'redefine';*residentBody::emit=sub{push@frames,[@_]};}
my$ep=residentBody::telemetryFrame()->{body_epoch};my$m={proto=>1,type=>'skill_start',action_id=>'hunt',idem_key=>'hunt',expect_epoch=>$ep,skill=>'hunt',params=>{map=>'prt_fild08',duration=>30,min_kills=>1},deadline_ts=>Time::HiRes::time()+60};
residentBody::command($m);residentBody::pollSkill();my$first=residentBody::telemetryFrame();residentBody::applyMode();my$rw=$Globals::config{route_randomWalk};residentBody::command({proto=>1,type=>'skill_cancel',action_id=>'hunt'});my$last=residentBody::telemetryFrame();
print JSON::PP::encode_json({first=>$first,last=>$last,rw=>$rw,attacks=>$Globals::char->{attacks},idle_attack=>$Globals::config{attackAuto},frames=>\@frames});'''
   p=subprocess.run(['perl','-e',code,str(ROOT/'plugins/residentBody/residentBody.pl'),d],capture_output=True,text=True)
   self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout);self.assertEqual(r['first']['mode'],'HUNT');self.assertEqual(r['first']['ai_action'],'');self.assertEqual(r['rw'],1);self.assertEqual(r['attacks'],1);self.assertEqual(r['last']['mode'],'IDLE_SAFE');self.assertIn('CANCELLED',p.stdout);self.assertEqual(r['idle_attack'],-1)
