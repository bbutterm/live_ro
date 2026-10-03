# Тест плагина economy на заглушках OpenKore: склад ценного, передача жителю, отказ чужим,
# торговля с жителем (offer_sell/offer_buy), почта RODEX, лавка из offer_shop, метрики продаж.
# Запуск: perl -Ibots/tests/stubs bots/tests/economy.t   (из корня репозитория)
use strict;
use utf8;
use FindBin;
use Test::More;
binmode(Test::More->builder->$_, ':utf8') for qw(output failure_output todo_output);

package FakeChar;
sub new { my ($c, %a) = @_; bless {%a}, $c }
sub inventory { $_[0]{inv} }
sub cartActive { $_[0]{cart} }
sub cart { $_[0]{cartItems} || [] }

package FakeList;                 # InventoryList почты: size()
sub new { my ($c, @i) = @_; bless [@i], $c }
sub size { scalar @{$_[0]} }

package FakePlayers;
sub new { my ($c, @p) = @_; bless [@p], $c }
sub getItems { [@{$_[0]}] }

package brainBridge;
our @events;
sub event { push @events, [@_] }

package main;
my $root = "$FindBin::Bin/../..";
%Globals::config = (dealAuto_names => 'Vera', economy_storeIds => '984,985');
%Globals::items_control = ('jellopy' => {keep => 0, storage => 0, sell => 1}, 'all' => {keep => 0, storage => 0, sell => 1});
my @inv = (
	{binID => 0, nameID => 4001, name => 'Poring Card', type => 6, amount => 1},
	{binID => 1, nameID => 984,  name => 'Oridecon', type => 3, amount => 2},
	{binID => 2, nameID => 909,  name => 'Jellopy', type => 3, amount => 30},
	{binID => 3, nameID => 501,  name => 'Red Potion', type => 0, amount => 35},
	{binID => 4, nameID => 2301, name => 'Cotton Shirt', type => 5, amount => 1, equipped => 1},
	{binID => 5, nameID => 1201, name => 'Knife', type => 4, amount => 1},
	{binID => 6, nameID => 2303, name => 'Jacket', type => 5, amount => 1},
);
$Globals::items_control{jacket} = {keep => 0, storage => 0, sell => 1};
$Globals::char = FakeChar->new(name => 'Arkady', zeny => 5000, inv => \@inv, pos => {x => 150, y => 180}, pos_to => {x => 150, y => 180});
my $vera = {name => 'Vera', pos => {x => 156, y => 185}, pos_to => {x => 156, y => 185}};
$Globals::playersList = FakePlayers->new($vera);
require "$root/bots/plugins/economy/economy.pl";
my $ic = \%Globals::items_control;

# ---- склад ----
Plugins::call('mainLoop_post');
is($ic->{4001}{storage}, 1, 'карта — на склад');
is($ic->{4001}{sell}, 0, 'карта — не продавать');
is($ic->{984}{storage}, 1, 'руда из economy_storeIds — на склад');
ok(!exists $ic->{909}, 'обычный лут не трогаю');
is($ic->{jellopy}{sell}, 1, 'своя строка items_control остаётся');
is($ic->{1201}{storage}, 1, 'оружие не из списка продажи — на склад');
ok(!exists $ic->{2303}, 'броня из списка продажи (по имени) — продаётся как раньше');
ok(!exists $ic->{2301}, 'надетое не трогаю');

# ---- состояние ----
is(economy::itemCounts()->{501}, 35, 'счётчик красных зелий');
is(economy::itemCounts()->{601}, 0, 'нет крыльев — 0');
is(economy::vendStatus()->{can}, 0, 'без навыка лавки нельзя');

# ---- проверки передачи ----
my ($ok, $why) = economy::startGive({id => 1, to => 'Stranger', item => 501, amount => 5});
ok(!$ok && $why =~ /dealAuto_names/, 'чужому не отдаю');
($ok, $why) = economy::startGive({id => 1, to => 'Vera', item => 501, amount => 99});
ok(!$ok && $why =~ /нет столько/, 'больше, чем есть, не отдаю');
($ok, $why) = economy::startGive({id => 1, to => 'Vera', item => 'zeny', amount => 9000});
ok(!$ok && $why =~ /зени/, 'зени больше, чем есть, не отдаю');
($ok, $why) = economy::startGive({id => 1, to => 'Vera', item => 2301, amount => 1});
ok(!$ok, 'надетое не отдаю');

# ---- фактическое приближение: pos_to уже цель, но персонаж ещё далеко ----
$Globals::char->{pos} = {x => 150, y => 180};
$Globals::char->{pos_to} = {x => 156, y => 185};
$vera->{pos} = {x => 156, y => 185};
is(economy::distanceTo($vera), 6, 'цель маршрута не считается прибытием');
$Globals::char->{test_position} = {x => 156, y => 185};
is(economy::distanceTo($vera), 0, 'завершённое движение использует расчёт движка, не начало шага');
delete $Globals::char->{test_position};
$Globals::char->{pos_to} = {x => 150, y => 180};
$Globals::char->{pos} = {x => 150, y => 180};

# ---- успешная передача ----
($ok, $why) = economy::startGive({id => 7, to => 'Vera', item => 501, amount => 10});
ok($ok, "передача начата: $why");
@Commands::ran = ();
$Globals::char->{sitting} = 1;
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['stand', 'move 156 185'], 'далеко — встаю и иду к жителю');
$Globals::char->{pos} = {x => 155, y => 184};
$Globals::char->{pos_to} = {x => 155, y => 184};
@Commands::ran = ();
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['deal "Vera"'], 'рядом — предлагаю сделку');
is($economy::give{phase}, 'request', 'жду ответа');
%Globals::currentDeal = (name => 'Vera');
Plugins::call('engaged_deal', {name => 'Vera'});
is($economy::give{phase}, 'add', 'сделка открыта');
$economy::give{since} -= 2;
@Commands::ran = ();
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['deal add 3 10'], 'кладу 10 зелий');
$economy::give{since} -= 2;
@Commands::ran = ();
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['deal'], 'подтверждаю');
is($economy::give{phase}, 'final', 'жду вторую сторону');
@brainBridge::events = ();
%Globals::currentDeal = ();
Plugins::call('complete_deal');
is($brainBridge::events[0][0], 'deal_complete', 'сделка завершена — событие с партнёром');
is({@{$brainBridge::events[0]}[1 .. 4]}->{with}, 'Vera', 'партнёр сделки');
is($brainBridge::events[1][0], 'give_result', 'событие итога');
my %ev = @{$brainBridge::events[1]}[1 .. $#{$brainBridge::events[1]}];
ok($ev{ok}, 'передано');
is($ev{id}, 7, 'id действия мозга');
ok(!%economy::give, 'передача закрыта');

# ---- таймаут ----
($ok) = economy::startGive({id => 8, to => 'Vera', item => 'zeny', amount => 1000});
Plugins::call('mainLoop_post');
%Globals::outgoingDeal = (ID => 1);
$economy::give{since} -= 100;
@Commands::ran = (); @brainBridge::events = ();
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['deal no'], 'таймаут — отменяю сделку');
%ev = @{$brainBridge::events[0]}[1 .. $#{$brainBridge::events[0]}];
ok(!$ev{ok} && $ev{reason} =~ /таймаут/, 'итог: не передано, таймаут');
%Globals::outgoingDeal = ();

# ---- отказ сервера ----
economy::startGive({id => 9, to => 'Vera', item => 'zeny', amount => 1000});
Plugins::call('mainLoop_post');
@brainBridge::events = ();
Plugins::call('error_deal', {type => 0});
%ev = @{$brainBridge::events[0]}[1 .. $#{$brainBridge::events[0]}];
ok(!$ev{ok} && $ev{reason} =~ /далеко/, 'сервер: слишком далеко');

# ---- чужие предложения ----
%Globals::incomingDeal = (name => 'Stranger');
my %args;
@Commands::ran = ();
Plugins::call('deal_incoming', \%args);
is_deeply(\@Commands::ran, ['deal no'], 'чужому — отказ');
ok($args{return}, 'стандартный dealAuto пропущен');
%Globals::incomingDeal = (name => 'Vera');
%args = (); @Commands::ran = ();
Plugins::call('deal_incoming', \%args);
ok(!@Commands::ran && !$args{return}, 'жителю — принимает dealAuto 3');

# ---- ценное в state.items (ORG-030) ----
my $counts = economy::itemCounts();
is($counts->{4001}, 1, 'карта видна мозгу');
is($counts->{984}, 2, 'руда из economy_storeIds видна мозгу');
is($counts->{1201}, 1, 'не надетое оружие видно мозгу');
is($counts->{909}, 30, 'прочий лут виден мозгу (цену знает мозг)');
ok(!exists $counts->{2301}, 'надетое не считается');

sub evs { my ($kind) = @_; return map { +{@{$_}[1 .. $#$_]} } grep { $_->[0] eq $kind } @brainBridge::events; }

# ---- продажа жителю: одна сделка, оплата проверяется до обмена (ORG-033) ----
%Globals::incomingDeal = ();
$Globals::char->{pos} = {x => 155, y => 184};
$Globals::char->{pos_to} = {x => 155, y => 184};
($ok, $why) = economy::startGive({id => 20, to => 'Vera', item => 4001, amount => 1, price => 1250});
ok($ok, "продажа начата: $why");
is(economy::giveStatus()->{price}, 1250, 'в state видна цена');
@Commands::ran = ();
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['deal "Vera"'], 'рядом — сделка');
%Globals::currentDeal = (name => 'Vera');
Plugins::call('engaged_deal', {name => 'Vera'});
$economy::give{since} -= 2;
@Commands::ran = ();
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['deal add 0 1'], 'кладу карту');
$economy::give{since} -= 2;
@Commands::ran = ();
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['deal'], 'подтверждаю своё');
$Globals::currentDeal{other_zeny} = 1000;              # покупатель положил меньше обещанного
$Globals::currentDeal{other_finalize} = 1;
@Commands::ran = (); @brainBridge::events = ();
Plugins::call('finalized_deal', {name => 'Vera'});
is_deeply(\@Commands::ran, ['deal no'], 'недоплата — отменяю до обмена');
my ($res) = evs('give_result');
ok(!$res->{ok} && $res->{reason} =~ /1000 зени из 1250/, 'итог: не продано, причина');
is($res->{price}, 1250, 'итог с ценой');
%Globals::currentDeal = ();

($ok) = economy::startGive({id => 21, to => 'Vera', item => 4001, amount => 1, price => 1250});
Plugins::call('mainLoop_post');
%Globals::currentDeal = (name => 'Vera');
Plugins::call('engaged_deal', {name => 'Vera'});
$economy::give{since} -= 2; Plugins::call('mainLoop_post');
$economy::give{since} -= 2; Plugins::call('mainLoop_post');
%Globals::currentDeal = (name => 'Vera', other_zeny => 1250, other_finalize => 1);
@Commands::ran = (); @brainBridge::events = ();
Plugins::call('finalized_deal', {name => 'Vera'});
ok(!@Commands::ran, 'оплата полная — обмен завершит dealAuto');
%Globals::currentDeal = ();
Plugins::call('complete_deal');
($res) = evs('give_result');
ok($res->{ok}, 'продано');
is($res->{paid}, 1250, 'оплата в итоге');
($ok, $why) = economy::startGive({id => 22, to => 'Vera', item => 'zeny', amount => 10, price => 5});
ok(!$ok, 'зени за зени не продаю');

# ---- покупка у жителя ----
($ok, $why) = economy::startBuy({id => 30, from => 'Stranger', item => 4001, amount => 1, price => 100});
ok(!$ok, 'у чужого не покупаю');
($ok, $why) = economy::startBuy({id => 30, from => 'Vera', item => 4001, amount => 1, price => 999999});
ok(!$ok && $why =~ /зени/, 'дороже, чем есть зени, — нет');
($ok, $why) = economy::startBuy({id => 31, from => 'Vera', item => 4002, amount => 1, price => 1200});
ok($ok, "жду продавца: $why");
is(economy::buyStatus()->{phase}, 'wait', 'статус покупки в state');
($ok) = economy::startGive({id => 32, to => 'Vera', item => 501, amount => 1});
ok(!$ok, 'пока жду продавца — не отдаю');
@Commands::ran = ();
%Globals::currentDeal = (name => 'Vera');
Plugins::call('engaged_deal', {name => 'Vera'});
is_deeply(\@Commands::ran, ['deal add z 1200'], 'сделка с продавцом — кладу зени');
$Globals::currentDeal{other} = {4002 => {amount => 0}};
@Commands::ran = (); @brainBridge::events = ();
Plugins::call('finalized_deal', {name => 'Vera'});
is_deeply(\@Commands::ran, ['deal no'], 'товара нет в окне сделки — отменяю');
($res) = evs('buy_result');
ok(!$res->{ok}, 'покупка не состоялась');
ok(!%economy::buy, 'покупка закрыта');
%Globals::currentDeal = ();

economy::startBuy({id => 33, from => 'Vera', item => 4002, amount => 1, price => 1200});
%Globals::currentDeal = (name => 'Vera');
Plugins::call('engaged_deal', {name => 'Vera'});
$Globals::currentDeal{other} = {4002 => {amount => 1, nameID => 4002}};
@Commands::ran = (); @brainBridge::events = ();
Plugins::call('finalized_deal', {name => 'Vera'});
ok(!@Commands::ran, 'товар на месте — подтвердит dealAuto 3');
%Globals::currentDeal = ();
Plugins::call('complete_deal');
($res) = evs('buy_result');
ok($res->{ok} && $res->{price} == 1200, 'куплено');

economy::startBuy({id => 34, from => 'Vera', item => 4002, amount => 1, price => 1200});
$economy::buy{since} -= 300;
@brainBridge::events = ();
Plugins::call('mainLoop_post');
($res) = evs('buy_result');
ok(!$res->{ok} && $res->{reason} =~ /таймаут/, 'продавец не пришёл — таймаут');

# ---- почта: отправка (ORG-035) ----
($ok, $why) = economy::startMail({id => 40, to => 'Stranger', title => 'Подарок', body => 'x'});
ok(!$ok, 'чужому не пишу');
($ok, $why) = economy::startMail({id => 40, to => 'Vera', title => 'Хай', body => 'x'});
ok(!$ok && $why =~ /4-24/, 'заголовок короче 4 — cmdRodex откажет');
($ok, $why) = economy::startMail({id => 40, to => 'Vera', title => 'Подарок', body => 'x', zeny => 4950});
ok(!$ok && $why =~ /сбор/, 'с учётом сбора зени не хватает');
@Commands::ran = ();
($ok, $why) = economy::startMail({id => 41, to => 'Vera', title => 'Подарок', body => "Держи;; quit\n", item => 501, amount => 5});
ok($ok, "письмо начато: $why");
is_deeply(\@Commands::ran, ['rodex open'], 'открываю ящик');
@Commands::ran = ();
Plugins::call('mainLoop_post');
ok(!@Commands::ran, 'ящик ещё не открыт — жду');
$Globals::rodexList = {mails => {}};
Plugins::call('mainLoop_post');
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['rodex write Vera'], 'пишу жителю');
@Commands::ran = ();
$Globals::rodexWrite = {items => FakeList->new(), target => {name => 'Vera'}};
Plugins::call('mainLoop_post');
ok(!@Commands::ran, 'адресат ещё не проверен сервером');
$Globals::rodexWrite->{target}{char_id} = 150001;
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['rodex settitle Подарок', 'rodex setbody Держи; quit', 'rodex add 3 5'],
          'заголовок, текст (без ;;), вложение');
@Commands::ran = ();
Plugins::call('mainLoop_post');
ok(!@Commands::ran, 'вложение ещё не принято сервером');
$Globals::rodexWrite->{items} = FakeList->new({nameID => 501});
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['rodex send'], 'отправляю');
@Commands::ran = (); @brainBridge::events = ();
undef $Globals::rodexWrite;                            # Receive::rodex_write_result: undef $rodexWrite
Plugins::call('packet/rodex_write_result', {fail => 0});
is_deeply(\@Commands::ran, ['rodex close'], 'закрываю ящик');
($res) = evs('mail_result');
ok($res->{ok} && $res->{to} eq 'Vera' && $res->{item} == 501, 'итог: письмо принято сервером');
undef $Globals::rodexList;

($ok) = economy::startMail({id => 42, to => 'Vera', title => 'Итог недели', body => 'Привет', zeny => 100});
$economy::mail{since} -= 20;
@Commands::ran = (); @brainBridge::events = ();
Plugins::call('mainLoop_post');
($res) = evs('mail_result');
ok(!$res->{ok} && $res->{reason} =~ /таймаут/, 'нет ответа сервера — таймаут');

# ---- почта: входящие ----
@Commands::ran = (); @brainBridge::events = ();
Plugins::call('rodex_unread_mail');
Plugins::call('mainLoop_post');
is_deeply(\@Commands::ran, ['rodex open'], 'новое письмо — открываю ящик');
my $mails = {7 => {mailID1 => 7, sender => 'Vera', title => 'Подарок', isRead => 0, attach => 'i', page_index => 0},
             8 => {mailID1 => 8, sender => 'Stranger', title => 'Spam', isRead => 0, attach => 'z', page_index => 1},
             9 => {mailID1 => 9, sender => 'Vera', title => 'Old', isRead => 1, page_index => 2}};
$Globals::rodexList = {mails => $mails};
@Commands::ran = ();
Plugins::call('rodex_mail_list', {mails => $mails});
my @got = evs('mail_received');
is(scalar @got, 1, 'сообщаю только непрочитанное от жителя');
is_deeply([@{$got[0]}{qw(mail_id from title attach)}], [7, 'Vera', 'Подарок', 'i'], 'письмо 7 от Vera');
is_deeply(\@Commands::ran, ['rodex close'], 'проверка окончена — закрываю');
undef $Globals::rodexList;
@brainBridge::events = ();
Plugins::call('rodex_mail_list', {mails => $mails});
ok(!evs('mail_received'), 'о том же письме второй раз не сообщаю');

# ---- почта: забрать вложение ----
@Commands::ran = ();
($ok) = economy::startMailTake({id => 50, mail_id => 7});
is_deeply(\@Commands::ran, ['rodex open'], 'забрать: открываю ящик');
$Globals::rodexList = {mails => $mails};
@Commands::ran = ();
Plugins::call('rodex_mail_list', {mails => $mails});
is_deeply(\@Commands::ran, ['rodex read 0'], 'короткий mail_id -> номер в списке (cmdRodex)');
@Commands::ran = ();
Plugins::call('rodex_mail', {mailID => 7, from => 'Vera', zeny => 300, items => [{nameID => 501, amount => 5}]});
is_deeply(\@Commands::ran, ['rodex getzeny 0'], 'сначала зени');
@Commands::ran = ();
Plugins::call('packet/rodex_get_zeny', {fail => 0, mailID1 => 7});
is_deeply(\@Commands::ran, ['rodex getitems 0'], 'потом предметы');
@Commands::ran = (); @brainBridge::events = ();
Plugins::call('packet/rodex_get_item', {fail => 0, mailID1 => 7});
is_deeply(\@Commands::ran, ['rodex close'], 'закрываю ящик');
($res) = evs('mail_taken');
ok($res->{ok} && $res->{zeny} == 300 && $res->{items}[0]{id} == 501, 'итог: забрал 300 зени и зелья');
undef $Globals::rodexList;

economy::startMailTake({id => 51, mail_id => 8});
$Globals::rodexList = {mails => $mails};
@brainBridge::events = ();
Plugins::call('rodex_mail_list', {mails => $mails});
($res) = evs('mail_taken');
ok(!$res->{ok} && $res->{reason} =~ /не от жителя/, 'письмо чужого не забираю');
undef $Globals::rodexList;
is(economy::mailRef(5), undef, 'неизвестное письмо — без номера');

# ---- лавка (ORG-034) ----
($ok, $why) = economy::setupShop({title => 'Лавка', items => [{id => 984, price => 700, amount => 2}]});
ok(!$ok && $why =~ /навыка/, 'без навыка лавки — нет');
$Globals::char->{skills} = {MC_VENDING => {lv => 3}, MC_OVERCHARGE => {lv => 5}};
$Globals::char->{cart} = 1;
$Globals::char->{cartItems} = [{nameID => 4001, name => 'Poring Card', amount => 1}];
my $v = economy::vendStatus();
is_deeply([@$v{qw(can overcharge slots)}], [1, 5, 5], 'лавка: навык, Overcharge, мест MC_VENDING + 2');
is_deeply($v->{cart}, {4001 => 1}, 'тележка в state');
($ok, $why) = economy::setupShop({title => 'Лавка #Arkady', items => [{id => 4001, price => 1250, amount => 1},
                                                                     {id => 984, price => 700, amount => 2},
                                                                     {id => '1; quit', price => 1, amount => 1}]});
ok($ok, "лавка подготовлена: $why");
is($ic->{984}{cart_add}, 1, 'руда — в тележку');
is($ic->{984}{sell}, 0, 'и не продавать NPC');
is($Globals::shop{title_line}, 'Лавка Arkady', 'название без #');
is_deeply($Globals::shop{items}, [{name => 'Poring Card', price => 1250, amount => 1}], 'в %shop — то, что уже в тележке');
push @{$Globals::char->{cartItems}}, {nameID => 984, name => 'Oridecon', amount => 2};
economy::buildShop();
is(scalar @{$Globals::shop{items}}, 2, 'руда в тележке — тоже товар');

# ---- метрики продаж ----
@brainBridge::events = ();
$Globals::char->{zeny} = 1000;
Plugins::call('AI_sell_auto');
$Globals::char->{zeny} = 1600;
Plugins::call('AI_sell_auto');
Plugins::call('AI_sell_auto_completed', {});
($res) = evs('npc_sold');
is($res->{zeny}, 600, 'выручка автопродажи NPC');
Plugins::call('vending_item_sold', {amount => 1, zenyEarned => 1250, vendArticle => {name => 'Poring Card', nameID => 4001}});
($res) = evs('vend_sold');
is($res->{zeny}, 1250, 'продажа из лавки');

# ---- sinks: покупки NPC (buyAuto) — сток для M18 (Т-46) ----
@brainBridge::events = ();
$Globals::char->{zeny} = 2000;
Plugins::call('AI_buy_auto');
$Globals::char->{zeny} = 1700;
Plugins::call('AI_buy_auto');                       # такт последовательности — замер не сдвигается
$Globals::char->{zeny} = 1400;
Plugins::call('AI_buy_auto_completed', {});
($res) = evs('npc_bought');
is($res->{zeny}, 600, 'sinks: потрачено на автозакупку NPC');
@brainBridge::events = ();
Plugins::call('AI_buy_auto');
$Globals::char->{zeny} = 1500;
Plugins::call('AI_buy_auto_completed', {});
is(scalar(evs('npc_bought')), 0, 'sinks: зени не убыли — нет события');
Plugins::call('AI_buy_auto_completed', {});
is(scalar(evs('npc_bought')), 0, 'sinks: без начала — нет события');

done_testing();
