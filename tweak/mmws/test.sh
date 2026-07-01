#!/bin/sh
# Host-compile + run the pure RFC-6455 unit tests (no device). Run via:
#   wsl -d Ubuntu -- bash "/mnt/c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh/tweak/mmws/test.sh"
set -e
cd "$(dirname "$0")"
CC="${CC:-gcc}"
"$CC" -Wall -Wextra -std=c99 -O2 -o /tmp/mmws_test mmws.c mmws_test.c
/tmp/mmws_test
