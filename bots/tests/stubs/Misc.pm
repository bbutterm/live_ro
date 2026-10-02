package Misc; our @modified; sub configModify { my ($k, $v) = @_; $Globals::config{$k} = $v; push @modified, $k } 1;
