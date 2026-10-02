# Тест плагина combatProfile на заглушках OpenKore.
# Запуск: perl -Ibots/tests/stubs bots/tests/combat_profile.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeChar;
sub new { my ($c, %a) = @_; bless {%a}, $c }
sub getSkillLevel { my ($s, $sk) = @_; my $x = $s->{skills}{$sk->getHandle}; $x ? $x->{lv} : 0 }

package main;
my $root = "$FindBin::Bin/../..";
$ENV{LIVE_RO_COMBAT} = "$root/bots/combat/classes.json";
# слоты из шаблона профиля (как в config.txt): индекс 0 существует
%Globals::config = (attackSkillSlot_0 => '', useSelf_skill_0 => '', partySkill_0 => '', useSelf_skill_1 => 'OLD');
require "$root/bots/plugins/combatProfile/combatProfile.pl";
my $c = \%Globals::config;

# Vera: Acolyte, Heal 5 и Blessing 3 изучены, Increase AGI в дереве (0), Angelus нет.
$Globals::char = FakeChar->new(jobID => 4, skills => {
	AL_HEAL => {lv => 5}, AL_BLESSING => {lv => 3}, AL_INCAGI => {lv => 0}, AL_DP => {lv => 0}});
Plugins::call('mainLoop_post');
is($combatProfile::current{class}, 'Acolyte', 'профиль по номеру профессии');
is($c->{partySkill_0}, 'AL_HEAL', 'лечение группы — первый слот');
is($c->{partySkill_0_target_hp}, '< 60%', 'условие HP цели');
is($c->{partySkill_0_sp}, '> 15%', 'условие SP');
is($c->{partySkill_0_maxDist}, 9, 'дистанция');
is($c->{partySkill_1}, 'AL_BLESSING', 'бафф группы');
ok(!exists $c->{partySkill_2} || $c->{partySkill_2_disabled}, 'Increase AGI не изучен — слота нет');
is($c->{useSelf_skill_0}, 'AL_HEAL', 'самолечение');
is($c->{useSelf_skill_0_hp}, '< 50%', 'самолечение при HP < 50%');
is($c->{useSelf_skill_2_disabled}, 1, 'старый лишний слот выключен') if exists $c->{useSelf_skill_2};
is($c->{attackSkillSlot_0_disabled}, 1, 'у Acolyte нет атакующих навыков — слот выключен');
like($c->{skillsAddAuto_list}, qr/AL_HEAL 10/, 'прокачка навыков из дерева');
unlike($c->{skillsAddAuto_list}, qr/AL_ANGELUS/, 'навык не из дерева в прокачку не попадает');
is($c->{statsAddAuto}, 1, 'авто-статы включены');
is($c->{attackUseWeapon}, 1, 'архетип melee');
ok(grep({ $_ eq 'AL_INCAGI' } @{$combatProfile::current{skipped}}), 'неизученное в отчёте');

# Повторный тик без изменений — настройки не трогаются.
@Misc::modified = ();
$combatProfile::force = 1;
Plugins::call('mainLoop_post');
is(scalar @Misc::modified, 0, 'без изменений — без записей в конфиг');

# Выучила Increase AGI — профиль переприменяется.
$Globals::char->{skills}{AL_INCAGI}{lv} = 1;
$combatProfile::force = 1;
Plugins::call('mainLoop_post');
is($c->{partySkill_2}, 'AL_INCAGI', 'новый навык подхвачен');
is($c->{partySkill_2_disabled}, 0, 'и включён');

# Knight наследует Swordsman: свои навыки первыми, затем родителя.
%Globals::config = (attackSkillSlot_0 => '');
$Globals::char = FakeChar->new(jobID => 7, skills => {
	KN_PIERCE => {lv => 3}, SM_BASH => {lv => 10}, SM_MAGNUM => {lv => 0}, KN_BOWLINGBASH => {lv => 0}});
$combatProfile::force = 1;
Plugins::call('mainLoop_post');
is($combatProfile::current{class}, 'Knight', 'Knight');
is_deeply($combatProfile::current{chain}, ['Knight', 'Swordsman'], 'наследование');
is($c->{attackSkillSlot_0}, 'KN_PIERCE', 'навык профессии первым');
is($c->{attackSkillSlot_1}, 'SM_BASH', 'затем навык родителя');

# Маг — архетип caster: без оружия, держит дистанцию, отдыхает по SP.
%Globals::config = (attackSkillSlot_0 => '');
$Globals::char = FakeChar->new(jobID => 2, skills => {MG_FIREBOLT => {lv => 4}, MG_COLDBOLT => {lv => 0}});
$combatProfile::force = 1;
Plugins::call('mainLoop_post');
is($c->{attackUseWeapon}, 0, 'маг не бьёт оружием');
is($c->{attackDistance}, 8, 'дистанция мага');
is($c->{sitAuto_sp_lower}, 25, 'отдых по SP');
is($c->{attackSkillSlot_0}, 'MG_FIREBOLT', 'Fire Bolt');
is($c->{attackSkillSlot_0_lvl}, 5, 'уровень из профиля');

# Неизвестная профессия — ничего не трогаем.
%Globals::config = (attackUseWeapon => 1);
$Globals::char = FakeChar->new(jobID => 9999, skills => {});
$combatProfile::force = 1;
Plugins::call('mainLoop_post');
is($c->{attackUseWeapon}, 1, 'нет профиля — настройки не меняются');
ok(!defined $combatProfile::current{class}, 'класс не определён');

done_testing();
