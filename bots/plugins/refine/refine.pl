# refine — заточка своего оружия у кузнеца только до безопасного уровня (live_ro, ORG-072, ТЗ Т-28). Без LLM.
#
# Мозг (brain/live_brain/refine.py) присылает действие refine {id, item, inv, target, ore, buy, shop, smith}.
# Плагин исполняет по шагам и проверяет каждый шаг по данным клиента, итог — событие refine_result.
#   buy    — buy > 0: дойти до продавца руды (Vurewell prt_in,56,68, npc/merchants/refine.txt:970, phramain) и
#            «talknpc X Y c r~/^<руда>/ c d<buy> n» (меню «Phracon - 200 Zeny», ввод количества); готово — руды
#            в рюкзаке стало на buy больше;
#   smith  — дойти до Hollgrehenn (prt_in,63,60, refine.txt:526) и «talknpc 63 60»: при feature.refineui (rAthena
#            по умолчанию, PACKETVER 20180620) он после mes/close2 открывает Refine UI — пакет 0AA0, OpenKore
#            ставит $refineUI->{open} (Network/Receive.pm refineui_opened);
#   uneq   — OpenKore не выбирает надетый предмет в Refine UI («Cannot select equipped», Commands.pm cmdRefineUI):
#            оружие снимается «uneq <inv>» и в конце надевается обратно «eq <inv>»;
#   select — «refineui select <inv>»; сервер присылает список руды 0AA2 {nameid, chance, zeny}
#            (clif_refineui_info: chance = Rate/100), OpenKore кладёт его в $refineUI->{materials};
#   refine — ТОЛЬКО если у руды chance == 100 (Rate 10000: успех всегда, поломки нет) — «refineui refine <inv>
#            <ore> 0» (без Blacksmith Blessing); готово — upgrade предмета вырос (пакет 0188 item_upgrade, OpenKore
#            сам меняет $item->{upgrade}); повтор до target. Шанс < 100 — стоп «дальше риск» (без попытки).
# Итог: refineui cancel, eq, событие refine_result {id, ok, item, from, to, done, reason}. ok — хотя бы один шаг
# заточки подтверждён клиентом. Тайм-аут шага, смерть, пропажа предмета или снижение upgrade — провал.
# Для мозга: refine::status() -> {running, phase, weapon {id, inv, name, upgrade, equipped}, ores {1010, 1011}}.
# НЕ ПРОВЕРЕНО В ИГРЕ: только тест на заглушках (bots/tests/refine.t).
package refine;

use strict;
use utf8;
use Time::HiRes qw(time);
use JSON::PP;
use Plugins;
use Globals qw($char $field %config $refineUI);
use Log qw(message warning);
use Commands;

Plugins::register('refine', 'заточка своего оружия до безопасного уровня (шаги от мозга)', \&onUnload);

my $hooks = Plugins::addHooks(['mainLoop_post', \&onTick]);

our @ORES = (1010, 1011);                    # Phracon, Emveretarcon — продаёт Vurewell (phramain)
my @SAVE = qw(lockMap lockMap_x lockMap_y route_randomWalk autoTalkCont attackAuto sitAuto_idle);
our %TIMEOUT = (buy_move => 600, buy_talk => 60, smith_move => 600, smith_talk => 45, uneq => 15, select => 15,
                refine => 20, total => 1500);
our %run;                                    # id, item, inv, target, ore, buy, shop, smith, phase, since, ...
our $lastTick = 0;                           # троттлинг 0.5 с (our — тест сбрасывает)

sub onUnload { Plugins::delHooks($hooks); }

# ---------- данные клиента ----------

sub inv { return $char && ref $char ne 'HASH' && $char->can('inventory') ? $char->inventory : undef; }

sub itemAt {
	my ($i) = @_;
	my $inv = inv() or return undef;
	return $inv->can('get') ? $inv->get($i) : undef;
}

sub count {
	my ($nameID) = @_;
	my $inv = inv() or return 0;
	my $n = 0;
	$n += $_->{amount} for grep { ($_->{nameID} // 0) == $nameID && !$_->{equipped} } @$inv;
	return $n;
}

sub weapon {
	my $w = $char && $char->{equipment} ? $char->{equipment}{rightHand} : undef;
	$w = itemAt($run{inv}) if !$w && %run;               # на время заточки оружие снято — то же, что точим
	return undef unless $w;
	return {id => ($w->{nameID} // 0) + 0, inv => ($w->{binID} // $w->{invIndex} // 0) + 0, name => "$w->{name}",
	        upgrade => ($w->{upgrade} // 0) + 0, equipped => ($w->{equipped} ? JSON::PP::true() : JSON::PP::false())};
}

sub status {
	return {running => (%run ? JSON::PP::true() : JSON::PP::false()), phase => $run{phase}, weapon => weapon(),
	        ores => {map { $_ => count($_) } @ORES}};
}

sub mapName { return $field ? $field->baseName : ''; }

sub near {
	my ($x, $y, $d) = @_;
	my $p = ($char && $char->{pos_to}) || {};
	return 0 unless defined $p->{x};
	my ($dx, $dy) = (abs($p->{x} - $x), abs($p->{y} - $y));
	return ($dx > $dy ? $dx : $dy) <= $d;
}

# ---------- запуск и итог ----------

sub okPoint {
	my ($p) = @_;
	return ref $p eq 'HASH' && ($p->{map} // '') =~ /^[a-z0-9_]{3,16}$/ && ($p->{x} // '') =~ /^\d{1,3}$/
		&& ($p->{y} // '') =~ /^\d{1,3}$/ && ref $p->{stand} eq 'HASH' && ($p->{stand}{x} // '') =~ /^\d{1,3}$/
		&& ($p->{stand}{y} // '') =~ /^\d{1,3}$/;
}

# Возвращает (1, описание) или (0, причина).
sub start {
	my ($a) = @_;
	return (0, 'уже точу') if %run;
	return (0, 'нет персонажа') unless $char;
	return (0, 'персонаж мёртв') if $char->{dead};
	for my $k (qw(item inv target buy)) {
		return (0, "неверное поле $k") unless defined $a->{$k} && $a->{$k} =~ /^\d{1,6}$/;
	}
	return (0, 'руда не из списка') unless grep { $_ == ($a->{ore} // 0) } @ORES;
	return (0, 'цель заточки 1..10') unless $a->{target} >= 1 && $a->{target} <= 10;
	return (0, 'купить руды 0..20') unless $a->{buy} <= 20;
	return (0, 'неверный кузнец') unless okPoint($a->{smith});
	return (0, 'неверный продавец руды') if $a->{buy} && !(okPoint($a->{shop}) && ($a->{shop}{menu} // '') =~ /^[A-Za-z]{3,20}$/);
	my $item = itemAt($a->{inv});
	return (0, 'предмета нет в рюкзаке') unless $item && ($item->{nameID} // 0) == $a->{item};
	return (0, "уже +$item->{upgrade}") if ($item->{upgrade} // 0) >= $a->{target};
	%run = (id => $a->{id}, item => $a->{item} + 0, inv => $a->{inv} + 0, target => $a->{target} + 0,
	        ore => $a->{ore} + 0, buy => $a->{buy} + 0, shop => $a->{shop}, smith => $a->{smith},
	        from => ($item->{upgrade} // 0) + 0, was_equipped => ($item->{equipped} ? 1 : 0), done => 0,
	        phase => ($a->{buy} ? 'buy_move' : 'smith_move'), since => time, phase_since => time,
	        saved => {map { $_ => $config{$_} } @SAVE});
	conf(lockMap => 'none', lockMap_x => 'none', lockMap_y => 'none', route_randomWalk => 0, autoTalkCont => 1,
	     attackAuto => 1, sitAuto_idle => 0);
	Commands::run('stand') if $char->{sitting};
	message "[refine] $item->{name}: до +$run{target}, руды докупить $run{buy}\n", 'system';
	return (1, "заточка $item->{name} до +$run{target}");
}

sub conf {
	my (%kv) = @_;
	for my $k (sort keys %kv) {
		my $v = $kv{$k};
		$v = 'none' if !defined $v || $v eq '';
		Commands::run("conf $k $v");
	}
}

sub finish {
	my ($ok, $reason) = @_;
	return unless %run;
	my %r = %run;
	%run = ();
	Commands::run('refineui cancel') if $refineUI;
	my $item = itemAt($r{inv});
	Commands::run("eq $r{inv}") if $r{was_equipped} && $item && !$item->{equipped};
	conf(%{$r{saved}});
	my $to = $item ? ($item->{upgrade} // 0) + 0 : undef;
	if ($ok) { message "[refine] готово: +$r{from} -> +" . ($to // '?') . " ($reason)\n", 'system'; }
	else     { warning "[refine] не вышло: $reason\n"; }
	my $ev = brainBridge->can('event');
	$ev->('refine_result', id => $r{id}, item => $r{item}, inv => $r{inv}, from => $r{from}, to => $to,
	      done => $r{done}, ok => ($ok ? JSON::PP::true() : JSON::PP::false()), reason => $reason) if $ev;
}

sub phase {
	my ($p) = @_;
	$run{phase} = $p;
	$run{phase_since} = time;
	delete $run{issued};
}

# ---------- шаги ----------

sub onTick {
	return unless %run;
	my $now = time;
	return if $now - $lastTick < 0.5;
	$lastTick = $now;
	return finish(0, 'персонаж мёртв') if !$char || $char->{dead};
	my $item = itemAt($run{inv});
	return finish(0, 'предмет пропал из рюкзака') unless $item && ($item->{nameID} // 0) == $run{item};
	my $up = ($item->{upgrade} // 0) + 0;
	return finish(0, "заточка снизилась до +$up") if $up < $run{from} + $run{done};
	return finish($run{done} > 0, 'общий тайм-аут') if $now - $run{since} > $TIMEOUT{total};
	my $limit = $TIMEOUT{$run{phase}} // 60;
	return finish($run{done} > 0, "шаг $run{phase}: тайм-аут ${limit} с") if $now - $run{phase_since} > $limit;
	my $sub = __PACKAGE__->can("do_$run{phase}");
	$sub->($item, $now);
}

sub moveTo {
	my ($p, $now) = @_;
	return 1 if mapName() eq $p->{map} && near($p->{stand}{x}, $p->{stand}{y}, 2);
	if (!$run{issued} || ($now - $run{issued} > 15 && (AI::action() // '') !~ /^(route|move)$/)) {
		Commands::run("move $p->{stand}{x} $p->{stand}{y} $p->{map}");
		$run{issued} = $now;
	}
	return 0;
}

sub do_buy_move {
	my (undef, $now) = @_;
	phase('buy_talk') if moveTo($run{shop}, $now);
}

sub do_buy_talk {
	my (undef, $now) = @_;
	if (!$run{issued}) {
		$run{ore_base} = count($run{ore});
		my $s = $run{shop};
		Commands::run("talknpc $s->{x} $s->{y} c r~/^$s->{menu}/ c d$run{buy} n");
		$run{issued} = $now;
		return;
	}
	phase('smith_move') if count($run{ore}) >= $run{ore_base} + $run{buy};
}

sub do_smith_move {
	my (undef, $now) = @_;
	phase('smith_talk') if moveTo($run{smith}, $now);
}

sub do_smith_talk {
	my (undef, $now) = @_;
	if ($refineUI && $refineUI->{open}) {
		phase('uneq');
		return;
	}
	return if $run{issued};
	Commands::run("talknpc $run{smith}{x} $run{smith}{y}");
	$run{issued} = $now;
}

sub do_uneq {
	my ($item, $now) = @_;
	if (!$item->{equipped}) {
		phase('select');
		return;
	}
	return if $run{issued};
	Commands::run("uneq $run{inv}");
	$run{issued} = $now;
}

sub do_select {
	my ($item, $now) = @_;
	if ($run{issued} && $refineUI && $refineUI->{materials}) {
		phase('refine');
		return;
	}
	return if $run{issued} && $now - $run{issued} < 5;
	return finish(0, 'Refine UI закрыт') unless $refineUI && $refineUI->{open};
	delete $refineUI->{materials};
	Commands::run("refineui select $run{inv}");
	$run{issued} = $now;
}

sub do_refine {
	my ($item, $now) = @_;
	my $up = ($item->{upgrade} // 0) + 0;
	if ($run{issued}) {
		return unless $up > $run{before};                     # ждём пакет 0188: upgrade вырос
		$run{done} = $up - $run{from};
		return finish(1, "+$up — цель") if $up >= $run{target};
		phase('select');                                      # список руды для следующего уровня
		return;
	}
	my ($mat) = grep { ($_->{nameid} // 0) == $run{ore} } @{$refineUI->{materials} || []};
	return finish($run{done} > 0, 'кузнец не берёт эту руду для предмета') unless $mat;
	return finish($run{done} > 0, "дальше риск: шанс $mat->{chance}%") unless ($mat->{chance} // 0) == 100;
	return finish($run{done} > 0, 'кончилась руда') unless count($run{ore}) >= 1;
	$run{before} = $up;
	Commands::run("refineui refine $run{inv} $run{ore} 0");
	$run{issued} = $now;
}

1;
