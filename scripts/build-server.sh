#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../upstream/rathena"
./configure --enable-packetver=20180620 --disable-lto
make -j1 login char map
printf 'RATHENA_BUILD_OK\n'
