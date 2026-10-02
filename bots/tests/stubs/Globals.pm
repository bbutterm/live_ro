package Globals; use Exporter 'import';
our @EXPORT_OK = qw($char $field $net $monstersList $playersList %config %jobs_lut %sex_lut @lastpm $messageSender
                    %items_control %currentDeal %outgoingDeal %incomingDeal $shopstarted $accountID %ai_v %friends @friendsID @chars %timeout %pet);
our ($char, $field, $net, $monstersList, $playersList, %config, %jobs_lut, %sex_lut, @lastpm, $messageSender,
     %items_control, %currentDeal, %outgoingDeal, %incomingDeal, $shopstarted, $accountID, %ai_v, %friends, @friendsID, @chars, %timeout, %pet); 1;
# society: чат-комната (brainBridge chat_room, ORG-026) — $currentChatRoom (ID комнаты, где я) и %chatRooms
package Globals; push @EXPORT_OK, qw($currentChatRoom %chatRooms); our ($currentChatRoom, %chatRooms); 1;
