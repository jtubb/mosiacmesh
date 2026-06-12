# iPad-1 WiFi Keep-Alive (802.11 PSM) — Diagnosis & Options

**Date:** 2026-06-12
**Status:** Diagnosis complete; fix not yet chosen (device-side software avenues exhausted).
**Fleet:** "OEB Sign 1" — 24× iPad 1,1 / iOS 5.1.1 (BCM4329 radio), hostnames `sign1screen1..24.home.lan`, IPs `192.168.1.50–.83`.
**Related memory:** `wifi-psm-root-cause.md`. Related: `verify-insomnia-post-recovery.md`, `ios5-video-gesture.md`, `device-automation-tooling.md`.

---

## 1. Symptom

The device **stays up running the kiosk webapp, the screen stays on, but its WiFi goes "inactive"**: server-side SSH lifecycle commands intermittently `connect to host: Connection timed out`, clients drop offline, and everything lags. **Pressing the physical home button revives WiFi immediately.**

## 2. Root cause (confirmed)

**802.11 Power-Save Mode (PSM) on the Broadcom BCM4329 radio, while still associated.** The radio sleeps between beacon wakes and only services packets on its wake schedule, adding ~1 s latency to everything.

**Evidence — idle ping sweep, all 24 devices, 20 pings each, parallel (2026-06-12):**

| Metric | Result | Interpretation |
|---|---|---|
| Packet loss | **0%** on all 24 | Not AP saturation / not signal (saturation drops packets) |
| Avg RTT | **~900–1100 ms** (max to 2081 ms) | Radio dozing ~1 s between wakes |
| Min RTT | **4–15 ms** on several devices | Link + AP are genuinely fast when the radio is awake |

The split — fast Min, ~1 s Avg, 0% loss — is the textbook PSM-doze signature, not contention.

## 3. What was ruled out (each with evidence)

- **Render pipeline** — the original "Tap to Start" complaint was a *separate* issue (coordinated-start holding GO until iPad-1 video is armed; see §7). Not WiFi-related. The 4 SCRIPT items don't render; OEB is 0/24 calibrated.
- **AP saturation / signal** — 0% packet loss rules it out.
- **Auto-lock / screen sleep** — screens stay on; `SBAutoLockTime` is absent (default 2 min, **not** pinned) but the symptom occurs with the screen lit.
- **Traffic starvation** — making the device generate sustained outbound traffic (`ping -c 36 -i 0.5` from the device) did **not** lower RTT (screen7 1494→982 ms; screen10 1139→1217 ms). The radio dozes regardless of traffic.
- **Insomnia** (`com.imalc.insomnia`) — installed + `Enabled=true` on all 24 (verified via audit), dylib present, **but ineffective**: it only hooks the *screen-lock* handler, so it does nothing for screen-on idle PSM. (Supersedes the assumption in `verify-insomnia-post-recovery.md` that "Insomnia on ⇒ WiFi healthy.")
- **Synthetic input — both layers, conclusively** — neither an Activator software event (`libactivator.menu.press`: 1076→1127 ms) nor a real Veency **IOHIDEvent** tap (vncdo move+click, even a 5-tap burst: 1184→1052→968 ms) cleared the PSM. **Only the physical button works.** The hardware button is a real interrupt that changes the system power/wake state; synthetic injection sits above that layer and the WiFi driver PM never sees a real wake. (Same hardware-vs-software gap as `ios5-video-gesture.md`.)
- **iOS power assertions / sleep settings** — `pmset -g assertions` shows `PreventUserIdleSystemSleep = 1` is **already held** (by SpringBoard + MobileSafari) and `pmset -g` shows `sleep 0`, *yet WiFi still PSMs*. So 802.11 PSM is **independent of the iOS idle-sleep / assertion system**. This proves assertion-based tools (**SleepDepriver, `caffeinate`, `pmset`**) cannot fix it — they assert the thing that's already asserted.

## 4. Device tooling reality (iOS 5.1.1, stripped IPSW)

- Present & useful: `cycript` + `cynject` + `ldid` (runtime API calls / tweak signing, no external toolchain), `wifid`, `pmset` (via the `powermanagement` package), `apt`.
- **Absent:** `Apple80211.framework`, `IO80211.framework` (so **no `Apple80211*` C ioctl API**, e.g. `APPLE80211_IOC_POWERSAVE`); no `wl` (Broadcom tool) on-device or in the repo/Legacy-iOS-Kit; no `apple80211` CLI.
- Only WiFi API present: **`MobileWiFi.framework`** — `WiFiManagerClient*` / `WiFiDeviceClient*` symbols resolve and are callable from cycript, **but `WiFiManagerClientCopyDevices(mgr)` returns nil** (manager creates fine) — device enumeration needs more RE, and the property it exposes may toggle the whole radio rather than PSM.
- **iOS-5 shell gotchas (for any future device scripting):** no `tr`, no `head`, no `awk`; `sysctl -n` misbehaves (use `sysctl <name>` + parse). The byte-mangling of quoted heredocs through PowerShell→ssh→bash is real — **author scripts locally and `scp` them** (the pattern used for plists and the cycript probes).

## 5. Avenues explored and why each is blocked

| Avenue | Result |
|---|---|
| `Apple80211` ioctl (`APPLE80211_IOC_POWERSAVE=0`) | **Blocked** — framework absent on device |
| Broadcom `wl PM 0` | **Blocked** — no binary; the driver-access layer (`IO80211`) is also absent, so a generic `wl` likely wouldn't bind |
| `MobileWiFi` `WiFiDeviceClientSetProperty` via cycript | **Stalled** — `CopyDevices` returns nil; needs more RE; may be the wrong knob |
| Insomnia / Insomnia Pro | **Ineffective** — screen-lock-only |
| SleepDepriver (`com.lheap.sleepdepriver`) | **Won't work** — assertion-based (prevents sleep/dim); the assertion is already held yet WiFi PSMs. Also awkward: enable is Activator-listener-only, no headless/CLI/persistent toggle. (Installed on screen1 for testing; harmless.) |
| `pmset` (`powermanagement`) | **No WiFi-PSM knob**; only sleep timers / `womp`. (Installed on screen1.) |
| Periodic synthetic tap/input keep-alive | **Disproven** — no synthetic input clears PSM |
| Hold an `IOPMAssertion` (caffeinate-style) | **Won't work** — equivalent assertion already held |

## 6. Tooling built / artifacts

- **`tools/audit_keepalive.ps1`** — reusable, read-only fleet audit over keyed SSH: per device reports Insomnia plist `Enabled`, dylib present, autolock-off LaunchDaemon present, uptime, kiosk-process, reachability; classifies OK / DEGRADED / UNREACHABLE; optional `-Csv`. Works around the missing `tr`/`head` and `sysctl -n`. **Re-run after every reflash / AP event.** (2026-06-12 run: 24/24 provisioned-OK but all dozing.)
- Scratch cycript probes: `tools/_psm_probe.cy`, `_psm_probe2.cy`, `_psm_probe3.cy` (symbol/device/property discovery — kept for resume).
- Installed on **screen1 only** (192.168.1.50) during testing, both harmless and reversible (`apt-get remove`): `com.lheap.sleepdepriver`, `powermanagement` (`pmset`).

## 7. Adjacent finding (the original "Tap to Start") — not WiFi

Pressing Play on a playlist that contains a video parks the whole group on "Tap to Start": `prepareFirstItem()` (`index.html`) arms off the *first video item in the playlist* even when item 0 is a no-gesture SCRIPT animation, and the server holds GO until every client is armed (`_release_expired_prepares`, `server.py`). On iPad-1 the video gesture needs a **real** tap (the auto-arm Veency tap can't satisfy it), so the wall waits for 24 physical taps. The operator confirmed **synchronized video is the desired behavior**, so this is working as designed; the up-front taps are expected. (A clean tap-driven release was never observed end-to-end because a Stop device-script interrupted the test.) Captured here so it isn't conflated with the PSM issue.

## 8. Remaining options (none are device-side software)

1. **AP-side mitigation — recommended first.** Lower the access point **DTIM to 1** and/or disable WMM/U-APSD client power-save ("no client power management"). This doesn't disable PSM but shrinks the wake interval from ~1 s toward ~100 ms — **one change, helps all 24 at once, no per-device work.** Verify against the AP model in use.
2. **Tolerate PSM + harden the server.** Accept ~1 s RTT: raise SSH `ConnectTimeout`/retries for lifecycle scripts (partly present — `ServerAliveInterval=15`, 4-attempt push loop), and rely on SockJS's latency tolerance for playback. The wall already mostly functions; this just removes the lifecycle-command timeouts.
3. **Build a Broadcom-ioctl tool/tweak** (replicate `wl PM 0`). High RE, uncertain — the `IO80211` user-client path is absent, so reaching the driver is itself a research problem. `ldid`/`cynject` are present if pursued.
4. **Source a prebuilt `wl`** for armv7/BCM4329/iOS-5 (external). Only clean device-side disable, but availability is the blocker, and (per §5) the absent driver layer makes even this uncertain.

## 9. Recommendation

- **Do (1) AP-side DTIM=1 / disable client power-save first** — lowest effort, fleet-wide, sidesteps the iOS driver entirely. Measure with the §10 ping sweep before/after.
- **Pair with (2)** as the durable posture: assume ~1 s RTT and make lifecycle tooling patient/retry-driven rather than fighting the radio.
- Treat (3)/(4) as a separate, opt-in R&D sub-project only if (1)+(2) prove insufficient.

## 10. How to reproduce the key measurements

```powershell
# Fleet keep-alive provisioning audit (read-only)
.\tools\audit_keepalive.ps1 -Csv .\keepalive-audit.csv

# Idle PSM ping sweep (the 0%-loss / ~1s-RTT signature) — PowerShell:
$hosts = gc tools\devices.txt | ? { $_ -and -not $_.StartsWith('#') }
$hosts | ForEach-Object -ThrottleLimit 24 -Parallel {
  $r = Test-Connection $_ -Count 20 -ErrorAction SilentlyContinue
  $ok = @($r | ? Status -eq Success); $lat = @($ok | % Latency)
  [pscustomobject]@{ Host=$_; Loss=[math]::Round((20-$ok.Count)/20*100)
    Min=($lat|measure -Min).Minimum; Avg=[math]::Round(($lat|measure -Average).Average); Max=($lat|measure -Max).Maximum }
} | Sort-Object Host | Format-Table

# Current power assertions on a device (shows PreventUserIdleSystemSleep already held):
ssh -i ~/.ssh/mosaic_ipad <legacy opts> root@192.168.1.50 "pmset -g assertions"
```
SSH legacy opts (matches `mosaicmesh/device_scripts.py`): `-o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes`.
