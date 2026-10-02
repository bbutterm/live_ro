# spar — исполнитель дружеского спарринга двух жителей на арене PvP Yoyo (live_ro, ORG-061, ТЗ Т-30). Без LLM.
#
# Решение «драться ли» принимает мозг (brain/live_brain/spar.py): только житель против жителя, только по согласию
# обоих. Плагин исполняет поход и бой и следит за безопасностью сам — каждый такт, не дожидаясь мозга.
# Действие от мозга (brainBridge: spar {to, role, room}; spar_stop {why}):
#   role first  — вызвавший: берёт комнату, только если в ней 0 игроков;
#   role second — партнёр: берёт ту же комнату, только если в ней ровно 1 игрок (вызвавший).
# Фазы:
#   gate  — к Gate Keeper prt_in,52,140 (npc/other/pvp.txt:278, скрипт gkut :178): «talknpc», меню по ТЕКСТУ:
#           ' PvP Yoyo Mode' (:187, цветовые коды OpenKore снимает — Receive.pm:7795), затем 'Move' (:200);
#           условие Zeny > 499 и BaseLevel > 30, плата 500 (:202-203) -> pvp_y_room (L_Warp :257);
#   room  — к приёмной «Free for all» pvp_y_room,54,85 (npc/re/other/pvp.txt:45) -> F_PVP_FSRS (pvp.txt:287):
#           меню «Prontera [n / 128]:...:Cancel.», n — getmapusers; комната только с ожидаемым n, иначе «talk no»
#           и отмена; выбор -> warp pvp_y_8-<i>,0,0 (:316) — случайная клетка;
#   meet  — к месту встречи (prontera 156,185 — pvp_y_8-1 это копия prontera, tables/kRO/resnametable.txt:518);
#   fight — «kill <номер игрока>» (Commands.pm:401, cmdKill :3981) ТОЛЬКО по сопернику. Автоатака OpenKore людей
#           не выбирает (только монстров); на время боя attackAuto 0, route_randomWalk 0, survival 0 и
#           useSelf_item_*_disabled 1 — без зелий, сдаваться раньше, чем пить.
# Стоп-условия каждый такт: посторонний игрок виден (в комнате или на арене) — aborted stranger; HP < spar_yieldHp
# (30) — yield; я упал — down; соперник упал — won; соперник пропал — gone; лимит боя — draw; spar_stop — stopped.
# Выход: Butterfly Wing (602) — на pvp_y_* noreturn закомментирован (npc/mapflag/noreturn.txt:165-201), крыло
# работает (src/map/pc.cpp:6329), Fly Wing — нет (noteleport). Выхода-NPC из pvp_y_room и pvp_y_* нет. Упал —
# OpenKore сам возрождается на точке сохранения; nopenalty (npc/mapflag/nopenalty.txt:243) — опыт не теряется.
# Мозгу: события spar_step {phase arena|fight, room} и spar_result {outcome, reason, room, hp_pct, wing};
# spar::status() -> {running, phase, to, room}. Настройки на время спарринга сохраняются и возвращаются.
# НЕ ПРОВЕРЕНО В ИГРЕ: только тест на заглушках (bots/tests/spar.t).
package spar;

use strict;
use utf8;
use Time::HiRes qw(time);
use JSON::PP;
use Plugins;
use Globals qw($char $field %config %talk $playersList);
use Log qw(message warning);
use Commands;
use AI;

Plugins::register('spar', 'дружеский спарринг жителей на арене PvP Yoyo (шаги от мозга)', \&onUnload);

my $hooks = Plugins::addHooks(
	['mainLoop_post',      \&onTick],
	['npc_talk_responses', \&onResponses],
	['npc_talk_done',      \&onTalkDone],
);

our %GATE      = (map => 'prt_in', x => 52, y => 140, stand => [52, 138]);       # npc/other/pvp.txt:278
our %RECEPTION = (map => 'pvp_y_room', x => 54, y => 85, stand => [54, 83]);     # npc/re/other/pvp.txt:45
# Комнаты приёмной #8 (pvp.txt:295-298: .@Maps$ pvp_y_8-1..5, .@Name$) — разрешена только проверенная геометрия.
our %ROOMS     = (Prontera => {map => 'pvp_y_8-1', meet => [156, 185]});
our ($FEE, $MIN_LV, $WING) = (500, 31, 602);
our %TIMEOUT = (gate => 900, room => 180, meet => 300, fight => 180, gone => 10);
my @SAVE = qw(lockMap lockMap_x lockMap_y route_randomWalk attackAuto autoTalkCont survival);
our %run;              # to, role, room, phase, since, phase_since, issued, talking, talked, saved, seen, quiet
our $lastTick = 0;     # троттлинг 0.3 с (our — тест сбрасывает)

sub onUnload { Plugins::delHooks($hooks); }

sub mapName { return $field ? $field->baseName : ''; }

sub running { return %run ? 1 : 0; }

sub status {
	return {running => (%run ? JSON::PP::true() : JSON::PP::false()), phase => $run{phase}, to => $run{to},
	        room => $run{room}};
}

sub event {
	my $ev = brainBridge->can('event');
	$ev->(@_) if $ev;
}

sub conf {
	my (%kv) = @_;
	for my $k (sort keys %kv) {
		my $v = $kv{$k};
		$v = 'none' if !defined $v || $v eq '';
		Commands::run("conf -f $k $v");
	}
}

sub findItem {
	my ($id) = @_;
	return undef unless $char && $char->can('inventory') && $char->inventory;
	my ($item) = grep { !$_->{equipped} && $_->{nameID} == $id && ($_->{amount} // 1) > 0 } @{$char->inventory};
	return $item;
}

sub hpPct { return ($char && $char->{hp_max}) ? 100 * $char->{hp} / $char->{hp_max} : 100; }

sub near {
	my ($x, $y, $d) = @_;
	my $p = ($char && $char->{pos_to}) || {};
	return 0 unless defined $p->{x};
	my ($dx, $dy) = (abs($p->{x} - $x), abs($p->{y} - $y));
	return ($dx > $dy ? $dx : $dy) <= $d;
}

sub players { return $playersList ? grep { defined $_->{name} && length $_->{name} } @{$playersList->getItems() || []} : (); }

sub inArenaMap { return mapName() =~ /^pvp_y_\d-\d$/; }

sub inPvpArea { my $m = mapName(); return $m eq $RECEPTION{map} || $m =~ /^pvp_y_\d-\d$/; }

# ---------- запуск и итог ----------

# (1, описание) — спарринг начат, или (0, причина).
sub start {
	my ($a) = @_;
	return (0, 'спарринг уже идёт') if %run;
	return (0, 'нет персонажа') unless $char;
	return (0, 'персонаж мёртв') if $char->{dead};
	my $to = $a->{to} // '';
	return (0, 'неверный соперник') unless $to =~ /^[^"\s]{1,23}$/ && $to ne ($char->{name} // '');
	my $role = $a->{role} // '';
	return (0, 'роль first или second') unless $role eq 'first' || $role eq 'second';
	my $room = $a->{room} // '';
	return (0, "комната $room не из списка") unless $ROOMS{$room};
	return (0, "уровень ниже $MIN_LV") unless ($char->{lv} // 0) >= $MIN_LV;
	return (0, "меньше $FEE зени") unless ($char->{zeny} // 0) >= $FEE;
	return (0, 'нет крыла бабочки — с арены не выйти') unless findItem($WING);
	%run = (to => $to, role => $role, room => $room, expect => ($role eq 'first' ? 0 : 1), phase => 'gate',
	        since => time, phase_since => time, saved => {map { $_ => $config{$_} } @SAVE});
	conf(lockMap => 'none', lockMap_x => 'none', lockMap_y => 'none', route_randomWalk => 0, autoTalkCont => 1,
	     attackAuto => 1);
	message "[spar] спарринг с $to ($role), комната $room\n", 'system';
	return (1, "спарринг с $to ($role)");
}

sub stop { finish('stopped', $_[0] // 'остановлен мозгом'); }

sub phase {
	my ($p) = @_;
	$run{phase} = $p;
	$run{phase_since} = time;
	delete @run{qw(issued talking talked)};
}

# Бой: без зелий и автоатаки (сдаться раньше, чем пить); прежние значения — в saved.
sub fightMode {
	my %kv = (attackAuto => 0, route_randomWalk => 0, survival => 0);
	for (my $i = 0; exists $config{"useSelf_item_$i"}; $i++) {
		$run{saved}{"useSelf_item_${i}_disabled"} = $config{"useSelf_item_${i}_disabled"};
		$kv{"useSelf_item_${i}_disabled"} = 1;
	}
	conf(%kv);
}

sub finish {
	my ($outcome, $reason) = @_;
	return unless %run;
	my %r = %run;
	%run = ();
	Commands::run('talk no') if %talk;
	Commands::run('ai clear');
	my %restore = %{$r{saved}};
	$restore{$_} //= 0 for grep { /_disabled$/ } keys %restore;
	conf(%restore);
	my $wing = 'none';
	if ($char && !$char->{dead} && inPvpArea()) {
		if (my $item = findItem($WING)) {
			Commands::run("is $item->{binID}");
			$wing = 'used';
		} else {
			$wing = 'missing';
			warning "[spar] нет крыла бабочки — не выйти с арены\n";
		}
	}
	my $hp = int(hpPct());
	message "[spar] итог с $r{to}: $outcome ($reason), HP $hp%\n", 'system';
	event('spar_result', to => $r{to}, role => $r{role}, room => $r{room}, outcome => $outcome, reason => $reason,
	      phase => $r{phase}, hp_pct => $hp, wing => $wing);
}

# ---------- такт ----------

sub onTick {
	return unless %run;
	my $now = time;
	return if $now - $lastTick < 0.3;
	$lastTick = $now;
	return finish('down', 'упал(а) на арене') if $char && $char->{dead} && inArenaMap();
	return finish('aborted', 'персонаж мёртв') if !$char || $char->{dead};
	if (inPvpArea()) {
		my @others = grep { $_->{name} ne $run{to} } players();
		return finish('aborted', 'stranger: ' . $others[0]{name}) if @others;
	}
	my $limit = $TIMEOUT{$run{phase}} // 60;
	if ($now - $run{phase_since} > $limit) {
		return finish('draw', 'лимит боя') if $run{phase} eq 'fight';
		return finish('aborted', "timeout: $run{phase}");
	}
	my $sub = __PACKAGE__->can("do_$run{phase}");
	$sub->($now);
}

sub moveTo {
	my ($map, $x, $y, $now) = @_;
	return if $run{issued} && $now - $run{issued} < 15;
	return if $run{issued} && (AI::action() // '') =~ /^(route|move|NPC)$/;
	Commands::run("move $x $y $map");
	$run{issued} = $now;
}

sub talkAt {
	my ($npc, $now) = @_;
	return if $run{talking};
	$run{talking} = 1;
	Commands::run("talknpc $npc->{x} $npc->{y}");
	$run{issued} = $now;
}

sub do_gate {
	my ($now) = @_;
	return phase('room') if mapName() eq $RECEPTION{map};
	if ($run{talked}) {                                    # диалог закрыт, а переноса нет — отказ NPC (зени/уровень)
		return finish('aborted', 'gate: Gate Keeper не пустил') if $now - $run{talked} > 20;
		return;
	}
	if (mapName() eq $GATE{map} && near(@{$GATE{stand}}, 3)) {
		talkAt(\%GATE, $now);
		return;
	}
	moveTo($GATE{map}, @{$GATE{stand}}, $now);
}

sub do_room {
	my ($now) = @_;
	my $room = $ROOMS{$run{room}};
	if (mapName() eq $room->{map}) {
		fightMode();
		phase('meet');
		event('spar_step', phase => 'arena', room => $run{room}, to => $run{to});
		return;
	}
	if ($run{talked}) {
		return finish('aborted', 'room: приёмная не перенесла') if $now - $run{talked} > 20;
		return;
	}
	if (mapName() eq $RECEPTION{map} && near(@{$RECEPTION{stand}}, 3)) {
		talkAt(\%RECEPTION, $now);
		return;
	}
	moveTo($RECEPTION{map}, @{$RECEPTION{stand}}, $now) if mapName() eq $RECEPTION{map};
}

sub opponent {
	my ($p) = grep { $_->{name} eq $run{to} } players();
	return $p;
}

sub do_meet {
	my ($now) = @_;
	return finish('aborted', 'вне арены') unless inArenaMap();
	my $opp = opponent();
	if ($opp && !$opp->{dead}) {
		phase('fight');
		$run{seen} = $now;
		event('spar_step', phase => 'fight', room => $run{room}, to => $run{to});
		return;
	}
	my $room = $ROOMS{$run{room}};
	return if near(@{$room->{meet}}, 2);
	moveTo($room->{map}, @{$room->{meet}}, $now);
}

sub do_fight {
	my ($now) = @_;
	return finish('down', 'вне арены — упал(а) и возродился(лась)?') unless inArenaMap();
	return finish('yield', sprintf('HP %d%% < %d%%', hpPct(), yieldHp())) if hpPct() < yieldHp();
	my $opp = opponent();
	if (!$opp) {
		return finish('gone', 'соперник пропал с арены') if $now - ($run{seen} // $now) > $TIMEOUT{gone};
		return;
	}
	$run{seen} = $now;
	return finish('won', 'соперник упал') if $opp->{dead};
	return if (AI::action() // '') eq 'attack' && $run{issued} && $now - $run{issued} < 5;
	return if $run{issued} && $now - $run{issued} < 2;
	my $idx = $playersList->can('find') ? $playersList->find($opp) : $opp->{binID};
	return unless defined $idx && $idx >= 0;
	Commands::run("kill $idx");
	$run{issued} = $now;
}

sub yieldHp {
	my $v = $config{spar_yieldHp};
	return (defined $v && $v =~ /^\d+$/ && $v >= 10 && $v <= 90) ? $v : 30;
}

# ---------- диалог ----------

sub onResponses {
	my (undef, $args) = @_;
	return unless %run && $run{talking};
	my @opts = map { my $o = $_ // ''; $o =~ s/^\s+|\s+$//g; $o } @{$args->{responses} || []};
	pop @opts if @opts && $opts[-1] =~ /^Cancel Chat$/i;           # пункт OpenKore, не из скрипта
	my $pick;
	if ($run{phase} eq 'gate') {
		($pick) = grep { $opts[$_] =~ /^'?\s*PvP Yoyo Mode\s*'?$/ } 0 .. $#opts;
		($pick) = grep { $opts[$_] eq 'Move' } 0 .. $#opts unless defined $pick;
	} elsif ($run{phase} eq 'room') {
		for my $i (0 .. $#opts) {
			next unless $opts[$i] =~ /^(\w+) \[(\d+) \/ (\d+)\]$/ && $1 eq $run{room};
			if ($2 != $run{expect}) {
				Commands::run('talk no');
				return finish('aborted', "busy: в комнате $1 игроков $2, ждал(а) $run{expect}");
			}
			$pick = $i;
		}
	}
	return finish('aborted', "menu: не из сценария: " . join(' | ', @opts)) unless defined $pick;
	Commands::run("talk resp $pick");
}

sub onTalkDone {
	return unless %run && $run{talking};
	$run{talked} = time;
}

1;
