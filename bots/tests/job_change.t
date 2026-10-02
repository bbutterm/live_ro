# Тест плагина jobChange на заглушках OpenKore: шаги этапа, ответы в меню по тексту, итог.
# Сценарии — из brain/world/progression.json (выведены из скриптов rAthena). Это проверка
# исполнителя на заглушках, а НЕ прохождение квеста в игре.
# Запуск: perl -Ibots/tests/stubs bots/tests/job_change.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use JSON::PP;
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeChar;
sub new { my ($c, %a) = @_; bless {%a}, $c }
sub inventory { $_[0]{inv} }

package FakeField;
sub new { my ($c, $m) = @_; bless {m => $m}, $c }
sub baseName { $_[0]{m} }

package brainBridge;
our @events;
sub event { push @events, [@_] }

package main;
my $root = "$FindBin::Bin/../..";
# Глобалы OpenKore, которых нет в общей заглушке: диалог, журнал квестов, чат-комнаты.
require Globals;
push @Globals::EXPORT_OK, qw(%talk $questList %chatRooms @chatRoomsID $currentChatRoom);
my $prog = JSON::PP->new->decode(do { local $/; open my $f, '<:raw', "$root/brain/world/progression.json" or die; <$f> });
my %stage = map { my $p = $_; map { ("$p/$_->{id}" => $_) } @{$prog->{paths}{$p}{stages}} } keys %{$prog->{paths}};

%Globals::config = (lockMap => 'prt_fild08', attackAuto => 2, route_randomWalk => 1, autoTalkCont => 0);
%Globals::jobs_lut = (1 => 'Swordsman', 7 => 'Knight', 4 => 'Acolyte', 8 => 'Priest');
$Globals::char = FakeChar->new(name => 'Arkady', jobID => 1, points_skill => 0, pos_to => {x => 150, y => 180},
	inv => [{nameID => 946, amount => 5}, {nameID => 1042, amount => 2}, {nameID => 501, amount => 30}]);
$Globals::field = FakeField->new('prontera');
$Globals::questList = {9003 => {}, 1234 => {}};
require "$root/bots/plugins/jobChange/jobChange.pl";

sub tick { $jobChange::lastTick = 0; Plugins::call('mainLoop_post'); }
sub ran { my @r = @Commands::ran; @Commands::ran = (); return grep { !/^conf / } @r; }
sub lastEvent { my $e = $brainBridge::events[-1]; return $e ? {@{$e}[1 .. $#$e]} : {}; }
sub start { my ($key, %extra) = @_; my $s = $stage{$key} or die $key;
	return jobChange::start({id => 5, path => (split '/', $key)[0], stage => $s->{id}, steps => $s->{steps},
	                         success => $s->{success}, %extra}); }
sub menu { Plugins::call('npc_talk_responses', {name => $_[0], responses => [@{$_[1]}, 'Cancel Chat']}); }

# ---- состояние для мозга ----
my $st = jobChange::status();
is_deeply($st->{quests}, [9003], 'журнал: только квесты смены профессии');
is($st->{items}{946}, 5, 'счётчик предмета Sir Andrew');
is($st->{skill_points}, 0, 'очки навыков');
ok(!$st->{running}, 'этап не идёт');

# ---- проверки запуска ----
my ($ok, $why) = jobChange::start({id => 1, steps => [{do => 'teleport'}]});
ok(!$ok && $why =~ /неизвестный шаг/, 'чужой шаг не исполняю');
($ok, $why) = jobChange::start({id => 1, steps => [{do => 'move', map => 'prt_in', x => 'a', y => 1}]});
ok(!$ok && $why =~ /координаты/, 'неверные координаты');

# ---- Siracuse: верные ответы ----
@Commands::ran = ();
($ok, $why) = start('knight/siracuse_quiz');
ok($ok, "этап начат: $why");
my @conf = grep { /^conf / } @Commands::ran;
ok((grep { $_ eq 'conf lockMap none' } @conf) && (grep { $_ eq 'conf autoTalkCont 1' } @conf), 'lockMap снят, autoTalkCont 1');
@Commands::ran = ();
tick();
is_deeply([ran()], ['move 71 93 prt_in'], 'иду к Sir Siracuse');
$Globals::field = FakeField->new('prt_in');
$Globals::char->{pos_to} = {x => 71, y => 92};
tick();
tick();
is_deeply([ran()], ['talknpc 71 91'], 'на месте — заговорить (без последовательности)');
%Globals::talk = (ID => 1);
menu('Sir Siracuse', ['Sir Andrew sent me to take your test.', 'Oh, nothing.']);
menu('Sir Siracuse', ['Katana', 'Slayer', 'Broadsword', 'Flamberge']);
menu('Sir Siracuse', ['Two Handed Sword Mastery Lv.5', 'Magnum Break Lv.3', 'Provoke Lv.10', 'Bash Lv.10']);
menu('Sir Siracuse', ['Pierce Lv.5', 'Spear Stab Lv.3', 'Spear Boomerang Lv.3', 'Peco Peco Ride Lv.1']);
menu('Sir Siracuse', ['Zephyrus', 'Lance', 'Bill Guisarme', 'Crescent Scythe']);
menu('Sir Siracuse', ['70 % of normal attack speed', '80 % of normal attack speed', '90 % of normal attack speed', '100 % of normal attack speed']);
menu('Sir Siracuse', ['Tell the Novice of a reasonable hunting area.', 'Let the Novice fight while you take the damage.', 'Give the Novice a bunch of Zeny and items.']);
menu('Sir Siracuse', ['Protect everyone in the front of the battle.', 'Gather monsters and destroy them at once.', 'Get as many items possible, at all cost.']);
menu('Sir Siracuse', ['Honor', 'Wealth', 'Status']);
is_deeply([ran()], ['talk resp 0', 'talk resp 3', 'talk resp 2', 'talk resp 2', 'talk resp 0', 'talk resp 1',
                    'talk resp 0', 'talk resp 0', 'talk resp 0'], 'ответы по скрипту (r = select-1)');
%Globals::talk = ();
Plugins::call('npc_talk_done', {ID => 1});
@brainBridge::events = ();
tick(); tick();
ok(!@brainBridge::events, 'квеста 9004 ещё нет — жду');
$Globals::questList = {9004 => {}};
tick();
is($brainBridge::events[-1][0], 'job_change_result', 'событие итога');
my $ev = lastEvent();
ok($ev->{ok}, 'этап пройден: квест 9004 в журнале');
is($ev->{stage}, 'siracuse_quiz', 'этап в событии');
@conf = grep { /^conf / } @Commands::ran;
ok((grep { $_ eq 'conf lockMap prt_fild08' } @conf) && (grep { $_ eq 'conf autoTalkCont 0' } @conf), 'настройки возвращены');
@Commands::ran = ();

# ---- меню не из сценария ----
start('knight/siracuse_quiz');
tick(); tick();
ran();
%Globals::talk = (ID => 2);
menu('Sir Siracuse', ['Sir Andrew sent me to take your test.', 'Oh, nothing.']);
menu('Sir Siracuse', ['Buy Claymore', 'End Conversation']);
$ev = lastEvent();
ok(!$ev->{ok} && $ev->{reason} =~ /меню не из сценария/, 'чужое меню — провал');
ok((grep { $_ eq 'talk no' } @Commands::ran), 'диалог закрыт');
%Globals::talk = ();
@Commands::ran = ();

# ---- Cecilia: одинаковые «Yes./No.» — строго по порядку ----
$Globals::field = FakeField->new('prt_church');
$Globals::char->{pos_to} = {x => 25, y => 24};
start('priest/oath');
tick(); tick();
is_deeply([ran()], ['talknpc 27 24'], 'к Cecilia');
%Globals::talk = (ID => 3);
menu('Sister Cecilia', ['Yes.', 'No!']);
menu('Sister Cecilia', ['Yes.', 'No.']) for 1 .. 6;
menu('Sister Cecilia', ['I do.', 'No.']);
is_deeply([ran()], ['talk resp 0', 'talk resp 1', 'talk resp 0', 'talk resp 0', 'talk resp 1', 'talk resp 1',
                    'talk resp 0', 'talk resp 0'], 'клятва: ответы по порядку из скрипта');
jobChange::stop('тест');
%Globals::talk = ();
@Commands::ran = ();

# ---- коридор искушений: диалоги начинает NPC ----
$Globals::field = FakeField->new('job_prist');
$Globals::char->{pos_to} = {x => 168, y => 17};
my ($walk) = grep { $_->{do} eq 'walk' && $_->{autotalk} } @{$stage{'priest/spiritual_test'}{steps}};
jobChange::start({id => 9, path => 'priest', stage => 'temptations', steps => [$walk], success => {map_xy => ['job_prist', 98, 40]}});
tick();
is_deeply([ran()], ['move 168 178 job_prist'], 'иду по коридору');
%Globals::talk = (ID => 4);
menu('Deviruchi#prst', ["You're right, I quit!", 'Out of my sight, demon!']);
menu('Deviruchi#prst', ["You're right, I'll take it!", 'Silence!']);
menu('Baphomet#prst', ['Deal.', 'No, Baphomet. You lose.']);
is_deeply([ran()], ['talk resp 1', 'talk resp 1', 'talk resp 1'], 'отказ демонам');
%Globals::talk = ();
$Globals::char->{pos_to} = {x => 98, y => 40};
tick(); tick();
ok(lastEvent()->{ok}, 'перенесён в зал мумий — шаг и этап пройдены');
@Commands::ran = ();

# ---- арена Knight: чат-комната, бой, таймаут ----
$Globals::field = FakeField->new('job_knt');
$Globals::char->{pos_to} = {x => 89, y => 104};
@Globals::chatRoomsID = ('c1', 'c2');
%Globals::chatRooms = (c1 => {title => 'Party'}, c2 => {title => 'Waiting Room'});
my @arena = grep { $_->{do} =~ /^(chat_join|fight)$/ } @{$stage{'knight/windsor_arena'}{steps}};
jobChange::start({id => 11, path => 'knight', stage => 'windsor_arena', steps => \@arena, success => {quest => 9007}});
tick();
is_deeply([ran()], ['chat join 1'], 'вход в комнату ожидания по заголовку');
$Globals::char->{pos_to} = {x => 43, y => 146};
tick(); tick();
ok((grep { $_ eq 'conf attackAuto 2' } @Commands::ran), 'бой: attackAuto 2');
$Globals::char->{pos_to} = {x => 43, y => 52};
tick(); tick();
is($jobChange::run{i}, 2, 'первая волна — скрипт перенёс на 43,52');
$jobChange::run{step_since} -= 300;
tick();
$ev = lastEvent();
ok(!$ev->{ok} && $ev->{reason} =~ /не успел/, 'не уложился во время — провал');

# ---- смерть ----
jobChange::start({id => 12, path => 'knight', stage => 'x', steps => [{do => 'wait', until_map => 'prt_in'}]});
$Globals::char->{dead} = 1;
tick();
ok(!lastEvent()->{ok} && lastEvent()->{reason} =~ /мёртв/, 'смерть прерывает этап');

done_testing();
