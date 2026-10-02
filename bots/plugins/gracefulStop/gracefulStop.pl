# gracefulStop — корректный выход OpenKore по SIGTERM/SIGINT (live_ro).
#
# Без плагина SIGTERM мгновенно убивает OpenKore: пакет выхода серверу не
# отправляется, и rAthena ещё какое-то время держит сессию ("The server still
# recognizes your last connection" при следующем входе).
#
# С плагином сигнал только поднимает флаг $quit (как консольная команда quit).
# Главный цикл завершается штатно, при выгрузке плагинов соединение закрывается
# через serverDisconnect, а он отправляет пакет выхода, если бот в игре.
package gracefulStop;

use strict;
use Plugins;
use Globals qw($net);
use Network;
use Log qw(message);

Plugins::register('gracefulStop', 'SIGTERM/SIGINT -> штатный quit с выходом из игры', \&onUnload);

my %old = (TERM => $SIG{TERM}, INT => $SIG{INT});
my $signalled = 0;
for my $sig (qw(TERM INT)) {
	$SIG{$sig} = sub {
		# Повторный сигнал — немедленный выход, как без плагина.
		exit(1) if $signalled++;
		message "[gracefulStop] получен SIG$sig: выхожу из игры\n", 'system';
		$Globals::quit = 1;
	};
}

sub onUnload {
	$SIG{$_} = $old{$_} for keys %old;
	return unless $signalled;
	if ($net && $net->getState() != Network::NOT_CONNECTED) {
		$net->serverDisconnect();
	}
}

1;
