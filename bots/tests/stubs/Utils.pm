# Позиция, рассчитанная движком; тест задаёт её отдельно от начала/цели шага.
package Utils;
sub calcPosition { $_[0]{test_position} || $_[0]{pos} }
1;
