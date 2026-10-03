# buyer — скупка (Buying Store) жителя live_ro (ORG-036, ТЗ Т-38). Без LLM: мозг решает, что и почём,
# плагин только исполняет и сообщает факты.
#
# Сервер (upstream/rathena e985006):
#   - навык ALL_BUYING_STORE (db/re/skill_db.yml:29272): SP 30 и 1 × Buy Market Permit (6377) на каждое открытие;
#     учит Mr. Hugh (alberta_in,58,52, npc/merchants/buying_shops.txt:104) — Merchant-ветка с MC_VENDING ≥ 1, 10 000 z,
#     в подарок 5 × 6377, дальше 200 z за штуку (до 50 за раз); 5 мест (skills/other/openbuyingstore.cpp);
#   - без навыка — Shabby Purchase Street Stall License 12548 (item_db_usable.yml:11082, script «buyingstore 2;»:
#     2 места), продаёт Mr. Jass (que_job01,68,84) по 500 z;
#   - buyingstore.cpp buyingstore_create: хотя бы 1 штука каждого предмета уже в рюкзаке; предмет с флагом
#     BuyingStore (руда, травы, лут etc — да; снаряжение — нет); цена 1..99 990 000; своё + покупка ≤ 9999;
#     лимит зени ≤ зени в кармане; не рядом с NPC (min_npc_vendchat_distance); не в торговле/лавке;
#   - продавец (buyingstore_trade): тот же map и AREA_SIZE (видимость); предмет без карт, не привязан.
# OpenKore (upstream/openkore 51de1dd): openbuyershop / closebuyershop (Misc.pm:6380 makeBuyerShop — товары по ИМЕНИ
# из %buyer_shop, навык и лицензия 6377 или предмет 12548), продавец — sendEnteringBuyer / sendBuyBulkBuyer
# (Commands.pm:6628 cmdBuyer). ОШИБКА upstream для нашего serverType kRO_RagexeRE_2018_06_20e: формат пакетов в
# Send/kRO/RagexeRE_2018_04_04b.pm:27-29 — 0811 buy_bulk_openShop 'a4 c a*' (нет len и storeName, лимит упакован
# как текст) и 0819 buy_bulk_buyer 'a4 a4 a*' (нет len), а сервер ждёт len,zeny,result,name[80],items
# (clif_shuffle.hpp:4754, packets_struct.hpp:3108) и len,AID,storeId,items (:3259). fixPackets() подменяет формат
# в $messageSender->{packet_list} (как Send/kRO/Sakexe_0.pm:228,231) — без правки подмодуля.
#
# Действия от мозга (brainBridge):
#   buyer_open {title, items:[{id, price, amount}]} -> %buyer_shop + openbuyershop (лимит зени = Σ цена × кол-во, не
#       больше зени — так считает OpenKore; бюджет задаёт мозг количествами);
#   buyer_close {}                                   -> closebuyershop;
#   (образцы списка — items_control keep 1: сервер требует хотя бы 1 штуку в рюкзаке при каждом открытии);
#   buyer_sell {from, items:[{id, keep, min}]}       -> войти в скупку жителя from, продать то, что она берёт по цене
#       ≥ min за штуку, оставив себе keep штук.
# События: buyer_result {ok, reason, items, limit} — открыта (пакет 0813) или нет (0812, таймаут);
#   buyer_bought {item, amount, zeny} — рост предмета из списка в рюкзаке и убыль зени, пока скупка открыта
#       (пакет 09E6 ZC_UPDATE_ITEM_FROM_BUYING_STORE2 OpenKore не разбирает — доказательство по рюкзаку);
#   buyer_closed {why, bought, spent};
#   buyer_sell_result {from, ok, reason, sold:[{item, amount, price}], zeny_gain} — продавец: пакеты 081C
#       (buying_store_item_delete) и прирост зени за сессию.
# Для мозга: buyer::status() -> state.buyer {can, skill, permits, shabby, slots, vending, pushcart, cart, open,
#   items, bought, spent, stores:[{name, title}], selling}.
# НЕ ПРОВЕРЕНО В ИГРЕ: только тест на заглушках (bots/tests/buyer.t).
package buyer;

use strict;
use utf8;
use Time::HiRes qw(time);
use JSON::PP;
use Plugins;
use Globals qw($char $playersList $messageSender %currentDeal $shopstarted $buyershopstarted %buyer_shop
               @selfBuyerItemList @buyerListsID %buyerLists $buyerPriceLimit %items_control);
use Log qw(message warning);
use Commands;

Plugins::register('buyer', 'скупка (Buying Store): открыть, продать в скупку жителя', \&onUnload);

my $hooks = Plugins::addHooks(
	['mainLoop_post',                        \&onTick],
	['packet/open_buying_store_item_list',   \&onOpened],
	['packet/open_buying_store_fail',        \&onOpenFail],
	['buyer_shop_closed',                    \&onClosed],
	['packet_buying_store2',                 \&onStoreList],
	['packet/buying_store_item_delete',      \&onSold],
	['packet/buying_store_fail',             \&onSellFail],
);

our $PERMIT = 6377;              # Buy_Market_Permit — расходуется навыком
our $SHABBY = 12548;             # Buy_Market_Permit2 — «buyingstore 2;» без навыка
our $MAX_PRICE = 99_990_000;     # buyingstore.cpp BUYINGSTORE_MAX_PRICE
our $MAX_AMOUNT = 9999;          # buyingstore.cpp BUYINGSTORE_MAX_AMOUNT
our $SKILL_SP = 30;              # skill_db.yml ALL_BUYING_STORE SpCost
my %TIMEOUT = (open => 15, enter => 10, settle => 3, sell => 12);
# Формат пакетов для сервера PACKETVER 20180620 (rAthena packets_struct.hpp), как в Send/kRO/Sakexe_0.pm:228,231.
our %FIX = (buy_bulk_openShop => ['v V C Z80 a*', [qw(len limitZeny result storeName itemInfo)]],
            buy_bulk_buyer    => ['v a4 a4 a*',   [qw(len buyerID buyingStoreID itemInfo)]]);
our %open;                       # своя скупка: phase (opening|open), since, zeny, base {id: n}, bought {id: n}, spent, why
our %sell;                       # продажа в чужую скупку: from, id, want, phase (enter|sell|settle), since, zeny, sold, pending
our $lastTick = 0;               # our — тест сбрасывает

sub onUnload { Plugins::delHooks($hooks); }

sub event {
	my $ev = brainBridge->can('event');
	$ev->(@_) if $ev;
}

# ---------- данные ----------

sub skillLv {
	my ($h) = @_;
	return $char && $char->{skills} && $char->{skills}{$h} ? ($char->{skills}{$h}{lv} // 0) + 0 : 0;
}

sub invItem {
	my ($id) = @_;
	return undef unless $char && $char->inventory;
	for my $it (@{$char->inventory}) {
		return $it if ($it->{nameID} // -1) == $id && !$it->{equipped};
	}
	return undef;
}

sub invCount {
	my ($id) = @_;
	return 0 unless $char && $char->inventory;
	my $n = 0;
	$n += $_->{amount} // 0 for grep { ($_->{nameID} // -1) == $id && !$_->{equipped} } @{$char->inventory};
	return $n;
}

# Скупки рядом: [{name, title}] — владелец по ID актёра (OpenKore держит их в @buyerListsID, пакет 0814).
sub stores {
	my @out;
	for my $id (grep { defined && length } @buyerListsID) {
		my $p = $playersList ? $playersList->getByID($id) : undef;
		next unless $p && defined $p->{name};
		push @out, {name => "$p->{name}", title => ($buyerLists{$id} || {})->{title} // ''};
		last if @out >= 10;
	}
	return \@out;
}

sub storeId {
	my ($name) = @_;
	for my $id (grep { defined && length } @buyerListsID) {
		my $p = $playersList ? $playersList->getByID($id) : undef;
		return $id if $p && ($p->{name} // '') eq $name;
	}
	return undef;
}

sub status {
	return undef unless $char;
	my %st = (skill => skillLv('ALL_BUYING_STORE'), vending => skillLv('MC_VENDING'), pushcart => skillLv('MC_PUSHCART'),
	          cart => (eval { $char->cartActive } ? 1 : 0), permits => invCount($PERMIT), shabby => invCount($SHABBY),
	          open => ($buyershopstarted ? 1 : 0));
	# Misc.pm makeBuyerShop: 5 мест, если в рюкзаке есть 6377, иначе 2; без навыка — предмет 12548.
	my $skill = $st{skill} && $st{permits};
	$st{can} = ($skill || $st{shabby}) ? 1 : 0;
	$st{slots} = $skill ? 5 : ($st{shabby} ? 2 : 0);
	if ($buyershopstarted) {
		$st{items} = [map { {id => ($_->{nameID} // 0) + 0, amount => ($_->{amount} // 0) + 0, price => ($_->{price} // 0) + 0} }
		              grep { ref $_ } @selfBuyerItemList];
	}
	if (%open) {
		$st{phase} = $open{phase};
		$st{bought} = {%{$open{bought} || {}}};
		$st{spent} = ($open{spent} // 0) + 0;
	}
	$st{stores} = stores();
	$st{selling} = $sell{from} if %sell;
	return \%st;
}

# ---------- формат пакетов (ошибка upstream для 2018_06_20e) ----------

# Возвращает число подменённых форматов. Подменяет, только если в текущем формате нет поля len.
sub fixPackets {
	return 0 unless $messageSender && ref $messageSender->{packet_list} eq 'HASH';
	my $n = 0;
	for my $name (sort keys %FIX) {
		my $id = ($messageSender->{packet_lut} || {})->{$name};
		($id) = grep { ref $messageSender->{packet_list}{$_} && $messageSender->{packet_list}{$_}[0] eq $name }
		        sort keys %{$messageSender->{packet_list}} unless $id;
		next unless $id;
		my $p = $messageSender->{packet_list}{$id};
		next unless ref $p eq 'ARRAY' && $p->[0] eq $name;
		next if ref $p->[2] eq 'ARRAY' && ($p->[2][0] // '') eq 'len';
		$messageSender->{packet_list}{$id} = [$name, @{$FIX{$name}}];
		message "[buyer] формат пакета $id ($name) исправлен для сервера: $FIX{$name}[0]\n", 'system';
		$n++;
	}
	return $n;
}

# ---------- своя скупка ----------

sub cleanTitle {
	my ($t) = @_;
	$t = '' unless defined $t;
	$t =~ s/[#"\r\n\t]//g;
	$t = join ' ', split ' ', $t;
	return substr($t, 0, 36);
}

# Возвращает (1, описание) или (0, причина).
sub startOpen {
	my ($a) = @_;
	return (0, 'нет персонажа') unless $char;
	return (0, 'скупка уже открыта') if $buyershopstarted || %open;
	return (0, 'открыта лавка') if $shopstarted;
	return (0, 'идёт продажа в скупку') if %sell;
	return (0, 'идёт сделка') if %currentDeal;
	return (0, 'персонаж мёртв') if $char->{dead};
	my $st = status();
	return (0, "нет навыка скупки с лицензией $PERMIT и нет лицензии $SHABBY") unless $st->{can};
	return (0, "мало SP для навыка ($SKILL_SP)") if $st->{skill} && $st->{permits} && ($char->{sp} // 0) < $SKILL_SP;
	my $title = cleanTitle($a->{title});
	return (0, 'нет названия скупки') unless length $title;
	my (@items, @skipped, %seen);
	for my $it (@{ref $a->{items} eq 'ARRAY' ? $a->{items} : []}) {
		next unless ref $it eq 'HASH' && ($it->{id} // '') =~ /^\d{1,6}$/ && ($it->{price} // '') =~ /^\d{1,8}$/
			&& ($it->{amount} // '') =~ /^\d{1,4}$/;
		my ($id, $price, $amount) = ($it->{id} + 0, $it->{price} + 0, $it->{amount} + 0);
		next if $seen{$id}++ || $price < 1 || $price > $MAX_PRICE || $amount < 1;
		my $inv = invItem($id);
		if (!$inv) {                                     # buyingstore.cpp:185 «At least one must be owned»
			push @skipped, $id;
			next;
		}
		$amount = $MAX_AMOUNT - invCount($id) if invCount($id) + $amount > $MAX_AMOUNT;
		next if $amount < 1;
		push @items, {id => $id, name => $inv->{name}, price => $price, amount => $amount};
		last if @items >= $st->{slots};
	}
	return (0, 'нет образца ни одного предмета в рюкзаке' . (@skipped ? ': ' . join(',', @skipped) : '')) unless @items;
	my $limit = 0;
	$limit += $_->{price} * $_->{amount} for @items;
	keepSamples(map { $_->{id} } @items);
	%Globals::buyer_shop = (title_line => $title, items => [map { {name => $_->{name}, price => $_->{price}, amount => $_->{amount}} } @items]);
	fixPackets();
	%open = (phase => 'opening', since => time, zeny => $char->{zeny} // 0, items => \@items, bought => {}, spent => 0,
	         base => {map { $_->{id} => invCount($_->{id}) } @items});
	Commands::run('openbuyershop');
	if (!$buyershopstarted) {                            # makeBuyerShop отказал (нет навыка/лицензии/названия)
		%open = ();
		return (0, 'OpenKore не открыл скупку');
	}
	return (1, "скупка «$title»: " . join(', ', map { "$_->{id} x$_->{amount} по $_->{price}z" } @items)
	           . "; лимит до $limit z" . (@skipped ? "; нет образца: " . join(',', @skipped) : ''));
}

# Образец для следующей скупки: не сдавать на склад и не продавать последнюю штуку (items_control keep 1;
# economy.pl keepValuables свою строку не перезаписывает — она только для ID без строки).
sub keepSamples {
	for my $id (@_) {
		my $c = $items_control{$id};
		next if $c && ($c->{keep} // 0) >= 1;
		$items_control{$id} = {storage => 1, cart_add => 0, cart_get => 0, %{$c || {}}, keep => 1, sell => 0};
	}
}

sub startClose {
	return (0, 'скупка не открыта') unless $buyershopstarted || %open;
	$open{why} = 'закрыл сам' if %open;
	Commands::run('closebuyershop') if $buyershopstarted;
	%open = () unless $buyershopstarted;                 # не открылась — закрывать нечего
	return (1, 'скупка закрыта');
}

sub onOpened {
	my (undef, $args) = @_;
	return unless %open && $open{phase} eq 'opening';
	$open{phase} = 'open';
	$open{since} = time;
	my @items = map { {id => ($_->{nameID} // 0) + 0, amount => ($_->{amount} // 0) + 0, price => ($_->{price} // 0) + 0} }
	            grep { ref $_ } @selfBuyerItemList;
	event('buyer_result', ok => JSON::PP::true(), items => \@items, limit => ($args->{zeny} // 0) + 0);
}

my %OPEN_FAIL = (1 => 'сервер не открыл скупку', 2 => 'вес покупок больше допустимого', 8 => 'неверные данные скупки');

sub onOpenFail {
	my (undef, $args) = @_;
	return unless %open;
	my $code = ($args->{result} // 0) + 0;
	%open = ();
	event('buyer_result', ok => JSON::PP::false(), reason => $OPEN_FAIL{$code} // "отказ сервера ($code)", code => $code);
}

# Покупки по рюкзаку: рост предмета из списка и убыль зени с прошлой проверки.
sub checkBought {
	return unless %open && $open{phase} eq 'open' && $char;
	my $zeny = $char->{zeny} // 0;
	for my $it (@{$open{items}}) {
		my $now = invCount($it->{id});
		my $was = $open{base}{$it->{id}} // 0;
		next unless $now > $was;
		my $paid = $open{zeny} > $zeny ? $open{zeny} - $zeny : 0;
		$open{base}{$it->{id}} = $now;
		$open{zeny} = $zeny;
		$open{bought}{$it->{id}} += $now - $was;
		$open{spent} += $paid;
		event('buyer_bought', item => $it->{id} + 0, amount => $now - $was, zeny => $paid + 0, price => $it->{price} + 0);
	}
}

sub onClosed {
	return unless %open;
	checkBought();
	my %o = %open;
	%open = ();
	return if $o{phase} eq 'opening' && !$o{why};       # отказ уже сообщён onOpenFail
	event('buyer_closed', why => $o{why} // 'закрыта сервером (куплено всё или кончились зени)',
	      bought => $o{bought} || {}, spent => ($o{spent} // 0) + 0);
}

# ---------- продажа в скупку жителя ----------

sub startSell {
	my ($a) = @_;
	return (0, 'нет персонажа') unless $char;
	return (0, 'уже продаю в скупку') if %sell;
	return (0, 'открыта лавка или скупка') if $shopstarted || $buyershopstarted || %open;
	return (0, 'идёт сделка') if %currentDeal;
	my $from = $a->{from} // '';
	return (0, 'неверный владелец скупки') unless $from =~ /^[^"\s]{1,23}$/;
	my %want;
	for my $it (@{ref $a->{items} eq 'ARRAY' ? $a->{items} : []}) {
		next unless ref $it eq 'HASH' && ($it->{id} // '') =~ /^\d{1,6}$/;
		my $keep = ($it->{keep} // 0) =~ /^\d{1,5}$/ ? $it->{keep} + 0 : 0;
		my $min = ($it->{min} // 1) =~ /^\d{1,8}$/ ? $it->{min} + 0 : 1;
		$want{$it->{id} + 0} = {keep => $keep, min => ($min < 1 ? 1 : $min)};
		last if keys %want >= 20;
	}
	return (0, 'нет предметов на продажу') unless %want;
	my $id = storeId($from);
	return (0, "скупки $from рядом нет") unless defined $id;
	return (0, 'нет messageSender') unless $messageSender;
	fixPackets();
	%sell = (from => $from, id => $id, want => \%want, phase => 'enter', since => time, zeny => $char->{zeny} // 0,
	         sold => [], pending => {});
	$messageSender->sendEnteringBuyer($id);
	return (1, "иду в скупку $from");
}

sub onStoreList {
	my (undef, $args) = @_;
	return unless %sell && $sell{phase} eq 'enter' && defined $args->{buyerID} && $args->{buyerID} eq $sell{id};
	my $list = $args->{itemList};
	my @store = $list && $list->can('getItems') ? @{$list->getItems} : ();
	my $left = ($buyerPriceLimit // 0) + 0;
	my @out;
	for my $s (@store) {
		my $w = $sell{want}{$s->{nameID} // -1} or next;
		next if ($s->{price} // 0) < $w->{min};
		my $inv = invItem($s->{nameID}) or next;
		my $amt = ($inv->{amount} // 0) - $w->{keep};
		$amt = $s->{amount} if $amt > ($s->{amount} // 0);
		$amt = int($left / $s->{price}) if $amt * $s->{price} > $left;
		next if $amt < 1;
		push @out, {ID => $inv->{ID}, itemID => $s->{nameID} + 0, amount => $amt};
		$sell{pending}{$inv->{ID}} = {item => $s->{nameID} + 0, price => $s->{price} + 0};
		$left -= $amt * $s->{price};
	}
	if (!@out) {
		finishSell(0, 'скупка не берёт мои предметы по моей цене');
		return;
	}
	$messageSender->sendBuyBulkBuyer($args->{buyerID}, \@out, $args->{buyingStoreID});
	$sell{phase} = 'settle';
	$sell{since} = time;
}

sub onSold {
	my (undef, $args) = @_;
	return unless %sell;
	my $p = $sell{pending}{$args->{ID} // ''} or return;
	push @{$sell{sold}}, {item => $p->{item}, amount => ($args->{amount} // 0) + 0, price => ($args->{zeny} // $p->{price}) + 0};
	$sell{last} = time;
}

my %SELL_FAIL = (5 => 'сделка не прошла', 6 => 'больше, чем скупка берёт', 7 => 'у покупателя кончились зени');

sub onSellFail {
	my (undef, $args) = @_;
	return unless %sell;
	my $code = ($args->{result} // 0) + 0;
	$sell{fail} = $SELL_FAIL{$code} // "отказ сервера ($code)";
	$sell{last} = time;
}

sub finishSell {
	my ($ok, $reason) = @_;
	return unless %sell;
	my %s = %sell;
	%sell = ();
	my $gain = $char ? ($char->{zeny} // 0) - $s{zeny} : 0;
	event('buyer_sell_result', from => $s{from}, ok => ($ok ? JSON::PP::true() : JSON::PP::false()), reason => $reason,
	      sold => $s{sold}, zeny_gain => $gain + 0);
}

sub driveSell {
	return unless %sell;
	my $now = time;
	if ($sell{phase} eq 'enter') {
		finishSell(0, 'скупка не показала список') if $now - $sell{since} > $TIMEOUT{enter};
		return;
	}
	return unless $sell{phase} eq 'settle';
	my $quiet = $sell{last} ? $now - $sell{last} > $TIMEOUT{settle} : 0;
	return unless $quiet || $now - $sell{since} > $TIMEOUT{sell};
	my $n = @{$sell{sold}};
	finishSell($n ? 1 : 0, $n ? 'ok' : ($sell{fail} // 'сервер не подтвердил продажу'));
}

# ---------- тик ----------

sub onTick {
	return unless $char;
	my $now = time;
	return if $now - $lastTick < 1;
	$lastTick = $now;
	if (%open && $open{phase} eq 'opening' && $now - $open{since} > $TIMEOUT{open}) {
		%open = ();
		Commands::run('closebuyershop') if $buyershopstarted;
		event('buyer_result', ok => JSON::PP::false(), reason => 'сервер не подтвердил открытие');
	}
	if (%open && $open{phase} eq 'open' && !$buyershopstarted) {   # relog/разрыв: functions.pl сбрасывает флаг без хука
		$open{why} //= 'связь прервана';
		onClosed();
	}
	checkBought();
	driveSell();
}

1;
