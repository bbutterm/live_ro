# combatProfile — боевой профиль по профессии для любого класса (live_ro).
#
# Профили: bots/combat/classes.json (путь: $ENV{LIVE_RO_COMBAT} или config combatProfile_file).
# Плагин сам определяет профессию в игре ($char->{jobID}), берёт профиль с наследованием
# (Knight <- Swordsman, Priest <- Acolyte, ...) и выставляет настройки OpenKore:
#   - архетип: дистанция атаки, отход от цели, атака оружием, порог отдыха по HP/SP;
#   - attack -> attackSkillSlot_N, self -> useSelf_skill_N, party -> partySkill_N;
#   - stats/skills -> statsAddAuto(_list)/skillsAddAuto(_list) (плагины raiseStat/raiseSkill).
# Включаются только ИЗУЧЕННЫЕ навыки; остальные слоты получают _disabled 1. В авто-прокачку
# навыков попадают только навыки из дерева текущей профессии.
# Профиль переприменяется при смене профессии, изучении навыка или изменении файла.
# Бой, лечение и баффы исполняет OpenKore по этим правилам — без LLM.
package combatProfile;

use strict;
use utf8;
use JSON::PP;
use Time::HiRes qw(time);
use Plugins;
use Globals qw($char %config %jobs_lut);
use Log qw(message warning);
use Misc;
use Skill;

Plugins::register('combatProfile', 'боевой профиль по профессии (bots/combat/classes.json)', \&onUnload);

my $hooks = Plugins::addHooks(
	['in_game',       sub { $combatProfile::force = 1 }],
	['mainLoop_post', \&onTick],
);

our %current;                 # для brainBridge: class, applied, skipped
our $force = 1;
my ($data, $dataMtime, $lastCheck, $signature) = (undef, 0, 0, '');
my @CONDITION_KEYS = qw(lvl dist maxDist maxUses sp hp timeout aggressives whenStatusInactive inLockOnly
                        target_hp target_whenStatusInactive);
my %BLOCK = (attack => 'attackSkillSlot', self => 'useSelf_skill', party => 'partySkill');

sub onUnload { Plugins::delHooks($hooks); }

sub path { return $ENV{LIVE_RO_COMBAT} || $config{combatProfile_file}; }

sub loadData {
	my $file = path();
	return undef unless $file && -f $file;
	my $mtime = (stat $file)[9];
	return $data if $data && $mtime == $dataMtime;
	open(my $fh, '<:raw', $file) or return undef;
	local $/;
	my $raw = <$fh>;
	close $fh;
	my $d = eval { JSON::PP->new->utf8->decode($raw) };
	if (!$d) {
		warning "[combatProfile] не удалось прочитать $file: $@\n";
		return undef;
	}
	($data, $dataMtime) = ($d, $mtime);
	return $data;
}

sub classForJob {
	my ($d, $jobID) = @_;
	for my $name (sort keys %{$d->{classes}}) {
		return $name if grep { $_ == $jobID } @{$d->{classes}{$name}{jobs} || []};
	}
	return undef;
}

# Профиль с наследованием: навыки ребёнка первыми (приоритет), без повторов.
sub resolve {
	my ($d, $name) = @_;
	my @chain;
	for (my $n = $name, my $guard = 0; $n && $guard < 5; $n = $d->{classes}{$n}{inherits}, $guard++) {
		push @chain, $n;
	}
	my %p = (class => $name, chain => [@chain], stats => $d->{classes}{$name}{stats}, skills => []);
	for my $n (@chain) {
		my $c = $d->{classes}{$n};
		$p{archetype} ||= $c->{archetype};
		unshift @{$p{skills}}, split(/\s*,\s*/, $c->{skills} || '');
		for my $fam (keys %BLOCK) {
			for my $e (@{$c->{$fam} || []}) {
				push @{$p{$fam}}, $e unless grep { $_->{skill} eq $e->{skill} } @{$p{$fam} || []};
			}
		}
	}
	$p{settings} = $d->{archetypes}{$p{archetype}} || {};
	return \%p;
}

sub skillLevel {
	my ($handle) = @_;
	my $lvl = eval { $char->getSkillLevel(Skill->new(handle => $handle)) };
	return $lvl || 0;
}

sub inTree { my ($handle) = @_; return exists $char->{skills}{$handle}; }

sub setKey {
	my ($key, $val) = @_;
	$val = '' unless defined $val;
	return 0 if defined $config{$key} && $config{$key} eq $val;
	Misc::configModify($key, $val, silent => 1);
	return 1;
}

sub applyBlock {
	my ($prefix, $entries) = @_;
	my (@on, @off);
	for my $e (@$entries) { (skillLevel($e->{skill}) > 0 ? push(@on, $e) : push(@off, $e->{skill})); }
	my $i = 0;
	for my $e (@on) {
		setKey("${prefix}_$i", $e->{skill});
		setKey("${prefix}_${i}_$_", $e->{$_}) for @CONDITION_KEYS;
		setKey("${prefix}_${i}_disabled", 0);
		$i++;
	}
	while (exists $config{"${prefix}_$i"}) {           # старые/лишние слоты выключить
		setKey("${prefix}_${i}_disabled", 1);
		$i++;
	}
	return ([map { $_->{skill} } @on], \@off);
}

sub apply {
	my ($p) = @_;
	setKey($_, $p->{settings}{$_}) for sort keys %{$p->{settings}};
	my (%applied, @skipped);
	for my $fam (sort keys %BLOCK) {
		my ($on, $off) = applyBlock($BLOCK{$fam}, $p->{$fam} || []);
		$applied{$fam} = $on;
		push @skipped, @$off;
	}
	if ($p->{stats}) {
		setKey('statsAddAuto_list', $p->{stats});
		setKey('statsAddAuto', 1);
	}
	my @tree = grep { my ($h) = split /\s+/; inTree($h) } @{$p->{skills}};
	setKey('skillsAddAuto_list', join(', ', @tree));
	setKey('skillsAddAuto', @tree ? 1 : 0);
	my %seen;
	%current = (class => $p->{class}, chain => $p->{chain}, archetype => $p->{archetype},
	            applied => \%applied, skipped => [grep { !$seen{$_}++ } @skipped]);
	message sprintf("[combatProfile] %s (%s): атака [%s], себе [%s], группе [%s]; не изучено: [%s]\n",
		$p->{class}, join(' <- ', @{$p->{chain}}),
		join(',', @{$applied{attack}}), join(',', @{$applied{self}}), join(',', @{$applied{party}}),
		join(',', @{$current{skipped}})), 'system';
}

sub onTick {
	return unless $char && defined $char->{jobID};
	return unless $force || time - $lastCheck >= 10;
	($force, $lastCheck) = (0, time);
	my $d = loadData() or return;
	my $class = classForJob($d, $char->{jobID});
	if (!$class) {
		if ($signature ne "none:$char->{jobID}") {
			warning "[combatProfile] нет профиля для профессии $char->{jobID} ("
				. ($jobs_lut{$char->{jobID}} || '?') . ") — настройки не меняю\n";
			$signature = "none:$char->{jobID}";
			%current = (class => undef, job => $char->{jobID});
		}
		return;
	}
	my $p = resolve($d, $class);
	my @handles = map { $_->{skill} } map { @{$p->{$_} || []} } keys %BLOCK;
	my @tree = grep { inTree((split /\s+/)[0]) } @{$p->{skills}};
	my $sig = join('|', $char->{jobID}, $dataMtime, (map { "$_=" . skillLevel($_) } sort @handles), sort @tree);
	return if $sig eq $signature;
	$signature = $sig;
	apply($p);
}

1;
