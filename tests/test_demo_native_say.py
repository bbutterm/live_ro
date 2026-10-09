import unittest,tempfile,subprocess,json,pathlib
class NativeSayTest(unittest.TestCase):
 def test_native_public_chat_once_and_no_gm_command(self):
  with tempfile.TemporaryDirectory() as d:
   code=r"""BEGIN{
package Globals;use Exporter 'import';our @EXPORT=qw($char $field $net $charID $accountID %config);our($char,$field,$net,$charID,$accountID);our%config;$INC{'Globals.pm'}=1;
package Plugins;sub register{}sub addHooks{};$INC{'Plugins.pm'}=1;
package Utils;use Exporter 'import';our @EXPORT_OK=qw(calcPosition);sub calcPosition{{x=>150,y=>150}};$INC{'Utils.pm'}=1;
package Network;sub IN_GAME(){5};$INC{'Network.pm'}=1;
package AI;sub action{''};$INC{'AI.pm'}=1;
package Commands;our@sent;sub run{push@sent,$_[0]};$INC{'Commands.pm'}=1;
}
require $ARGV[0];$ENV{RESIDENT_RUN_DIR}=$ARGV[1];$Globals::char={hp=>100,pos_to=>{x=>150,y=>150}};$Globals::field={baseName=>'prontera'};
my@frames;{no warnings 'redefine';*residentBody::emit=sub{push@frames,[@_]};}
my$ep=residentBody::telemetryFrame()->{body_epoch};my$m={proto=>1,type=>'skill_start',action_id=>'say',idem_key=>'say',expect_epoch=>$ep,skill=>'say',params=>{text=>'Hello Mira'},deadline_ts=>Time::HiRes::time()+30};residentBody::command($m);residentBody::command($m);residentBody::command({%$m,action_id=>'bad',idem_key=>'bad',params=>{text=>'@die'}});print JSON::PP::encode_json({sent=>\@Commands::sent,frames=>\@frames});"""
   p=subprocess.run(['perl','-e',code,str(pathlib.Path(__file__).parents[1]/'plugins/residentBody/residentBody.pl'),d],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout);self.assertEqual(r['sent'],['c Hello Mira']);self.assertIn('SPOKEN',p.stdout);self.assertIn('INVALID',p.stdout)
