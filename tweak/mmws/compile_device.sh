#!/bin/sh
# Syntax-check the device-only CFStream layer against the iPhoneOS SDK (no host runtime).
# Run: wsl -d Ubuntu -- bash "/mnt/c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh/tweak/mmws/compile_device.sh"
cd "$(dirname "$0")"
CLANG="$HOME/theos/toolchain/linux/iphone/bin/clang"
[ -x "$CLANG" ] || CLANG="$(find "$HOME/theos/toolchain" -name clang -type f 2>/dev/null | head -1)"
SDK="$HOME/theos/sdks/iPhoneOS9.3.sdk"
[ -d "$SDK" ] || SDK="$(find "$HOME/theos/sdks" -maxdepth 1 -name 'iPhoneOS*.sdk' 2>/dev/null | sort | tail -1)"
echo "clang: $CLANG"
echo "sdk:   $SDK"
"$CLANG" -target armv7-apple-ios5.1 -isysroot "$SDK" -Wall -Wextra -std=c99 -fsyntax-only mmwsconn.c
rc=$?
echo "mmwsconn.c syntax-check exit: $rc"
exit $rc
