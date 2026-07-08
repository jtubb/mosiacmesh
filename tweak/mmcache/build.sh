#!/bin/bash
# Build the mmcache-spike bridge tweak (Plan 2 de-risk). Mirrors tweak/mmws/build.sh.
export THEOS=$HOME/theos
SRC="$(cd "$(dirname "$0")" && pwd)"
rm -rf ~/mmcache && mkdir -p ~/mmcache
cp "$SRC"/Tweak.x "$SRC"/Makefile "$SRC"/mmcache.plist "$SRC"/control ~/mmcache/ 2>/dev/null
sed -i 's/\r$//' ~/mmcache/Makefile ~/mmcache/Tweak.x 2>/dev/null
cd ~/mmcache && make clean >/dev/null 2>&1 && make 2>&1 | grep -viE "tbd file|deprecated|Simulator" | tail -25
NM=/home/jtubb/theos/toolchain/linux/iphone/bin/llvm-nm
DY=$(find ~/mmcache -name '*.dylib' -not -path '*dSYM*' | head -1)
echo "== C++ unwind symbols? (want NONE) =="; "$NM" -u "$DY" 2>/dev/null | grep -iE "Unwind|gxx_personality" || echo "  CLEAN (plain ObjC)"
echo "== ObjC classes defined? (want NONE — static class SIGKILLs load) =="; "$NM" "$DY" 2>/dev/null | grep -iE "OBJC_CLASS_\\$" || echo "  NONE"
echo "== undefined symbols (should be libSystem/ObjC runtime only) =="; "$NM" -u "$DY" 2>/dev/null | head -50
echo "DYLIB=$DY  ($(stat -c%s "$DY" 2>/dev/null) bytes)"
