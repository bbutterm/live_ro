# brainBridge — мост OpenKore <-> live_brain (live_ro).
#
# OpenKore остаётся «телом»: бой, ходьба, лут, отдых. Мозг (brain/live_brain,
# отдельный Python-процесс) решает цели и общается. Связь — JSON-строки через
# Unix-сокет, который слушает мозг. Путь: $ENV{LIVE_RO_BRAIN_SOCKET} или
# config.txt brainBridge_socket. Мозг не запущен — бот играет как обычно и
# переподключается раз в 5 с.
#
# Наружу: hello, state (каждые brainBridge_stateInterval с, по умолчанию 15),
# события in_game, died, level_up, attack, kill, loot, chat_public, chat_private, ack;
# world_msg {text, source sys|broadcast} — объявление сервера (хуки packet_sysMsg и packet_localBroadcast, аргумент Msg).
# В state: HP/SP, уровень, карта, координаты, lockMap, режим AI, текущее занятие (activity)
# и до 10 игроков в зоне видимости (players), класс/пол/уровень самого бота и игроков.
# delivery: подтверждение сервером шёпота (результат отправки) и общего чата (эхо) или таймаут.
# Внутрь (только эти действия, всё остальное отклоняется):
#   say {text}            -> c <text>
#   whisper {to, text}    -> pm "<to>" <text>
#   set_hunt_map {map}    -> conf lockMap <map>
#   pause {}              -> не искать новых целей (attackAuto 1: только отбиваться), не бродить
#                            (route_randomWalk 0); прежние значения в brainBridge_paused.
#                            НЕ ai manual: в нём OpenKore не отбивается, не пьёт зелья и не делает респаун.
#   resume {}             -> вернуть значения из brainBridge_paused; ai auto
#   party_create {name}   -> party create "LR_<имя>"   (только имена LR_*)
#   party_invite {to}     -> party request "<to>"
#   party_accept {}       -> party join 1               (мозг решает по событию party_invite)
#   party_leave {}        -> party leave
#   follow {to} / unfollow {} -> follow <to> / follow stop
#   meet_point {map,x,y}  -> conf lockMap/lockMap_x/lockMap_y/randX 2/randY 2 (только исполнитель плана)
#   clear_point {}        -> conf lockMap_x/_y/_randX/_randY none (вернуться к охоте)
#   hunt {map} / sit / stand -> распорядок: охота на карте без точки / сесть / встать
#   unstuck {radius}      -> ai clear; move <случайная проходимая клетка в радиусе radius (5..30, по умолчанию 10)>
#   give {to,item,amount} -> плагин economy: подойти, сделка, положить предмет/зени, подтвердить
#                            (итог — событие give_result; только жителю из dealAuto_names)
#   friend_request {to}   -> friend request <to> (только жителю; видимому); запрос жителя принимается в хуке
#   job_change {path,stage,steps,success} -> плагин jobChange: этап квеста смены профессии (шаги из progression.json)
#   sleep {seconds}       -> relog <seconds> (600..43200): выйти из игры и войти через seconds — сон жителя (ORG-012)
#   service {}            -> autostorage (если есть что сдать на склад) или autosell — продать/сдать/докупить
#   shop_open / shop_close -> openshop / closeshop (лавка Merchant: навык MC_VENDING и тележка)
#   offer_sell {to,item,amount,price} -> economy: как give, но в той же сделке ждёт price зени от покупателя  # market:
#   offer_buy {from,item,amount,price} -> economy: в сделку продавца положить price зени (deal add z)  # market:
#   offer_shop {title,items:[{id,price,amount}]} -> economy: тележка (cart_add) и %shop для openshop  # market:
#   mail_send {to,title,body,zeny?,item?,amount?} / mail_check {} / mail_take {mail_id} -> economy: RODEX  # market:
#   emote {id}            -> e <команда> (Commands.pm cmdEmotion, tables/emotions.txt); только номера
#                            из %EMOTES (приветствие, смех, сердце, вопрос, спасибо...), как safety.EMOTES
# Группа (AUT-055): приглашение в группу LR_<житель> плагин принимает сразу в хуке — иначе
# partyAuto 1 успевает отказать раньше, чем ответит мозг. Жители: config residents (через запятую),
# иначе dealAuto_names. В state: party_members [{name, online, hp_pct, map, x, y, leader}], party_leader.
# Поддержка (AUT-061): пакет умения сервера (packet_skilluse) Heal/Blessing/Increase AGI со мной как
# источником или целью -> событие support {skill, from, to, amount}; amount у Heal — сколько HP вылечено.
# Смерть: если персонаж мёртв, а AI не в auto дольше 3 с (оператор поставил ai manual), плагин
# сам включает ai auto — иначе OpenKore не делает респаун (AI::CoreLogic processDead только в auto).
# В state от плагина economy: items (зелья/крылья по ID), vend {can, open}, give (идёт передача).
# От плагина survival: survival {dps, attackers, last_action}; события survival, escape, danger.
package brainBridge;

use strict;
use utf8;
use IO::Socket::UNIX;
use Socket qw(SOCK_STREAM);
use Errno qw(EAGAIN EWOULDBLOCK EINTR);
use JSON::PP;
use Time::HiRes qw(time);
use Plugins;
use Globals qw($char $field $net $monstersList $playersList %config %jobs_lut %sex_lut @lastpm $accountID %ai_v
               %friends @friendsID);
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
my $deadManualSince;

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
	['party_invite',       \&onPartyInvite],
	['packet_skilluse',    \&onSkillUse],
	['friend_request',     \&onFriendRequest],
	['packet_sysMsg',      sub { onWorldMsg('sys', $_[1]{Msg}) }],           # ORG-039: 009A system_chat (announce)
	['packet_localBroadcast', sub { onWorldMsg('broadcast', $_[1]{Msg}) }],  # ORG-039: 01C3/040C local_broadcast
);

my %SUPPORT = (28 => 'AL_HEAL', 29 => 'AL_INCAGI', 34 => 'AL_BLESSING', 35 => 'AL_CURE');

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
		hp        => $char->{hp} + 0, hp_max => $char->{hp_max} + 0,
		sp        => $char->{sp} + 0, sp_max => $char->{sp_max} + 0,
		sitting   => ($char->{sitting} ? JSON::PP::true : JSON::PP::false),
		statuses  => [grep { defined } (sort keys %{$char->{statuses} || {}})[0 .. 19]],   # AUT-004: эффекты (яд, баффы)
		paused    => (($config{brainBridge_paused} // '') ne '' ? JSON::PP::true : JSON::PP::false),
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
		party_members => partyMembers(),
		friends   => [map { {name => "$friends{$_}{name}", online => ($friends{$_}{online} ? JSON::PP::true : JSON::PP::false)} }
		              grep { $friends{$_} && defined $friends{$_}{name} } 0 .. $#friendsID],
		party_leader  => (isPartyLeader() ? JSON::PP::true : JSON::PP::false),
		follow    => ($config{follow} ? $config{followTarget} : undef),
		combat    => (%combatProfile::current ? {%combatProfile::current} : undef),
		activity  => (AI::action() || 'idle'),
		players   => nearbyPlayers(),
		(defined &jobChange::status ? (job_change => jobChange::status()) : ()),
		(defined &economy::itemCounts ? (items => economy::itemCounts(), vend => economy::vendStatus(),
		                                 give => economy::giveStatus()) : ()),
		(defined &economy::buyStatus ? (buy => economy::buyStatus()) : ()),   # market: жду продавца
		(defined &survival::status ? (survival => survival::status()) : ()),
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

sub residents {
	my $list = $config{residents} // $config{dealAuto_names} // '';
	return grep { length } split /\s*,\s*/, $list;
}

sub partyMembers {
	return [] unless $char && $char->{party} && $char->{party}{joined} && $char->{party}{users};
	my @out;
	for my $id (sort keys %{$char->{party}{users}}) {
		my $u = $char->{party}{users}{$id};
		next unless defined $u->{name} && $u->{name} ne $char->{name};
		(my $map = $u->{map} // '') =~ s/\.(gat|rsw)$//;
		# HP участника сервер шлёт только в зоне видимости — без видимости оно устаревает (ORG D14);
		# смерть — флаг видимого игрока ($player->{dead}), а не HP 0.
		my ($p) = $playersList ? grep { defined $_->{name} && $_->{name} eq $u->{name} } @{$playersList->getItems() || []} : ();
		push @out, {name => "$u->{name}", online => ($u->{online} ? JSON::PP::true : JSON::PP::false),
		            visible => ($p ? JSON::PP::true : JSON::PP::false),
		            dead => ($p && $p->{dead} ? JSON::PP::true : JSON::PP::false),
		            hp_pct => ($p ? pct($u->{hp}, $u->{hp_max}) : undef), map => (length $map ? $map : undef),
		            x => ($u->{pos} ? $u->{pos}{x} : undef), y => ($u->{pos} ? $u->{pos}{y} : undef),
		            leader => ($u->{admin} ? JSON::PP::true : JSON::PP::false)};
	}
	return \@out;
}

sub isPartyLeader {
	return 0 unless $char && $char->{party} && $char->{party}{users} && $accountID;
	my $me = $char->{party}{users}{$accountID};
	return $me && $me->{admin} ? 1 : 0;
}

sub onPartyInvite {
	my (undef, $args) = @_;
	my $party = "$args->{partyName}";
	my ($owner) = $party =~ /^LR_(.+)$/;
	if (defined $owner && grep { $_ eq $owner } residents()) {
		message "[brainBridge] приглашение в группу жителя $party — принимаю\n", 'system';
		Commands::run('party join 1');
		event('party_joined_auto', party => $party);
	} else {
		event('party_invite', party => $party);
	}
}

# ORG-024: запрос дружбы от жителя принимаем сразу (как приглашение в группу); чужие — решает человек.
sub onFriendRequest {
	my (undef, $args) = @_;
	my $name = "$args->{name}";
	if (grep { $_ eq $name } residents()) {
		message "[brainBridge] житель $name предлагает дружбу — принимаю\n", 'system';
		Commands::run('friend accept');
	}
	event('friend_request', from => $name);
}

sub nameOf {
	my ($id) = @_;
	return undef unless defined $id;
	return $char->{name} if $accountID && $id eq $accountID;
	my $p = $playersList ? $playersList->getByID($id) : undef;
	return $p ? "$p->{name}" : undef;
}

sub onSkillUse {
	my (undef, $args) = @_;
	my $skill = $SUPPORT{$args->{skillID} // -1} or return;
	return unless $accountID && (($args->{sourceID} // '') eq $accountID || ($args->{targetID} // '') eq $accountID);
	# amount у Heal — расчётное лечение из пакета (rAthena heal.cpp до status_heal), не прирост HP:
	# если цель — я, приложить HP до пакета (обновление HP приходит отдельным пакетом позже).
	my %mine = (($args->{targetID} // '') eq $accountID) ? (hp_before => $char->{hp} + 0, hp_max => $char->{hp_max} + 0) : ();
	event('support', skill => $skill, from => nameOf($args->{sourceID}), to => nameOf($args->{targetID}),
	      amount => ($args->{amount} // 0) + 0, %mine);
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

# ORG-039: объявление сервера -> событие world_msg {text, source}. Только данные: мозг пишет их в память
# как note и в шину мира как event, команд по ним не исполняет. Повтор того же текста за 60 с не шлётся.
my %worldMsgSeen;
sub onWorldMsg {
	my ($source, $msg) = @_;
	my $text = cleanText($msg);
	return unless length $text;
	my $now = time;
	delete $worldMsgSeen{$_} for grep { $now - $worldMsgSeen{$_} > 60 } keys %worldMsgSeen;
	return if exists $worldMsgSeen{$text};
	$worldMsgSeen{$text} = $now;
	event('world_msg', text => $text, source => $source);
}

sub cleanText {
	my ($t) = @_;
	$t = '' unless defined $t;
	$t =~ s/[\x00-\x1f\x7f]+/ /g;
	$t =~ s/^\s+|\s+$//g;
	return substr($t, 0, 120);
}

# Безопасные эмоции: номер -> команда OpenKore «e <команда>» (тот же список в brain/live_brain/safety.py).
our %EMOTES = (1 => '?', 2 => 'ho', 3 => 'lv', 5 => 'ic', 9 => '...', 12 => 'wav', 15 => 'thx', 17 => 'sry',
               18 => 'heh', 20 => 'hmm', 21 => 'no1', 28 => 'sob', 29 => 'gg', 33 => 'ok');

# Перед движением: если бот посажен командой sit (флаг sitAuto_forcedBySitCommand) или сидит — stand,
# иначе OpenKore не пойдёт к lockMap/за целью (флаг снимает только cmdStand).
sub standFirst {
	return ($ai_v{sitAuto_forcedBySitCommand} || ($char && $char->{sitting})) ? 'stand' : ();
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
		return (1, [standFirst(), "follow $to"]);
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
		return (1, [standFirst(), "conf lockMap $map", "conf lockMap_x $x", "conf lockMap_y $y",
		            'conf lockMap_randX 2', 'conf lockMap_randY 2']);
	} elsif ($kind eq 'hunt') {
		# Распорядок: охотиться на карте (без точки), встать, если сидел.
		my $map = $a->{map} || '';
		return (0, 'неверная карта') unless $map =~ /^[a-z0-9_]{3,16}$/;
		return (1, ["conf lockMap $map", 'conf lockMap_x none', 'conf lockMap_y none',
		            'conf lockMap_randX none', 'conf lockMap_randY none', 'stand']);
	} elsif ($kind eq 'unstuck') {
		# Застрял: сбросить очередь AI и шагнуть в случайную проходимую клетку в радиусе radius.
		my $pos = $char && $char->{pos_to};
		return (0, 'позиция неизвестна') unless $pos && $field;
		my $r = ($a->{radius} // 10) =~ /^\d+$/ ? $a->{radius} : 10;
		$r = 5 if $r < 5;
		$r = 30 if $r > 30;
		for (1 .. 40) {
			my ($x, $y) = ($pos->{x} + int(rand(2 * $r + 1)) - $r, $pos->{y} + int(rand(2 * $r + 1)) - $r);
			next if $x < 1 || $y < 1 || ($x == $pos->{x} && $y == $pos->{y});
			return (1, ['ai clear', "move $x $y"]) if $field->isWalkable($x, $y);
		}
		return (0, 'нет проходимой клетки рядом');
	} elsif ($kind eq 'sit') {
		# НЕ команда sit: она ставит sitAuto_forcedBySitCommand, и OpenKore перестаёт продавать, ходить к
		# lockMap и атаковать (Commands.pm cmdSit; CoreLogic processStartAutoStorageBuySell/processLockMap).
		# Сесть без флага OpenKore умеет сам: sitAuto_idle 1 + ai_sit_idle — бот садится, когда ему нечего делать.
		return (1, {note => 'сяду сам, когда освобожусь (sitAuto_idle)'});
	} elsif ($kind eq 'stand') {
		return (1, 'stand');
	} elsif ($kind eq 'clear_point') {
		return (1, ['conf lockMap_x none', 'conf lockMap_y none', 'conf lockMap_randX none', 'conf lockMap_randY none']);
	} elsif ($kind eq 'give') {
		return (0, 'плагин economy не загружен') unless defined &economy::startGive;
		my ($ok, $desc) = economy::startGive($a);
		return $ok ? (1, {note => $desc}) : (0, $desc);
	} elsif ($kind eq 'shop_open') {
		my $v = defined &economy::vendStatus ? economy::vendStatus() : undef;
		return (0, 'нет навыка лавки или тележки') unless $v && $v->{can};
		return (0, 'лавка уже открыта') if $v->{open};
		return (1, 'openshop');
	} elsif ($kind eq 'shop_close') {
		my $v = defined &economy::vendStatus ? economy::vendStatus() : undef;
		return (0, 'лавка не открыта') unless $v && $v->{open};
		return (1, 'closeshop');
	} elsif ($kind eq 'offer_sell') {                                     # market: продать жителю (предмет за зени, одна сделка)
		return (0, 'плагин economy не загружен') unless defined &economy::startGive;              # market:
		return (0, 'нет цены') unless defined $a->{price};                                       # market:
		my ($ok, $desc) = economy::startGive($a);                                                 # market:
		return $ok ? (1, {note => $desc}) : (0, $desc);                                           # market:
	} elsif ($kind eq 'offer_buy') {                                      # market: купить у жителя: положить зени в его сделку
		return (0, 'плагин economy не загружен') unless defined &economy::startBuy;               # market:
		my ($ok, $desc) = economy::startBuy($a);                                                  # market:
		return $ok ? (1, {note => $desc}) : (0, $desc);                                           # market:
	} elsif ($kind eq 'offer_shop') {                                     # market: товары лавки (тележка + %shop)
		return (0, 'плагин economy не загружен') unless defined &economy::setupShop;              # market:
		my ($ok, $desc) = economy::setupShop($a);                                                 # market:
		return $ok ? (1, {note => $desc}) : (0, $desc);                                           # market:
	} elsif ($kind eq 'mail_send') {                                      # market: письмо RODEX жителю
		return (0, 'плагин economy не загружен') unless defined &economy::startMail;              # market:
		my ($ok, $desc) = economy::startMail($a);                                                 # market:
		return $ok ? (1, {note => $desc}) : (0, $desc);                                           # market:
	} elsif ($kind eq 'mail_check') {                                     # market: открыть ящик -> mail_received
		return (0, 'плагин economy не загружен') unless defined &economy::startMailCheck;         # market:
		my ($ok, $desc) = economy::startMailCheck();                                              # market:
		return $ok ? (1, {note => $desc}) : (0, $desc);                                           # market:
	} elsif ($kind eq 'mail_take') {                                      # market: забрать вложение письма
		return (0, 'плагин economy не загружен') unless defined &economy::startMailTake;          # market:
		my ($ok, $desc) = economy::startMailTake($a);                                             # market:
		return $ok ? (1, {note => $desc}) : (0, $desc);                                           # market:
	} elsif ($kind eq 'emote') {
		my $id = $a->{id} // '';
		return (0, 'эмоция не из списка') unless $id =~ /^\d{1,2}$/ && exists $EMOTES{$id};
		return (1, "e $EMOTES{$id}");
	} elsif ($kind eq 'friend_request') {
		my $to = cleanText($a->{to});
		return (0, 'неверный адресат') unless $to =~ /^[^"\s]{1,23}$/;
		return (0, 'не житель') unless grep { $_ eq $to } residents();
		return (1, "friend request $to");
	} elsif ($kind eq 'job_change') {
		return (0, 'плагин jobChange не загружен') unless defined &jobChange::start;
		my ($ok, $desc) = jobChange::start($a);
		return $ok ? (1, {note => $desc}) : (0, $desc);
	} elsif ($kind eq 'sleep') {
		my $sec = $a->{seconds} // '';
		return (0, 'неверная длительность сна') unless $sec =~ /^\d+$/ && $sec >= 600 && $sec <= 43200;
		return (1, "relog $sec");
	} elsif ($kind eq 'service') {
		my $store = $config{storageAuto} && eval { AI::ai_storageAutoCheck() };
		return (1, $store ? 'autostorage' : 'autosell');
	} elsif ($kind eq 'pause') {
		return (1, []) if ($config{brainBridge_paused} // '') ne '';      # уже на паузе
		my $saved = join(' ', map { defined $config{$_} && $config{$_} ne '' ? $config{$_} : 0 } qw(attackAuto route_randomWalk));
		return (1, ["conf -f brainBridge_paused $saved", 'conf attackAuto 1', 'conf route_randomWalk 0']);
	} elsif ($kind eq 'resume') {
		my ($attack, $walk) = split ' ', ($config{brainBridge_paused} // '');
		return (1, ['ai auto']) unless defined $walk;
		return (1, ["conf attackAuto $attack", "conf route_randomWalk $walk", 'conf -f brainBridge_paused none', 'ai auto']);
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
	my ($ok, $res) = inGame() ? actionToCommand($msg) : (0, 'бот не в игре');
	if ($ok && ref $res eq 'HASH') {
		$res = $res->{note};                            # действие исполняет другой плагин (economy)
		message "[brainBridge] решение мозга -> $res\n", 'system';
	} elsif ($ok) {
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

# AUT-007: мёртвый персонаж в ai manual не сделает респаун — вернуть ai auto.
sub deadWatch {
	if (!inGame() || !$char->{dead} || AI::state() == AI::AUTO()) {
		undef $deadManualSince;
		return;
	}
	$deadManualSince //= time;
	return if time - $deadManualSince < 3;
	warning "[brainBridge] персонаж мёртв, а AI не в auto — включаю ai auto для респауна\n";
	Commands::run('ai auto');
	event('auto_resumed_dead');
	undef $deadManualSince;
}

sub onTick {
	deadWatch();
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
