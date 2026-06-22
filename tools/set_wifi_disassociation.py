"""Host-side helper: set DisassociationInterval in a (binary) com.apple.wifi.plist.

Used by onboard_devices.ps1 (and re-runnable by hand). The iPad-1 has no Python,
so the edit is done on the host against a scp'd copy, then scp'd back: load the
binary plist, set DisassociationInterval (float seconds), preserve every other
key, write it back as a binary plist. A `defaults write` is avoided on purpose —
it is cfprefsd-mediated and may not land on the raw file `wifid` reads.

Usage:  python tools/set_wifi_disassociation.py <path-to-com.apple.wifi.plist> [seconds]
        seconds defaults to 2147483647 (INT_MAX ~= 68 years), the soak-validated
        value that stops the radio from disassociating while the wall sits idle.
Prints: OLD=<prev> NEW=<set>   (or ERROR=<msg> and exit 1)
"""
import sys
import plistlib

DEFAULT_SECONDS = 2147483647.0   # INT_MAX seconds; soak-validated 2026-06


def main():
    if len(sys.argv) < 2:
        print("ERROR=usage: set_wifi_disassociation.py <plist> [seconds]")
        return 1
    path = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_SECONDS
    try:
        with open(path, "rb") as f:
            d = plistlib.load(f)
    except Exception as e:
        print("ERROR=load: %s" % e)
        return 1
    if not isinstance(d, dict):
        print("ERROR=not a plist dict")
        return 1
    old = d.get("DisassociationInterval")
    d["DisassociationInterval"] = seconds
    try:
        with open(path, "wb") as f:
            plistlib.dump(d, f, fmt=plistlib.FMT_BINARY)
    except Exception as e:
        print("ERROR=dump: %s" % e)
        return 1
    print("OLD=%r NEW=%r" % (old, seconds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
