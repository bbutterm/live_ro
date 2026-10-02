# Тест плагина economy на заглушках OpenKore: склад ценного, передача жителю, отказ чужим.
# Запуск: perl -Ibots/tests/stubs bots/tests/economy.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeChar;
sub new { my ($c, %a) = @_; bless {%a}, $c }
sub inventory { $_[0]{inv} }
sub cartActive { $_[0]{cart} }

package FakePlayers;
sub new { my ($c, @p) = @_; bless [@p], $c }
sub getItems { [@{$_[0]}] }

package brainBridge;
our @events;
sub event { push @events, [@_] }

package main;
my $root = "$FindBin::Bin/../..";
%Globals::config = (dealAuto_names => 'Vera', economy_storeIds => '984,985');
%Globals::items_control = ('jellopy' => {keep => 0, storage => 0, sell => 1}, 'all' => {keep => 0, storage => 0, sell => 1});
my @inv = (
	{binID => 0, nameID => 4001, name => 'Poring Card', type => 6, amount => 1},
	{binID => 1, nameID => 984,  name => 'Oridecon', type => 3, amount => 2},
	{binID => 2, nameID => 909,  name => 'Jellopy', type => 3, amount => 30},
	{binID => 3, nameID => 501,  name => 'Red Potion', type => 0, amount => 35},
	{binID => 4, nameID => 2301, name => 'Cotton Shirt', type => 5, amount => 1, equipped => 1},
);
$Globals::char = FakeChar->new(name => 'Arkady', zeny => 5000, inv => \@inv, pos_to => {x => 150, y => 180});
my $vera = {name => 'Vera', pos_to => {x => 156, y => 185}};
$Globals::playersList = FakePlayers->new($vera);
require "$root/bots/plugins/economy/economy.pl";
my $ic = \%Globals::items_control;

# ---- склад ----
Plugins::call('mainLoop_post');
is($ic->{4001}{storage}, 1, 'карта — на склад');
is($ic->{4001}{sell}, 0, 'карта — не продавать');
is($ic->{984}{storage}, 1, 'руда из economy_storeIds — на склад');
ok(!exists $ic->{909}, 'обычный лут не трогаю');
is($ic->{jellopy}{sell}, 1, 'своя строка items_control остаётся');

# ---- состояние ----
is(economy::itemCounts()->{501}, 35, 'счётчик красных зелий');
is(economy::itemCounts()->{601}, 0, 'нет крыльев — 0');
is(economy::vendStatus()->{can}, 0, 'без навыка лавки нельзя');

# ---- проверки передачи ----
my ($ok, $why) = economy::startGive({id => 1, to => 'Stranger', item => 501, amount => 5});
ok(!$ok && $why =~ /dealAuto_names/, 'чужому не отдаю');
($ok, $why) = economy::startGive({id => 1, to => 'Vera', item => 501, amount => 99});
ok(!$ok && $why =~ /нет столько/, 'больше, чем есть, не отдаю');
($ok, $why) = economy::startGive({id => 1, to => 'Vera', item => 'zeny', amount => 9000});
ok(!$ok && $why =~ /зени/, 'зени больше, чем есть, не отдаю');
($ok, $why) = economy::startGive({id => 1, to => 'Vera', item => 2301, amount => 1});
ok(!$ok, 'надетое не отдаю');

# ---- успешная передача ----
($ok, $why) = economy::startGive({id => 7, to => 'Vera', item => 501, amount => 10});
ok($ok, "передача начата: $why");
@Commands::ran = ();
$Globals::char->{sitting} = 1;
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['stand', 'move 156 185'], 'далеко — встаю и иду к жителю');
$Globals::char->{pos_to} = {x => 155, y => 184};
@Commands::ran = ();
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['deal "Vera"'], 'рядом — предлагаю сделку');
is($economy::give{phase}, 'request', 'жду ответа');
%Globals::currentDeal = (name => 'Vera');
Plugins::call('engaged_deal', {name => 'Vera'});
is($economy::give{phase}, 'add', 'сделка открыта');
$economy::give{since} -= 2;
@Commands::ran = ();
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['deal add 3 10'], 'кладу 10 зелий');
$economy::give{since} -= 2;
@Commands::ran = ();
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['deal'], 'подтверждаю');
is($economy::give{phase}, 'final', 'жду вторую сторону');
@brainBridge::events = ();
%Globals::currentDeal = ();
Plugins::call('complete_deal');
is($brainBridge::events[0][0], 'give_result', 'событие итога');
my %ev = @{$brainBridge::events[0]}[1 .. $#{$brainBridge::events[0]}];
ok($ev{ok}, 'передано');
is($ev{id}, 7, 'id действия мозга');
ok(!%economy::give, 'передача закрыта');

# ---- таймаут ----
($ok) = economy::startGive({id => 8, to => 'Vera', item => 'zeny', amount => 1000});
Plugins::call('mainLoop_post');
%Globals::outgoingDeal = (ID => 1);
$economy::give{since} -= 100;
@Commands::ran = (); @brainBridge::events = ();
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['deal no'], 'таймаут — отменяю сделку');
%ev = @{$brainBridge::events[0]}[1 .. $#{$brainBridge::events[0]}];
ok(!$ev{ok} && $ev{reason} =~ /таймаут/, 'итог: не передано, таймаут');
%Globals::outgoingDeal = ();

# ---- отказ сервера ----
economy::startGive({id => 9, to => 'Vera', item => 'zeny', amount => 1000});
Plugins::call('mainLoop_post');
@brainBridge::events = ();
Plugins::call('error_deal', {type => 0});
%ev = @{$brainBridge::events[0]}[1 .. $#{$brainBridge::events[0]}];
ok(!$ev{ok} && $ev{reason} =~ /далеко/, 'сервер: слишком далеко');

# ---- чужие предложения ----
%Globals::incomingDeal = (name => 'Stranger');
my %args;
@Commands::ran = ();
Plugins::call('deal_incoming', \%args);
is_deeply(\@Commands::ran, ['deal no'], 'чужому — отказ');
ok($args{return}, 'стандартный dealAuto пропущен');
%Globals::incomingDeal = (name => 'Vera');
%args = (); @Commands::ran = ();
Plugins::call('deal_incoming', \%args);
ok(!@Commands::ran && !$args{return}, 'жителю — принимает dealAuto 3');

done_testing();
