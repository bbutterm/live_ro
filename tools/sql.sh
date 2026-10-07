#!/bin/sh
set -eu
[ "$#" -eq 1 ] || exit 2
case "$1" in SELECT\ *|select\ *) ;; *) printf 'SELECT only\n' >&2; exit 2;; esac
printf '%s\n' "$1" | mariadb --defaults-extra-file=/root/ragnarok/run/private/reader.cnf -NBr 2>/dev/null
