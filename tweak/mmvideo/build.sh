#!/bin/bash
export THEOS=$HOME/theos
SRC="$(cd "$(dirname "$0")" && pwd)"
rm -rf ~/mmvideo && mkdir -p ~/mmvideo
cp "$SRC"/Tweak.x "$SRC"/Makefile "$SRC"/mmvideo.plist "$SRC"/control ~/mmvideo/ 2>/dev/null
cp "$SRC"/MMTransplantEngine.m "$SRC"/MMTransplantEngine.h "$SRC"/mmurl.h "$SRC"/mmbuiltins.c ~/mmvideo/ 2>/dev/null
sed -i 's/\r$//' ~/mmvideo/Makefile ~/mmvideo/Tweak.x 2>/dev/null
cd ~/mmvideo && make clean >/dev/null 2>&1 && make 2>&1 | grep -viE "tbd file|deprecated|Simulator" | tail -20
NM=/home/jtubb/theos/toolchain/linux/iphone/bin/llvm-nm
DY=$(find ~/mmvideo -name '*.dylib' -not -path '*dSYM*' | head -1)
echo "== C++ unwind symbols? (want NONE) =="; "$NM" -u "$DY" | grep -iE "Unwind|gxx_personality" || echo "  CLEAN (plain ObjC)"
echo "== remaining undefined compiler-rt builtins? (want NONE) =="; "$NM" -u "$DY" | grep -iE "^_+(float|fix|div|mul|add|sub|trunc|extend|cmp)[a-z]*[0-9]" || echo "  NONE (builtins resolved)"
echo "DYLIB=$DY"
