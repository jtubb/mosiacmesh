"""Stage tweak dylibs onto every online screen in a display group.

Edit PAYLOAD below for the build you are shipping (local path, installed name,
expected md5). Run with --dry-run first to see the target list.

CRITICAL PROCEDURE (audit 1.16): never `cp` OVER an installed dylib. Mutating it in
place leaves dyld/Substrate holding a cached signature for that inode that no longer
matches the contents, and the load is rejected -- SIGKILL at dyld, or after a clean
boot a silent refusal to launch. `rm` first so the new bytes land on a FRESH INODE.
That single mistake produced 25 consecutive "failed" loads and a day of misdiagnosis.

Sequential with pacing -- never 24-way burst SSH ([[fleet-ssh-no-burst]]).
Each device keeps a rollback copy under /var/mobile/<name>_pre_rollout.dylib.

This only STAGES the dylibs. A tweak is injected at process launch, so nothing takes
effect until each webclip relaunches -- which is deliberately left to the operator,
whose fleet reboot is far more reliable than scripted VNC taps.

    python rollout.py <group> [--dry-run]
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

API = "http://localhost:3000"
DLIB = "/Library/MobileSubstrate/DynamicLibraries"
SSH = r"C:/Windows/System32/OpenSSH/ssh.exe"
SCP = r"C:/Windows/System32/OpenSSH/scp.exe"
KEY = r"C:/Users/jtubb.SOLUTIONS/.ssh/mosaic_ipad"
OPTS = ["-o", "HostKeyAlgorithms=+ssh-rsa", "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
        "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=NUL", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]

# (local file, installed name, expected md5)
PAYLOAD = [
    ("tweak/mmvideo/mmvideo_kvoready.dylib",  "mmvideo",  "e9b6d229a4490c66238938ce6a2eb651"),
    ("tweak/mmcache/mmcache_fastpath.dylib",  "mmcache",  "eae0e7fb23b3acebe17a0d7c2d663175"),
]


def ssh(ip, cmd, timeout=90):
    p = subprocess.run([SSH, "-i", KEY] + OPTS + ["root@%s" % ip, cmd],
                       capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    return (p.stdout or "").strip()


def scp(ip, local, remote, timeout=120):
    p = subprocess.run([SCP, "-i", KEY] + OPTS + [local, "root@%s:%s" % (ip, remote)],
                       capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    return p.returncode == 0


def main():
    group = sys.argv[1]
    dry = "--dry-run" in sys.argv
    d = json.load(urllib.request.urlopen(API + "/api/discovery/devices", timeout=8))
    rows = [x for x in (d.get("devices", d) if isinstance(d, dict) else d) or []
            if x.get("displayID") == group]
    now = time.time()
    online = [x for x in rows if now - float(x.get("lastSeen") or 0) < 180]
    print("group %s: %d online of %d%s\n" % (group, len(online), len(rows),
                                             "  [DRY RUN]" if dry else ""))

    ok = fail = 0
    for x in sorted(online, key=lambda r: str(r.get("friendlyName"))):
        ip, name = x["ip"], x["friendlyName"]
        results = []
        for local, inst, want in PAYLOAD:
            if dry:
                results.append("%s:dry" % inst); continue
            if not scp(ip, local, "/var/root/_roll_%s.dylib" % inst):
                results.append("%s:SCPFAIL" % inst); continue
            out = ssh(ip, (
                "cp -p {D}/{I}.dylib /var/mobile/{I}_pre_rollout.dylib 2>/dev/null; "
                # rm FIRST -- fresh inode, see audit 1.16
                "rm -f {D}/{I}.dylib && cp /var/root/_roll_{I}.dylib {D}/{I}.dylib && "
                "chown root:wheel {D}/{I}.dylib && chmod 644 {D}/{I}.dylib && "
                "rm -f /var/root/_roll_{I}.dylib && md5sum {D}/{I}.dylib | cut -d' ' -f1"
            ).format(D=DLIB, I=inst))
            results.append("%s:%s" % (inst, "ok" if out.strip() == want else "MISMATCH(%s)" % out[:10]))
        good = all(r.endswith(":ok") or r.endswith(":dry") for r in results)
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        print("  %-15s %s" % (name, "  ".join(results)))
        time.sleep(1.5)          # pacing

    print("\nstaged cleanly on %d screens, %d with problems" % (ok, fail))
    print("NOT YET ACTIVE: a tweak injects at process launch, so each webclip must")
    print("relaunch (a fleet reboot is the reliable way) before these take effect.")
    print("Rollback on any device: rm the dylib, then cp /var/mobile/<name>_pre_rollout.dylib back.")


sys.exit(main())
