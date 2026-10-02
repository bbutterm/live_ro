# gracefulStop — корректный выход OpenKore по SIGTERM/SIGINT (live_ro).
#
# Почему недостаточно просто закрыть соединение (rAthena, src/map/clif.cpp):
#  - clif_parse_QuitGame: если персонаж был в бою меньше prevent_logout (10 с,
#    conf/battle/player.conf), сервер отвечает отказом (018B fail=1);
#  - clif_quitsave: если в этот момент закрыть сокет, персонаж остаётся в мире
#    ещё 10 с (clif_delayquit). Новый вход в это время получает
#    "The server still recognizes your last connection".
#
# Что делает плагин:
#  1. Сигнал не убивает процесс, а запускает остановку.
#  2. Автономный AI переводится в manual: бот перестаёт атаковать и ходить
#     (монстр, который бьёт бота, всё ещё может продлевать запрет выхода).
#  3. Каждые 2 с отправляется запрос выхода (quit_request) до ответа сервера
#     018B fail=0 — только он считается подтверждением выхода.
#  4. После подтверждения — штатный quit. Нет подтверждения за
#     gracefulStop_timeout секунд (по умолчанию 40) — quit с предупреждением:
#     сервер удержит персонажа до 10 с.
#  Повторный сигнал — немедленный выход.
package gracefulStop;

use strict;
use Time::HiRes qw(time);
use Plugins;
use Globals qw($net $messageSender %config);
use Network;
use AI;
use Commands;
use Log qw(message warning);

Plugins::register('gracefulStop', 'SIGTERM/SIGINT -> выход из игры с подтверждением сервера', \&onUnload);

my %old = (TERM => $SIG{TERM}, INT => $SIG{INT});
my ($stopping, $started, $lastRequest, $confirmed, $refusals) = (0, 0, 0, 0, 0);

for my $sig (qw(TERM INT)) {
	$SIG{$sig} = sub {
		exit(1) if $stopping++;
		$started = time;
	};
}

my $hooks = Plugins::addHooks(
	['mainLoop_pre',        \&onLoop],
	['packet/quit_response', \&onQuitResponse],
);

sub onUnload {
	Plugins::delHooks($hooks);
	$SIG{$_} = $old{$_} for keys %old;
}

sub inGame { return $net && $net->getState() == Network::IN_GAME; }

sub finish {
	my ($text) = @_;
	message "[gracefulStop] $text\n", 'system';
	$Globals::quit = 1;
}

sub onLoop {
	return unless $stopping;
	if ($stopping == 1) {
		$stopping = 2;
		message "[gracefulStop] получен сигнал остановки: AI -> manual, запрашиваю выход у сервера\n", 'system';
		AI::clear();
		Commands::run('ai manual');
	}
	return if $Globals::quit;
	if (!inGame()) {
		return finish('не в игре: выхожу без запроса к серверу');
	}
	my $timeout = $config{gracefulStop_timeout} || 40;
	my $elapsed = time - $started;
	if ($elapsed > $timeout) {
		warning sprintf("[gracefulStop] сервер не подтвердил выход за %d с (отказов: %d): выхожу без подтверждения, сервер удержит персонажа до 10 с\n",
			$timeout, $refusals), 'system';
		$Globals::quit = 1;
		return;
	}
	if (time - $lastRequest >= 2) {
		$lastRequest = time;
		$messageSender->sendQuit();
	}
}

sub onQuitResponse {
	my (undef, $args) = @_;
	return unless $stopping;
	if ($args->{fail}) {
		$refusals++;
		message sprintf("[gracefulStop] сервер отказал в выходе (бой < 10 с), отказ %d, жду\n", $refusals), 'system';
	} elsif (!$confirmed) {
		$confirmed = 1;
		finish(sprintf('сервер подтвердил выход через %.1f с (отказов: %d)', time - $started, $refusals));
	}
}

1;
