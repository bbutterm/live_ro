# Тест плагина refine на заглушках OpenKore: покупка руды, Refine UI, снятие оружия, заточка только при шансе 100,
# стоп «дальше риск», итог и возврат оружия. Это проверка исполнителя, а НЕ заточка в игре (ORG-072, ТЗ Т-28).
# Запуск: perl -Ibots/tests/stubs bots/tests/refine.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use JSON::PP;
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeInv;
sub new { my ($c, @items) = @_; bless [@items], $c }
sub get { my ($s, $i) = @_; my ($it) = grep { $_->{binID} == $i } @$s; $it }

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
require Globals;
push @Globals::EXPORT_OK, qw($refineUI);
our $refineUI;
*Globals::refineUI = \$refineUI;
my $data = JSON::PP->new->decode(do { local $/; open my $f, '<:raw', "$root/brain/world/refine.json" or die; <$f> });

my $knife = {binID => 3, nameID => 1201, name => 'Knife', upgrade => 4, equipped => 2};
my $phracon = {binID => 7, nameID => 1010, name => 'Phracon', amount => 1};
%Globals::config = (lockMap => 'prt_fild08', attackAuto => 2, route_randomWalk => 1, autoTalkCont => 0, sitAuto_idle => 1);
$Globals::char = FakeChar->new(name => 'Arkady', pos_to => {x => 150, y => 180}, inv => FakeInv->new($knife, $phracon),
                               equipment => {rightHand => $knife});
$Globals::field = FakeField->new('prontera');
require "$root/bots/plugins/refine/refine.pl";

sub tick { $refine::lastTick = 0; Plugins::call('mainLoop_post'); }
sub ran { my @r = grep { !/^conf / } @Commands::ran; @Commands::ran = (); return @r; }
sub lastEvent { my $e = $brainBridge::events[-1]; return $e ? {@{$e}[1 .. $#$e]} : {}; }
sub at { my ($m, $x, $y) = @_; $Globals::field = FakeField->new($m); $Globals::char->{pos_to} = {x => $x, y => $y}; }
my %shop = (%{$data->{shop}}, menu => 'Phracon');
my %action = (id => 9, item => 1201, inv => 3, target => 7, ore => 1010, buy => 2, shop => \%shop, smith => $data->{smith});

# ---- состояние для мозга ----
my $st = refine::status();
is($st->{weapon}{id}, 1201, 'оружие в правой руке');
is($st->{weapon}{upgrade}, 4, 'его заточка');
ok($st->{weapon}{equipped}, 'надето');
is($st->{ores}{1010}, 1, 'руда в рюкзаке');
ok(!$st->{running}, 'не точу');

# ---- отказы ----
my ($ok, $why) = refine::start({%action, ore => 984});
ok(!$ok && $why =~ /руда/, 'Oridecon плагин не берёт (не продаётся у Vurewell)');
($ok, $why) = refine::start({%action, inv => 5});
ok(!$ok && $why =~ /нет в рюкзаке/, 'чужой индекс — отказ');
($ok, $why) = refine::start({%action, target => 4});
ok(!$ok && $why =~ /уже \+4/, 'цель не выше текущей — отказ');
($ok, $why) = refine::start({%action, target => 11});
ok(!$ok && $why =~ /1\.\.10/, 'цель > 10 — отказ');
($ok, $why) = refine::start({%action, shop => {%shop, menu => 'r~/x/; quit'}});
ok(!$ok && $why =~ /продавец/, 'меню не из букв — отказ');

# ---- покупка руды ----
@Commands::ran = ();
($ok, $why) = refine::start(\%action);
ok($ok, "начал: $why");
my @conf = grep { /^conf / } @Commands::ran;
ok((grep { $_ eq 'conf lockMap none' } @conf) && (grep { $_ eq 'conf autoTalkCont 1' } @conf), 'lockMap снят, autoTalkCont 1');
@Commands::ran = ();
tick();
is_deeply([ran()], ['move 56 66 prt_in'], 'иду к Vurewell');
at('prt_in', 56, 66);
tick();
tick();
is_deeply([ran()], ['talknpc 56 68 c r~/^Phracon/ c d2 n'], 'покупка по последовательности: меню, ввод количества');
tick();
is(refine::status()->{phase}, 'buy_talk', 'руды ещё нет — жду');
$phracon->{amount} = 3;
tick();
is(refine::status()->{phase}, 'smith_move', 'руда пришла — к кузнецу');

# ---- кузнец и Refine UI ----
tick();
is_deeply([ran()], ['move 63 58 prt_in'], 'иду к Hollgrehenn');
at('prt_in', 63, 58);
tick();
tick();
is_deeply([ran()], ['talknpc 63 60'], 'заговорить (Refine UI откроет сервер)');
tick();
is_deeply([ran()], [], 'жду пакета 0AA0');
$refineUI = {open => 1};
tick();
tick();
is_deeply([ran()], ['uneq 3'], 'надетое Refine UI не берёт — снимаю');
$knife->{equipped} = 0;
tick();
tick();
is_deeply([ran()], ['refineui select 3'], 'выбрать оружие в Refine UI');
$refineUI->{materials} = [{nameid => 1010, chance => 100, zeny => 50}];
tick();
tick();
is_deeply([ran()], ['refineui refine 3 1010 0'], 'шанс 100 — точу без Blacksmith Blessing');
tick();
is_deeply([ran()], [], 'жду пакета 0188');
$knife->{upgrade} = 5;
$phracon->{amount} = 2;
tick();
tick();
is_deeply([ran()], ['refineui select 3'], '+5 — список руды для следующего уровня');
$refineUI->{materials} = [{nameid => 1010, chance => 100, zeny => 50}];
tick();
tick();
is_deeply([ran()], ['refineui refine 3 1010 0'], 'снова точу');
$knife->{upgrade} = 6;
$phracon->{amount} = 1;
tick();
tick();
ran();
$refineUI->{materials} = [{nameid => 1010, chance => 60, zeny => 50}];      # подменённый шанс — проверка стопа
tick();
tick();
my @end = ran();
ok(!grep({ /refineui refine/ } @end), 'шанс < 100 — попытки нет');
ok((grep { $_ eq 'refineui cancel' } @end) && (grep { $_ eq 'eq 3' } @end), 'Refine UI закрыт, оружие надето обратно');
my $ev = lastEvent();
is($brainBridge::events[-1][0], 'refine_result', 'итог мозгу');
ok($ev->{ok}, 'два шага подтверждены клиентом — ok');
is_deeply([@{$ev}{qw(from to done)}], [4, 6, 2], '+4 -> +6');
like($ev->{reason}, qr/дальше риск: шанс 60%/, 'причина остановки');
ok(!refine::status()->{running}, 'закончил');
is($Globals::config{lockMap}, 'prt_fild08', 'настройки вернул');

# ---- без покупки, цель достигнута ----
$knife->{equipped} = 2;
$refineUI = undef;
at('prt_in', 63, 58);
($ok) = refine::start({%action, buy => 0, target => 7});
ok($ok, 'руда есть — сразу к кузнецу');
tick();
tick();
$refineUI = {open => 1};
tick();
$knife->{equipped} = 0;
tick();
tick();
$refineUI->{materials} = [{nameid => 1010, chance => 100, zeny => 50}];
tick();
tick();
$knife->{upgrade} = 7;
@Commands::ran = ();
tick();
$ev = lastEvent();
ok($ev->{ok} && $ev->{to} == 7 && $ev->{reason} =~ /цель/, '+7 — цель, ok');

# ---- провалы ----
$knife->{equipped} = 2;
$knife->{upgrade} = 4;
$refineUI = undef;
($ok) = refine::start({%action, buy => 0});
local $refine::TIMEOUT{smith_talk} = -1;
tick();
tick();
tick();
$ev = lastEvent();
ok(!$ev->{ok} && $ev->{reason} =~ /smith_talk: тайм-аут/, 'UI не открылся — провал по тайм-ауту');
$refine::TIMEOUT{smith_talk} = 45;
($ok) = refine::start({%action, buy => 0});
$Globals::char->{inv} = FakeInv->new($phracon);
tick();
$ev = lastEvent();
ok(!$ev->{ok} && $ev->{reason} =~ /пропал/, 'предмет пропал — провал');
$Globals::char->{inv} = FakeInv->new($knife, $phracon);
$Globals::char->{dead} = 1;
($ok, $why) = refine::start({%action, buy => 0});
ok(!$ok && $why =~ /мёртв/, 'мёртвому — отказ');

done_testing();
