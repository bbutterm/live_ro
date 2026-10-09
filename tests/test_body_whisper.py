import json,pathlib,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
class WhisperTest(unittest.TestCase):
 def test_native_pm_result_not_send_attempt_decides_outcome(self):
  code=r'''BEGIN{
package Globals;use Exporter 'import';our @EXPORT=qw($char $field $net $charID $accountID %config);our($char,$field,$net,$charID,$accountID);our%config;$INC{'Globals.pm'}=1;
package Plugins;sub register{}sub addHooks{}sub delHooks{};$INC{'Plugins.pm'}=1;
package Utils;use Exporter 'import';our@EXPORT_OK=qw(calcPosition);sub calcPosition{{x=>20,y=>25}};$INC{'Utils.pm'}=1;
package Network;sub IN_GAME(){5};$INC{'Network.pm'}=1;
package Commands;our@sent;sub run{push@sent,$_[0]};$INC{'Commands.pm'}=1;
}
require $ARGV[0];$ENV{RESIDENT_RUN_DIR}=$ARGV[1];$ENV{RESIDENT_ESTOP}=$ARGV[1].'/ESTOP';$Globals::char={hp=>40,pos_to=>{x=>20,y=>25}};$Globals::field={baseName=>'prontera'};my@events;{no warnings 'redefine';*residentBody::emit=sub{push@events,[@_]};}
my$ep=residentBody::telemetryFrame()->{body_epoch};
for my$n(0..3){residentBody::command({proto=>1,type=>'skill_start',action_id=>"pm$n",idem_key=>"pm$n",expect_epoch=>$ep,skill=>'whisper',params=>{to_name=>'Tester',text=>'B acceptance probe'},deadline_ts=>Time::HiRes::time()+20});residentBody::privateMessageResult('packet_pre/private_message_sent',{type=>$n});}
print JSON::PP::encode_json({events=>\@events,sent=>\@Commands::sent});'''
  with tempfile.TemporaryDirectory() as d:
   p=subprocess.run(['perl','-e',code,str(ROOT/'plugins/residentBody/residentBody.pl'),d],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout);self.assertEqual(len(r['sent']),4)
   results=[ev[1]['code'] for ev in r['events'] if ev[0]=='skill_result'];self.assertEqual(results,['SENT','OFFLINE','IGNORED','REFUSED'])
