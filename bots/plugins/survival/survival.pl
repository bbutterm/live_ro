# survival — выживание в бою без LLM (live_ro, AUT-015, AUT-016, AUT-018).
#
# 1. Учёт входящего урона (хуки packet_attack, packet_skilluse): по каждому удару по нашему
#    персонажу запоминаются время, урон и источник; окно — последние 6 с.
# 2. Прогноз (AUT-016): dps = урон за окно / длительность окна (не меньше 2 с),
#    HP через survival_horizon секунд = hp - dps * horizon. Опасно, если за окно был урон и
#    прогноз <= 0 или HP% < survival_hp.
# 3. Экстренное действие при опасности (не чаще раза в 1 с, мёртвому — ничего):
#    а) лечебное зелье из survival_potions (по ID, сначала сильные) — команда «is <binID>»;
#    б) иначе при HP% < survival_escapeHp — Butterfly Wing (602, возврат на точку сохранения),
#       не чаще раза в 30 с, событие мозгу escape;
#    в) иначе — событие мозгу danger (не чаще раза в 10 с): hp_pct, dps, число нападающих.
#    Зелье и крыло дополнительно сообщаются событием survival {action, item, hp_pct, dps}.
#    Штатный useSelf_item (hp < 50%) продолжает работать; плагин — страховка на быстрый урон.
# 4. Безопасность отдыха (AUT-018): сидит, а за последние 2 с получен урон — встать
#    (не чаще раза в 3 с). Обычный удар сервер и так поднимает; это для ударов «без движения»
#    и навыков, после которых sitAuto снова садит бота под монстром.
#
# config.txt: survival (0 = выкл., по умолчанию вкл.), survival_hp (25), survival_escapeHp (15),
# survival_horizon (3), survival_potions (504,503,502,501,569).
# Для мозга: survival::status() -> {dps, attackers, last_action}.
package survival;

use strict;
use utf8;
use Time::HiRes qw(time);
use Plugins;
use Globals qw($char %config $accountID);
use Log qw(message warning);
use Commands;

Plugins::register('survival', 'выживание в бою: прогноз урона, зелья, крыло, подъём с отдыха', \&onUnload);

my $hooks = Plugins::addHooks(
	['packet_attack',   \&onAttack],
	['packet_skilluse', \&onSkillUse],
	['mainLoop_post',   \&onTick],
);

# Отметки времени — пакетные (our), чтобы тест мог «состарить» их.
our @hits;                  # [время, урон, sourceID] за последние $WINDOW с
our $lastAction = 0;        # последнее экстренное действие (троттлинг 1 с)
our $lastWing = 0;          # последнее крыло (раз в 30 с)
our $lastDanger = 0;        # последнее событие danger (раз в 10 с)
our $lastStand = 0;         # последний подъём с отдыха (раз в 3 с)
our $lastActionName = 'none';

my $WINDOW = 6;
my $MIN_SPAN = 2;
my $WING = 602;             # Butterfly Wing
my $DEFAULT_POTIONS = '504,503,502,501,569';   # White, Yellow, Orange, Red, Novice Potion

sub onUnload { Plugins::delHooks($hooks); }

sub enabled { return !defined $config{survival} || $config{survival} ne '0'; }

sub cfg {
	my ($key, $default) = @_;
	my $v = $config{$key};
	return (defined $v && $v =~ /^\d+(?:\.\d+)?$/) ? $v + 0 : $default;
}

# ---------- учёт урона ----------

sub prune {
	my $now = time;
	shift @hits while @hits && $now - $hits[0][0] > $WINDOW;
}

sub noteHit {
	my ($source, $target, $dmg) = @_;
	return unless enabled() && defined $accountID && defined $target && $target eq $accountID;
	return unless defined $dmg && $dmg > 0;
	push @hits, [time, $dmg, $source // ''];
	prune();
}

sub onAttack {
	my (undef, $a) = @_;
	noteHit($a->{sourceID}, $a->{targetID}, $a->{dmg});
}

sub onSkillUse {
	my (undef, $a) = @_;
	noteHit($a->{sourceID}, $a->{targetID}, $a->{damage});
}

# ---------- прогноз ----------

sub hpPercent {
	return undef unless $char && $char->{hp_max};
	return 100 * $char->{hp} / $char->{hp_max};
}

# {dps, attackers, hp_pct, predicted, danger}
sub forecast {
	prune();
	my %f = (dps => 0, attackers => 0, hp_pct => hpPercent(), predicted => undef, danger => 0);
	return \%f unless @hits;
	my $sum = 0;
	my %src;
	for my $h (@hits) { $sum += $h->[1]; $src{$h->[2]} = 1; }
	my $span = time - $hits[0][0];
	$span = $MIN_SPAN if $span < $MIN_SPAN;
	$f{dps} = $sum / $span;
	$f{attackers} = scalar keys %src;
	return \%f unless defined $f{hp_pct};
	$f{predicted} = $char->{hp} - $f{dps} * cfg('survival_horizon', 3);
	$f{danger} = ($f{predicted} <= 0 || $f{hp_pct} < cfg('survival_hp', 25)) ? 1 : 0;
	return \%f;
}

sub status {
	my $f = forecast();
	return {dps => sprintf('%.1f', $f->{dps}) + 0, attackers => $f->{attackers}, last_action => $lastActionName};
}

# ---------- действия ----------

sub findItem {
	my ($id) = @_;
	return undef unless $char && $char->inventory;
	my ($item) = grep { !$_->{equipped} && $_->{nameID} == $id && ($_->{amount} // 1) > 0 } @{$char->inventory};
	return $item;
}

sub potionIds {
	my $list = defined $config{survival_potions} && $config{survival_potions} ne '' ? $config{survival_potions} : $DEFAULT_POTIONS;
	return map { $_ + 0 } grep { /^\d+$/ } split /\s*,\s*/, $list;
}

sub event {
	my $ev = brainBridge->can('event');
	$ev->(@_) if $ev;
}

sub emergency {
	my ($f) = @_;
	my $hp = int($f->{hp_pct});
	my $dps = sprintf('%.1f', $f->{dps}) + 0;
	for my $id (potionIds()) {
		my $item = findItem($id) or next;
		message sprintf("[survival] HP %d%%, урон %.1f/с: пью %s (%d)\n", $hp, $dps, $item->{name} // 'зелье', $id), 'survival';
		Commands::run("is $item->{binID}");
		$lastActionName = 'potion';
		event('survival', action => 'potion', item => $id, hp_pct => $hp, dps => $dps);
		return;
	}
	if ($hp < cfg('survival_escapeHp', 15) && time - $lastWing >= 30) {
		if (my $wing = findItem($WING)) {
			$lastWing = time;
			warning sprintf("[survival] HP %d%%, зелий нет: крыло бабочки — на точку сохранения\n", $hp);
			Commands::run("is $wing->{binID}");
			$lastActionName = 'wing';
			event('survival', action => 'wing', item => $WING, hp_pct => $hp, dps => $dps);
			event('escape', item => $WING, hp_pct => $hp);
			@hits = ();                    # бой остался на прежней карте
			return;
		}
	}
	return if time - $lastDanger < 10;
	$lastDanger = time;
	$lastActionName = 'danger';
	warning sprintf("[survival] опасность: HP %d%%, урон %.1f/с, нападающих %d, лечиться нечем\n", $hp, $dps, $f->{attackers});
	event('danger', hp_pct => $hp, dps => $dps, attackers => $f->{attackers});
}

# ---------- тик ----------

sub onTick {
	return unless $char && enabled();
	if ($char->{dead}) { @hits = (); return; }
	prune();
	return unless @hits;
	my $now = time;
	if ($char->{sitting} && $now - $hits[-1][0] <= 2 && $now - $lastStand >= 3) {
		$lastStand = $now;
		message "[survival] бьют во время отдыха — встаю\n", 'survival';
		Commands::run('stand');
		$lastActionName = 'stand';
	}
	my $f = forecast();
	return unless $f->{danger};
	return if $now - $lastAction < 1;
	$lastAction = $now;
	emergency($f);
}

1;
