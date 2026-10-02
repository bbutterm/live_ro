package Plugins; my %h; sub register {} sub addHooks { push @{$h{$_->[0]}}, $_->[1] for @_; 1 } sub delHooks {}
sub call { my $n = shift; $_->($n, @_) for @{$h{$n} || []} } 1;
