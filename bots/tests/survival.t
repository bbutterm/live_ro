# Тест плагина survival на заглушках OpenKore: прогноз урона, зелье, крыло, danger, подъём с отдыха.
# Запуск: perl -Ibots/tests/stubs bots/tests/survival.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeChar;
sub new { my ($c, %a) = @_; bless {%a}, $c }
sub inventory { $_[0]{inv} }

package brainBridge;
our @events;
sub event { push @events, [@_] }

package main;
my $root = "$FindBin::Bin/../..";
my $ME = 'ME01';
$Globals::accountID = $ME;
%Globals::config = ();
my @inv;
$Globals::char = FakeChar->new(name => 'Arkady', hp => 400, hp_max => 400, inv => \@inv);
require "$root/bots/plugins/survival/survival.pl";

sub hit { Plugins::call('packet_attack', {sourceID => $_[0], targetID => $_[1] // $ME, dmg => $_[2] // 0}) }
sub tick { @Commands::ran = (); @brainBridge::events = (); Plugins::call('mainLoop_post'); }
sub events { map { $_->[0] } @brainBridge::events }
sub ev { my ($name) = @_; my ($e) = grep { $_->[0] eq $name } @brainBridge::events; return $e ? {@{$e}[1 .. $#$e]} : undef }
sub reset_state {
	@survival::hits = ();
	$survival::lastAction = $survival::lastWing = $survival::lastDanger = $survival::lastStand = 0;
	$survival::lastActionName = 'none';
}

# ---- урон по другому игроку не учитывается ----
hit('MOB1', 'OTHER', 300);
Plugins::call('packet_skilluse', {skillID => 1, sourceID => 'MOB1', targetID => 'OTHER', damage => 300});
is(scalar @survival::hits, 0, 'урон по чужому не считаю');
hit('MOB1', $ME, 0);
is(scalar @survival::hits, 0, 'промах не считаю');
tick();
is_deeply(\@Commands::ran, [], 'без урона — ничего');

# ---- прогноз смерти, есть Red Potion 501 ----
@inv = ({binID => 7, nameID => 501, name => 'Red Potion', amount => 5},
        {binID => 8, nameID => 504, name => 'White Potion', amount => 1, equipped => 1});
$Globals::char->{hp} = 200;                                  # 50% — выше порога, но урон быстрый
hit('MOB1', $ME, 150);
Plugins::call('packet_skilluse', {skillID => 5, sourceID => 'MOB2', targetID => $ME, damage => 100});
is(scalar @survival::hits, 2, 'удар и навык по мне учтены');
my $f = survival::forecast();
is($f->{dps}, 125, 'dps = 250 / 2 с (минимальная длительность окна)');
ok($f->{predicted} <= 0, 'прогноз HP через 3 с <= 0');
ok($f->{danger}, 'опасно');
tick();
is_deeply(\@Commands::ran, ['is 7'], 'пью зелье 501 по binID (надетое не трогаю)');
my $e = ev('survival');
is($e->{action}, 'potion', 'событие survival: potion');
is($e->{item}, 501, 'зелье 501');
is($e->{hp_pct}, 50, 'hp_pct');
tick();
is_deeply(\@Commands::ran, [], 'не чаще раза в 1 с');
$survival::lastAction -= 1.1;
tick();
is_deeply(\@Commands::ran, ['is 7'], 'через 1 с — снова зелье');
my $st = survival::status();
is_deeply($st, {dps => 125, attackers => 2, last_action => 'potion'}, 'status для brainBridge');

# ---- без урона в окне — не опасно ----
$_->[0] -= 7 for @survival::hits;
$survival::lastAction -= 2;
tick();
is_deeply(\@Commands::ran, [], 'урон старше 6 с забыт — ничего');
is(survival::status()->{dps}, 0, 'dps 0');

# ---- нет зелий, HP 10%, есть Butterfly Wing ----
reset_state();
@inv = ({binID => 3, nameID => 602, name => 'Butterfly Wing', amount => 2});
$Globals::char->{hp} = 40;
hit('MOB1', $ME, 10);
tick();
is_deeply(\@Commands::ran, ['is 3'], 'крыло бабочки');
is_deeply([events()], ['survival', 'escape'], 'события survival и escape');
is(ev('survival')->{action}, 'wing', 'survival: wing');
is_deeply(ev('escape'), {item => 602, hp_pct => 10}, 'escape: item, hp_pct');
hit('MOB1', $ME, 10);
$survival::lastAction -= 2;
tick();
is_deeply(\@Commands::ran, [], 'крыло не чаще раза в 30 с');
is_deeply([events()], ['danger'], 'вместо крыла — danger');

# ---- нет ничего: danger, повтор не раньше 10 с ----
reset_state();
@inv = ();
$Globals::char->{hp} = 80;                                   # 20% < survival_hp 25
hit('MOB1', $ME, 5); hit('MOB2', $ME, 5); hit('MOB1', $ME, 5);
tick();
is_deeply(\@Commands::ran, [], 'команд нет');
$e = ev('danger');
ok($e, 'событие danger');
is($e->{hp_pct}, 20, 'danger: hp_pct');
is($e->{attackers}, 2, 'danger: двое нападающих');
is($e->{dps}, 7.5, 'danger: dps');
$survival::lastAction -= 2; $survival::lastDanger -= 5;
tick();
ok(!ev('danger'), 'через 5 с повтора нет');
$survival::lastAction -= 2; $survival::lastDanger -= 6;
tick();
ok(ev('danger'), 'через 11 с — снова danger');
is(survival::status()->{last_action}, 'danger', 'last_action danger');

# ---- сидит и получил урон: встать ----
reset_state();
$Globals::char->{hp} = 390;
$Globals::char->{sitting} = 1;
hit('MOB1', $ME, 5);
tick();
is_deeply(\@Commands::ran, ['stand'], 'бьют на отдыхе — встаю');
ok(!events(), 'HP высокий — без экстренных событий');
tick();
is_deeply(\@Commands::ran, [], 'stand не чаще раза в 3 с');
$survival::lastStand -= 3;
$_->[0] -= 3 for @survival::hits;
tick();
is_deeply(\@Commands::ran, [], 'урон старше 2 с — не встаю');
$Globals::char->{sitting} = 0;

# ---- мёртв — ничего ----
reset_state();
@inv = ({binID => 7, nameID => 501, name => 'Red Potion', amount => 5});
$Globals::char->{hp} = 0;
hit('MOB1', $ME, 100);
$Globals::char->{dead} = 1;
tick();
is_deeply(\@Commands::ran, [], 'мёртвый — без команд');
ok(!events(), 'мёртвый — без событий');
$Globals::char->{dead} = 0;

# ---- survival 0 — ничего ----
reset_state();
$Globals::config{survival} = 0;
$Globals::char->{hp} = 40;
$Globals::char->{sitting} = 1;
hit('MOB1', $ME, 100);
is(scalar @survival::hits, 0, 'выключен — урон не учитываю');
push @survival::hits, [Time::HiRes::time(), 100, 'MOB1'];
tick();
is_deeply(\@Commands::ran, [], 'выключен — без команд');
ok(!events(), 'выключен — без событий');

done_testing();
