# brainBridge — мост OpenKore <-> live_brain (live_ro).
#
# OpenKore остаётся «телом»: бой, ходьба, лут, отдых. Мозг (brain/live_brain,
# отдельный Python-процесс) решает цели и общается. Связь — JSON-строки через
# Unix-сокет, который слушает мозг. Путь: $ENV{LIVE_RO_BRAIN_SOCKET} или
# config.txt brainBridge_socket. Мозг не запущен — бот играет как обычно и
# переподключается раз в 5 с.
#
# Наружу: hello, state (каждые brainBridge_stateInterval с, по умолчанию 15),
# события in_game, died, level_up, attack, kill, loot, chat_public, chat_private, ack.
# В state: HP/SP, уровень, карта, координаты, lockMap, режим AI и текущее занятие (activity).
# Внутрь (только эти действия, всё остальное отклоняется):
#   say {text}            -> c <text>
#   whisper {to, text}    -> pm "<to>" <text>
#   set_hunt_map {map}    -> conf lockMap <map>
#   pause {} / resume {}  -> ai manual / ai auto
package brainBridge;

use strict;
use utf8;
use IO::Socket::UNIX;
use Socket qw(SOCK_STREAM);
use Errno qw(EAGAIN EWOULDBLOCK EINTR);
use JSON::PP;
use Time::HiRes qw(time);
use Plugins;
use Globals qw($char $field $net $monstersList %config);
use Log qw(message warning);
use Commands;
use Network;
use AI;

Plugins::register('brainBridge', 'мост OpenKore <-> live_brain (Unix-сокет, JSON)', \&onUnload);

my $json = JSON::PP->new->utf8->canonical;
my ($sock, $inbuf, $lastTry, $lastState) = (undef, '', 0, 0);

my $hooks = Plugins::addHooks(
	['mainLoop_post',      \&onTick],
	['in_game',            sub { event('in_game') }],
	['self_died',          sub { event('died') }],
	['base_level_changed', sub { event('level_up', level => $_[1]{level}) }],
	['attack_start',       \&onAttack],
	['target_died',        \&onKill],
	['item_gathered',      sub { event('loot', item => "$_[1]{item}", amount => $_[1]{amount} + 0) }],
	['packet_pubMsg',      \&onPubMsg],
	['packet_privMsg',     \&onPrivMsg],
);

sub onUnload {
	Plugins::delHooks($hooks);
	close($sock) if $sock;
	undef $sock;
}

sub socketPath { return $ENV{LIVE_RO_BRAIN_SOCKET} || $config{brainBridge_socket}; }

sub inGame { return $net && $net->getState() == Network::IN_GAME && $char; }

sub dropConnection {
	my ($why) = @_;
	warning "[brainBridge] связь с мозгом потеряна: $why\n" if $sock;
	close($sock) if $sock;
	undef $sock;
	$inbuf = '';
}

sub tryConnect {
	return if $sock || time - $lastTry < 5;
	$lastTry = time;
	my $path = socketPath();
	return unless $path && -S $path;
	my $s = IO::Socket::UNIX->new(Type => SOCK_STREAM, Peer => $path) or return;
	$s->blocking(0);
	$sock = $s;
	message "[brainBridge] подключён к мозгу: $path\n", 'system';
	sendMsg({type => 'hello', char => (inGame() ? $char->{name} : undef)});
	sendState() if inGame();
}

sub sendMsg {
	my ($msg) = @_;
	return unless $sock;
	$msg->{ts} = time;
	my $line = $json->encode($msg) . "\n";
	local $SIG{PIPE} = 'IGNORE';
	my $off = 0;
	while ($off < length $line) {
		my $n = syswrite($sock, $line, length($line) - $off, $off);
		if (!defined $n) {
			next if $! == EINTR;
			return dropConnection("запись: $!");
		}
		$off += $n;
	}
}

sub event {
	my ($kind, %data) = @_;
	return unless $sock;
	sendMsg({type => 'event', kind => $kind, map => ($field ? $field->baseName : undef), %data});
}

sub pct { my ($a, $b) = @_; return $b ? int(100 * $a / $b) : undef; }

sub sendState {
	return unless inGame();
	my $pos = $char->{pos_to} || {};
	sendMsg({
		type      => 'state',
		name      => $char->{name},
		lv        => $char->{lv} + 0,
		job_lv    => $char->{lv_job} + 0,
		exp_pct   => pct($char->{exp}, $char->{exp_max}),
		hp_pct    => pct($char->{hp}, $char->{hp_max}),
		sp_pct    => pct($char->{sp}, $char->{sp_max}),
		weight_pct=> pct($char->{weight}, $char->{weight_max}),
		zeny      => $char->{zeny} + 0,
		map       => ($field ? $field->baseName : undef),
		x         => $pos->{x}, y => $pos->{y},
		lock_map  => $config{lockMap},
		ai        => (AI::state() == AI::AUTO() ? 'auto' : 'manual'),
		activity  => (AI::action() || 'idle'),
		dead      => ($char->{dead} ? JSON::PP::true : JSON::PP::false),
	});
}

sub onAttack {
	my (undef, $args) = @_;
	my $m = $monstersList ? $monstersList->getByID($args->{ID}) : undef;
	event('attack', monster => ($m ? "$m->{name}" : undef), hp_pct => pct($char->{hp}, $char->{hp_max}));
}

sub onKill {
	my (undef, $args) = @_;
	my $m = $args->{monster};
	event('kill', monster => ($m ? "$m->{name}" : undef));
}

sub onPubMsg {
	my (undef, $args) = @_;
	return if $char && $args->{pubMsgUser} eq $char->{name};
	event('chat_public', from => "$args->{pubMsgUser}", text => "$args->{pubMsg}");
}

sub onPrivMsg {
	my (undef, $args) = @_;
	event('chat_private', from => "$args->{privMsgUser}", text => "$args->{privMsg}");
}

sub cleanText {
	my ($t) = @_;
	$t = '' unless defined $t;
	$t =~ s/[\x00-\x1f\x7f]+/ /g;
	$t =~ s/^\s+|\s+$//g;
	return substr($t, 0, 120);
}

# Возвращает (1, команда) или (0, причина).
sub actionToCommand {
	my ($a) = @_;
	my $kind = $a->{action} || '';
	if ($kind eq 'say') {
		my $t = cleanText($a->{text});
		return (0, 'пустой текст') unless length $t;
		return (1, "c $t");
	} elsif ($kind eq 'whisper') {
		my $to = cleanText($a->{to});
		my $t  = cleanText($a->{text});
		return (0, 'неверный адресат') unless $to =~ /^[^"]{1,23}$/;
		return (0, 'пустой текст') unless length $t;
		return (1, qq{pm "$to" $t});
	} elsif ($kind eq 'set_hunt_map') {
		my $map = $a->{map} || '';
		return (0, 'неверное имя карты') unless $map =~ /^[a-z0-9_]{3,16}$/;
		return (1, "conf lockMap $map");
	} elsif ($kind eq 'pause') {
		return (1, 'ai manual');
	} elsif ($kind eq 'resume') {
		return (1, 'ai auto');
	}
	return (0, "неизвестное действие '$kind'");
}

sub handleLine {
	my ($line) = @_;
	my $msg = eval { $json->decode($line) };
	if (!$msg || ref $msg ne 'HASH') {
		warning "[brainBridge] не JSON от мозга, пропускаю\n";
		return;
	}
	return unless ($msg->{type} || '') eq 'action';
	my ($ok, $res) = actionToCommand($msg);
	if ($ok && !inGame()) { ($ok, $res) = (0, 'бот не в игре'); }
	if ($ok) {
		message "[brainBridge] решение мозга -> $res\n", 'system';
		Commands::run($res);
	} else {
		warning "[brainBridge] действие отклонено: $res\n";
	}
	sendMsg({type => 'ack', id => $msg->{id}, ok => ($ok ? JSON::PP::true : JSON::PP::false),
	         ($ok ? (command => $res) : (error => $res))});
}

sub readIncoming {
	return unless $sock;
	while (1) {
		my $n = sysread($sock, my $data, 65536);
		if (!defined $n) {
			last if $! == EAGAIN || $! == EWOULDBLOCK;
			next if $! == EINTR;
			return dropConnection("чтение: $!");
		}
		return dropConnection('мозг закрыл соединение') if $n == 0;
		$inbuf .= $data;
		return dropConnection('слишком длинная строка') if length $inbuf > 1_000_000;
	}
	while ($inbuf =~ s/^([^\n]*)\n//) {
		handleLine($1) if length $1;
	}
}

sub onTick {
	tryConnect();
	return unless $sock;
	readIncoming();
	my $interval = $config{brainBridge_stateInterval} || 15;
	if ($sock && time - $lastState >= $interval) {
		$lastState = time;
		sendState();
	}
}

1;
