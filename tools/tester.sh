#!/bin/sh
# Dedicated lab tmux server only; never selects another user's session.
set -eu
[ "$#" -eq 1 ] || { printf 'usage: tools/tester.sh "OpenKore command"\n' >&2; exit 2; }
tmux -L ro-residents has-session -t tester
tmux -L ro-residents send-keys -t tester -l -- "$1"
tmux -L ro-residents send-keys -t tester Enter
