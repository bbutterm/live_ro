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
#    открывает и закрывает её brainBridge командами openshop / closeshop (товары — shop.txt
#    или %shop, собранный из действия offer_shop: товар в тележке ищется по ID, ORG-034).
# 4. Торговля с жителем (ORG-033): одна двусторонняя сделка. Продавец (offer_sell) кладёт предмет
#    и подтверждает; покупатель (offer_buy) при открытии сделки с продавцом кладёт зени (deal add z).
#    Каждая сторона в хуке finalized_deal проверяет, что вторая положила обещанное, иначе deal no.
#    Обмен атомарен на сервере (rAthena trade): либо оба получили, либо никто.
#    Итоги: give_result (с price/paid) у продавца, buy_result у покупателя.
# 5. Почта RODEX (ORG-035), команды Commands.pm cmdRodex: rodex open -> write <имя> -> settitle ->
#    setbody -> setzeny -> add <инв.#> <кол-во> -> send -> close. Итог — хук packet/rodex_write_result
#    (fail 0 = отправлено) -> событие mail_result. Входящие: хук rodex_unread_mail или действие
#    mail_check -> rodex open -> хук rodex_mail_list -> событие mail_received (непрочитанные, от жителей).
#    Забрать (mail_take): rodex open -> read -> хук rodex_mail -> getzeny / getitems -> close -> mail_taken.
# 6. Для метрик (ORG-037): npc_sold (зени за автопродажу NPC), vend_sold (продажа из лавки).
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
	['finalized_deal', \&onFinalized],
	['rodex_unread_mail', \&onUnreadMail],
	['rodex_mail_list',   \&onMailList],
	['rodex_mail',        \&onMailRead],
	['packet/rodex_write_result', \&onMailWriteResult],
	['packet/rodex_get_zeny',     \&onMailGot],
	['packet/rodex_get_item',     \&onMailGot],
	['AI_sell_auto',           \&onNpcSellStart],
	['AI_sell_auto_completed', \&onNpcSellDone],
	['vending_item_sold',      \&onVendSold],
);

our %give;                      # текущая передача: id, to, item ('zeny' или nameID), amount, phase, since
our @TRACK = (569, 501, 502, 503, 504, 505, 506, 601, 602);   # зелья и крылья — счётчики для мозга
our %buy;                       # покупка у жителя: id, from, item, amount, price, phase (wait|deal), since
our %mail;                      # почта: op (send|check|take), phase, since и данные операции
our %wantShop;                  # лавка из offer_shop: title, items [{id, price, amount}]
my $MAX_VALUABLES = 40;         # сколько ценных позиций рюкзака отдавать мозгу (размер state)
my $BUY_TIMEOUT = 240;          # покупатель ждёт продавца
my %MAIL_TIMEOUT = (open => 15, write => 15, fill => 15, send => 20, read => 15, get => 15, list => 15);
my $MAIL_TAX_ITEM = 2500;       # rAthena conf/battle/misc.conf mail_attachment_price; Commands.pm cmdRodex send
my %seenMail;                   # о каких письмах уже сообщили мозгу
my $mailCheckWanted = 0;
my $npcSellZeny;
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

# Счётчики для мозга: зелья/крылья (@TRACK, всегда) и ценное в рюкзаке — карты, снаряжение (не надетое),
# руда из economy_storeIds, прочий лут (тип 3) — не больше $MAX_VALUABLES позиций (цены знает мозг, prices.json).
sub itemCounts {
	my %count = map { $_ => 0 } @TRACK;
	return \%count unless $char && $char->inventory;
	my %ids = map { $_ => 1 } storeIds();
	my %rank = (6 => 0, 4 => 2, 5 => 2, 3 => 3);
	my %extra;
	for my $item (@{$char->inventory}) {
		next if $item->{equipped};
		my $id = $item->{nameID};
		if (exists $count{$id}) {
			$count{$id} += $item->{amount};
			next;
		}
		my $r = $ids{$id} ? 1 : $rank{$item->{type} // -1};
		next unless defined $r;
		$extra{$id}{n} += $item->{amount};
		$extra{$id}{r} = $r;
	}
	my @keep = (sort { $extra{$a}{r} <=> $extra{$b}{r} || $a <=> $b } keys %extra)[0 .. $MAX_VALUABLES - 1];
	$count{$_} = $extra{$_}{n} for grep { defined } @keep;
	return \%count;
}

sub skillLv {
	my ($handle) = @_;
	return $char->{skills} && $char->{skills}{$handle} ? ($char->{skills}{$handle}{lv} // 0) + 0 : 0;
}

sub vendStatus {
	return undef unless $char;
	my $lv = skillLv('MC_VENDING');
	my $cart = $lv && eval { $char->cartActive } ? 1 : 0;
	my %st = (can => $cart, open => ($shopstarted ? 1 : 0),
	          overcharge => skillLv('MC_OVERCHARGE'), discount => skillLv('MC_DISCOUNT'));
	if ($cart) {                       # тележка: {id: количество}, не больше 30 позиций
		my %c;
		$c{$_->{nameID}} += $_->{amount} for grep { defined $_->{nameID} } @{$char->cart || []};
		my @ids = (sort { $a <=> $b } keys %c)[0 .. 29];
		$st{cart} = {map { $_ => $c{$_} } grep { defined } @ids};
		$st{slots} = $lv + 2;           # Misc::makeShop: не больше MC_VENDING + 2 товаров
	}
	return \%st;
}

sub giveStatus {
	return undef unless %give;
	return {map { $_ => $give{$_} } grep { defined $give{$_} } qw(id to item amount phase price)};
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
	      ok => ($ok ? JSON::PP::true() : JSON::PP::false()), reason => $reason,
	      (defined $g{price} ? (price => $g{price} + 0, paid => ($g{paid} // 0) + 0) : ())) if $ev;
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
	return (0, 'жду продавца') if %buy;
	return (0, 'занят почтой') if %mail;
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
	my $price;
	if (defined $a->{price}) {                       # продажа жителю (offer_sell): ждём зени в той же сделке
		return (0, 'неверная цена') unless $a->{price} =~ /^\d{1,9}$/ && $a->{price} > 0;
		return (0, 'за зени не продают зени') if $item eq 'zeny';
		$price = $a->{price} + 0;
	}
	%give = (id => $a->{id}, to => $to, item => $item, amount => $amount, phase => 'approach', since => time,
	         (defined $price ? (price => $price) : ()));
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
		return if $currentDeal{other_finalize} && !paymentOk();
		Commands::run('deal');            # подтвердить своё; обмен завершит dealAuto 3 после второй стороны
		phase('final');
	}
}

# Продажа: покупатель положил не меньше обещанного? Иначе отменить сделку (до обмена — ничего не теряется).
sub paymentOk {
	return 1 unless %give && defined $give{price};
	my $paid = $currentDeal{other_zeny} // 0;
	$give{paid} = $paid;
	return 1 if $paid >= $give{price};
	failGive("покупатель положил $paid зени из $give{price}", 1);
	return 0;
}

our $dealWith;               # с кем открыта сделка (обе стороны) — для события deal_complete

sub onEngaged {
	my (undef, $args) = @_;
	$dealWith = $args->{name};
	engagedBuy($args->{name});
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
	reportBuy(0, 'сделка отменена') if %buy && $buy{phase} eq 'deal';
}

# Вторая сторона подтвердила (Network::Receive deal_finalize -> хук finalized_deal): проверить её часть.
sub onFinalized {
	paymentOk() if %give && $give{phase} eq 'final';
	goodsOk() if %buy && $buy{phase} eq 'deal';
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
	reportBuy(1, 'сделка завершена сервером') if %buy && $buy{phase} eq 'deal';
}

# ---------- покупка у жителя (offer_buy) ----------

# Возвращает (1, описание) или (0, причина). Покупатель не подходит сам: продавец идёт к нему (startGive).
sub startBuy {
	my ($a) = @_;
	return (0, 'уже жду продавца') if %buy;
	return (0, 'идёт передача') if %give;
	return (0, 'занят почтой') if %mail;
	return (0, 'открыта лавка') if $shopstarted;
	my $from = $a->{from} // '';
	return (0, 'неверный продавец') unless $from =~ /^[^"]{1,23}$/;
	my @names = split /\s*,\s*/, ($config{dealAuto_names} // '');
	return (0, 'продавец не в dealAuto_names') unless grep { $_ eq $from } @names;
	my ($item, $amount, $price) = ($a->{item} // '', $a->{amount} // '', $a->{price} // '');
	return (0, 'неверный предмет') unless $item =~ /^\d{1,6}$/;
	return (0, 'неверное количество') unless $amount =~ /^\d{1,5}$/ && $amount > 0;
	return (0, 'неверная цена') unless $price =~ /^\d{1,9}$/ && $price > 0;
	return (0, 'не хватает зени') if $price > ($char->{zeny} // 0);
	%buy = (id => $a->{id}, from => $from, item => $item + 0, amount => $amount + 0, price => $price + 0,
	        phase => 'wait', since => time);
	return (1, "жду $from: предмет $item x $amount за $price зени");
}

sub buyStatus {
	return undef unless %buy;
	return {map { $_ => $buy{$_} } qw(id from item amount price phase)};
}

sub engagedBuy {
	my ($name) = @_;
	return unless %buy && $buy{phase} eq 'wait' && ($name // '') eq $buy{from};
	return reportBuy(0, 'не хватает зени') if $buy{price} > ($char->{zeny} // 0);
	Commands::run("deal add z $buy{price}");   # cmdDeal: you_zeny уйдёт на сервер при подтверждении (dealAuto 3)
	@buy{qw(phase since)} = ('deal', time);
}

# Продавец положил обещанное? Иначе отменить: dealAuto 3 подтвердил бы сделку с нашими зени.
sub goodsOk {
	my $got = $currentDeal{other} && $currentDeal{other}{$buy{item}} ? $currentDeal{other}{$buy{item}}{amount} // 0 : 0;
	$buy{got} = $got;
	return 1 if $got >= $buy{amount};
	Commands::run('deal no');
	reportBuy(0, "продавец положил $got из $buy{amount}");
	return 0;
}

sub reportBuy {
	my ($ok, $reason) = @_;
	my %b = %buy;
	%buy = ();
	if ($ok) { message "[economy] купил у $b{from}: предмет $b{item} x $b{amount} за $b{price} зени\n", 'system'; }
	else     { warning "[economy] покупка у $b{from} не состоялась: $reason\n"; }
	my $ev = brainBridge->can('event');
	$ev->('buy_result', id => $b{id}, from => $b{from}, item => $b{item}, amount => $b{amount}, price => $b{price},
	      ok => ($ok ? JSON::PP::true() : JSON::PP::false()), reason => $reason) if $ev;
}

sub driveBuy {
	return unless %buy;
	my $limit = $buy{phase} eq 'wait' ? $BUY_TIMEOUT : 60;
	if (time - $buy{since} > $limit) {
		Commands::run('deal no') if $buy{phase} eq 'deal' && %currentDeal;
		reportBuy(0, "таймаут на шаге $buy{phase}");
	}
}

# ---------- почта RODEX ----------

sub mailText {
	my ($t, $max) = @_;
	$t = '' unless defined $t;
	$t =~ s/[\x00-\x1f\x7f]+/ /g;
	$t =~ s/;;+/;/g;                         # Commands::run делит строку на команды по ';;'
	$t =~ s/^\s+|\s+$//g;
	return substr($t, 0, $max);
}

sub mailEvent {
	my ($kind, %data) = @_;
	my $ev = brainBridge->can('event');
	$ev->($kind, %data) if $ev;
}

sub mailPhase {
	my ($p) = @_;
	@mail{qw(phase since)} = ($p, time);
}

# Номер письма для команд rodex read/getzeny/getitems: cmdRodex понимает 1-3 цифры как номер в списке
# (page_index), длиннее — как mail_id. Короткий mail_id переводим в page_index, если он однозначен.
sub mailRef {
	my ($id) = @_;
	my $list = $Globals::rodexList;
	return undef unless $list && $list->{mails} && $list->{mails}{$id};
	return $id if $id > 999;
	my $pi = $list->{mails}{$id}{page_index};
	my @same = grep { ($list->{mails}{$_}{page_index} // -1) == $pi } keys %{$list->{mails}};
	return @same == 1 ? $pi : undef;
}

sub residentsList { return grep { length } split /\s*,\s*/, ($config{dealAuto_names} // ''); }

# mail_send: (1, описание) или (0, причина).
sub startMail {
	my ($a) = @_;
	return (0, 'почта занята') if %mail;
	return (0, 'идёт передача') if %give || %buy || %currentDeal || %outgoingDeal || %incomingDeal;
	return (0, 'открыта лавка') if $shopstarted;
	my $to = $a->{to} // '';
	return (0, 'неверный адресат') unless $to =~ /^[^"]{1,23}$/;
	return (0, 'адресат не житель (dealAuto_names)') unless grep { $_ eq $to } residentsList();
	my $title = mailText($a->{title}, 24);
	return (0, 'заголовок 4-24 символа') if length $title < 4;   # cmdRodex settitle: не короче 4
	my $body = mailText($a->{body}, 200);
	return (0, 'пустое письмо') unless length $body;
	my $zeny = $a->{zeny} // 0;
	return (0, 'неверная сумма') unless $zeny =~ /^\d{1,9}$/;
	my ($item, $amount, $bin) = ($a->{item}, $a->{amount} // 1, undef);
	if (defined $item) {
		return (0, 'неверный предмет') unless $item =~ /^\d{1,6}$/;
		return (0, 'неверное количество') unless $amount =~ /^\d{1,5}$/ && $amount > 0;
		my ($inv) = grep { !$_->{equipped} && $_->{nameID} == $item && $_->{amount} >= $amount } @{$char->inventory};
		return (0, "нет предмета $item x $amount") unless $inv;
		$bin = $inv->{binID};
	}
	my $tax = int($zeny / 50) + (defined $item ? $MAIL_TAX_ITEM : 0);
	return (0, "не хватает зени: $zeny + сбор $tax") if $zeny + $tax > ($char->{zeny} // 0);
	%mail = (op => 'send', id => $a->{id}, to => $to, title => $title, body => $body, zeny => $zeny + 0,
	         (defined $item ? (item => $item + 0, amount => $amount + 0, bin => $bin) : ()));
	mailPhase(defined $Globals::rodexList ? 'write' : 'open');
	Commands::run('rodex open') if $mail{phase} eq 'open';
	return (1, "письмо $to: $title");
}

sub startMailCheck {
	return (0, 'почта занята') if %mail;
	return (0, 'идёт сделка') if %give || %buy || %currentDeal;
	%mail = (op => 'check');
	mailPhase('list');
	Commands::run('rodex close') if defined $Globals::rodexList;   # открыть заново — свежий список
	Commands::run('rodex open');
	return (1, 'проверяю почту');
}

sub startMailTake {
	my ($a) = @_;
	return (0, 'почта занята') if %mail;
	return (0, 'идёт сделка') if %give || %buy || %currentDeal;
	my $id = $a->{mail_id} // '';
	return (0, 'неверный номер письма') unless $id =~ /^\d{1,10}$/;
	%mail = (op => 'take', id => $a->{id}, mail_id => $id + 0, zeny => 0, items => []);
	mailPhase('list');
	Commands::run('rodex close') if defined $Globals::rodexList;
	Commands::run('rodex open');
	return (1, "забираю письмо $id");
}

sub finishMail {
	my ($ok, $reason) = @_;
	my %m = %mail;
	%mail = ();
	Commands::run('rodex cancel') if defined $Globals::rodexWrite;
	Commands::run('rodex close') if defined $Globals::rodexList;
	if ($m{op} eq 'send') {
		if ($ok) { message "[economy] письмо отправлено $m{to}\n", 'system'; }
		else     { warning "[economy] письмо $m{to} не отправлено: $reason\n"; }
		mailEvent('mail_result', id => $m{id}, to => $m{to}, title => $m{title}, zeny => $m{zeny},
		          (defined $m{item} ? (item => $m{item}, amount => $m{amount}) : ()),
		          ok => ($ok ? JSON::PP::true() : JSON::PP::false()), reason => $reason);
	} elsif ($m{op} eq 'take') {
		mailEvent('mail_taken', id => $m{id}, mail_id => $m{mail_id}, from => $m{from}, zeny => $m{zeny},
		          items => $m{items}, ok => ($ok ? JSON::PP::true() : JSON::PP::false()), reason => $reason);
	}
}

sub driveMail {
	return unless %mail;
	my $p = $mail{phase};
	return finishMail(0, "таймаут почты на шаге $p") if time - $mail{since} > ($MAIL_TIMEOUT{$p} || 15);
	if ($mail{op} eq 'send') {
		my $w = $Globals::rodexWrite;
		if ($p eq 'open' && defined $Globals::rodexList) {
			mailPhase('write');
		} elsif ($p eq 'write' && !$mail{asked}) {
			$mail{asked} = 1;
			Commands::run("rodex write $mail{to}");
		} elsif ($p eq 'write' && $w && $w->{target} && $w->{target}{char_id}) {   # rodex_check_player: адресат найден
			Commands::run("rodex settitle $mail{title}");
			Commands::run("rodex setbody $mail{body}");
			Commands::run("rodex setzeny $mail{zeny}") if $mail{zeny};
			Commands::run("rodex add $mail{bin} $mail{amount}") if defined $mail{item};
			mailPhase('fill');
		} elsif ($p eq 'fill' && $w) {
			my $added = defined $mail{item} ? eval { $w->{items}->size } : 1;
			return unless $added;                 # ждём rodex_add_item от сервера
			Commands::run('rodex send');
			mailPhase('send');
		}
	}
}

sub onMailWriteResult {
	my (undef, $args) = @_;
	return unless %mail && $mail{op} eq 'send' && $mail{phase} eq 'send';
	return finishMail(0, 'сервер отклонил письмо') if $args->{fail};
	finishMail(1, 'сервер принял письмо');
}

sub onUnreadMail {
	$mailCheckWanted = 1;            # проверить, когда тело свободно (onTick)
}

sub onMailList {
	my (undef, $args) = @_;
	my $mails = $args->{mails} || {};
	if (%mail && $mail{op} eq 'take' && $mail{phase} eq 'list') {
		my $m = $mails->{$mail{mail_id}};
		return finishMail(0, 'письма нет в ящике') unless $m;
		$mail{from} = $m->{sender};
		return finishMail(0, 'письмо не от жителя') unless grep { $_ eq ($m->{sender} // '') } residentsList();
		my $ref = mailRef($mail{mail_id});
		return finishMail(0, 'номер письма неоднозначен') unless defined $ref;
		$mail{ref} = $ref;
		mailPhase('read');
		Commands::run("rodex read $ref");
		return;
	}
	for my $id (sort { $a <=> $b } keys %$mails) {
		my $m = $mails->{$id};
		next if $m->{isRead} || $seenMail{$id}++;
		next unless grep { $_ eq ($m->{sender} // '') } residentsList();
		mailEvent('mail_received', mail_id => $id + 0, from => $m->{sender}, title => $m->{title},
		          attach => $m->{attach});
	}
	if (%mail && $mail{op} eq 'check') {
		%mail = ();
		Commands::run('rodex close');
	}
}

sub onMailRead {
	my (undef, $args) = @_;
	return unless %mail && $mail{op} eq 'take' && $mail{phase} eq 'read' && $args->{mailID} == $mail{mail_id};
	$mail{zeny} = ($args->{zeny} // 0) + 0;
	$mail{items} = [map { {id => $_->{nameID} + 0, amount => $_->{amount} + 0} } @{$args->{items} || []}];
	$mail{want} = [($mail{zeny} ? 'zeny' : ()), (@{$mail{items}} ? 'items' : ())];
	return finishMail(1, 'вложений нет') unless @{$mail{want}};
	mailNextGet();
}

sub mailNextGet {
	my $what = shift @{$mail{want}};
	return finishMail(1, 'вложение получено') unless $what;
	mailPhase('get');
	Commands::run($what eq 'zeny' ? "rodex getzeny $mail{ref}" : "rodex getitems $mail{ref}");
}

sub onMailGot {
	my (undef, $args) = @_;
	return unless %mail && $mail{op} eq 'take' && $mail{phase} eq 'get';
	return finishMail(0, 'сервер не отдал вложение') if $args->{fail};
	mailNextGet();
}

# ---------- лавка: товары из offer_shop (ORG-034) ----------

# Возвращает (1, описание) или (0, причина). Товары: [{id, price, amount}]; в тележку их кладёт OpenKore
# (items_control cart_add), %shop собирается из того, что уже лежит в тележке (имя — как его видит OpenKore).
sub setupShop {
	my ($a) = @_;
	my $v = vendStatus();
	return (0, 'нет навыка лавки или тележки') unless $v && $v->{can};
	return (0, 'лавка открыта') if $shopstarted;
	my $title = mailText($a->{title}, 36);
	$title =~ s/#//g;                         # parseShopControl: '#' — комментарий
	return (0, 'нет названия лавки') unless length $title;
	my @items;
	for my $it (@{ref $a->{items} eq 'ARRAY' ? $a->{items} : []}) {
		next unless ($it->{id} // '') =~ /^\d{1,6}$/ && ($it->{price} // '') =~ /^\d{1,10}$/;
		next unless $it->{price} > 0 && $it->{price} <= 1_000_000_000;
		my $amount = ($it->{amount} // 0) =~ /^\d{1,5}$/ ? $it->{amount} + 0 : 0;
		push @items, {id => $it->{id} + 0, price => $it->{price} + 0, amount => $amount};
		last if @items >= 12;
	}
	return (0, 'нет товаров') unless @items;
	for my $it (@items) {                    # в тележку и не продавать NPC
		$items_control{$it->{id}} = {keep => 0, storage => 0, sell => 0, cart_add => 1, cart_get => 0};
	}
	%wantShop = (title => $title, items => \@items);
	my $n = buildShop();
	return (1, "лавка «$title»: товаров " . scalar(@items) . ", в тележке $n");
}

sub buildShop {
	return 0 unless %wantShop && $char && !$shopstarted && eval { $char->cartActive };
	my @sale;
	for my $it (@{$wantShop{items}}) {
		my ($c) = grep { ($_->{nameID} // -1) == $it->{id} } @{$char->cart || []};
		push @sale, {name => $c->{name}, price => $it->{price}, amount => $it->{amount} || undef} if $c;
	}
	%Globals::shop = (title_line => $wantShop{title}, items => \@sale);
	return scalar @sale;
}

# ---------- метрики: продажи NPC и из лавки ----------

sub onNpcSellStart { $npcSellZeny //= $char->{zeny} if $char; }

sub onNpcSellDone {
	return unless defined $npcSellZeny && $char;
	my $got = ($char->{zeny} // 0) - $npcSellZeny;
	undef $npcSellZeny;
	mailEvent('npc_sold', zeny => $got + 0) if $got > 0;
}

sub onVendSold {
	my (undef, $args) = @_;
	my $art = $args->{vendArticle} || {};
	mailEvent('vend_sold', item => $art->{nameID}, name => $art->{name}, amount => ($args->{amount} // 0) + 0,
	          zeny => ($args->{zenyEarned} // 0) + 0);
}

# ---------- тик ----------

sub onTick {
	return unless $char;
	if (time - $lastKeep >= 5) {
		$lastKeep = time;
		keepValuables();
		buildShop() if %wantShop;
	}
	driveGive();
	driveBuy();
	driveMail();
	if ($mailCheckWanted && !%mail && !%give && !%buy && !%currentDeal && !$shopstarted) {
		$mailCheckWanted = 0;
		startMailCheck();
	}
}

1;
