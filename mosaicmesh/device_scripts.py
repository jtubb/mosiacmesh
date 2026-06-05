"""Execution of per-device lifecycle scripts over SSH plus the Veency
VNC-tap launch helper.

This is the CURRENT (PR-1) layout: scripts and constants are still
hardcoded here. PR-3 of the admin-timeline-redesign spec will replace
this module's contents with the ScriptingProfile dispatcher -- until
then, behavior must remain byte-identical.

The shared SSH options live here too -- onboard_devices.ps1 has its own
copy of these in PowerShell array form for the bootstrap phase.
"""
import os
import logging
import asyncio

from mosaicmesh.template_vars import SafeDict

# --- Device lifecycle automation -----------------------------------------
# The server runs per-device shell scripts over SSH (login/start/stop/reboot),
# using the passphrase-less key installed by tools/onboard_devices.ps1 and the
# same legacy-crypto flags (the iPad-1's OpenSSH only speaks SHA-1-era crypto).
# Client.{login,start,stop,reboot}Script default to None and are backfilled with
# DEFAULT_DEVICE_SCRIPTS (editable per device via the discovery configure API).
SSH_KEY_PATH = os.path.expanduser(os.path.join("~", ".ssh", "mosaic_ipad"))
SSH_USER = "root"
SSH_LEGACY_OPTS = ["-o", "HostKeyAlgorithms=+ssh-rsa",
                   "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
                   "-o", "IdentitiesOnly=yes",           # only -i key; old sshd low MaxAuthTries
                   "-o", "StrictHostKeyChecking=accept-new",
                   "-o", "ConnectTimeout=10",
                   "-o", "BatchMode=yes"]                # never prompt (unattended)
# The wall's display page each device opens. Edit for your network.
DISPLAY_URL = "http://192.168.1.60:3000/"
# Bundle id of the MosaicMesh home-screen webclip, used by startScript
# to launch the display in WEBAPP MODE (chrome-less, fullscreen across
# script + video transitions). The webclip is installed by tools/
# onboard_devices.ps1 step 5.4g with a stable UUID across the fleet.
# Falls back to uiopen (Safari) on iPads where the webclip isn't
# installed yet. Hex spells "MosaicMeshKiosk1" in ASCII for grep-
# friendliness in `activator listeners` output.
WEBCLIP_BUNDLE_ID = "com.apple.webapp-4D6F736169634D6573684B696F736B31"
DEFAULT_DEVICE_SCRIPTS = {
    # Wake + unlock + keep the screen lit, via Activator. State-independent
    # (safe to call regardless of current iPad state): lockscreen.dismiss
    # wakes the screen if asleep AND skips slide-to-unlock if locked AND
    # no-ops if already unlocked. The previous version also pressed the
    # home button, which had the destructive side effect of minimizing
    # Safari (kicking the wall display to the home screen) if the iPad
    # was already foregrounded on MosaicMesh -- removed so login is safe
    # to fire from any starting state. The SBSettings autolock switch off
    # prevents re-sleeping. Verified on iPad-1 / iOS 5.1.1.
    # Also locks rotation to PORTRAIT on every login: a wall of iPads
    # has a fixed physical orientation, so any user-induced rotation
    # away from portrait (accidental, or by Veency input quirks) must
    # be reverted before the display layer renders. SBOrientationLocked*
    # are the prefs SpringBoard reads; `defaults write` routes through
    # cfprefsd which fires the CFPreferences-change notification that
    # SpringBoard's observer applies without a respring. Writing as
    # mobile via `su -c` is mandatory -- root's defaults land in
    # /var/root/Library/Preferences (wrong place); SpringBoard reads
    # /var/mobile/Library/Preferences/com.apple.springboard.plist.
    # Orientation enum is UIInterfaceOrientation: 1 = portrait.
    # The `2>/dev/null`s keep the loginScript output clean for the
    # admin UI; the LOGIN_OK terminator is what the server matches on.
    "loginScript":  "activator send libactivator.lockscreen.dismiss; sleep 1; "
                    "activator send switch-off.com.a3tweaks.switch.autolock; "
                    "su mobile -c 'defaults write com.apple.springboard SBOrientationLockedActive -bool YES' 2>/dev/null; "
                    "su mobile -c 'defaults write com.apple.springboard SBOrientationLockedOrientation -int 1' 2>/dev/null; "
                    "echo LOGIN_OK",
    # Open the display page in WEBAPP MODE via the home-screen webclip
    # (sbdidlaunch on the webclip's bundle id), falling back to mobile
    # Safari (uiopen) if the webclip isn't installed on this iPad yet.
    # Webapp mode gives chrome-less fullscreen across script+video
    # transitions; Safari mode keeps the URL bar visible and re-enters
    # iOS native fullscreen per video. See docs/superpowers/specs/
    # 2026-06-03-cache-progress-and-propagation-ui.md for context.
    # Like the old uiopen-only form: brings the app to the foreground
    # without forcing a reload, so a Safari/webapp instance that's
    # already at this URL just gets refocused.
    "startScript":  "sbdidlaunch '" + WEBCLIP_BUNDLE_ID + "' 2>/dev/null"
                    " || uiopen '" + DISPLAY_URL + "'; echo START_OK",
    # Open the display page with the ?tdbg query flag, which the client JS
    # uses to (1) draw an on-screen timing HUD with current playback frame /
    # offset / drift, and (2) stream debug state back to the server log so
    # operators can collect group-wide diagnostics without per-device touch.
    #
    # KILL Safari first then uiopen: on iOS 5 `uiopen` to a URL Safari is
    # already showing only brings Safari to the foreground -- it does NOT
    # reload the page. Tdbg mode needs a fresh page load (new SockJS
    # connection, fresh JS state, fresh ?tdbg flag in location.href). The
    # killall + relaunch is the only way to guarantee that on iOS 5.
    # testScript needs killall because it's changing the URL (regular ->
    # ?tdbg) and iOS 5 Safari otherwise stacks the new tab on top of
    # the old. Plain killall (SIGTERM) lets Safari clean up; -9 was
    # too aggressive. NO autolock toggle or SuspendState rm here --
    # those were experimental fixes that turned out to interact badly
    # with the always-awake state.
    "testScript":   "killall MobileSafari 2>/dev/null; sleep 1; "
                    "uiopen '" + DISPLAY_URL +
                    ("?tdbg" if "?" not in DISPLAY_URL else "&tdbg") +
                    "'; echo TEST_OK",
    # Close the display client (Web.app for the home-screen webclip
    # since 2026-06-03; MobileSafari for the legacy Safari fallback
    # path), re-enable auto-lock (start disabled it via the boot
    # LaunchDaemon's autolock-off), and sleep the screen now via the
    # sleep button. Killing Web AND MobileSafari is belt-and-suspenders:
    # whichever was foregrounded gets terminated, and the unused one
    # is a no-op. Symmetric with start: stop -> screen off + allowed
    # to stay asleep.
    "stopScript":   "killall Web 2>/dev/null; "
                    "killall MobileSafari 2>/dev/null; "
                    "activator send switch-on.com.a3tweaks.switch.autolock; "
                    "activator send libactivator.system.sleepbutton; echo STOP_OK",
    # Full device reboot.
    "rebootScript": "echo REBOOTING; reboot",
}

# Veency-side framebuffer coordinate of the MosaicMesh home-screen
# webclip icon when the iPad is in portrait orientation and the
# operator has dragged the icon to the LEFTMOST dock slot. The
# framebuffer is always landscape 1024x768 regardless of iPad
# orientation, rotated 90 CCW from the portrait user view -- so the
# user's portrait (96, 945) lands at framebuffer (945, 671). This
# pair was empirically verified on .50 (sign1screen1) in this
# session. If you reposition the icon, update this constant.
# See docs/superpowers/specs/ for the rationale: iOS 5 doesn't
# expose a CLI launcher that includes a webclip's URL context, so
# the only reliable "launch the webclip" path is to drive
# SpringBoard's own tap-handler via VNC.
WEBAPP_ICON_FBX = 945
WEBAPP_ICON_FBY = 671


# TODO(PR-3): the next two functions reach back into server.py for the
# Veency connection pool (_veency_pool, _veency_lock, _get_pooled_vnc, _do_tap).
# That cross-module dependency is the most awkward boundary in PR-1's split
# — it exists because _auto_arm_client (still in server.py) also needs the
# pool. When PR-3 of the admin-timeline-redesign spec replaces this module
# with the ScriptingProfile dispatcher, the pool itself + its lock + the
# tap primitive should migrate into the dispatcher's launch-method layer,
# and _auto_arm_client should be updated to call through the dispatcher
# instead of reaching directly. Removing the cross-import is a PR-3
# follow-up; for PR-1 the smell is bounded and documented.
#
# Style note: this module uses `import server as _server` (with the leading-
# underscore alias) rather than the bare `import server` used elsewhere in
# the mosaicmesh package. No functional difference — the alias visually
# distinguishes "reach-back to legacy" usages from the more common pattern.
# If a future PR-3 cleanup removes these reach-backs entirely, the alias
# goes with them.
async def _drop_pooled_vnc(client_key):
    """Evict and disconnect a pooled VNC client. Safe to call when
    the client_key isn't pooled (no-op). Called on per-tap failure
    (so the next attempt re-handshakes) and on client offline
    cleanup (so dead iPads don't leak file descriptors)."""
    import server as _server
    async with _server._veency_lock:
        proxy = _server._veency_pool.pop(client_key, None)
    if proxy is None:
        return
    try:
        await asyncio.get_event_loop().run_in_executor(None, proxy.disconnect)
    except Exception as e:
        logging.debug("veency pool disconnect for %s: %s", client_key, e)


async def _launch_webapp_via_vnc(client_key):
    """Launch the MosaicMesh home-screen webclip by sending a VNC
    tap at the icon's framebuffer coordinate. The iPad's SpringBoard
    receives the tap, reads the webclip's Info.plist (including its
    URL context), and launches Web.app with that URL -- the same
    code path a human finger triggers. Best-effort: a missing pool
    entry / handshake failure just logs.

    Requires the operator to have dragged the MosaicMesh icon to the
    LEFTMOST dock slot on each iPad (in portrait orientation). The
    onboarding script's webclip install (step 5.4g) creates the icon
    but does not pin its position; that's a one-time per-iPad
    manual step."""
    import server as _server
    client = _server.settings.clients.get(client_key)
    if not client or not getattr(client, "ip", ""):
        logging.warning("launch-webapp %s: no client/ip", client_key)
        return False
    loop = asyncio.get_event_loop()
    try:
        proxy = await _server._get_pooled_vnc(client_key, client.ip)
        await loop.run_in_executor(None, _server._do_tap, proxy,
                                   WEBAPP_ICON_FBX, WEBAPP_ICON_FBY)
        logging.info("launch-webapp: VNC-tapped %s at fb(%d,%d)",
                     client_key, WEBAPP_ICON_FBX, WEBAPP_ICON_FBY)
        return True
    except Exception as e:  # noqa: BLE001
        await _drop_pooled_vnc(client_key)
        logging.warning("launch-webapp tap failed for %s: %s",
                        client_key, e)
        return False


async def _run_device_script(client_key, which):
    """Run a device's lifecycle script (which in {login,start,stop,reboot}) over
    SSH, using the per-device field (or the fleet default). Best-effort: missing
    key / no IP / SSH failure just logs. Returns (rc, output) for the caller/log."""
    import server as _server
    client = _server.settings.clients.get(client_key)
    if not client or not getattr(client, "ip", ""):
        logging.warning("run-script %s %s: no client/ip", client_key, which)
        return (None, "no-ip")

    # Special-case "start": iOS 5 / iPad-1 has no CLI launcher that
    # passes a webclip's URL context to Web.app, so neither sbdidlaunch
    # nor `open <bundle-id>` nor `activator send` actually launch the
    # MosaicMesh webapp foreground with content (we tried all of them
    # in the 2026-06-03 session). The only working "launch the
    # webclip" path is SpringBoard's own tap handler. So for "start"
    # we (a) SSH-run the loginScript first to wake the screen +
    # disable autolock, then (b) VNC-tap the icon's framebuffer
    # coordinate (WEBAPP_ICON_FBX, WEBAPP_ICON_FBY) -- which requires
    # the operator to have dragged the icon to the leftmost dock slot
    # in portrait orientation on each iPad. Falls back to SSH-exec of
    # startScript if the VNC tap fails (Veency unreachable, pool
    # exhausted, etc.) so non-iPad-1 devices and emergency
    # uiopen-to-Safari paths still work.
    if which == "start":
        # Wake the screen only -- send libactivator.lockscreen.dismiss
        # (wakes if asleep, no-ops if awake). Do NOT also call the
        # autolock switch-off here: it produces a transient "Autolock
        # disabled" popup that intercepts the VNC tap below. Autolock
        # is already off from the boot LaunchDaemon (5.4a) which fires
        # the same switch-off command 30s after every boot, so the
        # autolock state is correct system-wide regardless.
        wake_script = "activator send libactivator.lockscreen.dismiss"
        login_cmd = (["ssh", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS +
                     ["%s@%s" % (SSH_USER, client.ip), wake_script])
        try:
            wake = await asyncio.create_subprocess_exec(
                *login_cmd, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            try:
                await asyncio.wait_for(wake.wait(), timeout=10)
            except asyncio.TimeoutError:
                try: wake.kill(); await wake.wait()
                except Exception: pass
        except Exception as e:  # noqa: BLE001
            logging.warning("run-script %s start: wake step failed: %s",
                            client_key, e)
        # Brief settle delay so SpringBoard has finished the wake
        # animation before we tap -- otherwise the tap can land
        # mid-transition and SpringBoard ignores it.
        await asyncio.sleep(0.8)
        ok = await _launch_webapp_via_vnc(client_key)
        if ok:
            return (0, "VNC_TAP_OK")
        logging.warning("run-script %s start: VNC tap failed, "
                        "falling back to startScript SSH exec",
                        client_key)
        # Fall through to the generic SSH path below.

    field = which + "Script"
    script = getattr(client, field, None) or DEFAULT_DEVICE_SCRIPTS.get(field)
    if not script:
        logging.warning("run-script %s %s: no script", client_key, which)
        return (None, "no-script")
    cmd = (["ssh", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS +
           ["%s@%s" % (SSH_USER, client.ip), script])
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            # CRITICAL: kill the subprocess on timeout. Without this the
            # ssh.exe stays alive in the background indefinitely -- on
            # iOS-5 fleets where SSH connects but the iPad's shell hangs
            # mid-command (WiFi power-save mid-handshake, slow respring,
            # etc.), every Start/Login/Test that times out for one
            # device leaves a leaked ssh.exe on the server. Observed in
            # production: 87 zombie ssh.exe processes accumulated over
            # 19-22 hours, saturating the Windows network stack until
            # iPad GET requests couldn't even reach aiohttp's listener.
            logging.warning("run-script %s %s: timeout (30s); killing ssh.exe",
                            client_key, which)
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return (None, "timeout")
        text = (out or b"").decode("utf-8", "replace").strip()
        logging.warning("run-script %s %s rc=%s: %s", client_key, which,
                        proc.returncode, text.replace("\n", " ")[:300])
        return (proc.returncode, text)
    except Exception as e:  # noqa: BLE001
        # Catch-all: any other error (proc creation failed, etc.) -- still
        # try to kill if proc was created.
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        logging.warning("run-script %s %s failed: %s", client_key, which, e)
        return (None, str(e))


# =============================================================================
# PR-3 dispatcher (added alongside the legacy _run_device_script during the
# stacked rollout — Task 7 deletes the legacy path and makes _run_device_script
# call into this dispatcher exclusively).
# =============================================================================


async def _exec_ssh(client, script_template, vars_):
    """Run `script_template` over SSH on `client.ip`, substituting `{tokens}`
    via SafeDict so unknown tokens stay literal. Returns (rc, output). Mirrors
    the SSH-construction + 30s-timeout + kill-on-timeout discipline of the
    legacy _run_device_script generic path so byte-for-byte command shape
    on the wire is preserved.

    Caller is responsible for picking the right script_template (e.g.
    `profile.scripts["start"]`); this function does not look up scripts."""
    if not script_template:
        logging.warning("_exec_ssh %s: empty script template", client.ip)
        return (None, "no-script")
    script = script_template.format_map(SafeDict(vars_))
    cmd = (["ssh", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS +
           ["%s@%s" % (SSH_USER, client.ip), script])
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            logging.warning("_exec_ssh %s: timeout (30s); killing ssh.exe",
                            client.ip)
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return (None, "timeout")
        text = (out or b"").decode("utf-8", "replace").strip()
        logging.warning("_exec_ssh %s rc=%s: %s", client.ip, proc.returncode,
                        text.replace("\n", " ")[:300])
        return (proc.returncode, text)
    except Exception as e:  # noqa: BLE001
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        logging.warning("_exec_ssh %s failed: %s", client.ip, e)
        return (None, str(e))


async def _vnc_tap_sequence(client, launch_cfg, vars_):
    """Send each tap in `launch_cfg['taps']` (a list of {fbX,fbY} dicts)
    to the client's framebuffer via the pooled Veency connection. Returns
    True if every tap landed, False on the first failure (and the pooled
    connection is dropped so the next attempt re-handshakes).

    The pool itself still lives in server.py (see PR-1 TODO comment) —
    moving it here is a follow-up.
    """
    import server as _server
    taps = launch_cfg.get("taps") or []
    if not taps:
        logging.warning("_vnc_tap_sequence %s: no taps configured",
                        client.clientID)
        return False
    if not getattr(client, "ip", ""):
        logging.warning("_vnc_tap_sequence %s: no ip", client.clientID)
        return False
    loop = asyncio.get_running_loop()
    try:
        proxy = await _server._get_pooled_vnc(client.clientID, client.ip)
        for t in taps:
            fbX = int(t.get("fbX", 0))
            fbY = int(t.get("fbY", 0))
            await loop.run_in_executor(None, _server._do_tap, proxy, fbX, fbY)
            logging.info("vnc-tap: %s at fb(%d,%d)",
                         client.clientID, fbX, fbY)
        return True
    except Exception as e:  # noqa: BLE001
        await _drop_pooled_vnc(client.clientID)
        logging.warning("vnc-tap-sequence failed for %s: %s",
                        client.clientID, e)
        return False


async def _ssh_then_vnc(client, profile, vars_):
    """Run `profile.launch['wakeScript']` (or fall back to a known-safe
    default) over SSH to wake the device, settle 0.8s for the SpringBoard
    animation, then VNC-tap the icon coordinates in profile.launch['taps'].
    On tap failure, fall back to SSH-exec'ing profile.scripts['start']
    (same fallback the legacy _run_device_script does today).

    Return shape varies — callers MUST normalize:
      - `True`            : VNC tap succeeded (preferred path).
      - `(rc, out)` tuple : tap failed, fell back to SSH; check `rc == 0`.
      - `False`           : tap failed AND fallback SSH had nothing to run
                            (empty scripts['start']) — outer _exec_ssh
                            returns (None, "no-script") so we never hit
                            this branch with a real profile, but bool
                            False is what _vnc_tap_sequence returns on
                            its own failure paths.
    Task 3's run_profile_action normalizes all three into a uniform
    `(rc, out)` shape before returning to its caller."""
    wake_script = (profile.launch.get("wakeScript")
                   or "activator send libactivator.lockscreen.dismiss")
    await _exec_ssh(client, wake_script, vars_)
    await asyncio.sleep(0.8)
    ok = await _vnc_tap_sequence(client, profile.launch, vars_)
    if ok:
        return True
    logging.warning("_ssh_then_vnc %s: tap failed; falling back to "
                    "scripts['start'] via SSH", client.clientID)
    return await _exec_ssh(client, profile.scripts.get("start", ""), vars_)


# Dispatch table consumed by run_profile_action (added in Task 3).
# A profile's `launch["method"]` field keys into this table to select
# how a "start" action gets executed. Other lifecycle actions (login,
# stop, test, reboot) always go through plain _exec_ssh — only "start"
# needs a VNC tap on iPad-1 because iOS 5 has no CLI launcher that
# passes a webclip's URL context to Web.app.
#
# Return shape varies by entry — see _ssh_then_vnc docstring. Callers
# (run_profile_action) MUST normalize bool True/False + (rc, out)
# tuples into a uniform shape before returning to outer callers.
LAUNCH_METHODS = {
    "shell":        lambda c, p, v: _exec_ssh(c, p.scripts.get("start", ""), v),
    "vnc-tap":      lambda c, p, v: _vnc_tap_sequence(c, p.launch, v),
    "ssh-then-vnc": lambda c, p, v: _ssh_then_vnc(c, p, v),
}
