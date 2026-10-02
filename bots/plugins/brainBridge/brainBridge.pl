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
# В state: HP/SP, уровень, карта, координаты, lockMap, режим AI, текущее занятие (activity)
# и до 10 игроков в зоне видимости (players), класс/пол/уровень самого бота и игроков.
# delivery: подтверждение сервером шёпота (результат отправки) и общего чата (эхо) или таймаут.
# Внутрь (только эти действия, всё остальное отклоняется):
#   say {text}            -> c <text>
#   whisper {to, text}    -> pm "<to>" <text>
#   set_hunt_map {map}    -> conf lockMap <map>
#   pause {} / resume {}  -> ai manual / ai auto
#   party_create {name}   -> party create "LR_<имя>"   (только имена LR_*)
#   party_invite {to}     -> party request "<to>"
#   party_accept {}       -> party join 1               (мозг решает по событию party_invite)
#   party_leave {}        -> party leave
#   follow {to} / unfollow {} -> follow <to> / follow stop
#   meet_point {map,x,y}  -> conf lockMap/lockMap_x/lockMap_y/randX 2/randY 2 (только исполнитель плана)
#   clear_point {}        -> conf lockMap_x/_y/_randX/_randY none (вернуться к охоте)
package brainBridge;

use strict;
use utf8;
use IO::Socket::UNIX;
use Socket qw(SOCK_STREAM);
use Errno qw(EAGAIN EWOULDBLOCK EINTR);
use JSON::PP;
use Time::HiRes qw(time);
use Plugins;
use Globals qw($char $field $net $monstersList $playersList %config %jobs_lut %sex_lut @lastpm);
use Log qw(message warning);
use Commands;
use Network;
use AI;

Plugins::register('brainBridge', 'мост OpenKore <-> live_brain (Unix-сокет, JSON)', \&onUnload);

my $json = JSON::PP->new->utf8->canonical;
my ($sock, $inbuf, $lastTry, $lastState) = (undef, '', 0, 0);
# Ожидают ответа сервера: шёпот (результат 0x9a/private_message_sent) и общий чат (эхо self_chat).
my (@pendingPM, @pendingSay);
my $DELIVERY_TIMEOUT = 15;

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
	['packet_pre/private_message_sent', \&onPMResult],
	['packet_selfChat',    \&onSelfChat],
	['party_invite',       sub { event('party_invite', party => "$_[1]{partyName}") }],
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
		lock_x    => (defined $config{lockMap_x} && $config{lockMap_x} ne '' ? $config{lockMap_x} + 0 : undef),
		lock_y    => (defined $config{lockMap_y} && $config{lockMap_y} ne '' ? $config{lockMap_y} + 0 : undef),
		ai        => (AI::state() == AI::AUTO() ? 'auto' : 'manual'),
		%{identity($char)},
		party     => ($char->{party} && $char->{party}{joined} ? "$char->{party}{name}" : undef),
		follow    => ($config{follow} ? $config{followTarget} : undef),
		activity  => (AI::action() || 'idle'),
		players   => nearbyPlayers(),
		dead      => ($char->{dead} ? JSON::PP::true : JSON::PP::false),
	});
}

sub identity {
	my ($actor) = @_;
	return {
		job => (defined $actor->{jobID} ? ($jobs_lut{$actor->{jobID}} || "job$actor->{jobID}") : undef),
		sex => (defined $actor->{sex} ? $sex_lut{$actor->{sex}} : undef),
		lv  => ($actor->{lv} ? $actor->{lv} + 0 : undef),
	};
}

sub nearbyPlayers {
	return [] unless $playersList;
	my @players = grep { defined $_->{name} && length $_->{name} } @{$playersList->getItems() || []};
	@players = @players[0 .. 9] if @players > 10;
	return [map { {name => $_->{name}, %{identity($_)},
	               x => ($_->{pos_to} ? $_->{pos_to}{x} : undef), y => ($_->{pos_to} ? $_->{pos_to}{y} : undef)} } @players];
}

# ---------- подтверждение доставки сервером ----------

sub delivery {
	my ($p, $ok, $code, $reason) = @_;
	sendMsg({type => 'delivery', id => $p->{id}, action => $p->{action}, to => $p->{to},
	         ok => ($ok ? JSON::PP::true : JSON::PP::false), code => $code, reason => $reason});
	my $what = $p->{to} ? "шёпот $p->{to}" : 'общий чат';
	if ($ok) { message "[brainBridge] сервер подтвердил: $what\n", 'system'; }
	else     { warning "[brainBridge] не доставлено ($what): $reason\n"; }
}

my %PM_RESULT = (0 => 'доставлено', 1 => 'адресат не в сети', 2 => 'адресат игнорирует', 3 => 'адресат не принимает сообщения');

sub onPMResult {
	my (undef, $args) = @_;
	my $to = $lastpm[0] ? $lastpm[0]{user} : undef;
	return unless defined $to;
	my ($i) = grep { lc $pendingPM[$_]{to} eq lc $to } 0 .. $#pendingPM;
	return unless defined $i;
	my $p = splice(@pendingPM, $i, 1);
	my $code = $args->{type} + 0;
	delivery($p, $code == 0, $code, $PM_RESULT{$code} || "код $code");
}

sub onSelfChat {
	my (undef, $args) = @_;
	my $msg = defined $args->{msg} ? $args->{msg} : '';
	$msg =~ s/^\s+|\s+$//g;
	my ($i) = grep { $pendingSay[$_]{text} eq $msg } 0 .. $#pendingSay;
	return unless defined $i;
	delivery(splice(@pendingSay, $i, 1), 1, 0, 'эхо сервера');
}

sub expirePending {
	my $now = time;
	for my $list (\@pendingPM, \@pendingSay) {
		while (@$list && $now - $list->[0]{ts} > $DELIVERY_TIMEOUT) {
			delivery(shift @$list, 0, 'timeout', "сервер не подтвердил за $DELIVERY_TIMEOUT с");
		}
	}
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
	} elsif ($kind eq 'party_create') {
		my $name = $a->{name} || '';
		return (0, 'имя группы должно быть LR_<имя>') unless $name =~ /^LR_[A-Za-z0-9_]{1,20}$/;
		return (1, qq{party create "$name"});
	} elsif ($kind eq 'party_invite') {
		my $to = cleanText($a->{to});
		return (0, 'неверный адресат') unless $to =~ /^[^"]{1,23}$/;
		return (1, qq{party request "$to"});
	} elsif ($kind eq 'party_accept') {
		return (1, 'party join 1');
	} elsif ($kind eq 'party_leave') {
		return (1, 'party leave');
	} elsif ($kind eq 'follow') {
		my $to = cleanText($a->{to});
		return (0, 'неверная цель') unless $to =~ /^[^"]{1,23}$/ && $to ne 'stop';
		return (1, "follow $to");
	} elsif ($kind eq 'unfollow') {
		return (1, 'follow stop');
	} elsif ($kind eq 'meet_point') {
		# Точка встречи: OpenKore сам идёт к lockMap_x/y, держится в радиусе 2 клеток и отбивается.
		my ($map, $x, $y) = ($a->{map} || '', $a->{x}, $a->{y});
		return (0, 'неверная карта') unless $map =~ /^[a-z0-9_]{3,16}$/;
		return (0, 'неверные координаты') unless defined $x && defined $y && $x =~ /^\d{1,3}$/ && $y =~ /^\d{1,3}$/;
		if ($field && $field->baseName eq $map && !$field->isWalkable($x, $y)) {
			return (0, "клетка $x,$y на $map непроходима");
		}
		return (1, ["conf lockMap $map", "conf lockMap_x $x", "conf lockMap_y $y",
		            'conf lockMap_randX 2', 'conf lockMap_randY 2']);
	} elsif ($kind eq 'clear_point') {
		return (1, ['conf lockMap_x none', 'conf lockMap_y none', 'conf lockMap_randX none', 'conf lockMap_randY none']);
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
		my @cmds = ref $res ? @$res : ($res);
		$res = join('; ', @cmds);
		message "[brainBridge] решение мозга -> $res\n", 'system';
		Commands::run($_) for @cmds;
		my $kind = $msg->{action} || '';
		if ($kind eq 'whisper') {
			push @pendingPM, {id => $msg->{id}, action => $kind, to => cleanText($msg->{to}), ts => time};
		} elsif ($kind eq 'say') {
			push @pendingSay, {id => $msg->{id}, action => $kind, text => cleanText($msg->{text}), ts => time};
		}
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
	expirePending();
	my $interval = $config{brainBridge_stateInterval} || 15;
	if ($sock && time - $lastState >= $interval) {
		$lastState = time;
		sendState();
	}
}

1;
