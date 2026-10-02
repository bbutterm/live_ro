# economy — экономика жителя (live_ro): склад ценного лута, передача вещей жителю, лавка.
#
# 1. Склад. Карты (тип предмета 6), оружие и броня (типы 4, 5; economy_storeEquipment) и предметы
#    из economy_storeIds (по умолчанию руда Oridecon/Elunium и необработанные) помечаются
#    «на склад, не продавать», если в items_control.txt для них нет своей строки (AUT-020:
#    правило «продавать всё» не уничтожает ценную вещь; дешёвое снаряжение продаётся по списку). Остальное делает OpenKore:
#    storageAuto в config.txt (Kafra) срабатывает вместе с продажей (itemsMaxWeight_sellOrStore).
# 2. Передача жителю (мозг: действие give). Подойти к жителю на 2 клетки (ограничение
#    rAthena TRADE_DISTANCE), предложить сделку, положить предмет или зени, подтвердить.
#    Вторая сторона принимает сама: dealAuto 3 + dealAuto_names <жители> в config.txt.
#    Итог — событие give_result {id, ok, reason}; «отдал» = сервер завершил сделку (complete_deal).
# 3. Лавка (только Merchant-ветка с навыком MC_VENDING и тележкой): статус для мозга;
#    открывает и закрывает её brainBridge командами openshop / closeshop (товары — shop.txt).
package economy;

use strict;
use utf8;
use Time::HiRes qw(time);
use JSON::PP;
use Plugins;
use Globals qw($char $field $playersList %config %items_control %currentDeal %outgoingDeal %incomingDeal $shopstarted);
use Log qw(message warning);
use Commands;

Plugins::register('economy', 'склад ценного лута, передача вещей жителю, лавка', \&onUnload);

my $hooks = Plugins::addHooks(
	['mainLoop_post',  \&onTick],
	['engaged_deal',   \&onEngaged],
	['error_deal',     \&onDealError],
	['cancelled_deal', \&onCancelled],
	['complete_deal',  \&onComplete],
	['deal_incoming',  \&onIncoming],
);

our %give;                      # текущая передача: id, to, item ('zeny' или nameID), amount, phase, since
our @TRACK = (569, 501, 502, 503, 504, 505, 506, 601, 602);   # зелья и крылья — счётчики для мозга
my $DEFAULT_STORE = '984,985,756,757';                    # Oridecon, Elunium, Rough Oridecon, Rough Elunium
my %STEP_TIMEOUT = (approach => 40, request => 15, add => 10, finalize => 10, final => 20);
my $TRADE_DISTANCE = 2;
my $lastKeep = 0;
my $lastMove = 0;
my $lastRefuse = 0;

sub onUnload { Plugins::delHooks($hooks); }

# ---------- склад: ценное не продавать ----------

sub storeIds {
	my $list = defined $config{economy_storeIds} ? $config{economy_storeIds} : $DEFAULT_STORE;
	return map { $_ + 0 } grep { /^\d+$/ } split /\s*,\s*/, $list;
}

sub keepValuables {
	return 0 unless $char && $char->inventory;
	my %ids = map { $_ => 1 } storeIds();
	my %types;
	$types{6} = 1 if !defined $config{economy_storeCards} || $config{economy_storeCards} ne '0';
	@types{4, 5} = (1, 1) if !defined $config{economy_storeEquipment} || $config{economy_storeEquipment} ne '0';
	my $n = 0;
	for my $item (@{$char->inventory}) {
		next if $item->{equipped};
		next unless $types{$item->{type} // -1} || $ids{$item->{nameID}};
		next if $items_control{lc($item->{name} // '')} || $items_control{$item->{nameID}};
		$items_control{$item->{nameID}} = {keep => 0, storage => 1, sell => 0, cart_add => 0, cart_get => 0};
		$n++;
	}
	return $n;
}

# ---------- состояние для мозга ----------

sub itemCounts {
	my %count = map { $_ => 0 } @TRACK;
	return \%count unless $char && $char->inventory;
	for my $item (@{$char->inventory}) {
		next if $item->{equipped};
		$count{$item->{nameID}} += $item->{amount} if exists $count{$item->{nameID}};
	}
	return \%count;
}

sub vendStatus {
	return undef unless $char;
	my $lv = $char->{skills} && $char->{skills}{MC_VENDING} ? $char->{skills}{MC_VENDING}{lv} : 0;
	return {can => ($lv && eval { $char->cartActive } ? 1 : 0), open => ($shopstarted ? 1 : 0)};
}

sub giveStatus {
	return undef unless %give;
	return {map { $_ => $give{$_} } qw(id to item amount phase)};
}

# ---------- передача жителю ----------

sub findPlayer {
	my ($name) = @_;
	return undef unless $playersList;
	my ($p) = grep { defined $_->{name} && $_->{name} eq $name } @{$playersList->getItems() || []};
	return $p;
}

sub distanceTo {
	my ($p) = @_;
	my ($a, $b) = ($char->{pos_to}, $p->{pos_to});
	return 999 unless $a && $b;
	my ($dx, $dy) = (abs($a->{x} - $b->{x}), abs($a->{y} - $b->{y}));
	return $dx > $dy ? $dx : $dy;
}

sub report {
	my ($ok, $reason) = @_;
	my %g = %give;
	%give = ();
	my $what = $g{item} eq 'zeny' ? "$g{amount} зени" : "предмет $g{item} x $g{amount}";
	if ($ok) { message "[economy] передал $g{to}: $what\n", 'system'; }
	else     { warning "[economy] не передал $g{to} ($what): $reason\n"; }
	my $ev = brainBridge->can('event');
	$ev->('give_result', id => $g{id}, to => $g{to}, item => $g{item}, amount => $g{amount},
	      ok => ($ok ? JSON::PP::true() : JSON::PP::false()), reason => $reason) if $ev;
}

sub failGive {
	my ($reason, $cancel) = @_;
	return unless %give;
	Commands::run('deal no') if $cancel && (%currentDeal || %outgoingDeal);
	report(0, $reason);
}

# Возвращает (1, описание) — передача начата, или (0, причина).
sub startGive {
	my ($a) = @_;
	return (0, 'уже идёт передача') if %give;
	return (0, 'идёт другая сделка') if %currentDeal || %outgoingDeal || %incomingDeal;
	return (0, 'открыта лавка') if $shopstarted;
	my $to = $a->{to} // '';
	return (0, 'неверный адресат') unless $to =~ /^[^"]{1,23}$/;
	my @names = split /\s*,\s*/, ($config{dealAuto_names} // '');
	return (0, 'адресат не в dealAuto_names') unless grep { $_ eq $to } @names;
	my $amount = int($a->{amount} // 0);
	return (0, 'неверное количество') unless $amount > 0;
	my $item = $a->{item} // '';
	if ($item eq 'zeny') {
		return (0, 'не хватает зени') if $amount > ($char->{zeny} // 0);
	} else {
		return (0, 'неверный предмет') unless $item =~ /^\d{1,6}$/;
		my $have = 0;
		$have += $_->{amount} for grep { !$_->{equipped} && $_->{nameID} == $item } @{$char->inventory};
		return (0, "нет столько: есть $have") if $have < $amount;
	}
	return (0, "$to не рядом") unless findPlayer($to);
	%give = (id => $a->{id}, to => $to, item => $item, amount => $amount, phase => 'approach', since => time);
	$lastMove = 0;
	return (1, "передача $to: " . ($item eq 'zeny' ? "$amount зени" : "предмет $item x $amount"));
}

sub phase {
	my ($p) = @_;
	@give{qw(phase since)} = ($p, time);
}

sub driveGive {
	return unless %give;
	if ($char->{dead}) { return failGive('персонаж мёртв', 1); }
	if (time - $give{since} > $STEP_TIMEOUT{$give{phase}}) {
		return failGive("таймаут на шаге $give{phase}", 1);
	}
	if ($give{phase} eq 'approach') {
		my $p = findPlayer($give{to}) or return failGive("$give{to} ушёл из виду");
		if (distanceTo($p) <= $TRADE_DISTANCE) {
			phase('request');
			Commands::run(qq{deal "$give{to}"});
		} elsif (time - $lastMove >= 5) {
			$lastMove = time;
			Commands::run('stand') if $char->{sitting};
			Commands::run("move $p->{pos_to}{x} $p->{pos_to}{y}");
		}
	} elsif ($give{phase} eq 'add' && time - $give{since} >= 1) {
		if ($give{item} eq 'zeny') {
			Commands::run("deal add z $give{amount}");
		} else {
			my ($inv) = grep { !$_->{equipped} && $_->{nameID} == $give{item} } @{$char->inventory};
			return failGive('предмет пропал из рюкзака', 1) unless $inv;
			my $amount = $inv->{amount} < $give{amount} ? $inv->{amount} : $give{amount};
			$give{amount} = $amount;
			Commands::run("deal add $inv->{binID} $amount");
		}
		phase('finalize');
	} elsif ($give{phase} eq 'finalize' && time - $give{since} >= 1) {
		Commands::run('deal');            # подтвердить своё; обмен завершит dealAuto 3 после второй стороны
		phase('final');
	}
}

our $dealWith;               # с кем открыта сделка (обе стороны) — для события deal_complete

sub onEngaged {
	my (undef, $args) = @_;
	$dealWith = $args->{name};
	return unless %give && $give{phase} eq 'request';
	if (($args->{name} // '') ne $give{to}) {
		return failGive("сделка открылась не с тем ($args->{name})", 1);
	}
	phase('add');
}

sub onDealError {
	my (undef, $args) = @_;
	return unless %give && $give{phase} eq 'request';
	my %why = (0 => 'слишком далеко', 2 => 'адресат занят другой сделкой', 5 => 'адресат у склада');
	failGive('сервер отклонил сделку: ' . ($why{$args->{type} // ''} || "код " . ($args->{type} // '?')));
}

sub onCancelled {
	undef $dealWith;
	failGive('сделка отменена') if %give && $give{phase} ne 'approach';
}

# dealAuto 3 с dealAuto_names молча игнорирует чужие предложения — висящее предложение
# мешало бы передаче между жителями. Чужим — явный отказ (как dealAuto 1).
sub onIncoming {
	my (undef, $args) = @_;
	return unless %incomingDeal;
	my @names = split /\s*,\s*/, ($config{dealAuto_names} // '');
	return if !@names || grep { $_ eq ($incomingDeal{name} // '') } @names;
	$args->{return} = 1;
	return if time - $lastRefuse < 2;
	$lastRefuse = time;
	message "[economy] отклоняю сделку от $incomingDeal{name}: не житель\n", 'system';
	Commands::run('deal no');
}

# Сервер завершил сделку. Получателю это доказательство «передача была именно сделкой с жителем»,
# а не покупкой у NPC (мозг: economy.check_request ждёт deal_complete от того, кого просил).
sub onComplete {
	my $with = $dealWith;
	undef $dealWith;
	my $ev = brainBridge->can('event');
	$ev->('deal_complete', with => $with, gave => (%give ? JSON::PP::true() : JSON::PP::false())) if $ev && defined $with;
	report(1, 'сделка завершена сервером') if %give && $give{phase} eq 'final';
}

# ---------- тик ----------

sub onTick {
	return unless $char;
	if (time - $lastKeep >= 5) {
		$lastKeep = time;
		keepValuables();
	}
	driveGive();
}

1;
