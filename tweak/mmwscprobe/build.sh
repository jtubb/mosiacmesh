#!/bin/bash
export THEOS=$HOME/theos
SRC="$(cd "$(dirname "$0")" && pwd)"
rm -rf ~/mmwscprobe && mkdir -p ~/mmwscprobe
cp "$SRC"/Tweak.x "$SRC"/Makefile "$SRC"/mmwscprobe.plist "$SRC"/control ~/mmwscprobe/ 2>/dev/null
sed -i 's/\r$//' ~/mmwscprobe/Makefile ~/mmwscprobe/Tweak.x 2>/dev/null
cd ~/mmwscprobe && make clean >/dev/null 2>&1 && make 2>&1 | grep -viE "tbd file|deprecated|Simulator" | tail -20
NM=/home/jtubb/theos/toolchain/linux/iphone/bin/llvm-nm
DY=$(find ~/mmwscprobe -name '*.dylib' -not -path '*dSYM*' | head -1)
echo "== C++ unwind? (want NONE) =="; "$NM" -u "$DY" 2>/dev/null | grep -iE "Unwind|gxx_personality" || echo "  CLEAN"
echo "== undefined syms =="; "$NM" -u "$DY" 2>/dev/null | head -30
echo "DYLIB=$DY ($(stat -c%s "$DY" 2>/dev/null)b)"
