"""Native call-seam regression for forced low-HP -> rest -> return failure.
Fixtures only; does not claim real game acceptance.
"""
import pathlib, subprocess, tempfile, unittest, json
ROOT=pathlib.Path(__file__).parents[1]
class RouteBoundaryTest(unittest.TestCase):
 def test_native_route_does_not_stop_outside_server_radius_or_resit_after_rest(self):
  with tempfile.TemporaryDirectory() as d:
   code=r'''BEGIN{
package Globals;use Exporter 'import';our @EXPORT=qw($char $charID $accountID $field $net $messageSender %config);our($char,$charID,$accountID,$field,$net,$messageSender);our%config;$INC{'Globals.pm'}=1;
package Plugins;sub register{}sub addHooks{return 1}sub delHooks{};$INC{'Plugins.pm'}=1;
package Utils;use Exporter 'import';our @EXPORT_OK=qw(calcPosition);sub calcPosition{{x=>128,y=>73}};$INC{'Utils.pm'}=1;
package Network;sub IN_GAME(){5};$INC{'Network.pm'}=1;
package AI;sub clear{}sub action{''};$INC{'AI.pm'}=1;
package FakeTask;
package FakeChar;sub route{my($self,$map,$x,$y,%args)=@_;$self->{route_args}=\%args;$self->{destination}=[$map,$x,$y]}sub args{bless{},'FakeTask'}
}
require $ARGV[0];$ENV{RESIDENT_RUN_DIR}=$ARGV[1];$ENV{RESIDENT_ESTOP}=$ARGV[1].'/ESTOP';$Globals::char=bless{hp=>638,hp_max=>795,sitting=>1,pos_to=>{x=>128,y=>73}},'FakeChar';$Globals::field={baseName=>'prt_in'};
my@frames;{no warnings 'redefine';*residentBody::emit=sub{push@frames,[@_]};}
my$ep=residentBody::telemetryFrame()->{body_epoch};
residentBody::command({proto=>1,type=>'skill_start',action_id=>'return-after-rest',idem_key=>'return-after-rest',expect_epoch=>$ep,skill=>'travel_to',params=>{map=>'prt_fild08',x=>170,y=>240,r=>3},deadline_ts=>Time::HiRes::time()+240});
print JSON::PP::encode_json({args=>$Globals::char->{route_args},destination=>$Globals::char->{destination},frames=>\@frames});'''
   p=subprocess.run(['perl','-e',code,str(ROOT/'plugins/residentBody/residentBody.pl'),d],capture_output=True,text=True)
   self.assertEqual(p.returncode,0,p.stderr);result=json.loads(p.stdout)
   self.assertEqual(result['destination'],['prt_fild08',170,240])
   self.assertEqual(result['args'].get('distFromGoal'),0,'Native path trimming can finish at radius+1; gateway contract must not be relaxed')
   self.assertEqual(result['args'].get('noSitAuto'),1,'Auto-sit must not hijack the post-rest route')
