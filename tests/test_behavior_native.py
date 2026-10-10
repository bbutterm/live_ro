"""Offline native seams only; never loads the live body or changes services."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/residentBody/residentBody.pl'


class BehaviorNativeTests(unittest.TestCase):
    def native(self, body, enabled=True):
        prelude = r'''BEGIN {
package Globals; use Exporter 'import'; our @EXPORT=qw($char $charID $accountID $field $net %config); our($char,$charID,$accountID,$field,$net,$messageSender,$monstersList,$playersList); our %config; $INC{'Globals.pm'}=1;
package Plugins; sub register{} sub addHooks{1} sub delHooks{} $INC{'Plugins.pm'}=1;
package Utils; use Exporter 'import'; our @EXPORT_OK=qw(calcPosition); sub calcPosition{$_[0]{pos_to}//$_[0]{pos}} $INC{'Utils.pm'}=1;
package Network; sub IN_GAME(){5} $INC{'Network.pm'}=1;
package AI; our @cleared; sub clear{push @cleared,@_} sub action{''} sub inQueue{0} $INC{'AI.pm'}=1;
package List; sub getItems{$_[0]{items}} sub clear{}
package Char; sub inventory{bless{items=>$_[0]{items}//[]},'List'} sub sendSit{$_[0]{sitting}=1} sub sendStand{die 'unexpected stand'} sub attack{die 'unexpected attack'}
package Sender; sub sendMove{} sub sendTalk{} sub sendNPCBuySellList{} sub sendAddSkillPoint{die 'unexpected learning'} sub sendRestart{die 'unexpected revive'}
package Misc; our @sold; our @bought; sub completeNpcSell{@sold=@{$_[0]}} sub completeNpcBuy{@bought=@{$_[0]}} $INC{'Misc.pm'}=1;
}
require $ARGV[0]; $ENV{RESIDENT_RUN_DIR}=$ARGV[1]; $ENV{RESIDENT_ESTOP}="$ARGV[1]/ESTOP";
$Globals::char=bless{hp=>90,hp_max=>100,sp=>10,sp_max=>20,lv=>20,lv_job=>10,points_skill=>4,zeny=>420,weight=>0,weight_max=>1000,pos=>{x=>1,y=>2},pos_to=>{x=>1,y=>2},skills=>{NV_BASIC=>{lv=>3},AL_HEAL=>{lv=>0}}},'Char';
$Globals::field={baseName=>'prt_fild08'}; $Globals::messageSender=bless{},'Sender';
$Globals::monstersList=bless{items=>[]},'List'; $Globals::playersList=bless{items=>[]},'List';
my @frames; {no warnings 'redefine'; *residentBody::emit=sub{push @frames,[@_]};}
my $epoch=residentBody::telemetryFrame()->{body_epoch};
sub start {my($skill,$params)=@_; residentBody::command({proto=>1,type=>'skill_start',action_id=>'test',idem_key=>'test',expect_epoch=>$epoch,skill=>$skill,params=>$params,deadline_ts=>residentBody::time()+100});}
'''
        with tempfile.TemporaryDirectory() as directory:
            if enabled:
                Path(directory, 'BEHAVIOR_V1').touch()
            result = subprocess.run(['perl', '-e', prelude + body, str(PLUGIN), directory], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_perl_compile_against_actual_openkore(self):
        kore = Path('/root/ragnarok/repos/openkore')
        if not (kore / 'src/Globals.pm').exists():
            self.skipTest('actual OpenKore checkout not available')
        result = subprocess.run(['perl', '-I'+str(kore/'src'), '-I'+str(kore/'src/deps'), '-c', str(PLUGIN)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('syntax OK', result.stderr)

    def test_real_telemetry_unknown_actor_list_is_null(self):
        result = self.native("$Globals::monstersList=undef; print JSON::PP::encode_json(residentBody::telemetryFrame());")
        self.assertEqual(result.get('job_level'), 10)
        self.assertEqual(result.get('free_skill_points'), 4)
        self.assertIn('near_monsters', result)
        self.assertIsNone(result['near_monsters'])
        self.assertEqual(result['learned_skills']['AL_HEAL'], 0)
        self.assertIn('online_minutes', result)
        self.assertIsNone(result['online_minutes'])

    def test_real_actor_positions_keep_legacy_players(self):
        result = self.native("$Globals::playersList->{items}=[{name=>'Mira',pos=>{x=>3,y=>4}}]; $Globals::monstersList->{items}=[{ID=>pack('V',123),nameID=>1002,pos=>{x=>5,y=>6}}]; print JSON::PP::encode_json(residentBody::telemetryFrame());")
        self.assertEqual(result['near_players'][0], {'name':'Mira','x':3,'y':4})
        self.assertEqual(result['nearby_players'][0]['pos'], {'x':3,'y':4})
        self.assertEqual(result['near_monsters'][0], {'id':'7b000000','nameID':1002,'x':5,'y':6})

    def test_hunt_safety_stops_at_55_and_unknown_monsters(self):
        for setup, expected in [("$Globals::char->{hp}=54;", 'NEEDS_REST'), ("$Globals::monstersList->{items}=[{ID=>'bad',nameID=>1008,pos=>{x=>3,y=>3}}];", 'UNSAFE_MONSTER'), ("$Globals::monstersList=undef;", 'UNSAFE_MONSTER')]:
            with self.subTest(expected=expected):
                result = self.native("start('hunt',{map=>'prt_fild08',duration=>30});" + setup + " residentBody::pollSkill(); print JSON::PP::encode_json({frames=>\\@frames,cleared=>\\@AI::cleared});")
                self.assertEqual(result['frames'][-1][1]['code'], expected)
                self.assertIn('attack', result['cleared'])

    def test_rest_does_not_learn_or_stand_and_requires_sitting(self):
        result = self.native("$Globals::char->{skills}{NV_BASIC}{lv}=2; start('rest',{hp_pct=>90}); residentBody::pollSkill(); print JSON::PP::encode_json(\\@frames);")
        self.assertEqual(result[-1][1]['code'], 'NEED_BASIC_SKILL')
        result = self.native("start('rest',{hp_pct=>90}); residentBody::pollSkill(); residentBody::pollSkill(); print JSON::PP::encode_json({frames=>\\@frames,sitting=>$Globals::char->{sitting}});")
        self.assertEqual(result['frames'][-1][1]['code'], 'RESTED')
        self.assertTrue(result['sitting'])

    def test_v1_revive_is_not_supported(self):
        result = self.native("$Globals::char->{hp}=0; start('respawn',{}); print JSON::PP::encode_json(\\@frames);")
        self.assertEqual(result[-1][1]['code'], 'UNSUPPORTED')

    def test_automatic_consumption_config_is_opt_in_and_restored(self):
        result = self.native("$Globals::config{useSelf_item_0}='Red Potion'; $Globals::config{useSelf_skill_0}='Heal'; $Globals::config{partySkill_0}='Heal'; $Globals::config{skillsAddAuto}=1; residentBody::applyMode(); my %on=%Globals::config; unlink \"$ARGV[1]/BEHAVIOR_V1\"; residentBody::applyMode(); print JSON::PP::encode_json({on=>\\%on,off=>\\%Globals::config});")
        for key in ['useSelf_item_0','useSelf_skill_0','partySkill_0','skillsAddAuto']:
            self.assertFalse(result['on'][key])
        self.assertEqual(result['off']['useSelf_item_0'], 'Red Potion')
        self.assertEqual(result['off']['useSelf_skill_0'], 'Heal')
        self.assertEqual(result['on']['dcOnDeath'], -1)
        self.assertIn("Plugins::callHook('configModify',{key=>'skillsAddAuto',val=>0})", PLUGIN.read_text())

    def test_purchase_uses_actual_quote_and_commits_once(self):
        setup = "$Globals::field->{baseName}='prt_in'; $Globals::char->{pos_to}={x=>126,y=>76}; $Globals::npcsList=bless{items=>[{ID=>'npc',name=>'Tool Dealer#test',pos=>{x=>126,y=>76}}]},'List'; $Globals::storeList=bless{items=>[{nameID=>501,price=>50}]},'List';"
        # clear() is a fixture no-op, preserving the subsequent server quote fixture.
        result = self.native(setup + "start('buy_potions',{item_id=>501,stock_goal=>8,max_zeny=>400}); $Globals::ai_v{npc_talk}{talk}='buy_or_sell'; residentBody::pollSkill(); residentBody::pollSkill(); residentBody::pollSkill(); print JSON::PP::encode_json(\\@Misc::bought);")
        self.assertEqual(result, [{'itemID':501,'amount':8}])
        result = self.native(setup + "$Globals::char->{zeny}=399; start('buy_potions',{item_id=>501,stock_goal=>8,max_zeny=>400}); $Globals::ai_v{npc_talk}{talk}='buy_or_sell'; residentBody::pollSkill(); residentBody::pollSkill(); print JSON::PP::encode_json({frames=>\\@frames,bought=>\\@Misc::bought});")
        self.assertEqual(result['frames'][-1][1]['code'], 'BUDGET')
        self.assertEqual(result['bought'], [])
        result = self.native(setup + "delete $Globals::storeList->{items}[0]{price}; start('buy_potions',{item_id=>501,stock_goal=>8,max_zeny=>400}); $Globals::ai_v{npc_talk}{talk}='buy_or_sell'; residentBody::pollSkill(); residentBody::pollSkill(); print JSON::PP::encode_json({frames=>\\@frames,bought=>\\@Misc::bought});")
        self.assertEqual(result['frames'][-1][1]['code'], 'INVALID_QUOTE')
        self.assertEqual(result['bought'], [])

    def test_partial_sale_commit_is_one_shot(self):
        result = self.native("$Globals::field->{baseName}='prt_in'; $Globals::char->{pos_to}={x=>126,y=>76}; $Globals::char->{items}=[{ID=>'stack',nameID=>909,amount=>5}]; $Globals::npcsList=bless{items=>[{ID=>'npc',name=>'Tool Dealer#test',pos=>{x=>126,y=>76}}]},'List'; start('sell_loot',{items=>{'909'=>2}}); $Globals::ai_v{npc_talk}{talk}='buy_or_sell'; residentBody::pollSkill(); $Globals::ai_v{npc_talk}{talk}='sell'; residentBody::pollSkill(); residentBody::pollSkill(); $Globals::char->{items}[0]{amount}=3; residentBody::pollSkill(); print JSON::PP::encode_json({frames=>\\@frames,sold=>\\@Misc::sold});")
        self.assertEqual(result['sold'], [{'ID':'stack','amount':2}])
        self.assertEqual(result['frames'][-1][1]['code'], 'SOLD')


    def test_explicit_sales_are_partial_bounded_and_whitelisted(self):
        result = self.native("print JSON::PP::encode_json([residentBody::salePlan({items=>{'909'=>2}},[{ID=>'a',nameID=>909,amount=>5},{ID=>'b',nameID=>713,amount=>3}])]);")
        self.assertEqual(result[0], [{'ID':'a','amount':2}])
        self.assertEqual(result[1], {'909':2})
        for params in ["{}", "{items=>{}}", "{items=>{'914'=>2}}", "{items=>{'909'=>0}}", "{items=>{'909'=>1001}}", "{items=>{'909'=>2.5}}"]:
            result = self.native("start('sell_loot',"+params+"); print JSON::PP::encode_json(\\@frames);")
            self.assertEqual(result[-1][1]['code'], 'INVALID')
        result = self.native("print JSON::PP::encode_json([residentBody::salePlan({items=>{'909'=>6}},[{ID=>'a',nameID=>909,amount=>5}])]);")
        self.assertIsNone(result[0])

    def test_legacy_sale_sells_junk_by_type_and_keeps_gear_potions_cards(self):
        # E05: the old fixed list could not sell what actually filled the bag.
        bag = ("[{ID=>'a',nameID=>909,amount=>12,type=>3},{ID=>'b',nameID=>1201,amount=>1,type=>5},{ID=>'c',nameID=>2301,amount=>1,type=>4,equipped=>2},"
               "{ID=>'d',nameID=>501,amount=>9,type=>0},{ID=>'e',nameID=>512,amount=>4,type=>0},{ID=>'f',nameID=>4001,amount=>1,type=>6},"
               "{ID=>'g',nameID=>713,amount=>3,type=>3},{ID=>'h',nameID=>717,amount=>2,type=>3},{ID=>'i',nameID=>1750,amount=>50,type=>10}]")
        result = self.native("$Globals::field->{baseName}='prt_in'; $Globals::char->{pos_to}={x=>126,y=>76}; $Globals::char->{items}=" + bag + "; $Globals::npcsList=bless{items=>[{ID=>'npc',name=>'Tool Dealer#test',pos=>{x=>126,y=>76}}]},'List'; my $t=residentBody::telemetryFrame(); start('sell_loot',{}); $Globals::ai_v{npc_talk}{talk}='buy_or_sell'; residentBody::pollSkill(); $Globals::ai_v{npc_talk}{talk}='sell'; residentBody::pollSkill(); my %gone=map{$_->{ID}=>1}@Misc::sold; $Globals::char->{items}=[grep{!$gone{$_->{ID}}}@{$Globals::char->{items}}]; residentBody::pollSkill(); print JSON::PP::encode_json({sellable=>$t->{sellable},sold=>[sort map{$_->{ID}}@Misc::sold],frames=>\\@frames});", enabled=False)
        self.assertEqual(result['sellable'], {'909':12,'1201':1,'713':3})
        self.assertEqual(result['sold'], ['a','b','g'])
        self.assertEqual(result['frames'][-1][1]['code'], 'SOLD')

    def test_native_monster_frame_passes_v1_contract_through_bridge(self):
        # E01: the real frame carries {id: GID hex, nameID, x, y}; v1 accepts exactly {id, x, y}.
        from packages.resident_behavior.bridge import snapshot_from_status
        from packages.resident_behavior.contracts import ContractError, validate_telemetry
        frame = self.native("$Globals::monstersList->{items}=[{ID=>pack('V',110001),nameID=>1002,pos=>{x=>5,y=>6}},{ID=>pack('V',110002),nameID=>1063,pos=>{x=>7,y=>8}}]; print JSON::PP::encode_json(residentBody::telemetryFrame());")
        frame.update(ts=1791590400.0, map='prt_fild08')
        snapshot = snapshot_from_status({'telemetry': frame}, 'resident_a', 1)
        self.assertEqual(snapshot['near_monsters'], [{'id':'1002','x':5,'y':6}, {'id':'1063','x':7,'y':8}])
        validate_telemetry(snapshot)
        raw = dict(snapshot, near_monsters=frame['near_monsters'])
        with self.assertRaisesRegex(ContractError, 'invalid_keys'):
            validate_telemetry(raw)

    def test_buy_stock_and_budget_validation_v1_legacy(self):
        for enabled, stock, budget, code in [(True,8,400,'WRONG_MAP'),(True,9,400,'INVALID'),(True,8,401,'INVALID'),(True,8,1.5,'INVALID'),(False,20,300,'WRONG_MAP'),(False,8,400,'INVALID')]:
            result = self.native("start('buy_potions',{item_id=>501,stock_goal=>%s,max_zeny=>%s}); print JSON::PP::encode_json(\\@frames);"%(stock,budget), enabled)
            self.assertEqual(result[-1][1]['code'], code)

    def test_observed_connection_uptime_resets_on_disconnect(self):
        result = self.native("{no warnings 'redefine'; *FakeNet::getState=sub{$_[0]{state}};} $Globals::net=bless{state=>5},'FakeNet'; $ENV{RESIDENT_ID}='fixture'; $ENV{RESIDENT_SOCKET}='/nonexistent/fixture.sock'; my $clock=1000; {no warnings 'redefine'; *residentBody::time=sub{$clock};} residentBody::tick(); $clock=1120; residentBody::tick(); my $up=residentBody::telemetryFrame()->{online_minutes}; $Globals::net->{state}=0; residentBody::tick(); $Globals::net->{state}=5; $clock=1200; residentBody::tick(); print JSON::PP::encode_json({up=>$up,reconnected=>residentBody::telemetryFrame()->{online_minutes}});")
        self.assertEqual(result, {'up':2,'reconnected':0})

    def test_safety_precedes_hunt_completion_and_no_native_potion_in_v1(self):
        source = PLUGIN.read_text()
        hunt = source.split("if($active->{skill}eq'hunt'){", 1)[1]
        self.assertLess(hunt.index('huntUnsafe'), hunt.index("time()-$active->{started}"))
        self.assertIn("if(!behaviorV1()&&", hunt)
