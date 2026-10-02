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
$Globals::playersList = FakePlayers->new(V1 => {name => 'Vera'});
my $members = brainBridge::partyMembers();
is(scalar @$members, 1, 'в составе только другие');
is($members->[0]{name}, 'Vera', 'имя участника');
is($members->[0]{hp_pct}, 25, 'HP видимого участника');
ok(!$members->[0]{dead}, 'жива');
$Globals::playersList = FakePlayers->new();
is(brainBridge::partyMembers()->[0]{hp_pct}, undef, 'не видна — HP устаревшее, не передаю');
$Globals::playersList = FakePlayers->new(V1 => {name => 'Vera', dead => 1});
ok(brainBridge::partyMembers()->[0]{dead}, 'видна мёртвой — dead');
is($members->[0]{map}, 'prt_fild08', 'карта без .gat');
ok(brainBridge::isPartyLeader(), 'я лидер');

# ---- поддержка: Heal подтверждён пакетом сервера ----
$Globals::playersList = FakePlayers->new(V1 => {name => 'Vera'});
my @ev;
{ no warnings 'redefine'; *brainBridge::event = sub { push @ev, [@_] }; }
Plugins::call('packet_skilluse', {skillID => 28, sourceID => 'V1', targetID => 'ME', amount => 120});
is($ev[0][0], 'support', 'событие поддержки');
is_deeply({@{$ev[0]}[1 .. $#{$ev[0]}]}, {skill => 'AL_HEAL', from => 'Vera', to => 'Arkady', amount => 120,
	hp_before => 3, hp_max => 400}, 'Heal от Vera мне — с HP до пакета');
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

# ---- D1: сидение не блокирует продажу и движение ----
my ($okS, $resS) = brainBridge::actionToCommand({action => 'sit'});
ok($okS && ref $resS eq 'HASH', 'sit — без команды sit (флаг sitAuto_forcedBySitCommand не ставится)');
$Globals::char->{sitting} = 1;
my ($okM, $resM) = brainBridge::actionToCommand({action => 'meet_point', map => 'prontera', x => 150, y => 180});
is($resM->[0], 'stand', 'сидит — сначала встать, потом к точке');
$Globals::char->{sitting} = 0;
$Globals::ai_v{sitAuto_forcedBySitCommand} = 1;
my ($okF, $resF) = brainBridge::actionToCommand({action => 'follow', to => 'Vera'});
is_deeply($resF, ['stand', 'follow Vera'], 'флаг sit-команды — снять через stand');
delete $Globals::ai_v{sitAuto_forcedBySitCommand};
($okF, $resF) = brainBridge::actionToCommand({action => 'follow', to => 'Vera'});
is_deeply($resF, ['follow Vera'], 'стоит — без лишнего stand');

# ---- сон (relog) и поездка по делам ----
my ($okSl, $resSl) = brainBridge::actionToCommand({action => 'sleep', seconds => 25200});
is($resSl, 'relog 25200', 'сон 7 ч — relog');
ok(!(brainBridge::actionToCommand({action => 'sleep', seconds => 60}))[0], 'короче 10 мин — нельзя');
ok(!(brainBridge::actionToCommand({action => 'sleep', seconds => 'x; quit'}))[0], 'не число — нельзя');
$Globals::config{storageAuto} = 0;
is((brainBridge::actionToCommand({action => 'service'}))[1], 'autosell', 'без склада — продать и докупить');

# ---- дружба (ORG-024): жителю — принять сразу; запрос — только жителю ----
@Commands::ran = ();
$Globals::config{residents} = 'Vera';
Plugins::call('friend_request', {name => 'Vera'});
is_deeply(\@Commands::ran, ['friend accept'], 'дружба жителя — принимаю');
@Commands::ran = ();
Plugins::call('friend_request', {name => 'Stranger'});
is_deeply(\@Commands::ran, [], 'чужому — решает человек');
is((brainBridge::actionToCommand({action => 'friend_request', to => 'Vera'}))[1], 'friend request Vera', 'запрос жителю');
ok(!(brainBridge::actionToCommand({action => 'friend_request', to => 'Stranger'}))[0], 'чужому — нельзя');

# ---- ORG-039: объявления сервера -> событие world_msg (хуки packet_sysMsg / packet_localBroadcast, аргумент Msg) ----
@ev = ();
Plugins::call('packet_sysMsg', {Msg => "  Ивент: нашествие порингов на prt_fild08!\n", RawMsg => 'x', MsgColor => undef});
is_deeply(\@ev, [['world_msg', text => 'Ивент: нашествие порингов на prt_fild08!', source => 'sys']],
	'системное сообщение — world_msg без управляющих символов');
@ev = ();
Plugins::call('packet_sysMsg', {Msg => 'Ивент: нашествие порингов на prt_fild08!'});
is_deeply(\@ev, [], 'тот же текст за 60 с — не повторяю');
Plugins::call('packet_localBroadcast', {Msg => 'Сервер перезагрузится через 5 минут', color => 'FFFF00'});
is_deeply(\@ev, [['world_msg', text => 'Сервер перезагрузится через 5 минут', source => 'broadcast']],
	'объявление (local_broadcast) — world_msg');
@ev = ();
Plugins::call('packet_sysMsg', {Msg => "\x00 \x01"});
is_deeply(\@ev, [], 'пустое после очистки — не событие');
Plugins::call('packet_sysMsg', {Msg => 'x' x 300});
is(length($ev[0][2]), 120, 'длинное обрезано до 120 символов');

# ---- society: чат-комната-вывеска (ORG-026) и отказ в группе (ORG-027) ----
undef $Globals::currentChatRoom;
%Globals::chatRooms = ();
my ($okC, $resC) = brainBridge::actionToCommand({action => 'chat_room', op => 'open', title => 'Ищу группу на prt_fild08', limit => 5});
ok($okC, 'society: вывеска принята');
is($resC, 'chat create "Ищу группу на prt_fild08" 5 1', 'society: chat create "<title>" <limit> 1 (Commands.pm cmdChatRoom)');
ok(!(brainBridge::actionToCommand({action => 'chat_room', op => 'open', title => 'Продаю "Jellopy"'}))[0], 'society: кавычки нельзя');
ok(!(brainBridge::actionToCommand({action => 'chat_room', op => 'open', title => 'Продаю #1'}))[0], 'society: # нельзя');
ok(!(brainBridge::actionToCommand({action => 'chat_room', op => 'open', title => 'Я' x 19}))[0], 'society: 38 байт UTF-8 — нельзя');
is((brainBridge::actionToCommand({action => 'chat_room', op => 'open', title => 'Отдыхаю', limit => 99}))[1],
	'chat create "Отдыхаю" 20 1', 'society: лимит не больше 20');
is_deeply((brainBridge::actionToCommand({action => 'chat_room', op => 'close'}))[1], [], 'society: не в комнате — закрывать нечего');
is(brainBridge::chatTitle(), undef, 'society: в state chat_room пусто');
$Globals::currentChatRoom = 'R1';
%Globals::chatRooms = (R1 => {title => 'Отдыхаю'});
is(brainBridge::chatTitle(), 'Отдыхаю', 'society: в state — заголовок комнаты');
ok(!(brainBridge::actionToCommand({action => 'chat_room', op => 'open', title => 'Отдыхаю'}))[0], 'society: уже в комнате');
is((brainBridge::actionToCommand({action => 'chat_room', op => 'close'}))[1], 'chat leave', 'society: закрыть — chat leave');
@Commands::ran = ();
brainBridge::handleLine('{"type":"action","id":7,"action":"follow","to":"Vera"}');
is_deeply(\@Commands::ran, ['chat leave', 'follow Vera'], 'society: перед движением — chat leave');
@Commands::ran = ();
brainBridge::handleLine('{"type":"action","id":57,"action":"emote","emotion":12}');
is_deeply(\@Commands::ran, ['e wav'], 'society: номер эмоции — emotion, id сообщения (57) не мешает; комната открыта');
@Commands::ran = ();
@ev = ();
$AI::action = 'route';
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['chat leave'], 'society: AI начал route — chat leave');
is($ev[0][0], 'chat_left', 'society: событие chat_left');
$AI::action = undef;
@Commands::ran = ();
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, [], 'society: AI свободен — комната остаётся');
undef $Globals::currentChatRoom;
$AI::action = 'route';
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, [], 'society: не в комнате — нечего закрывать');
$AI::action = undef;
@ev = ();
Plugins::call('packet/party_invite_result', {name => "Vera\0\0\0", type => 1});
is_deeply(\@ev, [['party_refused', name => 'Vera', code => 1]], 'society: отказ в группе — party_refused');
@ev = ();
Plugins::call('packet/party_invite_result', {name => 'Vera', type => 2});
is_deeply(\@ev, [], 'society: согласие — не событие');

# ---- crew: чат группы (ORG-053) ----
{
	my $saved = $Globals::char->{party};
	$Globals::char->{party} = undef;
	my ($okp, $why) = brainBridge::actionToCommand({action => 'party_say', text => 'Погнали!'});
	ok(!$okp && $why =~ /группы/, 'party_say без группы — отказ');
	$Globals::char->{party} = {joined => 1, name => 'LR_Arkady'};
	($okp, $why) = brainBridge::actionToCommand({action => 'party_say', text => 'Погнали!'});
	ok($okp && $why eq 'p Погнали!', 'party_say -> p <текст>');
	$Globals::char->{party} = $saved;
}

# ---- explore: экспедиция (ORG-054) ----
{
	local $Globals::char->{sitting} = 0;
	my ($oke, $re) = brainBridge::actionToCommand({action => 'explore', map => 'prt_fild06'});
	is_deeply($re, ['conf lockMap prt_fild06', 'conf lockMap_x none', 'conf lockMap_y none',
	                'conf lockMap_randX none', 'conf lockMap_randY none'], 'explore: поле — lockMap без точки');
	($oke, $re) = brainBridge::actionToCommand({action => 'explore', map => 'geffen', x => 119, y => 63});
	is_deeply($re, ['conf lockMap geffen', 'conf lockMap_x 119', 'conf lockMap_y 63', 'conf lockMap_randX 3',
	                'conf lockMap_randY 3'], 'explore: город — к клетке прибытия');
	ok(!(brainBridge::actionToCommand({action => 'explore', map => 'geffen; quit'}))[0], 'explore: неверная карта — отказ');
	ok(!(brainBridge::actionToCommand({action => 'explore', map => 'geffen', x => 'a', y => 1}))[0],
	   'explore: неверные координаты — отказ');
	ok(!(brainBridge::actionToCommand({action => 'explore', map => 'geffen', x => 5}))[0], 'explore: только x — отказ');
	no warnings 'redefine';
	local *FakeField::isWalkable = sub { 0 };
	my ($okw, $why) = brainBridge::actionToCommand({action => 'explore', map => 'prt_fild08', x => 10, y => 10});
	ok(!$okw && $why =~ /непроходима/, 'explore: на этой карте клетка непроходима — отказ');
}

done_testing();
