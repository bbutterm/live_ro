# pets — питомец жителя (live_ro, ORG-051): приручение, вылупление, корм. Без LLM.
#
# Что делать, решает мозг (brain/live_brain/pets.py по brain/world/pets.json из db/re/pet_db.yml); плагин
# только исполняет и проверяет по пакетам сервера:
#   pet_tame {item, mob}  — использовать предмет приручения (is <инв.>) → сервер присылает
#                           pet_capture_process (0x19E) → «pet c <монстр>» на ближайшем видимом монстре этого
#                           вида → pet_capture_result (0x1A0) → событие pet_tame_result {ok, mob, item}.
#                           Монстр дальше TAME_DIST клеток или не виден — отказ без попытки.
#   pet_hatch {egg}       — Pet Incubator (643): is <инкубатор> → egg_list (0x1A6) → «pet h <яйцо>» →
#                           pet_info (0x1A2) → событие pet_hatched {ok, egg, mob, name}.
#   Корм — OpenKore сам (pet_autoFeed 1, pet_hunger 25, pet_return 20 в config.txt). Факт кормления —
#   событие pet_fed {ok, food} по пакету pet_food (0x1A3). Докупка Pet Food: блок buyAuto «Pet Food» в
#   config.txt выключен (disabled 1); плагин включает его, пока есть питомец с кормом Pet Food (537).
# pet_setup {food_on, items, mobs} — что отслеживать для мозга (предметы приручения своих любимцев и их монстры)
#                         и докупать ли Pet Food. Мозг шлёт после каждого подключения тела.
# Для мозга: pets::status() -> {has, type, name, hungry, friendly, running, eggs, items {id: n}, near {mob: клеток}}.
# На время приручения attackAuto 1 (не нападать первым на цель), потом прежнее значение.
# НЕ ПРОВЕРЕНО В ИГРЕ: проверено только тестом на заглушках (bots/tests/pets.t).
package pets;

use strict;
use utf8;
use Time::HiRes qw(time);
use JSON::PP;
use Plugins;
use Globals qw($char $monstersList %config %pet);
use Log qw(message warning);
use Commands;

Plugins::register('pets', 'питомец жителя: приручение, вылупление, корм (шаги от мозга)', \&onUnload);

my $hooks = Plugins::addHooks(
	['mainLoop_post',               \&onTick],
	['packet/pet_capture_process',  \&onCaptureProcess],
	['packet/pet_capture_result',   \&onCaptureResult],
	['packet/egg_list',             \&onEggList],
	['packet/pet_info',             \&onPetInfo],
	['packet/pet_food',             \&onPetFood],
);

our $INCUBATOR = 643;
our $PET_FOOD = 537;
our $TAME_DIST = 8;
our %TIMEOUT = (use => 8, capture => 12, egg => 8, hatch => 10);
our %run;                   # what (tame|hatch), phase, since, item, mob, egg, target, saved
our $lastTick = 0;
our $foodOn;                # последнее выставленное состояние buyAuto «Pet Food» (undef — не выставлялось)
our %watch = (items => [], mobs => []);   # от мозга (pet_setup)
our $wantFood = 0;
our $NEAR = 14;

sub onUnload { Plugins::delHooks($hooks); }

sub ev {
	my $ev = brainBridge->can('event');
	$ev->(@_) if $ev;
}

sub bool { return $_[0] ? JSON::PP::true() : JSON::PP::false(); }

# ---------- рюкзак и монстры ----------

sub invItem {
	my ($id) = @_;
	return unless $char && $char->inventory;
	for my $item (@{$char->inventory}) {
		return $item if $item->{nameID} == $id && !$item->{equipped};
	}
	return;
}

sub eggs {
	my %eggs;
	return [] unless $char && $char->inventory;
	for my $item (@{$char->inventory}) {
		$eggs{$item->{nameID}} = 1 if $item->{nameID} >= 9001 && $item->{nameID} <= 9200;   # яйца питомцев
	}
	return [sort { $a <=> $b } keys %eggs];
}

sub dist {
	my ($p, $q) = @_;
	my ($dx, $dy) = (abs($p->{x} - $q->{x}), abs($p->{y} - $q->{y}));
	return $dx > $dy ? $dx : $dy;
}

sub nearestMob {
	my ($mob) = @_;
	return unless $monstersList && $char && $char->{pos_to};
	my ($best, $bd);
	for my $m (@{$monstersList->getItems}) {
		next unless $m && ($m->{nameID} // -1) == $mob && $m->{pos_to} && !$m->{dead};
		my $d = dist($char->{pos_to}, $m->{pos_to});
		($best, $bd) = ($m, $d) if $d <= $TAME_DIST && (!defined $bd || $d < $bd);
	}
	return $best;
}

sub hasPet { return %pet && $pet{hungry} ? 1 : 0; }

sub counts {
	my %n = map { $_ => 0 } (@{$watch{items}}, $INCUBATOR, $PET_FOOD);
	return \%n unless $char && $char->inventory;
	for my $item (@{$char->inventory}) {
		$n{$item->{nameID}} += $item->{amount} if exists $n{$item->{nameID}} && !$item->{equipped};
	}
	return \%n;
}

sub near {
	my %want = map { $_ => 1 } @{$watch{mobs}};
	my %near;
	return \%near unless %want && $monstersList && $char && $char->{pos_to};
	for my $m (@{$monstersList->getItems}) {
		next unless $m && $m->{pos_to} && !$m->{dead} && $want{$m->{nameID} // -1};
		my $d = dist($char->{pos_to}, $m->{pos_to});
		$near{$m->{nameID}} = $d if $d <= $NEAR && (!defined $near{$m->{nameID}} || $d < $near{$m->{nameID}});
	}
	return \%near;
}

sub status {
	return {has => bool(hasPet()), type => ($pet{type} // undef), name => ($pet{name} // undef),
	        hungry => (defined $pet{hungry} ? $pet{hungry} + 0 : undef),
	        friendly => (defined $pet{friendly} ? $pet{friendly} + 0 : undef),
	        running => ($run{what} // undef), eggs => eggs(), items => counts(), near => near()};
}

# ---------- запуск ----------

sub startTame {
	my ($a) = @_;
	return (0, 'уже идёт ' . $run{what}) if %run;
	return (0, 'нет персонажа') unless $char && !$char->{dead};
	return (0, 'питомец уже есть') if hasPet();
	my ($item, $mob) = ($a->{item} // '', $a->{mob} // '');
	return (0, 'неверный предмет или монстр') unless $item =~ /^\d{3,5}$/ && $mob =~ /^\d{4,5}$/;
	my $it = invItem($item) or return (0, "нет предмета $item");
	my $m = nearestMob($mob) or return (0, "монстр $mob не виден в $TAME_DIST клетках");
	%run = (what => 'tame', phase => 'use', since => time, item => $item, mob => $mob, target => $m->{ID},
	        saved => $config{attackAuto});
	Commands::run('conf attackAuto 1');
	Commands::run("is $it->{binID}");
	message "[pets] приручаю $mob предметом $item\n", 'system';
	return (1, "приручение $mob");
}

sub startHatch {
	my ($a) = @_;
	return (0, 'уже идёт ' . $run{what}) if %run;
	return (0, 'нет персонажа') unless $char && !$char->{dead};
	return (0, 'питомец уже есть') if hasPet();
	my $egg = $a->{egg} // '';
	return (0, 'неверное яйцо') unless $egg =~ /^\d{4}$/;
	invItem($egg) or return (0, "нет яйца $egg");
	my $inc = invItem($INCUBATOR) or return (0, 'нет Pet Incubator');
	%run = (what => 'hatch', phase => 'egg', since => time, egg => $egg);
	Commands::run("is $inc->{binID}");
	message "[pets] вылупляю яйцо $egg\n", 'system';
	return (1, "вылупление $egg");
}

sub finish {
	my ($ok, $reason, %data) = @_;
	my %r = %run;
	%run = ();
	Commands::run("conf attackAuto $r{saved}") if $r{what} eq 'tame' && defined $r{saved};
	if ($r{what} eq 'tame') {
		ev('pet_tame_result', ok => bool($ok), mob => $r{mob} + 0, item => $r{item} + 0, reason => $reason);
	} else {
		ev('pet_hatched', ok => bool($ok), egg => $r{egg} + 0, reason => $reason, %data);
	}
	$ok ? message("[pets] $r{what}: $reason\n", 'system') : warning("[pets] $r{what} не удалось: $reason\n");
}

# ---------- пакеты ----------

sub onCaptureProcess {
	return unless ($run{what} // '') eq 'tame' && $run{phase} eq 'use';
	my $m = $monstersList ? $monstersList->getByID($run{target}) : undef;
	$m = nearestMob($run{mob}) unless $m && !$m->{dead};
	return finish(0, 'цель исчезла') unless $m;
	Commands::run("pet c $m->{binID}");
	@run{qw(phase since)} = ('capture', time);
}

sub onCaptureResult {
	my (undef, $args) = @_;
	return unless ($run{what} // '') eq 'tame';
	finish($args->{success} ? 1 : 0, $args->{success} ? 'поймал' : 'сервер: не поймал');
}

sub onEggList {
	return unless ($run{what} // '') eq 'hatch' && $run{phase} eq 'egg';
	my $egg = invItem($run{egg}) or return finish(0, 'яйцо пропало');
	Commands::run("pet h $egg->{binID}");
	@run{qw(phase since)} = ('hatch', time);
}

sub onPetInfo {
	return unless ($run{what} // '') eq 'hatch' && $run{phase} eq 'hatch';
	finish(1, 'вылупился', mob => (defined $pet{type} ? $pet{type} + 0 : undef), name => $pet{name});   # не type: ключ сообщения моста
}

sub onPetFood {
	my (undef, $args) = @_;
	ev('pet_fed', ok => bool($args->{success}), food => ($args->{foodID} // 0) + 0);
}

# ---------- такт: таймауты и корм ----------

sub foodBlock {
	for (my $i = 0; exists $config{"buyAuto_$i"}; $i++) {
		return $i if ($config{"buyAuto_$i"} // '') =~ /^(Pet Food|537)$/i;
	}
	return;
}

sub syncFood {
	my $want = hasPet() && $wantFood ? 1 : 0;
	return if defined $foodOn && $foodOn == $want;
	my $i = foodBlock();
	return unless defined $i;
	Commands::run("conf buyAuto_${i}_disabled " . ($want ? 0 : 1));
	$foodOn = $want;
}

sub onTick {
	my $now = time;
	return if $now - $lastTick < 0.5;
	$lastTick = $now;
	syncFood();
	return unless %run;
	return finish(0, 'персонаж мёртв') if !$char || $char->{dead};
	finish(0, "нет ответа сервера на шаге $run{phase}") if $now - $run{since} > $TIMEOUT{$run{phase}};
}

# Мозг сообщает, что отслеживать и ест ли питомец Pet Food — тогда докупать его у Pet Groomer.
sub setup {
	my ($a) = @_;
	my ($ri, $rm) = map { ref $_ eq 'ARRAY' ? $_ : [] } ($a->{items}, $a->{mobs});
	return (0, 'слишком длинные списки') if @$ri > 20 || @$rm > 20;
	my @items = grep { defined && /^\d{3,5}$/ } @$ri;
	my @mobs = grep { defined && /^\d{4,5}$/ } @$rm;
	%watch = (items => \@items, mobs => \@mobs);
	$wantFood = $a->{food_on} ? 1 : 0;
	undef $foodOn;
	syncFood();
	return (1, 'питомцы: предметов ' . scalar(@items) . ', монстров ' . scalar(@mobs)
	           . ($wantFood ? ', докупать Pet Food' : ''));
}

1;
