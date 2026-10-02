# Тест плагина spar на заглушках OpenKore: поход к Gate Keeper и приёмной (меню по тексту), проверка числа
# игроков в комнате, бой только с соперником, посторонний — выход, сдача при HP < 30 %, итог и крыло.
# Меню и координаты — из скриптов rAthena (npc/other/pvp.txt:178-287, npc/re/other/pvp.txt:45). Это проверка
# исполнителя на заглушках, а НЕ спарринг в игре.
# Запуск: perl -Ibots/tests/stubs bots/tests/spar.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeChar;
sub new { my ($c, %a) = @_; bless {%a}, $c }
sub inventory { $_[0]{inv} }

package FakeField;
sub new { my ($c, $m) = @_; bless {m => $m}, $c }
sub baseName { $_[0]{m} }

package FakePlayers;
sub new { my ($c, @p) = @_; bless {p => [@p]}, $c }
sub getItems { $_[0]{p} }
sub find { my ($s, $x) = @_; for my $i (0 .. $#{$s->{p}}) { return $i if $s->{p}[$i] == $x } -1 }

package brainBridge;
our @events;
sub event { push @events, [@_] }

package main;
my $root = "$FindBin::Bin/../..";
require Globals;
push @Globals::EXPORT_OK, qw(%talk);
our %talk;
*Globals::talk = \%main::talk;

sub fresh {
	%Globals::config = (lockMap => 'prt_fild08', attackAuto => 2, route_randomWalk => 1, autoTalkCont => 0,
	                    useSelf_item_0 => 'Red Potion', useSelf_item_0_hp => '< 50');
	$Globals::char = FakeChar->new(name => 'Arkady', lv => 35, zeny => 2000, hp => 400, hp_max => 400, dead => 0,
		pos_to => {x => 150, y => 180}, inv => [{nameID => 602, amount => 2, binID => 7}, {nameID => 501, amount => 30, binID => 3}]);
	$Globals::field = FakeField->new('prontera');
	$Globals::playersList = FakePlayers->new();
	@brainBridge::events = ();
	@Commands::ran = ();
	$AI::action = undef;
}
fresh();
require "$root/bots/plugins/spar/spar.pl";

sub tick { $spar::lastTick = 0; Plugins::call('mainLoop_post'); }
sub ran { my @r = @Commands::ran; @Commands::ran = (); return grep { !/^conf / } @r; }
sub confs { return grep { /^conf / } @Commands::ran; }
sub lastEvent { my $e = $brainBridge::events[-1]; return $e ? {kind => $e->[0], @{$e}[1 .. $#$e]} : {}; }
sub menu { Plugins::call('npc_talk_responses', {name => $_[0], responses => [@{$_[1]}, 'Cancel Chat']}); }
sub at { my ($map, $x, $y) = @_; $Globals::field = FakeField->new($map); $Globals::char->{pos_to} = {x => $x, y => $y}; }
my @GATE = ("' PvP Nightmare Mode'", "' PvP Yoyo Mode'", "' PvP Event Mode'", 'Quit');   # цвет снят (Receive.pm:7795)
my @ROOMS = ('Prontera [0 / 128]', 'Izlude [0 / 128]', 'Payon [3 / 128]', 'Alberta [0 / 128]', 'Morocc [0 / 128]', 'Cancel.');

# ---- проверки запуска ----
my ($ok, $why) = spar::start({to => 'Vera', role => 'first', room => 'Payon'});
ok(!$ok && $why =~ /не из списка/, 'комната не из списка — отказ');
($ok, $why) = spar::start({to => 'Vera', role => 'boss', room => 'Prontera'});
ok(!$ok && $why =~ /роль/, 'неверная роль');
$Globals::char->{lv} = 30;
($ok, $why) = spar::start({to => 'Vera', role => 'first', room => 'Prontera'});
ok(!$ok && $why =~ /уровень/, 'уровень 30 — Gate Keeper не пустит (BaseLevel > 30)');
$Globals::char->{lv} = 35;
$Globals::char->{inv} = [];
($ok, $why) = spar::start({to => 'Vera', role => 'first', room => 'Prontera'});
ok(!$ok && $why =~ /крыла/, 'без крыла бабочки не иду — с арены не выйти');
fresh();
ok(!spar::running(), 'не идёт');

# ---- вызвавший: весь путь и сдача ----
($ok, $why) = spar::start({to => 'Vera', role => 'first', room => 'Prontera'});
ok($ok, "начат: $why");
my @c = confs();
ok((grep { $_ eq 'conf -f lockMap none' } @c) && (grep { $_ eq 'conf -f autoTalkCont 1' } @c), 'lockMap снят, autoTalkCont 1');
@Commands::ran = ();
tick();
is_deeply([ran()], ['move 52 138 prt_in'], 'иду к Gate Keeper');
at('prt_in', 52, 138);
tick();
is_deeply([ran()], ['talknpc 52 140'], 'на месте — заговорить');
%talk = (ID => 1);
menu('Gate Keeper#gke3', \@GATE);
menu('Gate Keeper#gke3', ['Move', 'Cancel']);
is_deeply([ran()], ['talk resp 1', 'talk resp 0'], 'Yoyo, затем Move — по тексту пунктов');
%talk = ();
Plugins::call('npc_talk_done', {});
at('pvp_y_room', 46, 38);
tick();
is(spar::status()->{phase}, 'room', 'в pvp_y_room — к приёмной');
tick();
is_deeply([ran()], ['move 54 83 pvp_y_room'], 'иду к приёмной #8');
at('pvp_y_room', 54, 83);
tick();
is_deeply([ran()], ['talknpc 54 85'], 'заговорить с приёмной');
%talk = (ID => 2);
menu('Fight Square Reception#8', \@ROOMS);
is_deeply([ran()], ['talk resp 0'], 'Prontera [0 / 128] — пустая, беру');
%talk = ();
Plugins::call('npc_talk_done', {});
at('pvp_y_8-1', 20, 300);
@Commands::ran = ();
tick();
is(lastEvent()->{kind}, 'spar_step', 'событие мозгу');
is(lastEvent()->{phase}, 'arena', 'на арене');
@c = confs();
ok((grep { $_ eq 'conf -f attackAuto 0' } @c) && (grep { $_ eq 'conf -f survival 0' } @c)
	&& (grep { $_ eq 'conf -f useSelf_item_0_disabled 1' } @c), 'бой: без автоатаки, без зелий и survival');
@Commands::ran = ();
tick();
is_deeply([ran()], ['move 156 185 pvp_y_8-1'], 'к месту встречи');
my $vera = {name => 'Vera', dead => 0};
$Globals::playersList = FakePlayers->new($vera);
tick();
is(lastEvent()->{phase}, 'fight', 'соперник виден — бой');
tick();
is_deeply([ran()], ['kill 0'], 'атакую только соперника (kill <номер>)');
tick();
is_deeply([ran()], [], 'не спамлю kill');
$Globals::char->{hp} = 110;
tick();
my $e = lastEvent();
is($e->{kind}, 'spar_result', 'итог');
is($e->{outcome}, 'yield', 'HP 27% < 30% — сдаюсь');
is($e->{wing}, 'used', 'выход крылом');
@c = confs();
my @r = ran();
ok((grep { $_ eq 'ai clear' } @r) && (grep { $_ eq 'is 7' } @r), 'ai clear и Butterfly Wing (is <binID>)');
ok((grep { $_ eq 'conf -f attackAuto 2' } @c) && (grep { $_ eq 'conf -f lockMap prt_fild08' } @c)
	&& (grep { $_ eq 'conf -f useSelf_item_0_disabled 0' } @c), 'настройки возвращены');
ok(!spar::running(), 'спарринг закончен');

# ---- партнёр: в комнате не ровно 1 игрок — отмена ----
fresh();
ok((spar::start({to => 'Vera', role => 'second', room => 'Prontera'}))[0], 'партнёр начал');
at('pvp_y_room', 54, 83);
tick();
tick();
%talk = (ID => 2);
@Commands::ran = ();
menu('Fight Square Reception#8', \@ROOMS);
$e = lastEvent();
is($e->{outcome}, 'aborted', 'партнёр: Prontera [0 / 128], ждал 1 — отмена');
like($e->{reason}, qr/busy/, 'причина busy');
ok((grep { $_ eq 'talk no' } @Commands::ran) && (grep { $_ eq 'is 7' } @Commands::ran), 'закрыть диалог и выйти крылом');

# ---- посторонний на арене — выход ----
fresh();
spar::start({to => 'Vera', role => 'first', room => 'Prontera'});
at('pvp_y_room', 54, 83);
tick();
at('pvp_y_8-1', 156, 185);
tick();
$Globals::playersList = FakePlayers->new({name => 'Vera', dead => 0}, {name => 'Stranger', dead => 0});
tick();
$e = lastEvent();
is($e->{outcome}, 'aborted', 'посторонний игрок — отмена');
like($e->{reason}, qr/stranger: Stranger/, 'причина: stranger');
ok(!grep({ /^kill/ } @Commands::ran), 'постороннего не бью и соперника при нём — тоже');

# ---- соперник упал — победа; стоп от мозга ----
fresh();
spar::start({to => 'Vera', role => 'first', room => 'Prontera'});
at('pvp_y_room', 54, 83);
tick();
at('pvp_y_8-1', 156, 185);
tick();
$vera = {name => 'Vera', dead => 0};
$Globals::playersList = FakePlayers->new($vera);
tick();
tick();
$vera->{dead} = 1;
tick();
is(lastEvent()->{outcome}, 'won', 'соперник упал — победа');
fresh();
spar::start({to => 'Vera', role => 'first', room => 'Prontera'});
spar::stop('Vera сдалась');
is(lastEvent()->{outcome}, 'stopped', 'стоп от мозга');
is(lastEvent()->{wing}, 'none', 'не на арене — крыло не трачу');

# ---- упал сам ----
fresh();
spar::start({to => 'Vera', role => 'first', room => 'Prontera'});
at('pvp_y_room', 54, 83);
tick();
at('pvp_y_8-1', 156, 185);
tick();
$Globals::playersList = FakePlayers->new({name => 'Vera', dead => 0});
tick();
$Globals::char->{dead} = 1;
tick();
is(lastEvent()->{outcome}, 'down', 'упал(а) — down, без крыла');
is(lastEvent()->{wing}, 'none', 'мёртвому крыло не нужно (nopenalty, возрождение)');

# ---- Gate Keeper не пустил ----
fresh();
spar::start({to => 'Vera', role => 'first', room => 'Prontera'});
at('prt_in', 52, 138);
tick();
%talk = (ID => 1);
menu('Gate Keeper#gke3', ['Something else']);
is(lastEvent()->{outcome}, 'aborted', 'меню не из сценария — отмена');
like(lastEvent()->{reason}, qr/menu/, 'причина menu');

done_testing();
