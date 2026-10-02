package Skill; sub new { my ($c, %a) = @_; bless {handle => $a{handle}}, $c } sub getHandle { $_[0]{handle} } 1;
