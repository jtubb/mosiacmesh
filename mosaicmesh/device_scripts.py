"""ScriptingProfile dispatcher — executes profile-driven lifecycle actions
over SSH and/or Veency VNC taps.

Public entry point: `_run_device_script(client_key, which)` (aliased to
`run_profile_action` for backwards compatibility with the pre-PR-3 call
sites in mosaicmesh/websocket/legacy.py and ad-hoc tests).

The three launch primitives — `_exec_ssh`, `_vnc_tap_sequence`,
`_ssh_then_vnc` — are selected by `profile.launch['method']` via the
`LAUNCH_METHODS` table. Lifecycle actions other than 'start' always go
through plain `_exec_ssh` because iOS 5 only requires the VNC dance to
launch a webclip; stopping or testing a running webapp just needs a
shell command.

SSH constants and the Veency `_drop_pooled_vnc` helper live here. The
pool ITSELF (`_veency_pool`, `_veency_lock`, `_get_pooled_vnc`, `_do_tap`)
still lives in server.py — see TODO in `_vnc_tap_sequence` for the
deferred cleanup."""
import os
import logging
import asyncio

from mosaicmesh.template_vars import SafeDict, build_vars

# SSH constants used by the dispatcher and re-exported to server.py for the
# VNC pool helpers (_get_pooled_vnc, _auto_arm_client) that still live there.
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


async def _run_device_script(client_key, which):
    """Public entry point — delegates to run_profile_action. Kept under the
    old name so existing call sites (mosaicmesh/websocket/legacy.py
    RUN_SCRIPT handler, ad-hoc tests that patch server._run_device_script)
    continue to work without change."""
    return await run_profile_action(client_key, which)


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
    # REQUIRED: run_profile_action uses this entry as the fallback for
    # unknown launch methods. Do not remove without updating that fallback.
    "shell":        lambda c, p, v: _exec_ssh(c, p.scripts.get("start", ""), v),
    "vnc-tap":      lambda c, p, v: _vnc_tap_sequence(c, p.launch, v),
    "ssh-then-vnc": lambda c, p, v: _ssh_then_vnc(c, p, v),
}


async def run_profile_action(client_key, which):
    """Run a single profile action ('login'|'start'|'stop'|'test'|'reboot')
    on a single client. Resolves the profile via client.profileName, builds
    the template-variable substitution map, then either:
      - routes 'start' through LAUNCH_METHODS[profile.launch['method']]
        (so 'ssh-then-vnc' / 'vnc-tap' / 'shell' all work uniformly), or
      - SSH-execs profile.scripts[which] for the other lifecycle actions
        (login/stop/test/reboot always go through plain SSH).

    Robust to missing profile (profileName=None or profile not in
    settings.profiles): logs a warning and returns (None, "no-profile").
    The dispatcher never raises into the caller — this is fleet-management
    code where one bad client must not break the loop.

    Returns the (rc, output) tuple from the underlying primitive (or
    True/False for VNC-only methods, which the caller treats as truthy
    success / falsy failure)."""
    import server as _server
    client = _server.settings.clients.get(client_key)
    if client is None:
        logging.warning("run_profile_action %s %s: no client", client_key, which)
        return (None, "no-client")
    if not getattr(client, "ip", ""):
        # Parity with the pre-PR-3 _run_device_script: a Client without an
        # IP can't be reached — skip silently rather than attempting
        # `ssh root@` (or a VNC connect to "") which would log noisy errors
        # and waste a 10s connect timeout per device on every fleet-wide
        # broadcast.
        logging.warning("run_profile_action %s %s: no ip", client_key, which)
        return (None, "no-ip")
    profile_name = getattr(client, "profileName", None)
    profile = (_server.settings.profiles.get(profile_name)
               if profile_name else None)
    if not profile:
        logging.warning("run_profile_action %s %s: no profile assigned "
                        "(profileName=%r)", client_key, which, profile_name)
        return (None, "no-profile")
    vars_ = build_vars(client, profile, displayUrl=_server.DISPLAY_URL)
    if which == "start":
        method = profile.launch.get("method", "shell")
        fn = LAUNCH_METHODS.get(method) or LAUNCH_METHODS["shell"]
        result = await fn(client, profile, vars_)
        # Normalize bool returns from VNC-only methods to (rc, out)-ish
        if isinstance(result, bool):
            return (0 if result else None, "VNC_TAP_OK" if result else "vnc-tap-failed")
        return result
    # login / stop / test / reboot — always SSH
    return await _exec_ssh(client, profile.scripts.get(which, ""), vars_)
