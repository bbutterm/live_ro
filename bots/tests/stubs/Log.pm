package Log; use Exporter 'import'; our @EXPORT_OK = qw(message warning error); our @lines;
sub message { push @lines, $_[0] } sub warning { push @lines, "WARN: $_[0]" } sub error { push @lines, "ERR: $_[0]" } 1;
