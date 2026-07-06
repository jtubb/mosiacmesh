#!/bin/bash
# Build main's milestone mmvideo engine (no player-reuse) in WSL/theos.
set -e
export THEOS=$HOME/theos
SRC="${1:-/mnt/c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh/tweak/mmvideo}"
OUTNAME="${2:-mmvideo_milestone.dylib}"
WINTWEAK=/mnt/c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh/tweak/mmvideo
rm -rf ~/mmvideo && mkdir -p ~/mmvideo
cp "$SRC"/Tweak.x "$SRC"/Makefile "$SRC"/mmvideo.plist "$SRC"/control \
   "$SRC"/MMTransplantEngine.m "$SRC"/MMTransplantEngine.h "$SRC"/mmurl.h "$SRC"/mmbuiltins.c ~/mmvideo/
cd ~/mmvideo
# strip CR from Windows-checked-out sources
for f in *.x *.m *.c *.h Makefile control; do sed -i 's/\r$//' "$f" 2>/dev/null || true; done
make clean >/dev/null 2>&1 || true
echo "=== make ==="
make 2>&1 | grep -viE "tbd file|deprecated|Simulator" | tail -25
DY=$(find ~/mmvideo -name '*.dylib' -not -path '*dSYM*' | head -1)
echo "DYLIB=$DY"
if [ -n "$DY" ]; then
  ls -la "$DY"
  strings "$DY" | grep -oE 'build=[A-Za-z0-9._-]+' | head -1 || echo "(no build tag)"
  echo "RECREATE-LAYER in dylib? $(strings "$DY" | grep -c RECREATE-LAYER) (want 0 = milestone)"
  cp "$DY" "$WINTWEAK/$OUTNAME"
  echo "COPIED_TO=$WINTWEAK/$OUTNAME"
fi
