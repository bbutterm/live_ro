# Тест плагина pets на заглушках OpenKore: приручение, вылупление, корм, состояние для мозга (ORG-051).
# Пакеты сервера подменяются вызовом хуков; это проверка исполнителя, а НЕ приручение в игре.
# Запуск: perl -Ibots/tests/stubs bots/tests/pets.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use JSON::PP;
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeChar;
sub new { my ($c, %a) = @_; bless {%a}, $c }
sub inventory { $_[0]{inv} }

package FakeMobs;
sub new { my ($c, @m) = @_; bless [@m], $c }
sub getItems { [@{$_[0]}] }
sub getByID { my ($s, $id) = @_; (grep { $_->{ID} eq $id } @$s)[0] }

package brainBridge;
our @events;
sub event { push @events, [@_] }

package main;
my $root = "$FindBin::Bin/../..";
%Globals::config = (attackAuto => 2, buyAuto_0 => 'Red Potion', buyAuto_0_npc => 'prt_in 126 76',
                    buyAuto_1 => 'Pet Food', buyAuto_1_npc => 'prontera 218 211', buyAuto_1_disabled => 1);
$Globals::char = FakeChar->new(name => 'Vera', pos_to => {x => 100, y => 100},
	inv => [{nameID => 619, amount => 2, binID => 7}, {nameID => 501, amount => 30, binID => 1}]);
$Globals::monstersList = FakeMobs->new({ID => 'p1', nameID => 1002, binID => 3, pos_to => {x => 104, y => 101}},
                                       {ID => 'p2', nameID => 1002, binID => 4, pos_to => {x => 130, y => 130}});
require "$root/bots/plugins/pets/pets.pl";

sub tick { $pets::lastTick = 0; Plugins::call('mainLoop_post'); }
sub ran { my @r = @Commands::ran; @Commands::ran = (); return @r; }
sub lastEvent { my $e = $brainBridge::events[-1]; return $e ? {kind => $e->[0], @{$e}[1 .. $#$e]} : {}; }

# ---- состояние и настройка ----
my ($ok, $why) = pets::setup({items => [619], mobs => [1002], food_on => 1});
ok($ok, "настройка: $why");
my $st = pets::status();
ok(!$st->{has}, 'питомца нет');
is($st->{items}{619}, 2, 'предмет приручения в рюкзаке');
is($st->{near}{1002}, 4, 'ближайший Poring в 4 клетках');
tick();
ok(!grep({ /buyAuto_1_disabled 0/ } ran()), 'без питомца Pet Food не докупается');
($ok, $why) = pets::setup({items => [(1) x 21]});
ok(!$ok, 'длинный список отклонён');
pets::setup({items => [619], mobs => [1002], food_on => 1});

# ---- приручение ----
($ok, $why) = pets::startTame({item => 'x', mob => 1002});
ok(!$ok && $why =~ /неверный/, 'неверный предмет');
($ok, $why) = pets::startTame({item => 620, mob => 1002});
ok(!$ok && $why =~ /нет предмета/, 'предмета нет');
($ok, $why) = pets::startTame({item => 619, mob => 1031});
ok(!$ok && $why =~ /не виден/, 'монстр не виден');
ran();
($ok, $why) = pets::startTame({item => 619, mob => 1002});
ok($ok, "приручение начато: $why");
my @r = ran();
ok((grep { $_ eq 'conf attackAuto 1' } @r) && (grep { $_ eq 'is 7' } @r), 'attackAuto 1 и использовать предмет');
($ok, $why) = pets::startHatch({egg => 9001});
ok(!$ok && $why =~ /уже идёт/, 'второе действие не начинаю');
Plugins::call('packet/pet_capture_process', {});
is_deeply([ran()], ['pet c 3'], 'pet c на ближайшем Poring');
Plugins::call('packet/pet_capture_result', {success => 1});
my $e = lastEvent();
is($e->{kind}, 'pet_tame_result', 'событие итога');
ok($e->{ok} && $e->{mob} == 1002 && $e->{item} == 619, 'поймал Poring');
ok(grep({ $_ eq 'conf attackAuto 2' } ran()), 'attackAuto вернул');

# неудача и таймаут
pets::startTame({item => 619, mob => 1002});
Plugins::call('packet/pet_capture_process', {});
Plugins::call('packet/pet_capture_result', {success => 0});
ok(!lastEvent()->{ok}, 'сервер: не поймал');
pets::startTame({item => 619, mob => 1002});
$pets::run{since} -= 100;
tick();
like(lastEvent()->{reason}, qr/нет ответа сервера/, 'таймаут без пакета');
ok(!%pets::run, 'после таймаута свободен');

# ---- вылупление ----
push @{$Globals::char->{inv}}, {nameID => 9001, amount => 1, binID => 9};
is_deeply(pets::status()->{eggs}, [9001], 'яйцо видно');
($ok, $why) = pets::startHatch({egg => 9001});
ok(!$ok && $why =~ /Incubator/, 'без инкубатора не вылупить');
push @{$Globals::char->{inv}}, {nameID => 643, amount => 1, binID => 10};
ran();
($ok, $why) = pets::startHatch({egg => 9001});
ok($ok, 'вылупление начато');
is_deeply([ran()], ['is 10'], 'использовать инкубатор');
Plugins::call('packet/egg_list', {});
is_deeply([ran()], ['pet h 9'], 'pet h яйцо');
%Globals::pet = (name => 'Poring', hungry => 50, friendly => 250, type => 1002);
Plugins::call('packet/pet_info', {});
$e = lastEvent();
ok($e->{kind} eq 'pet_hatched' && $e->{ok} && $e->{mob} == 1002, 'вылупился Poring');
ok(pets::status()->{has}, 'питомец есть');
($ok, $why) = pets::startTame({item => 619, mob => 1002});
ok(!$ok && $why =~ /уже есть/, 'второго не приручаю');

# ---- корм ----
tick();
ok(grep({ $_ eq 'conf buyAuto_1_disabled 0' } ran()), 'с питомцем Pet Food докупается');
tick();
is(scalar(ran()), 0, 'повторно не переключаю');
Plugins::call('packet/pet_food', {success => 1, foodID => 537});
$e = lastEvent();
ok($e->{kind} eq 'pet_fed' && $e->{ok} && $e->{food} == 537, 'кормление подтверждено пакетом');
%Globals::pet = ();
tick();
ok(grep({ $_ eq 'conf buyAuto_1_disabled 1' } ran()), 'питомца нет — докупку выключил');

# ---- мёртвый ----
$Globals::char->{dead} = 1;
($ok, $why) = pets::startTame({item => 619, mob => 1002});
ok(!$ok, 'мёртвому нельзя');

done_testing();
