#!/bin/sh
# Host-compile + run the pure RFC-6455 unit tests (no device). Run via:
#   wsl -d Ubuntu -- bash "/mnt/c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh/tweak/mmws/test.sh"
cd "$(dirname "$0")"
CC="${CC:-gcc}"
rc=0
"$CC" -Wall -Wextra -std=c99 -O2 -o /tmp/mmws_test mmws.c mmws_test.c || exit 1
"$CC" -Wall -Wextra -std=c99 -O2 -D_GNU_SOURCE -o /tmp/mmws_sm_test mmws.c mmws_sm.c mmws_sm_test.c || exit 1
echo "== mmws pure functions =="; /tmp/mmws_test    || rc=1
echo ""
echo "== mmws_sm state machine ==";  /tmp/mmws_sm_test || rc=1
exit $rc
