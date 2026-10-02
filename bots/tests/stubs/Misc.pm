package Misc; our (@modified, @created); our $createResult = 1;
sub configModify { my ($k, $v) = @_; $Globals::config{$k} = $v; push @modified, $k }
sub createCharacter { push @created, [@_]; return $createResult } 1;
