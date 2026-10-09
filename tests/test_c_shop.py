import tempfile,subprocess,json,pathlib,unittest
ROOT=pathlib.Path(__file__).parents[1]
class ShopTest(unittest.TestCase):
 def test_potions_by_id_sale_whitelist_and_single_commit(self):
  with tempfile.TemporaryDirectory() as d:
   code=r"""BEGIN{
package Globals;use Exporter 'import';our @EXPORT=qw($char $charID $accountID $field $net $messageSender %config);our($char,$charID,$accountID,$field,$net,$messageSender);our%config;$INC{'Globals.pm'}=1;
package Plugins;sub register{}sub addHooks{return 1}sub delHooks{};$INC{'Plugins.pm'}=1;
package Utils;use Exporter 'import';our @EXPORT_OK=qw(calcPosition);sub calcPosition{{x=>126,y=>76}};$INC{'Utils.pm'}=1;
package Network;sub IN_GAME(){5};$INC{'Network.pm'}=1;
package AI;sub clear{};$INC{'AI.pm'}=1;
package FakeList;sub getItems{$_[0]{items}}sub clear{$_[0]{items}=[]}
package FakeChar;sub inventory{$_[0]{bag}}
package FakeSender;sub sendTalk{$_[0]{talk}++}sub sendNPCBuySellList{$_[0]{choice}=$_[2]}sub sendBuyBulk{$_[0]{buy}=$_[1];$_[0]{buys}++}sub sendSellBulk{$_[0]{sell}=$_[1];$_[0]{sells}++}
package Misc;sub completeNpcBuy{$Globals::messageSender->sendBuyBulk($_[0])}sub completeNpcSell{$Globals::messageSender->sendSellBulk($_[0])};$INC{'Misc.pm'}=1;
}
require $ARGV[0];$ENV{RESIDENT_RUN_DIR}=$ARGV[1];$ENV{RESIDENT_ESTOP}=$ARGV[1].'/ESTOP';my$pot={ID=>'p1',nameID=>501,amount=>1,equipped=>0};my$loot={ID=>'l1',nameID=>909,amount=>2,equipped=>0};my$knife={ID=>'k1',nameID=>1201,amount=>1,equipped=>256};my$bag=bless{items=>[$pot,$loot,$knife]},'FakeList';$Globals::char=bless{hp=>40,hp_max=>40,zeny=>450,weight=>100,weight_max=>2000,bag=>$bag},'FakeChar';$Globals::field={baseName=>'prt_in'};$Globals::messageSender=bless{},'FakeSender';$Globals::npcsList=bless{items=>[{ID=>'n1',name=>'Tool Dealer#prt1',pos=>{x=>126,y=>76}}]},'FakeList';$Globals::storeList=bless{items=>[]},'FakeList';my@frames;{no warnings 'redefine';*residentBody::emit=sub{push@frames,[@_]};}my$ep=residentBody::telemetryFrame()->{body_epoch};my$m={proto=>1,type=>'skill_start',action_id=>'buy',idem_key=>'buy',expect_epoch=>$ep,skill=>'buy_potions',params=>{item_id=>501,stock_goal=>8,max_zeny=>100},deadline_ts=>Time::HiRes::time()+60};residentBody::command($m);$Globals::ai_v{npc_talk}{talk}='buy_or_sell';residentBody::pollSkill();$Globals::storeList->{items}=[{nameID=>501,price=>10}];residentBody::pollSkill();residentBody::pollSkill();$pot->{amount}=8;residentBody::pollSkill();residentBody::command({%$m,action_id=>'sell',idem_key=>'sell',skill=>'sell_loot',params=>{}});$Globals::ai_v{npc_talk}{talk}='buy_or_sell';residentBody::pollSkill();$Globals::ai_v{npc_talk}{talk}='sell';residentBody::pollSkill();$loot->{amount}=0;residentBody::pollSkill();print JSON::PP::encode_json({frames=>\@frames,buy=>$Globals::messageSender->{buy},sell=>$Globals::messageSender->{sell},buys=>$Globals::messageSender->{buys},sells=>$Globals::messageSender->{sells}});"""
   p=subprocess.run(['perl','-e',code,str(ROOT/'plugins/residentBody/residentBody.pl'),d],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout);self.assertEqual(r['buys'],1);self.assertEqual(r['sells'],1);self.assertEqual(r['buy'],[{'itemID':501,'amount':7}]);self.assertEqual(r['sell'],[{'ID':'l1','amount':2}]);self.assertIn('BOUGHT',p.stdout);self.assertIn('SOLD',p.stdout)
