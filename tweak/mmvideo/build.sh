#!/bin/bash
export THEOS=$HOME/theos
SRC="$(cd "$(dirname "$0")" && pwd)"
rm -rf ~/mmvideo && mkdir -p ~/mmvideo
cp "$SRC"/Tweak.xm "$SRC"/Makefile "$SRC"/mmvideo.plist "$SRC"/control ~/mmvideo/ 2>/dev/null
cp "$SRC"/MMTransplantEngine.* "$SRC"/mmurl.h ~/mmvideo/ 2>/dev/null
sed -i 's/\r$//' ~/mmvideo/Makefile ~/mmvideo/Tweak.xm 2>/dev/null
cd ~/mmvideo && make clean >/dev/null 2>&1 && make 2>&1 | grep -viE "tbd file|deprecated|Simulator" | tail -20
find ~/mmvideo -name '*.dylib' -not -path '*dSYM*'
