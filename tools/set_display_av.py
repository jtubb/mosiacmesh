"""Set brightness (max), disable auto-brightness, and set media volume (max) on
MosaicMesh iPad-1 (iOS 5.1.1) display clients over keyed SSH.

iOS 5.1.1 has no `defaults`/`plutil`/`PlistBuddy` on-device, so we can't edit the
binary preference plists in place. Instead, per host, we:
  1. scp the plist down to a temp file,
  2. surgically edit ONE key with plistlib (preserving every other key) and write
     it back as a binary plist (matching the on-device format),
  3. scp it back, then restore owner/mode (mobile:mobile, 600) -- scp lands the
     file root-owned, which SpringBoard (running as `mobile`) would refuse to read,
  4. respring (killall SpringBoard) so SpringBoard re-reads brightness on launch.

Settings (all reversible; keys verified by toggle-and-diff on the fleet 2026-06-23).
Each is opt-in via a flag -- an omitted flag leaves that key untouched:
  --brightness X   com.apple.springboard.plist  SBBacklightLevel2            -> X (0..1)
  --als on|off     com.apple.springboard.plist  SBEnableALS                  -> True/False
  --volume-max     com.apple.celestial.plist    volumes.Speaker.Audio/Video  -> 1.0 (media vol max)

SBEnableALS (Ambient Light Sensor) is the real auto-brightness control on iOS 5.1.1
-- NOT SBBacklightAutoBrightness (that key is read by nothing; we delete it if a
prior run wrote it). The media volume lives in the per-route volumes dict, not in
volumeMultiplier (a runtime multiplier mediaserverd rewrites on its own).

Usage:
    # revert to 70% brightness + auto-brightness on, whole fleet:
    python tools/set_display_av.py --host-file tools/devices.txt --brightness 0.7 --als on
    python tools/set_display_av.py --hosts sign1screen1.home.lan --brightness 0.7 --als on --dry-run
    python tools/set_display_av.py --host-file tools/devices.txt --brightness 1 --als off --volume-max
    python tools/set_display_av.py --host-file tools/devices.txt --verify-only

Offline / unreachable hosts (e.g. a dozed radio) are reported and skipped; the run
continues. Re-run later to catch them.
"""
import argparse
import os
import plistlib
import subprocess
import sys
import tempfile

KEY = os.path.expanduser(os.path.join("~", ".ssh", "mosaic_ipad"))
SSH_OPTS = [
    "-o", "HostKeyAlgorithms=+ssh-rsa",
    "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
    "-o", "IdentitiesOnly=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=8",
]

SB_PLIST = "/var/mobile/Library/Preferences/com.apple.springboard.plist"
CEL_PLIST = "/var/mobile/Library/Preferences/com.apple.celestial.plist"


def _ssh(host, command):
    return subprocess.run(
        ["ssh", "-i", KEY, *SSH_OPTS, f"root@{host}", command],
        capture_output=True, text=True,
    )


def _scp(src, dst):
    return subprocess.run(
        ["scp", "-i", KEY, *SSH_OPTS, src, dst],
        capture_output=True, text=True,
    )


def _pull_edit_push(host, remote_path, edit_fn, dry_run):
    """scp the plist down, apply edit_fn(dict)->changed_summary, push back + fix perms.
    Returns (ok, summary_str). edit_fn returns a list of '(key: old -> new)' strings."""
    with tempfile.TemporaryDirectory() as td:
        local = os.path.join(td, "p.plist")
        r = _scp(f"root@{host}:{remote_path}", local)
        if r.returncode != 0:
            return False, f"scp-down failed: {r.stderr.strip() or 'unreachable'}"
        with open(local, "rb") as f:
            data = plistlib.load(f)
        changes = edit_fn(data)
        if not changes:
            return True, "already at target (no change)"
        if dry_run:
            return True, "DRY-RUN would set: " + "; ".join(changes)
        with open(local, "wb") as f:
            plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)
        r = _scp(local, f"root@{host}:{remote_path}")
        if r.returncode != 0:
            return False, f"scp-up failed: {r.stderr.strip()}"
        # Restore owner/mode -- scp lands it root-owned; SpringBoard runs as mobile.
        fix = _ssh(host, f"chown mobile:mobile '{remote_path}' && chmod 600 '{remote_path}'")
        if fix.returncode != 0:
            return False, f"chown/chmod failed: {fix.stderr.strip()}"
        return True, "set: " + "; ".join(changes)


def _edit_springboard(d, brightness, als):
    """Set brightness (SBBacklightLevel2, 0..1) and/or auto-brightness
    (SBEnableALS). Either target may be None to leave that key untouched."""
    changes = []
    if brightness is not None:
        cur = d.get("SBBacklightLevel2")
        if cur != brightness:
            d["SBBacklightLevel2"] = brightness
            changes.append(f"SBBacklightLevel2: {cur!r} -> {brightness}")
    if als is not None:
        cur = d.get("SBEnableALS")
        if cur is not als:
            d["SBEnableALS"] = als
            changes.append(f"SBEnableALS: {cur!r} -> {als}")
    # Clean up the wrong key a prior version of this tool may have written.
    if "SBBacklightAutoBrightness" in d:
        del d["SBBacklightAutoBrightness"]
        changes.append("SBBacklightAutoBrightness: deleted (ineffective key)")
    return changes


def _edit_celestial(d):
    vols = d.get("volumes")
    if not isinstance(vols, dict):
        vols = {}
    spk = vols.get("Speaker")
    if not isinstance(spk, dict):
        spk = {}
    cur = spk.get("Audio/Video")
    if cur == 1.0:
        return []
    spk["Audio/Video"] = 1.0
    vols["Speaker"] = spk
    d["volumes"] = vols
    return [f"volumes.Speaker.Audio/Video: {cur!r} -> 1.0"]


def process_host(host, dry_run, respring, brightness, als, volume_max):
    print(f"\n[{host}]")
    if brightness is not None or als is not None:
        ok1, s1 = _pull_edit_push(
            host, SB_PLIST, lambda d: _edit_springboard(d, brightness, als), dry_run)
        print(f"  springboard: {s1}")
        if not ok1:
            return False
    if volume_max:
        ok2, s2 = _pull_edit_push(host, CEL_PLIST, _edit_celestial, dry_run)
        print(f"  celestial:   {s2}")
        if not ok2:
            return False
    if not dry_run and respring:
        # SpringBoard re-reads brightness + ALS at launch; mediaserverd re-reads the
        # per-route volume at launch. Both own their plist at runtime, so the file
        # edit only takes effect once they relaunch.
        r = _ssh(host, "killall SpringBoard; killall mediaserverd")
        print(f"  respring:    killall SpringBoard + mediaserverd ({'ok' if r.returncode == 0 else (r.stderr.strip() or 'best-effort')})")
    return True


def verify_host(host):
    """Re-read both plists and print the three values (post-change check)."""
    print(f"\n[verify {host}]")
    with tempfile.TemporaryDirectory() as td:
        for name, remote, keys in (
            ("springboard", SB_PLIST, ("SBBacklightLevel2", "SBEnableALS")),
            ("celestial", CEL_PLIST, ("volumes", "volumeMultiplier")),
        ):
            local = os.path.join(td, name)
            r = _scp(f"root@{host}:{remote}", local)
            if r.returncode != 0:
                print(f"  {name}: scp failed ({r.stderr.strip() or 'unreachable'})")
                continue
            with open(local, "rb") as f:
                d = plistlib.load(f)
            for k in keys:
                print(f"  {k} = {d.get(k)!r}")


def load_hosts(args):
    hosts = []
    if args.hosts:
        for chunk in args.hosts:
            hosts += [h.strip() for h in chunk.split(",") if h.strip()]
    if args.host_file:
        with open(args.host_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    hosts.append(line)
    return hosts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hosts", nargs="+", help="host(s), comma or space separated")
    ap.add_argument("--host-file", help="file with one host per line (# = comment)")
    ap.add_argument("--brightness", type=float, default=None,
                    help="SBBacklightLevel2 target, 0..1 (e.g. 0.7); omit to leave brightness as-is")
    ap.add_argument("--als", choices=["on", "off"], default=None,
                    help="auto-brightness (SBEnableALS): on=True, off=False; omit to leave as-is")
    ap.add_argument("--volume-max", action="store_true",
                    help="also set media volume to max (celestial); off by default")
    ap.add_argument("--dry-run", action="store_true", help="read + show planned changes, write nothing")
    ap.add_argument("--no-respring", action="store_true", help="skip killall SpringBoard")
    ap.add_argument("--verify-only", action="store_true", help="just read back the three values")
    args = ap.parse_args()

    if args.brightness is not None and not (0.0 <= args.brightness <= 1.0):
        ap.error("--brightness must be in 0..1")

    hosts = load_hosts(args)
    if not hosts:
        ap.error("no hosts (use --hosts or --host-file)")

    if args.verify_only:
        for h in hosts:
            verify_host(h)
        return

    als = {"on": True, "off": False}.get(args.als)   # None if unset
    if args.brightness is None and als is None and not args.volume_max:
        ap.error("nothing to do: pass --brightness, --als, and/or --volume-max")

    ok = bad = 0
    for h in hosts:
        if process_host(h, args.dry_run, not args.no_respring,
                        args.brightness, als, args.volume_max):
            ok += 1
        else:
            bad += 1
    print(f"\n=== {ok} ok, {bad} failed/skipped of {len(hosts)} ===")


if __name__ == "__main__":
    main()
