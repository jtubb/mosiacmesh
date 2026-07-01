#!/bin/bash
export THEOS=$HOME/theos
SRC="$(cd "$(dirname "$0")" && pwd)"
rm -rf ~/mmwsprobe && mkdir -p ~/mmwsprobe
cp "$SRC"/Tweak.x "$SRC"/Makefile "$SRC"/mmwsprobe.plist "$SRC"/control ~/mmwsprobe/ 2>/dev/null
sed -i 's/\r$//' ~/mmwsprobe/Makefile ~/mmwsprobe/Tweak.x 2>/dev/null
cd ~/mmwsprobe && make clean >/dev/null 2>&1 && make 2>&1 | grep -viE "tbd file|deprecated|Simulator" | tail -25
NM=/home/jtubb/theos/toolchain/linux/iphone/bin/llvm-nm
DY=$(find ~/mmwsprobe -name '*.dylib' -not -path '*dSYM*' | head -1)
echo "== C++ unwind symbols? (want NONE) =="; "$NM" -u "$DY" 2>/dev/null | grep -iE "Unwind|gxx_personality" || echo "  CLEAN (plain ObjC)"
echo "== undefined symbols (should be libSystem/objc/substrate only) =="; "$NM" -u "$DY" 2>/dev/null | head -40
echo "DYLIB=$DY"
