# Тест плагина autoCreate на заглушках OpenKore: создание по имени, выбор, отказ сервера без повторов.
# Запуск: perl -Ibots/tests/stubs bots/tests/auto_create.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use File::Temp qw(tempdir);
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeSender;
sub new { bless {logins => []}, shift }
sub sendCharLogin { push @{$_[0]{logins}}, $_[1] }

package main;
my $root = "$FindBin::Bin/../..";
my $logs = tempdir(CLEANUP => 1);
$Settings::logs_folder = $logs;
$Globals::messageSender = FakeSender->new;
require "$root/bots/plugins/autoCreate/autoCreate.pl";

sub screen {
	my %a = (autoLogin => $_[0]);
	Plugins::call('charSelectScreen', \%a);
	return \%a;
}
sub reset_all {
	%Globals::config = (char => 0);
	@Globals::chars = ();
	@Misc::created = (); @Misc::modified = ();
	$Misc::createResult = 1;
	$Globals::messageSender->{logins} = [];
	@Log::lines = ();
	$autoCreate::pending = $autoCreate::stopped = 0;
	unlink "$logs/autoCreate.refused";
}
sub errors { grep { /^ERR:/ } @Log::lines }

# ---- выключен: ничего не делает ----
reset_all();
my $a = screen(1);
ok(!exists $a->{return}, 'autoCreate 0: экран не перехвачен');
is(scalar @Misc::created, 0, 'autoCreate 0: персонаж не создаётся');

# ---- пустой аккаунт: создать в первом свободном слоте ----
reset_all();
%Globals::config = (char => 0, autoCreate => 1, autoCreate_name => 'Bram', autoCreate_sex => 'M',
                    autoCreate_hairStyle => 4, autoCreate_hairColor => 2);
$a = screen(1);
is_deeply(\@Misc::created, [[0, 'Bram', 4, 2, 'novice', 'M']], 'createCharacter(0, Bram, 4, 2, novice, M)');
is($a->{return}, 2, 'ждём ответа сервера: return 2 (charSelectScreen ничего не делает)');
ok($autoCreate::pending, 'создание ожидает ответа');

# повторный экран до ответа сервера — без второго пакета
$a = screen(0);
is(scalar @Misc::created, 1, 'пока ждём — повторно не создаём');
is($a->{return}, 2, 'пока ждём — return 2');

# ---- сервер создал: char_created, затем charSelectScreen() без autoLogin -> входим сами ----
$Globals::chars[0] = {name => 'Bram', slot => 0};
Plugins::call('char_created', {char => $Globals::chars[0]});
$a = screen(0);
is_deeply($Globals::messageSender->{logins}, [0], 'вход созданным персонажем: sendCharLogin(0)');
is($a->{return}, 1, 'return 1: персонаж выбран');
ok(!$autoCreate::pending, 'ожидание снято');

# ---- персонаж уже есть в другом слоте: выставить char, вход — штатный (autoLogin) ----
reset_all();
%Globals::config = (char => 0, autoCreate => 1, autoCreate_name => 'Ilsa');
@Globals::chars = ({name => 'Other'}, undef, {name => 'Ilsa'});
$a = screen(1);
is($Globals::config{char}, 2, 'config char -> слот найденного персонажа');
is_deeply(\@Misc::modified, ['char'], 'configModify(char)');
ok(!exists $a->{return}, 'autoLogin: вход делает OpenKore по config char');
is(scalar @Misc::created, 0, 'существующего не создаём');
is_deeply($Globals::messageSender->{logins}, [], 'сами не входим при autoLogin');

# ---- слот из config занят другим персонажем: стоп ----
reset_all();
%Globals::config = (char => 0, autoCreate => 1, autoCreate_name => 'Rook', autoCreate_slot => 0);
@Globals::chars = ({name => 'Other'});
$a = screen(1);
is(scalar @Misc::created, 0, 'занятый слот: не создаём');
ok((grep { /слот 0 занят персонажем Other/ } errors()), 'сообщение о занятом слоте');
ok($autoCreate::stopped, 'остановлено');
screen(1);
is(scalar @Misc::created, 0, 'после стопа — без повторов');

# ---- пустой слот из config: создать именно там, пол F по умолчанию из config ----
reset_all();
%Globals::config = (char => 0, autoCreate => 1, autoCreate_name => 'Odette', autoCreate_slot => 1, autoCreate_sex => 'f');
@Globals::chars = ({name => 'Other'});
screen(1);
is_deeply(\@Misc::created, [[1, 'Odette', 1, 1, 'novice', 'F']], 'слот 1, причёска/цвет по умолчанию 1, пол F');

# ---- отказ сервера: метка, стоп, без повторов даже после «перезапуска» ----
reset_all();
%Globals::config = (char => 0, autoCreate => 1, autoCreate_name => 'Vera');
screen(1);
is(scalar @Misc::created, 1, 'первая попытка создания');
Plugins::call('packet_pre/character_creation_failed', {type => 0});
ok(-e "$logs/autoCreate.refused", 'метка отказа записана');
ok((grep { /имя уже занято/ } errors()), 'причина: имя занято');
my $again = screen(0);                      # Receive.pm вызывает charSelectScreen после отказа
is(scalar @Misc::created, 1, 'после отказа повторно не создаём');
ok(!exists $again->{return}, 'экран отдан OpenKore (оператор решает)');
$autoCreate::stopped = 0;                   # как после перезапуска процесса сторожем
@Log::lines = ();
screen(1);
is(scalar @Misc::created, 1, 'после перезапуска: метка есть — не создаём');
ok((grep { /метка отказа/ } errors()), 'сообщение о метке');
open(my $fh, '<:encoding(UTF-8)', "$logs/autoCreate.refused") or die;
my $m = do { local $/; <$fh> };
like($m, qr/name=Vera\ncode=0\nreason=имя уже занято/, 'в метке имя, код и причина');

# ---- неверное имя: стоп без попытки ----
reset_all();
%Globals::config = (char => 0, autoCreate => 1, autoCreate_name => 'Al');
screen(1);
is(scalar @Misc::created, 0, 'имя короче 4 — не создаём');
ok((grep { /autoCreate_name 'Al'/ } errors()), 'сообщение о неверном имени');

# ---- OpenKore отклонил параметры (createCharacter вернул 0): стоп ----
reset_all();
%Globals::config = (char => 0, autoCreate => 1, autoCreate_name => 'Bram');
$Misc::createResult = 0;
$a = screen(1);
ok($autoCreate::stopped && !$autoCreate::pending, 'createCharacter 0 -> стоп');
ok(!exists $a->{return}, 'экран отдан OpenKore');

# ---- сервер не ответил за таймаут: стоп без повтора ----
reset_all();
%Globals::config = (char => 0, autoCreate => 1, autoCreate_name => 'Bram');
screen(1);
$autoCreate::pending -= $autoCreate::PENDING_TIMEOUT + 1;
$a = screen(0);
is(scalar @Misc::created, 1, 'таймаут: повторно не создаём');
ok($autoCreate::stopped, 'таймаут: стоп');

done_testing();
