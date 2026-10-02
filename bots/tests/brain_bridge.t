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

# ---- guild: гильдия жителей (ORG-052) ----
{
	$Globals::config{residents} = 'Vera';
	$Globals::char->{guild} = undef;
	is(brainBridge::guildState(), undef, 'guild: не в гильдии — state.guild пусто');
	my ($okg, $cg) = brainBridge::actionToCommand({action => 'guild_create', name => 'Hearth of  Prontera'});
	ok($okg && $cg eq 'guild create Hearth of Prontera', 'guild: guild create <имя> (Commands.pm cmdGuild)');
	ok(!(brainBridge::actionToCommand({action => 'guild_create', name => 'LR_Guild'}))[0], 'guild: _ нет в char_name_letters — нельзя');
	ok(!(brainBridge::actionToCommand({action => 'guild_create', name => 'A' x 24}))[0], 'guild: длиннее 23 — нельзя');
	ok(!(brainBridge::actionToCommand({action => 'guild_create', name => 'Гильдия'}))[0], 'guild: кириллица — нельзя');
	ok(!(brainBridge::actionToCommand({action => 'guild_invite', to => 'Vera'}))[0], 'guild: не в гильдии — звать нельзя');
	ok(!(brainBridge::actionToCommand({action => 'guild_say', text => 'Привет'}))[0], 'guild: не в гильдии — чат нельзя');

	# приглашение: без ожидания — не принимаю (решает guildAutoDeny), после guild_expect — принимаю сразу
	@Commands::ran = (); @ev = ();
	Plugins::call('packet/guild_request', {ID => 'G1', name => "Hearth of Prontera\0\0"});
	is_deeply(\@Commands::ran, [], 'guild: неожиданное приглашение — не принимаю');
	is_deeply(\@ev, [['guild_invite', guild => 'Hearth of Prontera']], 'guild: событие guild_invite');
	my ($oke, $ce) = brainBridge::actionToCommand({action => 'guild_expect', name => 'Hearth of Prontera'});
	ok($oke && ref $ce eq 'HASH', 'guild: guild_expect — без команды OpenKore');
	@ev = ();
	Plugins::call('packet/guild_request', {ID => 'G2', name => 'Other Guild'});
	is_deeply(\@Commands::ran, [], 'guild: другая гильдия — не принимаю');
	Plugins::call('packet/guild_request', {ID => 'G1', name => 'Hearth of Prontera'});
	is_deeply(\@Commands::ran, ['guild join 1'], 'guild: ожидаемая гильдия жителя — guild join 1');
	is_deeply($ev[-1], ['guild_joined_auto', guild => 'Hearth of Prontera'], 'guild: событие guild_joined_auto');
	@Commands::ran = ();
	Plugins::call('packet/guild_request', {ID => 'G1', name => 'Hearth of Prontera'});
	is_deeply(\@Commands::ran, [], 'guild: ожидание одноразовое');

	# состав по пакетам: 0A84 (master_char_id) + 0AA5 (имена дозапрошены), я — по $charID
	$Globals::charID = 'C1';
	$Globals::char->{guild} = {name => 'Hearth of Prontera'};
	%Globals::guild = (master_char_id => 'C2', member => [
		{charID => 'C1', online => 1, lv => 12}, {charID => 'C2', name => 'Vera', online => 1, lv => 15},
		{charID => 'C3', name => 'Boris', online => 0, lv => 9}]);
	is_deeply(brainBridge::guildState(), {name => 'Hearth of Prontera', master => 'Vera', online => 2, members => [
		{name => 'Arkady', online => JSON::PP::true(), lv => 12}, {name => 'Vera', online => JSON::PP::true(), lv => 15},
		{name => 'Boris', online => JSON::PP::false(), lv => 9}]}, 'guild: state.guild — имя, мастер по charID, состав, онлайн');
	ok(!(brainBridge::actionToCommand({action => 'guild_create', name => 'Second'}))[0], 'guild: уже в гильдии — создать нельзя');
	$Globals::playersList = FakePlayers->new(V1 => {name => 'Vera'});
	is((brainBridge::actionToCommand({action => 'guild_invite', to => 'Vera'}))[1], 'guild request Vera', 'guild: guild request жителю');
	ok(!(brainBridge::actionToCommand({action => 'guild_invite', to => 'Stranger'}))[0], 'guild: чужого — нельзя');
	$Globals::playersList = FakePlayers->new();
	my ($okv, $whyv) = brainBridge::actionToCommand({action => 'guild_invite', to => 'Vera'});
	ok(!$okv && $whyv =~ /не виден/, 'guild: житель не виден — guild request не найдёт (Match::player)');
	is((brainBridge::actionToCommand({action => 'guild_say', text => "Кто в городе?\n"}))[1], 'g Кто в городе?', 'guild: g <текст>');

	@ev = ();
	Plugins::call('packet/guild_create_result', {type => 3});
	Plugins::call('packet/guild_invite_result', {type => 2});
	Plugins::call('packet_guildMsg', {MsgUser => 'Vera', Msg => 'Кто в городе?'});
	Plugins::call('packet_guildMsg', {MsgUser => 'Arkady', Msg => 'моё'});
	is_deeply(\@ev, [['guild_create_result', code => 3], ['guild_invite_result', code => 2],
		['chat_guild', from => 'Vera', text => 'Кто в городе?']], 'guild: события создания, приглашения и чата (своё — нет)');

	$Globals::char->{inventory} = [{nameID => 714, amount => 1}, {nameID => 501, amount => 5}];
	is(brainBridge::emperiumCount(), 1, 'guild: Emperium в рюкзаке');
	delete $Globals::char->{inventory};
	is(brainBridge::emperiumCount(), 0, 'guild: нет рюкзака — 0');
	$Globals::char->{guild} = undef;
	%Globals::guild = ();
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

# ---- healer: Heal/Blessing/AGI на игрока (ORG-069) ----
package FakeList; sub new { my ($c, @p) = @_; bless {items => [@p]}, $c } sub getItems { [@{$_[0]{items}}] }
sub find { my ($s, $x) = @_; for my $i (0 .. $#{$s->{items}}) { return $i if $s->{items}[$i] == $x } -1 }
sub getByID { undef }
package main;
{
	local $Globals::char->{name} = 'Vera';
	local $Globals::char->{dead} = 0;
	local $Globals::char->{pos_to} = {x => 237, y => 310};
	local $Globals::char->{skills} = {AL_HEAL => {lv => 10}, AL_BLESSING => {lv => 0}, AL_INCAGI => {lv => 3},
	                                   AL_DP => {lv => 5}, MG_FIREBOLT => {lv => 4}};
	my $savedPlayers = $Globals::playersList;             # local не виден через импорт Globals — присваиваю
	$Globals::playersList = FakeList->new({name => 'Somebody', pos_to => {x => 1, y => 1}},
		{name => 'Arkady', pos_to => {x => 240, y => 305}}, {name => 'Far Away', pos_to => {x => 237, y => 330}},
		{name => 'Ghost', dead => 1, pos_to => {x => 237, y => 311}});
	is_deeply(brainBridge::supportSkills(), {AL_HEAL => 10, AL_INCAGI => 3}, 'healer: support_skills — выученные из списка');
	my ($okh, $rh) = brainBridge::actionToCommand({action => 'skill_on_player', skill => 'AL_HEAL', to => 'Arkady'});
	ok($okh, 'healer: Heal на видимого жителя принят');
	is($rh, 'sp 28 1', 'healer: sp <id навыка> <номер игрока> (Commands.pm cmdUseSkill sp)');
	is((brainBridge::actionToCommand({action => 'skill_on_player', skill => 'AL_INCAGI', to => 'Arkady'}))[1], 'sp 29 1',
	   'healer: Increase AGI');
	my @no = (
		[{skill => 'MG_FIREBOLT', to => 'Arkady'}, qr/не из списка/, 'атакующий навык'],
		[{skill => 'AL_DP', to => 'Arkady'}, qr/не из списка/, 'навык вне allowlist'],
		[{skill => 'AL_BLESSING', to => 'Arkady'}, qr/не выучен/, 'не выученный Blessing'],
		[{skill => 'AL_HEAL', to => 'Nobody'}, qr/не виден/, 'невидимая цель'],
		[{skill => 'AL_HEAL', to => 'Far Away'}, qr/дальше 9/, 'дальше 9 клеток'],
		[{skill => 'AL_HEAL', to => 'Ghost'}, qr/мёртв/, 'мёртвая цель'],
		[{skill => 'AL_HEAL', to => 'Vera'}, qr/себя/, 'на себя'],
		[{skill => 'AL_HEAL', to => 'Ar"kady'}, qr/неверный/, 'кавычка в имени'],
	);
	for my $c (@no) {
		my ($okn, $why) = brainBridge::actionToCommand({action => 'skill_on_player', %{$c->[0]}});
		ok(!$okn && $why =~ $c->[1], "healer: отказ — $c->[2]");
	}
	$Globals::currentChatRoom = 'R9';
	%Globals::chatRooms = (R9 => {title => 'Лечу у собора'});
	@Commands::ran = ();
	brainBridge::handleLine('{"type":"action","id":91,"action":"skill_on_player","skill":"AL_HEAL","to":"Arkady"}');
	is_deeply(\@Commands::ran, ['chat leave', 'sp 28 1'], 'healer: в чат-комнате — сначала chat leave (rAthena chatID)');
	undef $Globals::currentChatRoom;
	%Globals::chatRooms = ();
	local $Globals::char->{dead} = 1;
	ok(!(brainBridge::actionToCommand({action => 'skill_on_player', skill => 'AL_HEAL', to => 'Arkady'}))[0],
	   'healer: мёртвый не лечит');
	$Globals::playersList = $savedPlayers;
}


# ---- spar: спарринг (ORG-061) — только с жителем; пока идёт, тело у плагина, от мозга — только реплики ----
{
	no warnings 'once';
	my ($sparOn, @sparArgs) = (0);
	local *spar::start = sub { push @sparArgs, $_[0]; $sparOn = 1; return (1, "спарринг с $_[0]{to}") };
	local *spar::running = sub { $sparOn };
	local *spar::stop = sub { $sparOn = 0 };
	$Globals::config{residents} = 'Vera';
	my ($okn, $why) = brainBridge::actionToCommand({action => 'spar', to => 'Stranger', role => 'first', room => 'Prontera'});
	ok(!$okn && $why =~ /только с жителем/, 'spar: не житель — отказ');
	my ($oks, $res) = brainBridge::actionToCommand({action => 'spar', to => 'Vera', role => 'first', room => 'Prontera'});
	ok($oks && ref $res eq 'HASH', 'spar: житель — плагин spar');
	is($sparArgs[0]{to}, 'Vera', 'spar: соперник передан плагину');
	@Commands::ran = ();
	brainBridge::handleLine('{"type":"action","id":101,"action":"hunt","map":"prt_fild08"}');
	is_deeply(\@Commands::ran, [], 'spar: охота во время спарринга отклонена');
	brainBridge::handleLine('{"type":"action","id":102,"action":"whisper","to":"Vera","text":"gg [spar:yield]"}');
	is_deeply(\@Commands::ran, ['pm "Vera" gg [spar:yield]'], 'spar: шёпот во время спарринга можно');
	($oks) = brainBridge::actionToCommand({action => 'spar_stop', why => 'Vera сдалась'});
	ok($oks && !$sparOn, 'spar_stop останавливает');
	($okn) = brainBridge::actionToCommand({action => 'spar_stop'});
	ok(!$okn, 'spar_stop без спарринга — отказ');
}


# ---- achieve: достижения сервера (ORG-080) — пакеты 0A23/0A24/0A26 -> события; награда только невзятая ----
{
	no warnings 'redefine';
	my @ev;
	local *brainBridge::event = sub { my ($kind, %d) = @_; push @ev, {kind => $kind, %d} };
	%Globals::achievements = (200005 => {title => 'Official Adventurer'});
	# список при входе (OpenKore уже заполнил $achievementList — хук packet/ вызывается после обработчика)
	$Globals::achievementList = {
		200005 => {achievementID => 200005, completed => 1, completed_at => 1700000000, reward => 0},
		220005 => {achievementID => 220005, completed => 1, completed_at => 1700000100, reward => 1},
		128000 => {achievementID => 128000, completed => 0, completed_at => 0, reward => 0},
	};
	Plugins::call('packet/achievement_list', {total_points => 20, rank => 1});
	is($ev[0]{kind}, 'achievement_list', 'achieve: 0A23 -> achievement_list');
	is_deeply($ev[0]{done}, [[200005, 1700000000, 0], [220005, 1700000100, 1]], 'achieve: только выполненные');
	is($ev[0]{points}, 20, 'achieve: очки из заголовка');
	is_deeply(brainBridge::achState(), {points => 20, rank => 1, done => 2}, 'achieve: state.achievements');
	@ev = ();
	Plugins::call('packet/achievement_update', {total_points => 20, rank => 1, achievementID => 128000, completed => 0});
	is(scalar @ev, 0, 'achieve: прогресс без выполнения — не событие');
	Plugins::call('packet/achievement_update', {total_points => 20, rank => 1, achievementID => 200005, completed => 1,
		completed_at => 1700000000, reward => 0});
	is(scalar @ev, 0, 'achieve: уже сообщённое в списке — не повторяю');
	$Globals::achievementList->{128000}{completed} = 1;
	Plugins::call('packet/achievement_update', {total_points => 30, rank => 1, achievementID => 128000, completed => 1,
		completed_at => 1700000500, reward => 0});
	is($ev[0]{kind}, 'achievement', 'achieve: новое выполненное — событие');
	is($ev[0]{id}, 128000, 'achieve: номер');
	is($ev[0]{points}, 30, 'achieve: очки');
	Plugins::call('packet/achievement_reward_ack', {received => 1, achievementID => 200005});
	is($ev[-1]{kind}, 'achievement_reward', 'achieve: 0A26 -> achievement_reward');
	ok($ev[-1]{ok}, 'achieve: 1 — награда выдана');
	is((brainBridge::actionToCommand({action => 'achieve_reward', id => 200005}))[1], 'achieve reward 200005',
	   'achieve: награда выполненного');
	my @no = ([220005, qr/уже получена/], [999, qr/нет в списке/], ['1;quit', qr/неверный/]);
	$Globals::achievementList->{777} = {achievementID => 777, completed => 0};
	push @no, [777, qr/не выполнено/];
	for my $c (@no) {
		my ($okn, $why) = brainBridge::actionToCommand({action => 'achieve_reward', id => $c->[0]});
		ok(!$okn && $why =~ $c->[1], "achieve: отказ награды $c->[0]");
	}
	undef $Globals::achievementList;
}

done_testing();
