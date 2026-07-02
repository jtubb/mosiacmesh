#!/bin/bash
export THEOS=$HOME/theos
SRC="$(cd "$(dirname "$0")" && pwd)"
MMWS="$SRC/../mmws"
rm -rf ~/mmwsc && mkdir -p ~/mmwsc
cp "$SRC"/Tweak.x "$SRC"/Makefile "$SRC"/mmwsc.plist "$SRC"/control ~/mmwsc/ 2>/dev/null
cp "$MMWS"/mmws.c "$MMWS"/mmws.h "$MMWS"/mmws_sm.c "$MMWS"/mmws_sm.h \
   "$MMWS"/mmwsconn.c "$MMWS"/mmwsconn.h "$MMWS"/mmwsbuiltins.c ~/mmwsc/ 2>/dev/null
sed -i 's/\r$//' ~/mmwsc/*.x ~/mmwsc/*.c ~/mmwsc/*.h ~/mmwsc/Makefile 2>/dev/null
cd ~/mmwsc && make clean >/dev/null 2>&1 && make 2>&1 | grep -viE "tbd file|deprecated|Simulator" | tail -25
NM=/home/jtubb/theos/toolchain/linux/iphone/bin/llvm-nm
DY=$(find ~/mmwsc -name '*.dylib' -not -path '*dSYM*' | head -1)
echo "== C++ unwind? =="; "$NM" -u "$DY" 2>/dev/null | grep -iE "Unwind|gxx_personality" || echo "  CLEAN"
# CRITICAL: ad-hoc sign. An UNSIGNED dylib is amfi-SIGKILLed at load in dyld with NO message
# (looks like a bind failure but is not). The small exposure build slipped through; the larger
# transplant build did not. Always ship signed; re-sign on the device after scp as a backstop.
ldid -S "$DY" 2>/dev/null && echo "  SIGNED (ldid -S)" || echo "  !! ldid sign FAILED — will amfi-crash on load"
echo "DYLIB=$DY ($(stat -c%s "$DY" 2>/dev/null)b)"
