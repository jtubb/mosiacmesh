#!/usr/bin/env python
"""Add WebKit media-autoplay keys to a pulled MobileSafari prefs plist.

Disables the "media playback requires a user gesture" gate so <video> can
autoplay/play() without a tap on iOS 5.1.1 MobileSafari. Writes the result in
the SAME plist format (binary vs XML) as the original.

Usage:
    python build_safari_plist.py com.apple.mobilesafari.original.plist com.apple.mobilesafari.new.plist
"""
import plistlib
import sys


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "com.apple.mobilesafari.original.plist"
    dst = sys.argv[2] if len(sys.argv) > 2 else "com.apple.mobilesafari.new.plist"

    with open(src, "rb") as f:
        raw = f.read()
    fmt = plistlib.FMT_BINARY if raw[:8] == b"bplist00" else plistlib.FMT_XML
    d = plistlib.loads(raw)

    changes = {
        # The gesture gate WebCore/HTMLMediaElement checked in this era.
        "WebKitMediaPlaybackRequiresUserGesture": False,
        # Allow inline (non-fullscreen) playback too.
        "WebKitMediaPlaybackAllowsInline": True,
    }
    before = {k: d.get(k, "<unset>") for k in changes}
    d.update(changes)

    with open(dst, "wb") as f:
        plistlib.dump(d, f, fmt=fmt)

    print("format:", "binary" if fmt == plistlib.FMT_BINARY else "xml")
    for k in changes:
        print(f"  {k}: {before[k]!r} -> {d[k]!r}")
    print("wrote:", dst)


if __name__ == "__main__":
    main()
