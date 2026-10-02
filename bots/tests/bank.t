# Тест банка в brainBridge (ORG-073) на заглушках OpenKore: действия bank_* и события по пакетам сервера.
# Запуск: perl -Ibots/tests/stubs bots/tests/bank.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeNet; sub new { bless {}, shift } sub getState { 5 }
package FakeSender; our @sent;
sub new { bless {}, shift }
sub sendBankingCheck { shift; push @sent, ['check', @_] }
sub sendBankingDeposit { shift; push @sent, ['deposit', @_] }
sub sendBankingWithdraw { shift; push @sent, ['withdraw', @_] }

package main;
my $root = "$FindBin::Bin/../..";
$Globals::net = FakeNet->new;
$Globals::char = {name => 'Arkady', zeny => 50000, dead => 0};
$Globals::accountID = 'AID1';
require "$root/bots/plugins/brainBridge/brainBridge.pl";

# Без отправителя пакетов — отказ.
my ($ok, $res) = brainBridge::actionToCommand({action => 'bank_check'});
ok(!$ok, 'нет messageSender — отказ');
$Globals::messageSender = FakeSender->new;

($ok, $res) = brainBridge::actionToCommand({action => 'bank_check'});
ok($ok, 'запрос вклада принят');
is(ref $res, 'HASH', 'исполняется мостом, команд OpenKore нет');
is_deeply(\@FakeSender::sent, [['check', 'AID1']], 'пакет 09AB с AID');

@FakeSender::sent = ();
($ok, $res) = brainBridge::actionToCommand({action => 'bank_deposit', zeny => 20000});
ok($ok, 'вклад принят');
is_deeply(\@FakeSender::sent, [['deposit', 'AID1', 20000]], 'пакет 09A7: AID и сумма');

@FakeSender::sent = ();
for my $bad (0, -5, '1e5', 'abc', 10_000_001, 60000) {
	($ok, $res) = brainBridge::actionToCommand({action => 'bank_deposit', zeny => $bad});
	ok(!$ok, "вклад $bad отклонён ($res)");
}
is_deeply(\@FakeSender::sent, [], 'неверные суммы и больше кармана — пакетов нет');

($ok, $res) = brainBridge::actionToCommand({action => 'bank_withdraw', zeny => 70000});
ok($ok, 'снять больше кармана можно (проверит сервер по вкладу)');
is_deeply(\@FakeSender::sent, [['withdraw', 'AID1', 70000]], 'пакет 09A9');

# Тело занято — отказ.
@FakeSender::sent = ();
%Globals::currentDeal = (id => 1);
($ok, $res) = brainBridge::actionToCommand({action => 'bank_deposit', zeny => 1000});
ok(!$ok && $res =~ /сделка/, 'идёт сделка — отказ');
%Globals::currentDeal = ();
$Globals::shopstarted = 1;
($ok, $res) = brainBridge::actionToCommand({action => 'bank_withdraw', zeny => 1000});
ok(!$ok && $res =~ /лавка/, 'открыта лавка — отказ');
$Globals::shopstarted = 0;
$Globals::char->{dead} = 1;
($ok, $res) = brainBridge::actionToCommand({action => 'bank_check'});
ok(!$ok, 'мёртвому — отказ');
$Globals::char->{dead} = 0;
is_deeply(\@FakeSender::sent, [], 'отказы без пакетов');

# События по пакетам сервера: перехватить отправку мозгу.
my @events;
{
	no warnings 'redefine';
	*brainBridge::event = sub { my ($kind, %d) = @_; push @events, {kind => $kind, %d} };
}
Plugins::call('packet/banking_check', {zeny => 12345, zeny2 => 0, reason => 0});
is_deeply($events[0], {kind => 'bank_balance', vault => 12345}, '09A6 -> bank_balance');
Plugins::call('packet/banking_deposit', {reason => 0, zeny => 32345, zeny2 => 0, balance => 30000});
is($events[1]{kind}, 'bank_result', '09A8 -> bank_result');
is($events[1]{op}, 'deposit', 'операция');
ok($events[1]{ok}, 'reason 0 — успех');
is($events[1]{vault}, 32345, 'вклад после операции');
is($events[1]{zeny}, 30000, 'в кармане после операции');
Plugins::call('packet/banking_withdraw', {reason => 1, zeny => 0, zeny2 => 1, balance => 30000});
ok(!$events[2]{ok}, 'reason 1 — отказ');
is($events[2]{reason}, 1, 'код отказа');
is($events[2]{vault}, 4294967296, 'старшее слово вклада (int64)');

done_testing();
