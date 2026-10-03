# Тест плагина buyer (скупка, ORG-036) на заглушках OpenKore: статус, открытие и отказы, исправление формата
# пакетов 0811/0819 (сверка раскладки с rAthena packets_struct.hpp), покупки по рюкзаку, закрытие,
# продажа в скупку жителя.
# Запуск: perl -Ibots/tests/stubs bots/tests/buyer.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeChar;
sub new { my ($c, %a) = @_; bless {%a}, $c }
sub inventory { $_[0]{inv} }
sub cartActive { $_[0]{cart} }

package FakeList;
sub new { my ($c, @i) = @_; bless [@i], $c }
sub getItems { [@{$_[0]}] }

package FakePlayers;
sub new { my ($c, %p) = @_; bless {%p}, $c }
sub getByID { $_[0]{$_[1]} }

package FakeSender;
our @sent;
sub new { my ($c, %a) = @_; bless {%a}, $c }
sub sendEnteringBuyer { push @sent, ['enter', $_[1]] }
sub sendBuyBulkBuyer { push @sent, ['sell', $_[1], $_[2], $_[3]] }

package brainBridge;
our @events;
sub event { push @events, [@_] }

package main;
my $root = "$FindBin::Bin/../..";
my $openOk = 1;
require Commands;                                    # заглушка — до подмены run
{
	no warnings 'redefine';
	*Commands::run = sub {                              # как Misc.pm openBuyerShop/closeBuyerShop
		push @Commands::ran, $_[0];
		$Globals::buyershopstarted = 1 if $_[0] eq 'openbuyershop' && $openOk;
		if ($_[0] eq 'closebuyershop') { $Globals::buyershopstarted = 0; Plugins::call('buyer_shop_closed'); }
	};
}
my @inv = (
	{ID => "\x05\x00", nameID => 984, name => 'Oridecon', amount => 1},
	{ID => "\x06\x00", nameID => 507, name => 'Red Herb', amount => 10},
	{ID => "\x07\x00", nameID => 6377, name => 'Buy Market Permit', amount => 2},
	{ID => "\x08\x00", nameID => 2301, name => 'Cotton Shirt', amount => 1, equipped => 1},
);
$Globals::char = FakeChar->new(name => 'Bram', zeny => 20000, sp => 50, inv => \@inv, cart => 1,
                               skills => {MC_VENDING => {lv => 3}, MC_PUSHCART => {lv => 3}});
$Globals::messageSender = FakeSender->new(
	packet_lut  => {buy_bulk_openShop => '0811', buy_bulk_buyer => '0819'},
	packet_list => {'0811' => ['buy_bulk_openShop', 'a4 c a*', [qw(limitZeny result itemInfo)]],   # 2018_04_04b.pm:29
	                '0819' => ['buy_bulk_buyer', 'a4 a4 a*', [qw(buyerID buyingStoreID itemInfo)]]});  # :27
require "$root/bots/plugins/buyer/buyer.pl";

sub ev { my ($kind) = @_; my @e = grep { $_->[0] eq $kind } @brainBridge::events; return $e[-1] ? {@{$e[-1]}[1 .. $#{$e[-1]}]} : undef }

# ---- статус ----
my $st = buyer::status();
is($st->{can}, 0, 'без навыка и лицензии 12548 — скупки нет');
is_deeply([@$st{qw(vending pushcart cart permits)}], [3, 3, 1, 2], 'навыки торговца, тележка, лицензии 6377');
$Globals::char->{skills}{ALL_BUYING_STORE} = {lv => 1};
$st = buyer::status();
is_deeply([@$st{qw(can skill slots)}], [1, 1, 5], 'навык + лицензия 6377 — 5 мест (openbuyingstore.cpp)');
my ($ok, $why) = buyer::startOpen({title => 'Куплю', items => [{id => 984, price => 3000, amount => 5}]});
ok($ok, 'открытие с навыком') or diag $why;
buyer::startClose();
@brainBridge::events = ();

# ---- отказы ----
$Globals::shopstarted = 1;
($ok, $why) = buyer::startOpen({title => 'Куплю', items => [{id => 984, price => 3000, amount => 5}]});
ok(!$ok && $why =~ /лавка/, 'открыта лавка — скупку не открываю');
$Globals::shopstarted = 0;
($ok, $why) = buyer::startOpen({title => 'Куплю', items => [{id => 999, price => 50, amount => 5}]});
ok(!$ok && $why =~ /нет образца.*999/, 'нет ни одной штуки в рюкзаке — отказ (buyingstore.cpp:185)');
$Globals::char->{sp} = 10;
($ok, $why) = buyer::startOpen({title => 'Куплю', items => [{id => 984, price => 3000, amount => 5}]});
ok(!$ok && $why =~ /SP/, 'мало SP для навыка');
$Globals::char->{sp} = 50;
($ok, $why) = buyer::startOpen({title => '#', items => [{id => 984, price => 3000, amount => 5}]});
ok(!$ok && $why =~ /названия/, 'название из одного # — отказ');
$openOk = 0;
($ok, $why) = buyer::startOpen({title => 'Куплю', items => [{id => 984, price => 3000, amount => 5}]});
ok(!$ok && $why =~ /OpenKore/ && !%buyer::open, 'OpenKore не открыл — состояние сброшено');
$openOk = 1;

# ---- открытие ----
@Commands::ran = ();
($ok, $why) = buyer::startOpen({title => 'Bram#: руда', items => [
	{id => 984, price => 3000, amount => 5}, {id => 999, price => 50, amount => 5}, {id => 507, price => 20, amount => 30},
	{id => 984, price => 1, amount => 1}]});
ok($ok, 'скупка открывается') or diag $why;
like($why, qr/нет образца: 999/, 'предмет без образца пропущен, остальные — в скупке');
is_deeply(\@Commands::ran, ['openbuyershop'], 'команда openbuyershop');
is($Globals::buyer_shop{title_line}, 'Bram: руда', 'название без #');
is_deeply($Globals::buyer_shop{items}, [{name => 'Oridecon', price => 3000, amount => 5}, {name => 'Red Herb', price => 20, amount => 30}],
          '%buyer_shop по ИМЕНИ из рюкзака (makeBuyerShop ищет по имени), повтор ID отброшен');
my $pl = $Globals::messageSender->{packet_list};
is($pl->{'0811'}[1], 'v V C Z80 a*', '0811: формат с len и названием (Sakexe_0.pm:228)');
is($pl->{'0819'}[1], 'v a4 a4 a*', '0819: формат с len (Sakexe_0.pm:231)');
is(buyer::fixPackets(), 0, 'повторно не подменяется');
is_deeply([@{$Globals::items_control{507}}{qw(keep sell storage)}], [1, 0, 1], 'образец Red Herb: последнюю штуку не сдаю и не продаю');

# Раскладка как у OpenKore reconstruct (PacketParser.pm:197-205, Send.pm:820-825) против rAthena packets_struct.hpp:3108.
my ($name, $fmt, $vars) = @{$pl->{'0811'}};
my %args = (len => 0, limitZeny => 157000, result => 1, storeName => 'Bram: руда',
            itemInfo => pack('(a8)*', map { pack 'v2 V', @$_ } [984, 5, 3000], [507, 30, 20]));
my $packet = pack("v $fmt", 0x0811, @args{@$vars});
substr($packet, 2, 2) = pack('v', length $packet);
my ($hdr, $len, $zeny, $res) = unpack('v v V C', $packet);
is_deeply([$hdr, $len, $zeny, $res], [0x0811, 89 + 16, 157000, 1], 'len@2, zenyLimit@4, result@8');
is_deeply([unpack('v v V', substr($packet, 89, 8))], [984, 5, 3000], 'товар @89: itemId(2) amount(2) price(4) — PACKETVER < 20180704');
($name, $fmt, $vars) = @{$pl->{'0819'}};
%args = (len => 0, buyerID => pack('V', 2000001), buyingStoreID => pack('V', 7),
         itemInfo => pack('(a6)*', pack('a2 v2', "\x06\x00", 507, 8)));
$packet = pack("v $fmt", 0x0819, @args{@$vars});
substr($packet, 2, 2) = pack('v', length $packet);
is_deeply([unpack('v v V V', $packet)], [0x0819, 18, 2000001, 7], '0819: len@2, AID@4, storeId@8 (packets_struct.hpp:3259)');
is_deeply([unpack('v v v', substr($packet, 12))], [6, 507, 8], 'продажа @12: index, itemId, amount');

# ---- подтверждение сервера ----
@Globals::selfBuyerItemList = ({nameID => 984, amount => 5, price => 3000}, {nameID => 507, amount => 30, price => 20});
Plugins::call('packet/open_buying_store_item_list', {zeny => 15600});
my $e = ev('buyer_result');
ok($e && $e->{ok}, 'buyer_result ok по пакету 0813');
is($e->{limit}, 15600, 'лимит зени от сервера');
is(buyer::status()->{open}, 1, 'state.buyer.open');

# ---- покупки по рюкзаку ----
$inv[1]{amount} = 22;                 # купил 12 Red Herb
$Globals::char->{zeny} = 20000 - 240;
buyer::checkBought();
$e = ev('buyer_bought');
is_deeply([@$e{qw(item amount zeny)}], [507, 12, 240], 'buyer_bought: рост предмета и убыль зени');
buyer::checkBought();
is(scalar(grep { $_->[0] eq 'buyer_bought' } @brainBridge::events), 1, 'без нового роста — без события');
is_deeply(buyer::status()->{bought}, {507 => 12}, 'state.buyer.bought');

# ---- закрытие ----
($ok) = buyer::startClose();
ok($ok, 'закрываю сам');
$e = ev('buyer_closed');
is_deeply([@$e{qw(why spent)}, $e->{bought}{507}], ['закрыл сам', 240, 12], 'buyer_closed: итог');
ok(!%buyer::open && !$Globals::buyershopstarted, 'скупка закрыта');

# ---- отказ сервера ----
($ok) = buyer::startOpen({title => 'Куплю', items => [{id => 507, price => 20, amount => 30}]});
Plugins::call('packet/open_buying_store_fail', {result => 2});
$Globals::buyershopstarted = 0;       # Receive.pm open_buying_store_fail
$e = ev('buyer_result');
ok(!$e->{ok} && $e->{reason} =~ /вес/, 'отказ 0812 (2) — вес');
ok(!%buyer::open, 'после отказа — свободен');

# ---- relog посреди скупки ----
@brainBridge::events = ();
buyer::startOpen({title => 'Куплю', items => [{id => 507, price => 20, amount => 30}]});
Plugins::call('packet/open_buying_store_item_list', {zeny => 600});
$Globals::buyershopstarted = 0;       # functions.pl:888 — без хука
$buyer::lastTick = 0;
Plugins::call('mainLoop_post');
ok(ev('buyer_closed') && ev('buyer_closed')->{why} =~ /связь/, 'разрыв: buyer_closed без хука OpenKore');

# ---- продажа в скупку жителя ----
@brainBridge::events = ();
@Globals::buyerListsID = ('', 'AID1');
%Globals::buyerLists = (AID1 => {title => 'Bram: руда'});
$Globals::playersList = FakePlayers->new(AID1 => {name => 'Bram'});
is_deeply(buyer::status()->{stores}, [{name => 'Bram', title => 'Bram: руда'}], 'state.buyer.stores: скупка рядом');
($ok, $why) = buyer::startSell({from => 'Vera', items => [{id => 507}]});
ok(!$ok && $why =~ /рядом нет/, 'скупки Vera рядом нет');
($ok, $why) = buyer::startSell({from => 'Bram', items => [{id => 507, keep => 2, min => 10}, {id => 984, min => 5000}]});
ok($ok, 'иду в скупку Bram') or diag $why;
is_deeply($FakeSender::sent[-1], ['enter', 'AID1'], 'sendEnteringBuyer');
$Globals::buyerPriceLimit = 100;      # лимит скупки режет количество: 100 / 15 = 6
Plugins::call('packet_buying_store2', {buyerID => 'AID1', buyingStoreID => 'S1', itemList => FakeList->new(
	{nameID => 507, price => 15, amount => 100}, {nameID => 984, price => 3000, amount => 5})});
is_deeply($FakeSender::sent[-1], ['sell', 'AID1', [{ID => "\x06\x00", itemID => 507, amount => 6}], 'S1'],
          'продаю Red Herb (оставив keep, в пределах лимита); руду — нет: цена ниже min');
Plugins::call('packet/buying_store_item_delete', {ID => "\x06\x00", amount => 6, zeny => 15});
$Globals::char->{zeny} += 90;
$buyer::sell{last} = time - 10;
buyer::driveSell();
$e = ev('buyer_sell_result');
ok($e && $e->{ok}, 'buyer_sell_result ok');
is_deeply([$e->{from}, $e->{zeny_gain}, $e->{sold}], ['Bram', 90, [{item => 507, amount => 6, price => 15}]],
          'продано по пакету 081C, прирост зени');
($ok) = buyer::startSell({from => 'Bram', items => [{id => 984, min => 5000}]});
Plugins::call('packet_buying_store2', {buyerID => 'AID1', buyingStoreID => 'S1', itemList => FakeList->new(
	{nameID => 984, price => 3000, amount => 5})});
$e = ev('buyer_sell_result');
ok(!$e->{ok} && $e->{reason} =~ /не берёт/, 'нечего продать по своей цене — отказ без пакета');
($ok) = buyer::startSell({from => 'Bram', items => [{id => 507}]});
$buyer::sell{since} = time - 60;
buyer::driveSell();
ok(!ev('buyer_sell_result')->{ok}, 'скупка не показала список — таймаут');
$Globals::buyershopstarted = 1;
($ok, $why) = buyer::startSell({from => 'Bram', items => [{id => 507}]});
ok(!$ok, 'своя скупка открыта — в чужую не продаю');
$Globals::buyershopstarted = 0;

done_testing();
