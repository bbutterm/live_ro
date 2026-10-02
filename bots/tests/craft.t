# Тест ремесла в brainBridge на заглушках OpenKore (ORG-076 травник, ORG-075 стрелы): state.craft, craft_setup
# (не продавать, докупка бутылок). Это проверка исполнителя, а НЕ поездки к фармацевту в игре.
# Запуск: perl -Ibots/tests/stubs bots/tests/craft.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeNet; sub new { bless {}, shift } sub getState { 5 }

package main;
my $root = "$FindBin::Bin/../..";
%Globals::config = (attackAuto => 2, buyAuto_0 => 'Red Potion', buyAuto_0_npc => 'prt_in 126 76',
                    buyAuto_1 => 'Empty Bottle', buyAuto_1_npc => 'prt_in 126 76', buyAuto_1_disabled => 1);
$Globals::net = FakeNet->new;
$Globals::char = {name => 'Arkady', hp => 300, hp_max => 400, dead => 0, weight => 700, weight_max => 2400,
	inv => [{nameID => 509, amount => 12}, {nameID => 713, amount => 3}, {nameID => 504, amount => 2},
	        {nameID => 1019, amount => 5, equipped => 0}, {nameID => 1750, amount => 99, equipped => 1}]};
require "$root/bots/plugins/brainBridge/brainBridge.pl";

# ---- state.craft ----
my $st = brainBridge::craftStatus();
is($st->{items}{509}, 12, 'White Herb в рюкзаке');
is($st->{items}{713}, 3, 'бутылки');
is($st->{items}{1019}, 5, 'Trunk');
is($st->{items}{1750}, 0, 'надетые стрелы не считаются');
ok(!exists $st->{items}{504}, 'зелья — в items плагина economy, не здесь');
is($st->{weight_free}, 1700, 'свободный вес');
is_deeply($st->{kept}, [], 'пока ничего не держу');
is($st->{skills}{AC_MAKINGARROW}, 0, 'навыка нет');

# ---- craft_setup keep ----
my ($ok, $cmds) = brainBridge::actionToCommand({action => 'craft_setup', keep => [507, 509, 713]});
ok($ok, 'keep принят');
is_deeply($cmds, [], 'без команд консоли');
is($Globals::items_control{509}{sell}, 0, 'White Herb не продавать');
is($Globals::items_control{509}{storage}, 0, 'и не складывать');
is_deeply(brainBridge::craftStatus()->{kept}, [507, 509, 713], 'state.kept');
%Globals::items_control = ();                                   # «reload items_control.txt» — записи пропали
is_deeply(brainBridge::craftStatus()->{kept}, [], 'после перезагрузки таблиц kept пуст — мозг пришлёт снова');
($ok, $cmds) = brainBridge::actionToCommand({action => 'craft_setup', keep => ['x']});
ok(!$ok, 'неверный ID отклонён');
($ok, $cmds) = brainBridge::actionToCommand({action => 'craft_setup', keep => [(1) x 41]});
ok(!$ok, 'длинный список отклонён');
($ok, $cmds) = brainBridge::actionToCommand({action => 'craft_setup'});
ok(!$ok, 'пустая настройка');

# ---- craft_setup bottles ----
($ok, $cmds) = brainBridge::actionToCommand({action => 'craft_setup', bottles => 6});
ok($ok, 'бутылки');
is_deeply($cmds, ['conf buyAuto_1_maxAmount 6', 'conf buyAuto_1_minAmount 5', 'conf buyAuto_1_disabled 0'],
	'блок Empty Bottle включён');
($ok, $cmds) = brainBridge::actionToCommand({action => 'craft_setup', bottles => 0});
is_deeply($cmds, ['conf buyAuto_1_disabled 1'], 'выключить докупку');
($ok, $cmds) = brainBridge::actionToCommand({action => 'craft_setup', bottles => 101});
ok(!$ok, 'больше 100 — нет');
delete $Globals::config{buyAuto_1};
($ok, $cmds) = brainBridge::actionToCommand({action => 'craft_setup', bottles => 6});
ok(!$ok && $cmds =~ /нет блока/, 'нет блока в config.txt — отказ');

done_testing();
