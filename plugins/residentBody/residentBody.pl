package residentBody;
# Новый body v0: только телеметрия; ACK/действия добавляются отдельным срезом.
use strict;
use warnings;
use Plugins;
use Globals;
use Utils qw(calcPosition);
use Network;
use JSON::PP;
use IO::Socket::UNIX;
use Socket qw(SOCK_STREAM MSG_NOSIGNAL MSG_DONTWAIT);
use Time::HiRes qw(time);
my ($connection,$lastTick,$lastConnect)=(undef,0,0);
my $seq=0;
open(my $uuid,'<','/proc/sys/kernel/random/uuid') or die 'body UUID unavailable';
my $epoch=<$uuid>;close($uuid);chomp($epoch);
my $mode='IDLE_SAFE';
Plugins::register('residentBody','Residents body v0 telemetry',\&unload);
my $hooks=Plugins::addHooks(['AI_start',\&tick,undef]);

sub telemetryFrame {
 my $p=calcPosition($char);
 return {proto=>1,type=>'telemetry',body_epoch=>$epoch,seq=>++$seq,ts=>time(),
  map=>$field->{baseName},pos=>{x=>0+$p->{x},y=>0+$p->{y}},dest=>{x=>0+$char->{pos_to}{x},y=>0+$char->{pos_to}{y}},
  hp=>0+($char->{hp}//0),hp_max=>0+($char->{hp_max}//0),sp=>0+($char->{sp}//0),sp_max=>0+($char->{sp_max}//0),
  zeny=>0+($char->{zeny}//0),weight=>0+($char->{weight}//0),weight_max=>0+($char->{weight_max}//0),
  dead=>($char->{dead}||!$char->{hp})?JSON::PP::true:JSON::PP::false,
  sitting=>$char->{sitting}?JSON::PP::true:JSON::PP::false,mode=>$mode};
}
sub sendFrame {
 my ($frame)=@_;my $bytes=encode_json($frame)."\n";
 my $n=send($connection,$bytes,MSG_NOSIGNAL|MSG_DONTWAIT);
 if(!defined($n)||$n!=length($bytes)){close($connection);undef $connection;return 0;}
 return 1;
}
sub tick {
 return unless $ENV{RESIDENT_SOCKET} && $ENV{RESIDENT_ID};
 return unless $net && $net->getState()==Network::IN_GAME && $char && $field && $char->{pos_to};
 my $now=time();return if $now-$lastTick<1;$lastTick=$now;
 # Настройки меняются только в памяти. Нет конфиг-редактора или произвольных команд.
 @config{qw(attackAuto route_randomWalk sellAuto buyAuto storageAuto follow partyAuto)}=(1,0,0,0,0,0,0);
 $config{lockMap}='';
 if(!$connection){
  return if $now-$lastConnect<2;$lastConnect=$now;
  $connection=IO::Socket::UNIX->new(Type=>SOCK_STREAM,Peer=>$ENV{RESIDENT_SOCKET});return unless $connection;
  my $cid=unpack('V',$charID);my $aid=unpack('V',$accountID);
  return unless sendFrame({proto=>1,type=>'hello',resident=>$ENV{RESIDENT_ID},char_id=>$cid,account_id=>$aid,body_epoch=>$epoch,plugin_version=>'0.1.0',seq=>++$seq,ts=>$now});
 }
 sendFrame(telemetryFrame());
}
sub unload {
 Plugins::delHooks($hooks);close($connection) if $connection;undef $connection;
}
1;
