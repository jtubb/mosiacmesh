#!/bin/bash
export THEOS=$HOME/theos
SRC="$(cd "$(dirname "$0")" && pwd)"
rm -rf ~/mmwsc && mkdir -p ~/mmwsc
cp "$SRC"/Tweak.x "$SRC"/Makefile "$SRC"/mmwsc.plist "$SRC"/control ~/mmwsc/ 2>/dev/null
sed -i 's/\r$//' ~/mmwsc/Makefile ~/mmwsc/Tweak.x 2>/dev/null
cd ~/mmwsc && make clean >/dev/null 2>&1 && make 2>&1 | grep -viE "tbd file|deprecated|Simulator" | tail -20
NM=/home/jtubb/theos/toolchain/linux/iphone/bin/llvm-nm
DY=$(find ~/mmwsc -name '*.dylib' -not -path '*dSYM*' | head -1)
echo "== C++ unwind? =="; "$NM" -u "$DY" 2>/dev/null | grep -iE "Unwind|gxx_personality" || echo "  CLEAN"
echo "DYLIB=$DY ($(stat -c%s "$DY" 2>/dev/null)b)"
