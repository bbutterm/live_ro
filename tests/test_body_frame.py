import pathlib,subprocess,unittest
ROOT=pathlib.Path(__file__).parents[1]
class BodyFrameTest(unittest.TestCase):
 def test_actual_perl_builder_uses_interpolated_position_not_destination(self):
  plugin=ROOT/'plugins/residentBody/residentBody.pl';self.assertTrue(plugin.exists(),'residentBody missing')
  harness=r'''BEGIN {
   package Globals; use Exporter 'import'; our @EXPORT=qw($char $charID $accountID $field $net %config);our($char,$charID,$accountID,$field,$net);our %config;$INC{'Globals.pm'}=1;
   package Plugins;sub register{} sub addHooks{return 1} sub delHooks{};$INC{'Plugins.pm'}=1;
   package Utils;use Exporter 'import';our @EXPORT_OK=qw(calcPosition);sub calcPosition{return {x=>11,y=>20}};$INC{'Utils.pm'}=1;
   package FakePlayers;sub getItems{[{name=>'Tester',pos=>{x=>11,y=>20}}]}
   package Network;sub IN_GAME(){5};$INC{'Network.pm'}=1;
  }
  require $ARGV[0];$Globals::playersList=bless{},'FakePlayers'; $Globals::char={pos=>{x=>10,y=>20},pos_to=>{x=>99,y=>88},hp=>40,hp_max=>40};$Globals::field={baseName=>'prontera'};
  print JSON::PP::encode_json(residentBody::telemetryFrame());
  '''
  import json
  p=subprocess.run(['perl','-e',harness,str(plugin)],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr)
  frame=json.loads(p.stdout);self.assertEqual(frame['pos'],{'x':11,'y':20});self.assertEqual(frame['dest'],{'x':99,'y':88});self.assertEqual(frame['hp'],40);self.assertEqual(frame['mode'],'IDLE_SAFE');self.assertEqual(frame['nearby_players'][0]['name'],'Tester');self.assertEqual(frame['nearby_players'][0]['pos'],{'x':11,'y':20})
