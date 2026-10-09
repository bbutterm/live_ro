import tempfile,time,unittest,json,pathlib,subprocess
ROOT=pathlib.Path(__file__).parents[1]
class MaintenanceTest(unittest.TestCase):
 def test_native_rest_requires_actual_hp_and_respawn_uses_native_packet(self):
  with tempfile.TemporaryDirectory() as d:
   code=r'''BEGIN{
package Globals;use Exporter 'import';our @EXPORT=qw($char $charID $accountID $field $net $messageSender %config);our($char,$charID,$accountID,$field,$net,$messageSender);our%config;$INC{'Globals.pm'}=1;
package Plugins;sub register{}sub addHooks{return 1}sub delHooks{};$INC{'Plugins.pm'}=1;
package Utils;use Exporter 'import';our @EXPORT_OK=qw(calcPosition);sub calcPosition{{x=>20,y=>25}};$INC{'Utils.pm'}=1;
package Network;sub IN_GAME(){5};$INC{'Network.pm'}=1;
package AI;sub clear{}sub action{''};$INC{'AI.pm'}=1;
package FakeChar;sub sendSit{$_[0]{sitting}=1}sub sendStand{$_[0]{sitting}=0}
package FakeSender;sub sendRestart{$_[0]{restart}=$_[1]+1}
}
require $ARGV[0];$ENV{RESIDENT_RUN_DIR}=$ARGV[1];$ENV{RESIDENT_ESTOP}=$ARGV[1].'/ESTOP';$Globals::char=bless{hp=>10,hp_max=>40,weight=>0,weight_max=>1000,skills=>{NV_BASIC=>{lv=>3}},pos_to=>{x=>20,y=>25}},'FakeChar';$Globals::field={baseName=>'prontera'};$Globals::messageSender=bless{},'FakeSender';
my@frames;{no warnings 'redefine';*residentBody::emit=sub{push@frames,[@_]};}
my$ep=residentBody::telemetryFrame()->{body_epoch};my$m={proto=>1,type=>'skill_start',action_id=>'rest',idem_key=>'rest',expect_epoch=>$ep,skill=>'rest',params=>{hp_pct=>80},deadline_ts=>Time::HiRes::time()+60};residentBody::command($m);residentBody::pollSkill();my$start=residentBody::telemetryFrame();$Globals::char->{hp}=35;residentBody::pollSkill();my$done=residentBody::telemetryFrame();$Globals::char->{hp}=0;$Globals::char->{dead}=1;residentBody::command({%$m,action_id=>'respawn',idem_key=>'respawn',skill=>'respawn',params=>{}});my$restart=$Globals::messageSender->{restart};$Globals::char->{hp}=20;$Globals::char->{dead}=0;residentBody::pollSkill();print JSON::PP::encode_json({start=>$start,done=>$done,restart=>$restart,frames=>\@frames});'''
   p=subprocess.run(['perl','-e',code,str(ROOT/'plugins/residentBody/residentBody.pl'),d],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout);self.assertEqual(r['start']['mode'],'REST');self.assertTrue(r['start']['sitting']);self.assertEqual(r['done']['mode'],'IDLE_SAFE');self.assertEqual(r['restart'],1);self.assertIn('RESTED',p.stdout);self.assertIn('RESPAWNED',p.stdout)
