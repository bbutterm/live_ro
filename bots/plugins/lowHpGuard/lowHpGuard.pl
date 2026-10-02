# lowHpGuard — безопасный fallback выживания без телепорта (live_ro).
#
# Проблема: при низком HP бот продолжал выбирать новые цели, а teleportAuto_*
# без навыка/крыльев только очищал очередь AI и бросал текущий бой.
# Кроме того, sitAuto в OpenKore работает лишь при Basic Skill >= 3.
#
# Поведение:
#  - HP < lowHpGuard_lower %: новые цели не выбираются (хук checkMonsterAutoAttack).
#    Монстров, которые уже атакуют бота, OpenKore по-прежнему отбивает.
#  - Если сесть нельзя (Basic Skill < 3) и агрессивных нет: бот стоит на месте
#    (действие AI "lowHpGuard"), не уходит в randomWalk и восстанавливается.
#  - Если сесть можно, отдых делает штатный sitAuto.
#  - Защита снимается при HP >= lowHpGuard_upper %.
#
# config.txt: lowHpGuard_lower (по умолч. 40, 0 = выкл.), lowHpGuard_upper (90).
package lowHpGuard;

use strict;
use Plugins;
use Globals qw($char %config);
use Log qw(message);
use AI;

my $active = 0;

Plugins::register('lowHpGuard', 'не начинать бой при низком HP, восстанавливаться без телепорта', \&onUnload);
my $hooks = Plugins::addHooks(
	['checkMonsterAutoAttack', \&onCheckMonster],
	['AI_pre',                 \&onAI],
);

sub onUnload {
	Plugins::delHooks($hooks);
}

sub hpPercent {
	return undef unless $char && $char->{hp_max};
	return 100 * $char->{hp} / $char->{hp_max};
}

sub canSit {
	return ($char->{skills}{NV_BASIC}{lv} || 0) >= 3
		|| ($char->{skills}{SU_BASIC_SKILL}{lv} || 0) == 1;
}

# Обновляет состояние защиты с гистерезисом, возвращает 1, если защита активна.
sub update {
	my $hp = hpPercent();
	return 0 if !defined $hp || $char->{dead};
	my $lower = defined $config{lowHpGuard_lower} ? $config{lowHpGuard_lower} : 40;
	my $upper = defined $config{lowHpGuard_upper} ? $config{lowHpGuard_upper} : 90;
	if (!$active && $lower > 0 && $hp < $lower) {
		$active = 1;
		message sprintf("[lowHpGuard] HP %d%% < %d%%: новые цели не выбираю, восстанавливаюсь\n", $hp, $lower), 'lowHpGuard';
	} elsif ($active && ($hp >= $upper || $lower <= 0)) {
		$active = 0;
		message sprintf("[lowHpGuard] HP %d%% >= %d%%: защита снята\n", $hp, $upper), 'lowHpGuard';
	}
	return $active;
}

sub onCheckMonster {
	my (undef, $args) = @_;
	$args->{return} = 0 if update();
}

sub onAI {
	return unless $char;
	my $guard = update();
	my $waiting = (AI::action() eq 'lowHpGuard');
	my $aggressive = scalar(AI::ai_getAggressives());

	if ($waiting) {
		# Отдать управление штатной логике: бой с агрессивными, смерть, восстановление.
		AI::dequeue() if !$guard || $aggressive || $char->{dead};
		return;
	}
	return unless $guard && !$aggressive && !$char->{dead} && AI::isIdle();
	return if canSit() && ($config{sitAuto_hp_lower} || 0) > 0;
	AI::queue('lowHpGuard');
}

1;
