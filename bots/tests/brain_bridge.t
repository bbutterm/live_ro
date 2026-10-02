# Тест brainBridge на заглушках OpenKore: пауза без ai manual, респаун из ai manual.
# Запуск: perl -Ibots/tests/stubs bots/tests/brain_bridge.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeNet; sub new { bless {}, shift } sub getState { 5 }

package main;
my $root = "$FindBin::Bin/../..";
%Globals::config = (attackAuto => 2, route_randomWalk => 1);
$Globals::net = FakeNet->new;
$Globals::char = {name => 'Arkady', hp => 3, hp_max => 400, dead => 0};
require "$root/bots/plugins/brainBridge/brainBridge.pl";

# Пауза: только отбиваться, не бродить; прежние значения сохранены. Никакого ai manual.
my ($ok, $cmds) = brainBridge::actionToCommand({action => 'pause'});
ok($ok, 'пауза принята');
is_deeply($cmds, ['conf -f brainBridge_paused 2 1', 'conf attackAuto 1', 'conf route_randomWalk 0'], 'пауза без ai manual');
ok(!grep({ /ai manual/ } @$cmds), 'нет ai manual');
$Globals::config{brainBridge_paused} = '2 1';
$Globals::config{attackAuto} = 1;
($ok, $cmds) = brainBridge::actionToCommand({action => 'pause'});
is_deeply($cmds, [], 'повторная пауза не затирает сохранённое');
($ok, $cmds) = brainBridge::actionToCommand({action => 'resume'});
is_deeply($cmds, ['conf attackAuto 2', 'conf route_randomWalk 1', 'conf -f brainBridge_paused none', 'ai auto'],
	'снятие паузы возвращает прежнее');
delete $Globals::config{brainBridge_paused};
($ok, $cmds) = brainBridge::actionToCommand({action => 'resume'});
is_deeply($cmds, ['ai auto'], 'без сохранённого — просто ai auto');

# Мёртв в ai manual: через 3 с плагин включает ai auto (иначе OpenKore не сделает респаун).
@Commands::ran = ();
$Globals::char->{dead} = 1;
$AI::state = 1;
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, [], 'сразу не трогаю');
sleep 3;                                        # отметка времени лексическая — ждём по-настоящему
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['ai auto'], 'мёртв и не auto 3 с — ai auto');
@Commands::ran = ();
$AI::state = 2;
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, [], 'в auto — ничего');
$Globals::char->{dead} = 0;
$AI::state = 1;
Plugins::call('mainLoop_post'); sleep 3; Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, [], 'живой в ручном режиме — воля оператора, не трогаю');

# ---- группа: приглашение жителя принимается сразу в хуке (partyAuto 1 не успеет отказать) ----
package FakePlayers; sub new { my ($c, %p) = @_; bless {%p}, $c } sub getByID { $_[0]{$_[1]} } sub getItems { [values %{$_[0]}] }
package main;
$Globals::config{residents} = 'Vera';
@Commands::ran = ();
Plugins::call('party_invite', {partyName => 'LR_Vera'});
is_deeply(\@Commands::ran, ['party join 1'], 'группа жителя — принимаю сразу');
@Commands::ran = ();
Plugins::call('party_invite', {partyName => 'LR_Stranger'});
is_deeply(\@Commands::ran, [], 'чужая группа — решает partyAuto');

$Globals::accountID = 'ME';
$Globals::char->{party} = {joined => 1, name => 'LR_Arkady', users => {
	ME => {name => 'Arkady', admin => 1, online => 1},
	V1 => {name => 'Vera', online => 1, hp => 50, hp_max => 200, map => 'prt_fild08.gat', pos => {x => 10, y => 12}}}};
my $members = brainBridge::partyMembers();
is(scalar @$members, 1, 'в составе только другие');
is($members->[0]{name}, 'Vera', 'имя участника');
is($members->[0]{hp_pct}, 25, 'HP участника');
is($members->[0]{map}, 'prt_fild08', 'карта без .gat');
ok(brainBridge::isPartyLeader(), 'я лидер');

# ---- поддержка: Heal подтверждён пакетом сервера ----
$Globals::playersList = FakePlayers->new(V1 => {name => 'Vera'});
my @ev;
{ no warnings 'redefine'; *brainBridge::event = sub { push @ev, [@_] }; }
Plugins::call('packet_skilluse', {skillID => 28, sourceID => 'V1', targetID => 'ME', amount => 120});
is_deeply($ev[0], ['support', skill => 'AL_HEAL', from => 'Vera', to => 'Arkady', amount => 120], 'Heal от Vera мне');
@ev = ();
Plugins::call('packet_skilluse', {skillID => 28, sourceID => 'V1', targetID => 'X9', amount => 99});
is_deeply(\@ev, [], 'чужое лечение не про меня — нет события');
Plugins::call('packet_skilluse', {skillID => 5, sourceID => 'ME', targetID => 'M1', damage => 50});
is_deeply(\@ev, [], 'атакующее умение — не поддержка');

# ---- лестница застревания: радиус шага 5..30 ----
package FakeField; sub new { bless {}, shift } sub isWalkable { 1 } sub baseName { 'prt_fild08' }
package main;
$Globals::field = FakeField->new;
$Globals::char->{pos_to} = {x => 100, y => 100};
my $far = 0;
for (1 .. 50) {
	my ($ok2, $c) = brainBridge::actionToCommand({action => 'unstuck', radius => 25});
	my ($x, $y) = $c->[1] =~ /^move (\d+) (\d+)$/;
	$far = 1 if abs($x - 100) > 10 || abs($y - 100) > 10;
	die "вне радиуса" if abs($x - 100) > 25 || abs($y - 100) > 25;
}
ok($far, 'радиус 25 — шаги дальше 10 клеток бывают, дальше 25 нет');
my ($ok3, $c3) = brainBridge::actionToCommand({action => 'unstuck', radius => 999});
my ($x3) = $c3->[1] =~ /^move (\d+)/;
ok(abs($x3 - 100) <= 30, 'радиус ограничен 30');

# ---- эмоции: только номера из allowlist, команда OpenKore «e <команда>» ----
my ($ok4, $c4) = brainBridge::actionToCommand({action => 'emote', id => 12});
ok($ok4, 'эмоция 12 принята');
is($c4, 'e wav', 'приветствие — e wav');
($ok4, $c4) = brainBridge::actionToCommand({action => 'emote', id => 3});
is($c4, 'e lv', 'сердце — e lv');
($ok4, $c4) = brainBridge::actionToCommand({action => 'emote', id => 6});
ok(!$ok4, 'ругательная эмоция 6 не из списка');
($ok4, $c4) = brainBridge::actionToCommand({action => 'emote', id => '12; quit'});
ok(!$ok4, 'мусор вместо номера отклонён');
($ok4, $c4) = brainBridge::actionToCommand({action => 'emote'});
ok(!$ok4, 'без номера отклонено');

done_testing();
