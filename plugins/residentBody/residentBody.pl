package residentBody;
use strict;use warnings;
use Plugins;use Globals;use Utils qw(calcPosition);use Network;use JSON::PP;use IO::Socket::UNIX;
use Socket qw(SOCK_STREAM MSG_NOSIGNAL MSG_DONTWAIT);use Time::HiRes qw(time);use Scalar::Util qw(blessed);
my($connection,$lastTick,$lastConnect,$input)=(undef,0,0,'');my$seq=0;
open(my$uuid,'<','/proc/sys/kernel/random/uuid') or die 'body UUID unavailable';my$epoch=<$uuid>;close$uuid;chomp$epoch;
my$mode='IDLE_SAFE';my($active,$lastOrch,$loaded)=(undef,time(),0);my%ledger;my@order;
Plugins::register('residentBody','Residents bounded skills v1',\&unload);
my$hooks=Plugins::addHooks(['AI_start',\&tick,undef],['packet_pre/private_message_sent',\&privateMessageResult,undef]);
sub applyMode{
 $Globals::masterServer->{serverEncoding}='UTF-8' if$ENV{RESIDENT_CHAT_UTF8};
 @config{qw(attackAuto route_randomWalk sellAuto buyAuto storageAuto follow partyAuto dealAuto)}=($mode eq'IDLE_SAFE'?-1:1,0,0,0,0,0,0,0);
 $config{lockMap}='';$config{route_teleport}=0;$config{route_teleportAuto}=0;
 $config{itemsTakeAuto}=$mode eq'IDLE_SAFE'?0:2;$config{itemsGatherAuto}=0;
 if($mode eq'HUNT'&&$active){$config{route_randomWalk}=1;$config{lockMap}=$active->{params}{map}}
}
sub bagItems{my$l=eval{$char->inventory->getItems()}//[];return$l}
sub inventoryFrame{my%bag;for my$i(@{bagItems()}){$bag{$i->{nameID}}+=0+($i->{amount}//0)if$i->{nameID}}return\%bag}
sub pollShop{
 my$sk=$active->{skill};my$phase=$active->{phase};
 if($phase eq'opening'&&($Globals::ai_v{npc_talk}{talk}//'')eq'buy_or_sell'){
  $Globals::messageSender->sendNPCBuySellList($active->{npc_id},$sk eq'buy_potions'?0:1);$active->{phase}='quote';return;
 }
 if($phase eq'quote'){
  my@order;
  if($sk eq'buy_potions'){
   my$list=eval{$Globals::storeList->getItems()}//[];my($pot)=grep{$_->{nameID}==501}@$list;return unless$pot;
   my$n=$active->{params}{stock_goal}-(inventoryFrame()->{501}//0);my$cost=$n*$pot->{price};
   if($n<=0){finish('error','NO_PURCHASE_NEEDED','stock already sufficient');return}
   if($cost>$active->{params}{max_zeny}||$cost>$char->{zeny}){finish('error','BUDGET','quoted cost exceeds budget');return}
   if($char->{weight}+7*$n>$char->{weight_max}*.45){finish('error','OVERWEIGHT','purchase would exceed safe weight');return}
   @order=({itemID=>501,amount=>$n});$active->{expected}={501=>$n};require Misc;Misc::completeNpcBuy(\@order);
  }else{
   return unless($Globals::ai_v{npc_talk}{talk}//'')eq'sell';my%allow=map{$_=>1}(909,914,705,935,919,908,916);my%expected;
   for my$i(@{bagItems()}){next unless$allow{$i->{nameID}}&&$i->{amount}>0&&!$i->{equipped}&&!$i->{unsellable};push@order,{ID=>$i->{ID},amount=>$i->{amount}};$expected{$i->{nameID}}+=$i->{amount}}
   if(!@order){finish('error','NO_LOOT','no sellable whitelisted loot');return}
   $active->{expected}=\%expected;require Misc;Misc::completeNpcSell(\@order);
  }
  $active->{phase}='committed';return;
 }
 if($phase eq'committed'){
  my$bag=inventoryFrame();my$changed=1;
  for my$id(keys%{$active->{expected}}){my$d=($bag->{$id}//0)-($active->{before}{$id}//0);$changed=0 if($sk eq'buy_potions'?$d:-$d)<$active->{expected}{$id}}
  finish('done',$sk eq'buy_potions'?'BOUGHT':'SOLD','inventory delta observed; picklog confirmation required')if$changed;
 }
}
sub pollRest{
 my$goal=$active->{params}{hp_pct};
 if(($char->{hp_max}//0)&&$char->{hp}*100/$char->{hp_max}>=$goal){$char->sendStand()if$char->{sitting};finish('done','RESTED','measured HP target reached');return}
 my$basic=$char->{skills}{NV_BASIC}{lv}//0;
 if($basic<3){
  if(($char->{points_skill}//0)<3-$basic){finish('error','NEED_BASIC_SKILL','insufficient earned skill points for Basic3');return}
  if(defined$active->{learn_level}&&$active->{learn_level}==$basic){finish('error','SKILL_LEARN_TIMEOUT','no server skill acknowledgement')if time()-$active->{learn_ts}>5;return}
  $active->{learn_level}=$basic;$active->{learn_ts}=time();$Globals::messageSender->sendAddSkillPoint(1);return;
 }
 $char->sendSit()unless$char->{sitting};
}
sub telemetryFrame{
 my$p=calcPosition($char);
 return{proto=>1,type=>'telemetry',body_epoch=>$epoch,seq=>++$seq,ts=>time(),map=>$field->{baseName},pos=>{x=>0+$p->{x},y=>0+$p->{y}},dest=>{x=>0+$char->{pos_to}{x},y=>0+$char->{pos_to}{y}},hp=>0+($char->{hp}//0),hp_max=>0+($char->{hp_max}//0),sp=>0+($char->{sp}//0),sp_max=>0+($char->{sp_max}//0),zeny=>0+($char->{zeny}//0),weight=>0+($char->{weight}//0),weight_max=>0+($char->{weight_max}//0),dead=>($char->{dead}||!$char->{hp})?JSON::PP::true:JSON::PP::false,sitting=>$char->{sitting}?JSON::PP::true:JSON::PP::false,basic_skill=>0+($char->{skills}{NV_BASIC}{lv}//0),skill_points=>0+($char->{points_skill}//0),ai_action=>eval{AI::action()}//'',hunt_observation=>($active&&$active->{skill}eq'hunt'?{%{$active->{hunt_observation}}}:undef),inventory=>inventoryFrame(),base_level=>0+($char->{lv}//0),mode=>$mode,running=>$active?{action_id=>$active->{action_id},phase=>'running'}:undef};
}
sub sendFrame{
 return 0 unless$connection;my$bytes=encode_json($_[0])."\n";my$n=send($connection,$bytes,MSG_NOSIGNAL|MSG_DONTWAIT);
 if(!defined$n||$n!=length$bytes){close$connection;undef$connection;$input='';return 0}return 1;
}
sub emit{my($type,$data)=@_;sendFrame({%$data,proto=>1,type=>$type,body_epoch=>$epoch,seq=>++$seq,ts=>time()})}
sub ledgerPath{my$dir=$ENV{RESIDENT_RUN_DIR};return unless$dir&&-d$dir;return "$dir/ledger.jsonl"}
sub loadLedger{
 return if$loaded++;my$path=ledgerPath();return unless$path&&-f$path;
 open(my$f,'<',$path)or die'Cannot load ledger';while(my$l=<$f>){my$r=decode_json($l);$ledger{$r->{idem_key}}=$r;push@order,$r->{idem_key}}close$f;
}
sub saveLedger{
 my$path=ledgerPath();return unless$path;while(@order>500){delete$ledger{shift@order}}
 my$tmp="$path.$$.tmp";open(my$f,'>',$tmp)or die'Cannot persist ledger';chmod 0600,$tmp;print$f encode_json($ledger{$_})."\n"for@order;close$f or die'Cannot flush ledger';rename$tmp,$path or die'Cannot commit ledger';
}
sub helloLedger{my@recent=@order>50?@order[-50..-1]:@order;return[map{$ledger{$_}}@recent]}
sub finish{
 my($outcome,$code,$detail)=@_;return unless$active;
 my$a=$active;undef$active;$mode='IDLE_SAFE';applyMode();
 my$r={action_id=>$a->{action_id},outcome=>$outcome,code=>$code,detail=>$detail};$ledger{$a->{idem_key}}{result}=$r;saveLedger();emit('skill_result',$r);
}
sub stopMotion{
 require AI;eval{$active->{task}->stop()if$active&&$active->{task}};AI::clear('route','move','mapRoute','attack','items_take','take','items_gather');
 my$p=calcPosition($char);eval{$Globals::messageSender->sendMove(int$p->{x},int$p->{y})if$Globals::messageSender};
}
sub reject{my($a,$code,$why,$prev)=@_;emit('skill_rejected',{action_id=>$a,code=>$code,reason=>$why,prev_outcome=>$prev})}
sub command{
 my$m=shift;return unless ref$m eq'HASH'&&($m->{proto}//0)==1;my$t=$m->{type}//'';
 if($t eq'heartbeat'){$lastOrch=time();return}
 if($t eq'safe_stop'||$t eq'freeze'){stopMotion();finish('cancelled','CANCELLED','operator safe stop');$mode='IDLE_SAFE';applyMode();return}
 if($t eq'skill_cancel'){if($active&&$active->{action_id}eq($m->{action_id}//'')){stopMotion();if(($m->{reason}//'')eq'deadline'){finish('error','TIMEOUT','skill deadline exceeded')}else{finish('cancelled','CANCELLED','operator cancellation')}}return}
 return unless$t eq'skill_start';my$a=$m->{action_id}//'';my$k=$m->{idem_key}//'';loadLedger();
 return reject($a,'INVALID','invalid identity')unless$a=~/^[\w-]{1,80}$/&&$k=~/^[\w-]{1,128}$/;
 my$existing=exists$ledger{$k}?$k:(grep{($ledger{$_}{action_id}//'')eq$a}@order)[0];
 return reject($a,'DUPLICATE','already journalled',$ledger{$existing}{result})if defined$existing;
 return reject($a,'EPOCH','body restarted')unless($m->{expect_epoch}//'')eq$epoch;
 return reject($a,'BUSY','another skill active')if$active;
 return reject($a,'ESTOP','emergency stop file present')if-f($ENV{RESIDENT_ESTOP}//'/root/ragnarok/run/ESTOP');
 return reject($a,'DIED','character not alive')if(!$char||!$char->{hp}||$char->{dead})&&($m->{skill}//'')ne'respawn';
 return reject($a,'TIMEOUT','expired deadline')unless($m->{deadline_ts}//0)>time();
 if(($m->{skill}//'')eq'say'){
  my$p=$m->{params};return reject($a,'INVALID','no commands or controls in public speech')unless ref$p eq'HASH'&&defined$p->{text}&&length$p->{text}>0&&length$p->{text}<=120&&$p->{text}!~/[\x00-\x1f\x7f]/&&$p->{text}!~/^\s*[\@#\/]/;
  $ledger{$k}={action_id=>$a,idem_key=>$k,result=>{outcome=>'unknown',code=>'IN_PROGRESS'}};push@order,$k;saveLedger();$active={%$m};
  emit('skill_accepted',{action_id=>$a,code=>'ACCEPTED'});emit('skill_progress',{action_id=>$a,phase=>'running',detail=>'native public chat; server chatlog confirmation required'});require Commands;
  my$ok=eval{Commands::run('c '.$p->{text});1};finish($ok?'done':'error',$ok?'SPOKEN':'SEND_ERROR','public chat dispatched; server proof required');return;
 }
 if(($m->{skill}//'')eq'whisper'){
  my$p=$m->{params};return reject($a,'INVALID','invalid whisper')unless ref$p eq'HASH'&&($p->{to_name}//'')=~/^[\p{L}\p{N}_ -]{1,24}$/&&defined$p->{text}&&length$p->{text}>0&&length$p->{text}<=120&&$p->{text}!~/[\x00-\x1f\x7f]/;
  $ledger{$k}={action_id=>$a,idem_key=>$k,result=>{outcome=>'unknown',code=>'IN_PROGRESS'}};push@order,$k;saveLedger();$active={%$m};
  emit('skill_accepted',{action_id=>$a,code=>'ACCEPTED'});emit('skill_progress',{action_id=>$a,phase=>'running',detail=>'awaiting server PM result'});require Commands;
  my$ok=eval{Commands::run('pm "'.$p->{to_name}.'" '.$p->{text});1};finish('error','SEND_ERROR','PM command failed')unless$ok;return;
 }
 if(($m->{skill}//'')eq'buy_potions'||($m->{skill}//'')eq'sell_loot'){
  my$sk=$m->{skill};my$p=$m->{params};return reject($a,'INVALID','invalid shop params')unless ref$p eq'HASH';
  if($sk eq'buy_potions'){return reject($a,'INVALID','501 only, bounded stock and budget')unless($p->{item_id}//0)==501&&($p->{stock_goal}//0)=~/^\d+$/&&$p->{stock_goal}>0&&$p->{stock_goal}<=20&&($p->{max_zeny}//0)>0&&$p->{max_zeny}<=300}
  return reject($a,'WRONG_MAP','verified tool dealer only')unless$field->{baseName}eq'prt_in';
  my$pos=calcPosition($char);my$list=eval{$Globals::npcsList->getItems()}//[];my($npc)=grep{($_->{name}//'')=~/^Tool Dealer#/&&abs($_->{pos}{x}-126)<=2&&abs($_->{pos}{y}-76)<=2}@$list;
  return reject($a,'NPC_NOT_NEAR','tool dealer not observed or too far')unless$npc&&abs($pos->{x}-126)<=4&&abs($pos->{y}-76)<=4;
  $ledger{$k}={action_id=>$a,idem_key=>$k,result=>{outcome=>'unknown',code=>'IN_PROGRESS'}};push@order,$k;saveLedger();$active={%$m,started=>time(),phase=>'opening',npc_id=>$npc->{ID},before=>inventoryFrame()};stopMotion();$mode='SHOP';delete$Globals::ai_v{npc_talk};eval{$Globals::storeList->clear()};
  emit('skill_accepted',{action_id=>$a,code=>'ACCEPTED'});emit('skill_progress',{action_id=>$a,phase=>'running',detail=>'native shop, at most one economic commit'});$Globals::messageSender->sendTalk($npc->{ID});return;
 }
 if(($m->{skill}//'')eq'rest'||($m->{skill}//'')eq'respawn'){
  my$sk=$m->{skill};my$p=$m->{params};return reject($a,'INVALID','invalid maintenance params')unless ref$p eq'HASH';
  if($sk eq'rest'){return reject($a,'INVALID','HP target 80..100')unless($p->{hp_pct}//0)=~/^\d+$/&&$p->{hp_pct}>=80&&$p->{hp_pct}<=100;return reject($a,'OVERWEIGHT','regeneration disabled at 50 percent weight')if($char->{weight_max}//0)&&$char->{weight}*2>=$char->{weight_max}}
  else{return reject($a,'NOT_DEAD','respawn allowed only while dead')unless$char->{dead}||!$char->{hp}}
  $ledger{$k}={action_id=>$a,idem_key=>$k,result=>{outcome=>'unknown',code=>'IN_PROGRESS'}};push@order,$k;saveLedger();$active={%$m,started=>time()};stopMotion();$mode=$sk eq'rest'?'REST':'RESPAWN';
  if($sk eq'respawn'){$Globals::messageSender->sendRestart(0)}
  emit('skill_accepted',{action_id=>$a,code=>'ACCEPTED'});emit('skill_progress',{action_id=>$a,phase=>'running',detail=>'native maintenance; authoritative result required'});return;
 }
 if(($m->{skill}//'')eq'hunt'){
  my$p=$m->{params};return reject($a,'INVALID','hunt only on verified low-level field')unless ref$p eq'HASH'&&($p->{map}//'')eq'prt_fild08'&&$p->{map}eq$field->{baseName}&&($p->{duration}//0)=~/^\d+$/&&$p->{duration}>0&&$p->{duration}<=3600;
  $ledger{$k}={action_id=>$a,idem_key=>$k,result=>{outcome=>'unknown',code=>'IN_PROGRESS'}};push@order,$k;saveLedger();$active={%$m,started=>time(),last_attack=>0,hunt_observation=>{samples=>0,eligible_seen=>0,attack_attempts=>0,attack_errors=>0,attack_rejections=>0}};$mode='HUNT';applyMode();emit('skill_accepted',{action_id=>$a,code=>'ACCEPTED'});emit('skill_progress',{action_id=>$a,phase=>'running',detail=>'bounded native hunt; server kills required'});return;
 }
 return reject($a,'UNSUPPORTED','skill not allowed')unless($m->{skill}//'')eq'travel_to';
 my$p=$m->{params};return reject($a,'INVALID','bad params')unless ref$p eq'HASH'&&($p->{map}//'')=~/^[a-zA-Z0-9_]{1,24}$/;
 my$coords=defined$p->{x}||defined$p->{y};
 return reject($a,'INVALID','invalid coordinates')if$coords&&(!defined$p->{x}||!defined$p->{y}||$p->{x}!~/^\d+$/||$p->{y}!~/^\d+$/||$p->{x}>2048||$p->{y}>2048);
 return reject($a,'INVALID','invalid radius')unless defined$p->{r}&&$p->{r}=~/^\d+$/&&$p->{r}<=8;
 if($coords&&$p->{map}eq$field->{baseName}&&!$field->isWalkable($p->{x},$p->{y})){return reject($a,'UNWALKABLE','target cell is not walkable')}
 $ledger{$k}={action_id=>$a,idem_key=>$k,result=>{outcome=>'unknown',code=>'IN_PROGRESS'}};push@order,$k;saveLedger();
 $active={%$m};$mode='TRAVEL';applyMode();require AI;AI::clear();
 my$ok=eval{$char->route($p->{map},$coords?$p->{x}:undef,$coords?$p->{y}:undef,maxRouteTime=>$m->{deadline_ts}-time(),distFromGoal=>0,noSitAuto=>1,notifyUponArrival=>1,attackOnRoute=>1);$active->{task}=$char->args();die'No task reference'unless blessed($active->{task});1};
 if(!$ok){stopMotion();finish('error','NO_ROUTE','route task creation failed');return}
 emit('skill_accepted',{action_id=>$a,code=>'ACCEPTED'});emit('skill_progress',{action_id=>$a,phase=>'running',detail=>'native route task queued'});
}
sub privateMessageResult{
 my($hook,$args)=@_;return unless$active&&$active->{skill}eq'whisper';my@codes=qw(SENT OFFLINE IGNORED REFUSED);my$n=$args->{type};
 return unless defined$n&&$n=~/^[0-3]$/;finish($n==0?'done':'error',$codes[$n],$n==0?'server accepted whisper':'server rejected whisper');
}
sub pollSkill{
 return unless$active;
 if($active->{skill}eq'respawn'){
  if(!$char->{dead}&&$char->{hp}){finish('done','RESPAWNED','native alive after restart packet');return}
  if(time()>$active->{deadline_ts}){finish('error','TIMEOUT','respawn deadline exceeded')}return;
 }
 if($char->{dead}||!$char->{hp}){stopMotion();finish('error','DIED','character died');return}
 if(time()>$active->{deadline_ts}){stopMotion();finish('error','TIMEOUT','skill deadline exceeded');return}
 if($active->{skill}eq'buy_potions'||$active->{skill}eq'sell_loot'){pollShop();return}
 if($active->{skill}eq'rest'){
  pollRest();return;
 }
 if($active->{skill}eq'hunt'){
  if($field->{baseName}ne$active->{params}{map}){stopMotion();finish('error','WRONG_MAP','hunt left verified field');return}
  if(time()-$active->{started}>=$active->{params}{duration}){my$o=$active->{hunt_observation};my$detail='bounded hunt completed; native kills required; '.join(' ',map{"$_=$o->{$_}"}qw(samples eligible_seen attack_attempts attack_errors attack_rejections));my$empty=$o->{samples}>0&&$o->{eligible_seen}==0&&$o->{attack_attempts}==0&&!$active->{hunt_selection_failed};stopMotion();if($empty){finish('error','NO_TARGET','no eligible target observed in native selection samples; '.$detail)}else{finish('done','HUNTED',$detail)}return}
  if(($char->{hp_max}//0)&&$char->{hp}*100/$char->{hp_max}<55&&time()-($active->{last_potion}//0)>=2){my($pot)=grep{$_->{nameID}==501&&$_->{amount}>0}@{bagItems()};if($pot){my$used=eval{$pot->use()};if($used){$active->{last_potion}=time();return}}}
  return if time()-($active->{last_potion}//0)<1;
  if(($char->{hp_max}//0)&&$char->{hp}*100/$char->{hp_max}<30){stopMotion();finish('error','NEEDS_REST','HP safety threshold reached');return}
  require AI;
  if(time()-$active->{last_attack}>=2&&!AI::inQueue('attack')&&(AI::action()//'')ne'items_take'){
   $active->{last_attack}=time();my$list=eval{$Globals::monstersList->getItems()};if($@||ref($list)ne'ARRAY'){$active->{hunt_selection_failed}=1;return}my@seen=@$list;
   my@targets=grep{$_&&(($_->{nameID}//0)==1002)&&!$_->{dead}}@seen;
   $active->{hunt_observation}{samples}++;$active->{hunt_observation}{eligible_seen}+=scalar@targets;
   my$attempted=0;for my$target(@targets){last if$attempted++>=3;$active->{hunt_observation}{attack_attempts}++;my$accepted;my$ok=eval{$accepted=$char->attack($target->{ID});1};if(!$ok){$active->{hunt_observation}{attack_errors}++;last}if($accepted){last}$active->{hunt_observation}{attack_rejections}++}
  }return;
 }
 my$task=$active->{task};return unless$task&&$task->getStatus()==4;
 my$e=$task->getError();if($e){my$text=$e->{message}//'native route error';my$code=$text=~/time/i?'TIMEOUT':$text=~/stuck/i?'STUCK':'NO_ROUTE';finish('error',$code,$text)}else{finish('done','ARRIVED','native task completed; server verification required')}
}
sub tick{
 return unless$ENV{RESIDENT_SOCKET}&&$ENV{RESIDENT_ID};return unless$net&&$net->getState()==Network::IN_GAME&&$char&&$field&&$char->{pos_to};
 applyMode();loadLedger();my$now=time();
 if(-f($ENV{RESIDENT_ESTOP}//'/root/ragnarok/run/ESTOP')||$now-$lastOrch>60){if($active){stopMotion();finish('cancelled','CANCELLED','ESTOP or heartbeat watchdog')} $mode='IDLE_SAFE'}
 if(!$connection&&$now-$lastConnect>=2){$lastConnect=$now;$connection=IO::Socket::UNIX->new(Type=>SOCK_STREAM,Peer=>$ENV{RESIDENT_SOCKET});if($connection){$connection->blocking(0);$input='';emit('hello',{resident=>$ENV{RESIDENT_ID},char_id=>unpack('V',$charID),account_id=>unpack('V',$accountID),plugin_version=>'1.0.0',ledger=>helloLedger()})}}
 if($connection){my$buf='';my$n=sysread($connection,$buf,8192);if(defined$n){if(!$n){close$connection;undef$connection;$input=''}else{$input.=$buf;if(length$input>65536){close$connection;undef$connection;$input=''}else{while($input=~s/^(.*?)\n//s){my$m=eval{decode_json($1)};command($m)if$m}}}}}
 pollSkill();if($now-$lastTick>=1){$lastTick=$now;sendFrame(telemetryFrame())}
}
sub unload{stopMotion()if$active;Plugins::delHooks($hooks);close$connection if$connection;undef$connection}
1;
