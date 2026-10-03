# jobChange — исполнитель этапа квеста смены профессии (live_ro, AUT-080). Без LLM.
#
# Шаги этапа присылает мозг (brain/live_brain/progression.py: stage_action -> действие job_change
# {id, path, stage, steps, success}); сами сценарии выведены из скриптов rAthena
# (brain/world/progression.json, ссылки file:line). Плагин только исполняет и проверяет:
#   move      — «move <x> <y> <map>», готово на карте в 2 клетках от точки;
#   talk      — «talknpc <x> <y>» без последовательности: меню отвечает плагин (хук npc_talk_responses)
#               по ТЕКСТУ пункта, как progression.choose_answer: первый ответ шага, совпавший с пунктом
#               (ordered — строго по порядку). Меню не из сценария — «talk no» и провал этапа.
#               «Далее» нажимает OpenKore: на время этапа autoTalkCont 1. expect_map / expect_map_xy —
#               после диалога ждать перехода (warp из скрипта);
#               input_text / input_number — ответ на ввод строки/числа (input в скрипте: «talk text» / «talk num»;  # buying:
#               Mr. Hugh, npc/merchants/buying_shops.txt:221 и :126 — скупка ORG-036);  # buying:
#   chat_join — войти в чат-комнату с заголовком title (комната ожидания арены Knight);
#   fight     — attackAuto 2; готово, когда скрипт перенёс на next_xy / next_map (все убиты); иначе
#               таймаут time_limit + 15 с (скрипт сам выкидывает по своему таймеру);
#   wait      — стоять (attack: false -> attackAuto 0) до перехода на until_map (тест терпения Knight);
#   walk      — идти к x,y; диалоги, начатые NPC (OnTouch), отвечать из autotalk[npc] (коридор
#               искушений Priest); готово по next_xy / next_map.
# unless_map в шаге — пропустить шаг, если персонаж уже на этой карте.
# Итог — событие job_change_result {id, path, stage, ok, reason, step} после проверки success
# (квест в журнале, профессия, карта, текст NPC). На время этапа: lockMap пусто, route_randomWalk 0,
# autoTalkCont 1, attackAuto по шагу; по окончании прежние значения возвращаются.
# Для мозга: jobChange::status() -> {running, stage, step, quests, skill_points, items}.
# НЕ ПРОВЕРЕНО В ИГРЕ: проверено только тестом на заглушках (bots/tests/job_change.t).
package jobChange;

use strict;
use utf8;
use Time::HiRes qw(time);
use JSON::PP;
use Plugins;
use Globals qw($char $field %config %jobs_lut %talk $questList %chatRooms @chatRoomsID $currentChatRoom);
use Log qw(message warning);
use Commands;

Plugins::register('jobChange', 'исполнитель этапа квеста смены профессии (шаги от мозга)', \&onUnload);

my $hooks = Plugins::addHooks(
	['mainLoop_post',      \&onTick],
	['npc_talk',           \&onNpcText],
	['npc_talk_responses', \&onResponses],
	['npc_talk_done',      \&onTalkDone],
	['packet/npc_talk_text',   \&onInputText],                    # buying: input .@name$
	['packet/npc_talk_number', \&onInputNumber],                  # buying: input .@input
);

# Квесты смены профессии (журнал для мозга): Knight 9000-9012, Priest 8009-8016 (db/re/quest_db.yml).
our @QUESTS = (9000 .. 9012, 8009 .. 8016);
# Предметы Sir Andrew (progression.json paths.knight.item_sets) — счётчики для мозга.
our @ITEMS = (1040, 7006, 931, 1057, 903, 1028, 1042, 950, 1032, 966, 7031, 946);
my @SAVE = qw(lockMap lockMap_x lockMap_y route_randomWalk autoTalkCont attackAuto);
my %TIMEOUT = (move => 900, talk => 90, chat_join => 120, fight => 195, wait => 340, walk => 600, check => 10);
our %run;                   # id, path, stage, steps, success, i, phase, since, step_since, texts, saved, pos, issued
our $lastTick = 0;          # троттлинг 0.5 с (our — тест сбрасывает)

sub onUnload { Plugins::delHooks($hooks); }

# ---------- запуск и итог ----------

# Возвращает (1, описание) — этап начат, или (0, причина).
sub start {
	my ($a) = @_;
	return (0, 'уже идёт этап ' . $run{stage}) if %run;
	return (0, 'нет персонажа') unless $char;
	my $steps = $a->{steps};
	return (0, 'нет шагов') unless ref $steps eq 'ARRAY' && @$steps;
	for my $s (@$steps) {
		return (0, 'неизвестный шаг') unless ref $s eq 'HASH' && ($s->{do} // '') =~ /^(move|talk|chat_join|fight|wait|walk)$/;
		return (0, 'неверные координаты') if $s->{do} =~ /^(move|talk|walk)$/
			&& !(($s->{x} // '') =~ /^\d{1,3}$/ && ($s->{y} // '') =~ /^\d{1,3}$/);
		return (0, 'неверная карта') if defined $s->{map} && $s->{map} !~ /^[a-z0-9_]{3,16}$/;
		return (0, 'неверный ввод') if (defined $s->{input_text} && $s->{input_text} !~ /^[A-Za-z0-9 ]{1,23}$/)   # buying:
			|| (defined $s->{input_number} && $s->{input_number} !~ /^\d{1,3}$/);                               # buying:
	}
	%run = (id => $a->{id}, path => $a->{path} // '', stage => $a->{stage} // '', steps => $steps,
	        success => $a->{success} || {}, i => 0, phase => 'begin', since => time, step_since => time,
	        texts => [], saved => {map { $_ => $config{$_} } @SAVE});
	conf(lockMap => 'none', lockMap_x => 'none', lockMap_y => 'none', route_randomWalk => 0, autoTalkCont => 1,
	     attackAuto => 1);
	keepSaved();                                    # review4-след: копия на случай выхода посреди этапа
	message "[jobChange] этап $run{path}/$run{stage}: шагов " . scalar(@$steps) . "\n", 'system';
	return (1, "этап $run{path}/$run{stage}");
}

sub stop { finish(0, $_[0] // 'остановлен'); }

sub conf {
	my (%kv) = @_;
	for my $k (sort keys %kv) {
		my $v = $kv{$k};
		$v = 'none' if !defined $v || $v eq '';
		Commands::run("conf $k $v");
	}
}


# review4-след: «conf» пишет config.txt. Выход OpenKore посреди этапа (gracefulStop, падение, перезапуск) оставлял бы
# в профиле lockMap none, route_randomWalk 0, attackAuto 1 — следующая охота не на своей карте. Прежние значения
# дублируются в config (jobChange_saved) и возвращаются первым тактом без этапа (restoreSaved), если finish до них не дошёл.
sub keepSaved {
	my $saved = $run{saved} || {};
	my @kv;
	for my $k (sort keys %$saved) {
		my $v = $saved->{$k};
		$v = 'none' if !defined $v || $v eq '';
		next if $v =~ /[\s,=]/;
		push @kv, "$k=$v";
	}
	Commands::run('conf -f jobChange_saved ' . join(',', @kv)) if @kv;
}

sub restoreSaved {
	my $line = $config{jobChange_saved};
	return unless defined $line && $line ne '' && $line ne 'none';
	my %kv = map { /^([^=]+)=(.*)$/ ? ($1 => $2) : () } split /,/, $line;
	warning "[jobChange] этап прервался выходом — возвращаю настройки профиля\n";
	conf(%kv) if %kv;
	conf(jobChange_saved => 'none');
	$config{jobChange_saved} = undef;
}

sub finish {
	my ($ok, $reason) = @_;
	return unless %run;
	my %r = %run;
	%run = ();
	Commands::run('talk no') if !$ok && %talk;
	conf(%{$r{saved}});
	conf(jobChange_saved => 'none');                         # review4-след: вернули сами
	if ($ok) { message "[jobChange] этап $r{path}/$r{stage} пройден\n", 'system'; }
	else     { warning "[jobChange] этап $r{path}/$r{stage} не пройден: $reason\n"; }
	my $ev = brainBridge->can('event');
	$ev->('job_change_result', id => $r{id}, path => $r{path}, stage => $r{stage}, step => $r{i},
	      ok => ($ok ? JSON::PP::true() : JSON::PP::false()), reason => $reason) if $ev;
}

# ---------- состояние ----------

sub mapName { return $field ? $field->baseName : ''; }

sub charPos { return ($char && $char->{pos_to}) || {}; }

sub near {
	my ($x, $y, $d) = @_;
	my $p = charPos();
	return 0 unless defined $p->{x};
	my ($dx, $dy) = (abs($p->{x} - $x), abs($p->{y} - $y));
	return ($dx > $dy ? $dx : $dy) <= $d;
}

sub quests {
	return [] unless $questList && ref $questList eq 'HASH';
	my %want = map { $_ => 1 } @QUESTS;
	return [sort { $a <=> $b } grep { $want{$_} } map { $_ + 0 } keys %$questList];
}

sub hasQuest {
	my ($id) = @_;
	return $questList && ref $questList eq 'HASH' && exists $questList->{$id};
}

sub itemCounts {
	my %count = map { $_ => 0 } @ITEMS;
	return \%count unless $char && $char->inventory;
	for my $item (@{$char->inventory}) {
		$count{$item->{nameID}} += $item->{amount} if exists $count{$item->{nameID}} && !$item->{equipped};
	}
	return \%count;
}

sub status {
	return {running => (%run ? JSON::PP::true() : JSON::PP::false()), stage => $run{stage}, step => $run{i},
	        quests => quests(), skill_points => ($char ? ($char->{points_skill} // 0) + 0 : undef),
	        items => itemCounts()};
}

# ---------- шаги ----------

sub step { return $run{steps}[$run{i}]; }

sub nextStep {
	$run{i}++;
	$run{phase} = 'begin';
	$run{step_since} = time;
	delete @run{qw(pos issued talking talked)};
}

sub arrived {
	my ($s) = @_;
	return 1 if $s->{next_map} && mapName() eq $s->{next_map};
	return 1 if $s->{next_xy} && near($s->{next_xy}[0], $s->{next_xy}[1], 3);
	return 0;
}

sub onTick {
	restoreSaved() unless %run;                    # review4-след: после выхода посреди этапа
	return unless %run;
	my $now = time;
	return if $now - $lastTick < 0.5;
	$lastTick = $now;
	if (!$char || $char->{dead}) {
		finish(0, 'персонаж мёртв');
		return;
	}
	my $s = step();
	if (!$s) {                                           # все шаги сделаны — проверка итога
		$run{check_since} //= $now;
		my $why = successMissing();
		return finish(1, 'ok') unless defined $why;
		finish(0, "шаги пройдены, но $why") if $now - $run{check_since} > $TIMEOUT{check};
		return;
	}
	if ($s->{unless_map} && mapName() eq $s->{unless_map}) {
		nextStep();
		return;
	}
	my $limit = $TIMEOUT{$s->{do}};
	$limit = $s->{time_limit} + 15 if $s->{time_limit};
	if ($now - $run{step_since} > $limit) {
		finish(0, "шаг $run{i} ($s->{do}): таймаут ${limit} с — не успел");
		return;
	}
	my $sub = __PACKAGE__->can("do_$s->{do}");
	$sub->($s, $now);
}

sub do_move {
	my ($s, $now) = @_;
	if (mapName() eq $s->{map} && near($s->{x}, $s->{y}, $s->{dist} // 2)) {
		nextStep();
		return;
	}
	if (!$run{issued} || ($now - $run{issued} > 15 && (AI::action() // '') !~ /^(route|move|NPC)$/)) {
		Commands::run("move $s->{x} $s->{y} $s->{map}");
		$run{issued} = $now;
	}
}

sub do_talk {
	my ($s, $now) = @_;
	if ($run{talked}) {                                  # диалог закрыт — ждём перехода, если он обещан
		if ($s->{expect_map}) {
			return unless mapName() eq $s->{expect_map};
		} elsif ($s->{expect_map_xy}) {
			return unless near($s->{expect_map_xy}[0], $s->{expect_map_xy}[1], 3);
		}
		nextStep();
		return;
	}
	return if $run{issued};
	$run{pos} = 0;
	$run{talking} = {answers => $s->{answers} || [], ordered => $s->{ordered},
	                 input_text => $s->{input_text}, input_number => $s->{input_number}};   # buying:
	Commands::run("talknpc $s->{x} $s->{y}");
	$run{issued} = $now;
}

sub do_chat_join {
	my ($s, $now) = @_;
	if ($s->{expect_map_xy} && near($s->{expect_map_xy}[0], $s->{expect_map_xy}[1], 3)) {
		nextStep();
		return;
	}
	return if defined $currentChatRoom && $currentChatRoom ne '';       # уже внутри — ждём старта
	return if $run{issued} && $now - $run{issued} < 5;
	for my $i (0 .. $#chatRoomsID) {
		my $id = $chatRoomsID[$i];
		next unless defined $id && $chatRooms{$id};
		next unless ($chatRooms{$id}{title} // '') eq $s->{title};
		Commands::run("chat join $i");
		$run{issued} = $now;
		return;
	}
}

sub do_fight {
	my ($s, $now) = @_;
	if (!$run{issued}) {
		conf(attackAuto => 2);
		$run{issued} = $now;
	}
	if (arrived($s)) {
		conf(attackAuto => 1);
		nextStep();
	}
}

sub do_wait {
	my ($s, $now) = @_;
	if (!$run{issued}) {
		conf(attackAuto => ($s->{attack} ? 2 : 0));
		Commands::run('ai clear') unless $s->{attack};
		$run{issued} = $now;
	}
	if (mapName() eq ($s->{until_map} // '')) {
		conf(attackAuto => 1);
		nextStep();
	}
}

sub do_walk {
	my ($s, $now) = @_;
	if (arrived($s)) {
		conf(attackAuto => 1);
		nextStep();
		return;
	}
	if (!$run{issued}) {
		conf(attackAuto => (defined $s->{attack} && !$s->{attack} ? 0 : 1));
		$run{talking} = {autotalk => $s->{autotalk} || []};
	}
	return if %talk;                                     # NPC остановил — отвечаем в хуке
	if (!$run{issued} || ($now - $run{issued} > 10 && (AI::action() // '') !~ /^(route|move|NPC)$/)) {
		Commands::run("move $s->{x} $s->{y} $s->{map}");
		$run{issued} = $now;
	}
}

# ---------- диалог ----------

# Номер пункта (с 1) — то же правило, что progression.choose_answer.
sub chooseAnswer {
	my ($answers, $options, $pos) = @_;
	my @opts = map { my $o = $_ // ''; $o =~ s/^\s+|\s+$//g; $o } @$options;
	my @cand = defined $pos ? ($answers->[$pos]) : @$answers;
	for my $a (@cand) {
		return undef unless $a;
		(my $text = $a->{text} // '') =~ s/^\s+|\s+$//g;
		for my $i (0 .. $#opts) {
			return $i + 1 if $opts[$i] eq $text;
		}
	}
	return undef;
}

sub onNpcText {
	my (undef, $args) = @_;
	return unless %run;
	push @{$run{texts}}, $args->{msg} // '';
	shift @{$run{texts}} while @{$run{texts}} > 40;
}

sub onResponses {
	my (undef, $args) = @_;
	return unless %run && $run{talking};
	my @opts = @{$args->{responses} || []};
	pop @opts if @opts && $opts[-1] =~ /^Cancel Chat$/i;             # пункт OpenKore, не из скрипта
	my $t = $run{talking};
	my ($answers, $pos) = ($t->{answers}, $t->{ordered} ? $run{pos} : undef);
	if ($t->{autotalk}) {
		my $name = $args->{name} // '';
		my ($entry) = grep { index($name, $_->{npc}) == 0 } @{$t->{autotalk}};
		return finish(0, "диалог с '$name' не из сценария: " . join(' | ', @opts)) unless $entry;
		$answers = $entry->{answers};
		$pos = undef;
	}
	my $n = chooseAnswer($answers, \@opts, $pos);
	return finish(0, 'меню не из сценария: ' . join(' | ', @opts)) unless $n;
	$run{pos}++ if defined $pos;
	Commands::run('talk resp ' . ($n - 1));
}

# buying: ввод в диалоге — только из шага сценария; без него этап проваливается (не угадываем).
sub onInputText {
	return unless %run && $run{talking};
	my $t = $run{talking}{input_text};
	return finish(0, 'NPC просит ввести текст — нет в сценарии') unless defined $t;
	Commands::run("talk text $t");
}

sub onInputNumber {
	return unless %run && $run{talking};
	my $n = $run{talking}{input_number};
	return finish(0, 'NPC просит ввести число — нет в сценарии') unless defined $n;
	Commands::run("talk num $n");
}

sub onTalkDone {
	return unless %run && $run{talking};
	my $s = step();
	$run{talked} = 1 if $s && $s->{do} eq 'talk';
}

# ---------- итог этапа ----------

# undef — условие success выполнено, иначе — чего не хватает.
sub successMissing {
	my $c = $run{success} || {};
	return "нет квеста $c->{quest} в журнале" if $c->{quest} && !hasQuest($c->{quest});
	return 'нет ни одного квеста из ' . join(',', @{$c->{quests_any}})
		if $c->{quests_any} && !grep { hasQuest($_) } @{$c->{quests_any}};
	if ($c->{job}) {
		my $job = $char && defined $char->{jobID} ? ($jobs_lut{$char->{jobID}} // '') : '';
		return "профессия не $c->{job}" unless $job eq $c->{job};
	}
	return "не на карте $c->{map}" if $c->{map} && mapName() ne $c->{map};
	if ($c->{map_xy}) {
		my ($m, $x, $y) = @{$c->{map_xy}};
		return "не в точке $m $x,$y" unless mapName() eq $m && near($x, $y, 3);
	}
	if ($c->{text}) {
		my $text = join("\n", @{$run{texts} || []});
		return 'NPC не сказал ожидаемого' unless $text =~ /$c->{text}/;
	}
	return undef;
}

1;
