# autoCreate — автосоздание персонажа жителя на экране выбора персонажа (live_ro, ORG-042). Без LLM.
#
# Включается только config `autoCreate 1`. Имя — `autoCreate_name` (рендер берёт его из brain/world/roster.json),
# слот — `autoCreate_slot` (пусто — первый свободный), внешность — `autoCreate_hairStyle`/`autoCreate_hairColor`,
# пол — `autoCreate_sex` M|F (рендер берёт из BOTNN_SEX env-файла).
#
# Где встроен (upstream/openkore, serverType kRO_RagexeRE_2018_06_20e -> Send/kRO/RagexeRE_2015_10_01b.pm:72
# char_create_version 0x0A39):
#  - хук `charSelectScreen` (src/Misc.pm:1617, аргумент {autoLogin}); `return` хука возвращает сам charSelectScreen.
#    1 — персонаж выбран (мы отправили sendCharLogin), 2 — «создан/удалён»: вызывающий ничего не делает и ждём ответ;
#  - Misc::createCharacter(slot, name, hair_style, hair_color, 'novice', 'M'|'F') (src/Misc.pm:2208, ветка 0x0A39:
#    job novice, пол из аргумента; сервер rAthena PACKETVER >= 20151001 берёт пол из пакета, char_clif.cpp:1273);
#  - успех: хук `char_created` (src/Network/Receive.pm:992), затем charSelectScreen() БЕЗ autoLogin — поэтому вход
#    в созданного персонажа отправляет плагин;
#  - отказ: пакет 006E character_creation_failed (Receive/ServerType0.pm:69, поле type; Receive.pm:1009 читает flag,
#    поэтому берём оба). Хук `packet_pre/character_creation_failed` срабатывает ДО повторного charSelectScreen.
# Отказ сервера (имя занято, запрещённые символы, нет слота) — сообщение и стоп без повторов: пишется метка
# <logs>/autoCreate.refused, и пока она есть, плагин ничего не создаёт даже после перезапуска сторожем.
# Оператор разбирается (scripts/lab doctor, docs/POPULATION.md) и удаляет метку вручную.
# НЕ ПРОВЕРЕНО В ИГРЕ: проверено только тестом на заглушках (bots/tests/auto_create.t).
package autoCreate;

use strict;
use utf8;
use Time::HiRes qw(time);
use Plugins;
use Globals qw(%config @chars %timeout $messageSender);
use Log qw(message warning error);
use Misc;

Plugins::register('autoCreate', 'создать персонажа жителя по имени из config autoCreate_name', \&onUnload);

my $hooks = Plugins::addHooks(
	['charSelectScreen',                      \&onCharSelect],
	['packet_pre/character_creation_failed', \&onRefused],
	['char_created',                          \&onCreated],
);

our $PENDING_TIMEOUT = 60;   # ответ сервера на создание; дольше — стоп без повтора
our ($pending, $stopped) = (0, 0);
our %REFUSE = (0 => 'имя уже занято', 1 => 'отказ по возрасту', 2 => 'запрещённые символы или неверные данные',
               3 => 'нет права на слот', 255 => 'создание запрещено сервером');

sub onUnload { Plugins::delHooks($hooks); }

sub say_ { message "[autoCreate] $_[0]\n", 'connection'; }
sub stop_ {
	my ($why) = @_;
	error "[autoCreate] $why — автосоздание остановлено, повторов не будет\n";
	$stopped = 1;
	$pending = 0;
}

sub marker {
	my $dir = defined $Settings::logs_folder && $Settings::logs_folder ne '' ? $Settings::logs_folder : 'logs';
	return "$dir/autoCreate.refused";
}

sub validName { return defined $_[0] && $_[0] =~ /^[A-Za-z][A-Za-z0-9]{3,22}$/; }

sub findSlot {
	my ($name) = @_;
	for my $i (0 .. $#chars) {
		return $i if $chars[$i] && ref $chars[$i] && defined $chars[$i]{name} && $chars[$i]{name} eq $name;
	}
	return undef;
}

sub freeSlot {
	my $want = $config{autoCreate_slot};
	if (defined $want && $want ne '') {
		return (undef, "autoCreate_slot '$want' — не число") unless $want =~ /^\d+$/;
		return (undef, "слот $want занят персонажем $chars[$want]{name}") if $chars[$want] && ref $chars[$want] && %{$chars[$want]};
		return ($want, undef);
	}
	for my $i (0 .. 14) {
		return ($i, undef) unless $chars[$i] && ref $chars[$i] && %{$chars[$i]};
	}
	return (undef, 'нет свободного слота');
}

sub onCharSelect {
	my (undef, $args) = @_;
	return unless $config{autoCreate};
	return if $stopped;
	my $name = $config{autoCreate_name};
	unless (validName($name)) {
		stop_("autoCreate_name '" . ($name // '') . "' — нужно 4-23 латинских букв/цифр, первая буква");
		return;
	}
	my $slot = findSlot($name);
	if (defined $slot) {
		$pending = 0;
		if ($chars[$slot]{deleteDate}) {
			stop_("персонаж $name (слот $slot) помечен на удаление");
			return;
		}
		Misc::configModify('char', $slot, 1) if !defined $config{char} || $config{char} ne $slot;
		return if $args->{autoLogin};        # штатный вход OpenKore по config char
		say_("вхожу персонажем $name (слот $slot)");
		$messageSender->sendCharLogin($slot);
		$timeout{charlogin}{time} = time;
		$args->{return} = 1;
		return;
	}
	if (-e marker()) {
		stop_("есть метка отказа " . marker() . " (прошлый отказ сервера); разберитесь и удалите её");
		return;
	}
	if ($pending) {
		if (time - $pending > $PENDING_TIMEOUT) {
			stop_("сервер не ответил на создание $name за $PENDING_TIMEOUT с");
			return;
		}
		$args->{return} = 2;                 # ждём ответ сервера
		return;
	}
	my ($free, $why) = freeSlot();
	unless (defined $free) {
		stop_($why);
		return;
	}
	my $sex = uc($config{autoCreate_sex} // 'M');
	unless ($sex =~ /^[MF]$/) {
		stop_("autoCreate_sex '$sex' — нужно M или F");
		return;
	}
	my $hs = $config{autoCreate_hairStyle} // 1;
	my $hc = $config{autoCreate_hairColor} // 1;
	$hs = 1 unless $hs =~ /^\d+$/;
	$hc = 1 unless $hc =~ /^\d+$/;
	say_("персонажа $name нет — создаю в слоте $free (причёска $hs, цвет $hc, пол $sex, Novice)");
	$pending = time;
	$timeout{charlogin}{time} = time;
	unless (Misc::createCharacter($free, $name, $hs, $hc, 'novice', $sex)) {
		stop_("OpenKore отклонил параметры создания (см. сообщение выше)");
		return;
	}
	$args->{return} = 2;
}

sub onRefused {
	my (undef, $args) = @_;
	return unless $config{autoCreate} && $pending;
	my $code = $args->{type} // $args->{flag};
	my $why = defined $code && exists $REFUSE{$code} ? $REFUSE{$code} : 'неизвестная причина' . (defined $code ? " ($code)" : '');
	my $name = $config{autoCreate_name} // '';
	if (open(my $fh, '>:encoding(UTF-8)', marker())) {
		print $fh "name=$name\ncode=" . ($code // '') . "\nreason=$why\nts=" . int(time) . "\n";
		close $fh;
	} else {
		warning "[autoCreate] не записать метку " . marker() . ": $!\n";
	}
	stop_("сервер отказал в создании $name: $why");
}

sub onCreated {
	my (undef, $args) = @_;
	return unless $config{autoCreate} && $pending;
	my $c = $args->{char} || {};
	say_("сервер создал персонажа " . ($c->{name} // '?') . " в слоте " . ($c->{slot} // '?'));
}

1;
