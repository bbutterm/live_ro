import pathlib,subprocess,json,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
class BodyActionTest(unittest.TestCase):
 def test_route_reference_duplicate_and_cancel_without_second_dispatch(self):
  with tempfile.TemporaryDirectory() as d:
   code=r'''BEGIN{
package Globals;use Exporter 'import';our @EXPORT=qw($char $charID $accountID $field $net $messageSender %config);our($char,$charID,$accountID,$field,$net,$messageSender);our%config;$INC{'Globals.pm'}=1;
package Plugins;sub register{}sub addHooks{return 1}sub delHooks{};$INC{'Plugins.pm'}=1;
package Utils;use Exporter 'import';our @EXPORT_OK=qw(calcPosition);sub calcPosition{{x=>20,y=>25}};$INC{'Utils.pm'}=1;
package Network;sub IN_GAME(){5};$INC{'Network.pm'}=1;
package AI;sub clear{}sub action{'route'};$INC{'AI.pm'}=1;
package FakeTask;sub getStatus{$_[0]{status}}sub getError{undef}sub stop{$_[0]{status}=3}
package FakeChar;sub route{$_[0]{routes}++;$_[0]{last_x}=$_[2];$_[0]{task}=bless{status=>0},'FakeTask'}sub args{$_[0]{task}}
package FakeField;sub isWalkable{$_[1]!=0}
}
require $ARGV[0];$ENV{RESIDENT_RUN_DIR}=$ARGV[1];$ENV{RESIDENT_ESTOP}=$ARGV[1].'/ESTOP';$Globals::char=bless{hp=>40,hp_max=>40,pos_to=>{x=>20,y=>25}},'FakeChar';$Globals::field=bless{baseName=>'prontera'},'FakeField';
my@frames;{no warnings 'redefine';*residentBody::emit=sub{push@frames,[@_]};}
my$ep=residentBody::telemetryFrame()->{body_epoch};my$m={proto=>1,type=>'skill_start',action_id=>'first_route',idem_key=>'route-1',expect_epoch=>$ep,skill=>'travel_to',params=>{map=>'prontera',x=>150,y=>150,r=>0},deadline_ts=>Time::HiRes::time()+120};
residentBody::command($m);residentBody::command($m);residentBody::command({proto=>1,type=>'skill_cancel',action_id=>'first_route'});residentBody::command({%$m,idem_key=>'same-action-new-key'});
my$routes=$Globals::char->{routes};residentBody::command({%$m,action_id=>'map-only',idem_key=>'map-only',params=>{map=>'prt_fild08',r=>0}});my$coordinate_defined=defined$Globals::char->{last_x};residentBody::command({proto=>1,type=>'skill_cancel',action_id=>'map-only'});residentBody::command({%$m,action_id=>'deadline',idem_key=>'deadline'});residentBody::command({proto=>1,type=>'skill_cancel',action_id=>'deadline',reason=>'deadline'});for my$i(1..501){my$aid='bounded-'.$i;residentBody::command({%$m,action_id=>$aid,idem_key=>$aid});residentBody::command({proto=>1,type=>'skill_cancel',action_id=>$aid});}
print JSON::PP::encode_json({coordinate_defined=>$coordinate_defined,routes=>$routes,frames=>\@frames,hello=>residentBody::helloLedger(),final=>residentBody::telemetryFrame()});'''
   p=subprocess.run(['perl','-e',code,str(ROOT/'plugins/residentBody/residentBody.pl'),d],capture_output=True,text=True)
   self.assertEqual(p.returncode,0,'Body action handler missing or broken: '+p.stderr)
   r=json.loads(p.stdout);self.assertFalse(r['coordinate_defined']);self.assertEqual(len(pathlib.Path(d+'/ledger.jsonl').read_text().splitlines()),500);self.assertEqual(len(r['hello']),50);self.assertLess(len(json.dumps(r['hello'])),60000);self.assertEqual(r['routes'],1);self.assertEqual(r['final']['mode'],'IDLE_SAFE');self.assertIn('DUPLICATE',p.stdout);self.assertIn('CANCELLED',p.stdout);self.assertIn('TIMEOUT',p.stdout)
