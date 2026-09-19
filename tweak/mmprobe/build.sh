#!/bin/bash
# Build BOTH probe arms from one source. The control arm exists so a failed subclass
# arm is attributable: audit 1.16 shows this loader rejects freshly built dylibs for
# reasons no symbol diff explains, so "it did not load" alone proves nothing.
#
# The arms are produced by STRIPPING marked regions, not by #ifdef -- the Logos
# preprocessor runs before the C preprocessor, so an #ifdef'd %subclass still emits
# its scaffolding and the control arm would not be a control.
export THEOS=$HOME/theos
SRC="$(cd "$(dirname "$0")" && pwd)"

build_arm() {           # $1 = arm name, $2 = sed program applied to Tweak.x
  rm -rf ~/mmprobe && mkdir -p ~/mmprobe
  cp "$SRC"/Makefile "$SRC"/mmprobe.plist "$SRC"/control ~/mmprobe/
  sed -e 's/\r$//' $2 "$SRC"/Tweak.x > ~/mmprobe/Tweak.x
  sed -i 's/\r$//' ~/mmprobe/Makefile
  cd ~/mmprobe && make clean >/dev/null 2>&1
  if ! make 2>&1 | grep -iE "error|Linking|Signing" | grep -viE "tbd file|Simulator"; then :; fi
  DY=$(find ~/mmprobe -name '*.dylib' -not -path '*dSYM*' | head -1)
  if [ -z "$DY" ]; then echo "  arm=$1 BUILD FAILED"; return 1; fi
  cp "$DY" "$SRC/mmprobe_$1.dylib"
  echo "  arm=$1 bytes=$(stat -c%s "$DY") md5=$(md5sum "$DY" | cut -d' ' -f1)"
  NM=$HOME/theos/toolchain/linux/iphone/bin/llvm-nm
  echo "  objc/runtime symbols it needs:"
  "$NM" -u "$DY" 2>/dev/null | grep -iE "objc|class_|sel_" | sed 's/^/    /'
}

STRIP_HOOK='-e /PROBE-HOOK-BEGIN/,/PROBE-HOOK-END/d -e /PROBE-HOOKINIT-BEGIN/,/PROBE-HOOKINIT-END/d'
STRIP_SUB='-e /PROBE-SUBCLASS-BEGIN/,/PROBE-SUBCLASS-END/d -e /PROBE-EXERCISE-BEGIN/,/PROBE-EXERCISE-END/d'

echo "===== CONTROL arm (no hook, no class) ====="
build_arm control "$STRIP_HOOK $STRIP_SUB"
echo "===== HOOK arm (%hook only -- pulls in CydiaSubstrate/MSHookMessageEx) ====="
build_arm hook "$STRIP_SUB"
echo "===== SUBCLASS arm (runtime class + method + property) ====="
build_arm subclass "$STRIP_HOOK"
