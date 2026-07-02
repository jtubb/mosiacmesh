#!/bin/bash
# Build the mmws bridge tweak. Generates mmws_js.h from bridge-shim.js + mmws.js (DRY: the JS
# is not duplicated in C — it's embedded at build time), then builds via Theos.
export THEOS=$HOME/theos
SRC="$(cd "$(dirname "$0")" && pwd)"
rm -rf ~/mmws && mkdir -p ~/mmws
cp "$SRC"/Tweak.x "$SRC"/Makefile "$SRC"/mmws.plist "$SRC"/control ~/mmws/ 2>/dev/null
cp "$SRC"/mmws.c "$SRC"/mmws.h "$SRC"/mmws_sm.c "$SRC"/mmws_sm.h "$SRC"/mmwsconn.c "$SRC"/mmwsconn.h "$SRC"/mmwsbuiltins.c ~/mmws/ 2>/dev/null

# embed bridge-shim.js + mmws.js as a C string (json.dumps output is a valid C string literal)
python3 -c "
import json
shim = open('$SRC/bridge-shim.js').read()
poly = open('$SRC/mmws.js').read()
print('static const char * const MMWS_JS = ' + json.dumps(shim + chr(10) + poly) + ';')
" > ~/mmws/mmws_js.h || { echo 'ERROR: mmws_js.h generation failed (python3?)'; exit 1; }
echo "mmws_js.h: $(wc -c < ~/mmws/mmws_js.h) bytes"

sed -i 's/\r$//' ~/mmws/Makefile ~/mmws/Tweak.x 2>/dev/null
cd ~/mmws && make clean >/dev/null 2>&1 && make 2>&1 | grep -viE "tbd file|deprecated|Simulator" | tail -30
NM=/home/jtubb/theos/toolchain/linux/iphone/bin/llvm-nm
DY=$(find ~/mmws -name '*.dylib' -not -path '*dSYM*' | head -1)
echo "== C++ unwind symbols? (want NONE) =="; "$NM" -u "$DY" 2>/dev/null | grep -iE "Unwind|gxx_personality" || echo "  CLEAN (plain ObjC)"
echo "== undefined symbols =="; "$NM" -u "$DY" 2>/dev/null | head -60
echo "DYLIB=$DY  ($(stat -c%s "$DY" 2>/dev/null) bytes)"
