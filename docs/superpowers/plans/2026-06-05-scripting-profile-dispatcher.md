# ScriptingProfile Dispatcher Implementation Plan (PR-3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded per-Client lifecycle scripts (`loginScript`/`startScript`/`stopScript`/`testScript`/`rebootScript` + `DEFAULT_DEVICE_SCRIPTS`) with the `ScriptingProfile`-driven dispatcher described in section 7 of the admin-timeline-redesign spec. Existing fleet behavior must remain **byte-identical** after migration.

**Architecture:** `mosaicmesh/device_scripts.py` becomes the dispatcher — three launch primitives (`_exec_ssh`, `_vnc_tap_sequence`, `_ssh_then_vnc`) selected by `profile.launch["method"]` and parameterized by `profile.scripts[which]` + a template-variable substitution map. Profile assignment lives on `Client.profileName`; auto-match on REGISTER fills it from `profile.matchDeviceType`. A bootstrap seeds the `ipad1-ios5` default profile on first boot of a server with empty `settings.profiles`, with content **byte-identical** to the current `DEFAULT_DEVICE_SCRIPTS` literal. The old per-Client script fields are deleted from the class and stripped from settings.dat on first migration.

**Tech Stack:** Python 3.14, aiohttp, jsonpickle (settings persistence), `str.format_map` with `SafeDict` for template variables, pytest. No new third-party deps.

**Stacks on:** `feature/pr2-rest-endpoints` (PR #4). Branch this work on `feature/pr3-scripting-profile-dispatcher` (already created at plan-writing time).

**Spec section reference:** `docs/superpowers/specs/2026-06-04-admin-timeline-redesign-design.md` section 7 (Scripting Profiles), plus Appendix A's "Deleted from `server.py` (PR-3)".

---

## File Structure

| File | Action | Responsibility after this PR |
|---|---|---|
| `mosaicmesh/template_vars.py` | **Create** | `SafeDict` + `build_vars(client, profile, **extra)` returning a substitution dict. Pure functions, no I/O. |
| `mosaicmesh/device_scripts.py` | **Rewrite** | Dispatcher: `LAUNCH_METHODS` table, `_exec_ssh`, `_vnc_tap_sequence`, `_ssh_then_vnc`, public `_run_device_script(client_key, which)`. SSH constants stay; `DEFAULT_DEVICE_SCRIPTS`, `WEBCLIP_BUNDLE_ID`, `WEBAPP_ICON_FBX`, `WEBAPP_ICON_FBY`, `_launch_webapp_via_vnc` are **deleted**. |
| `mosaicmesh/state.py` | **Modify** | `Client.__init__`: add `profileName=None`, remove `loginScript`/`startScript`/`stopScript`/`testScript`/`rebootScript`. Delete `_apply_default_scripts`. Migrate function gains a profile-bootstrap step + per-Client `profileName` backfill + old-script-field deletion. |
| `mosaicmesh/profile_bootstrap.py` | **Create** | `DEFAULT_PROFILE_IPAD1_IOS5` literal + `seed_default_profile_if_empty(settings)` + `migrate_client_script_fields(settings)`. Isolated from state.py so the byte-identical literal is easy to find and audit. |
| `mosaicmesh/api/discovery.py` | **Modify** | `api_discovery_configure` no longer accepts the five `*Script` field updates (the block at lines 432-435 is deleted). |
| `server.py` | **Modify** | Drop `DEFAULT_DEVICE_SCRIPTS` and `_apply_default_scripts` from the imports. Keep `_run_device_script` re-export (call sites unchanged). |
| `tests/unit/test_template_vars.py` | **Create** | SafeDict semantics, build_vars output shape, unknown-token leave-literal. |
| `tests/unit/test_launch_dispatcher.py` | **Create** | Each `LAUNCH_METHODS` entry routes to the right primitive with the right args. |
| `tests/unit/test_profile_bootstrap.py` | **Create** | First-boot seeding is byte-identical to the old `DEFAULT_DEVICE_SCRIPTS` literal after template substitution; idempotent on second boot. |
| `tests/unit/test_client_migration.py` | **Create** | A Client loaded with the old script fields ends up with `profileName="ipad1-ios5"` and no `*Script` attributes. |
| `tests/unit/test_device_scripts.py` | **Rewrite** | Existing tests reference deleted constants. New tests target the dispatcher path. |
| `tests/unit/test_module_layout.py` | **Modify** | Remove `DEFAULT_DEVICE_SCRIPTS` and per-Client script-field assertions; add Client.profileName + dispatcher importability + default-profile presence assertions. |
| `CLAUDE.md` | **Modify** | Layout section updates: `device_scripts.py` and `state.py` lines reflect the new responsibilities. |
| `admin.html` | **Out of scope** | The `*Script` keys in the `displayKeys` map (line 91-94) will be removed by **PR-6**'s admin-timeline cleanup. Leaving them in this PR means the legacy admin UI shows blank values for those rows after migration — acceptable, since the old UI is being removed anyway. Document this in CLAUDE.md. |

---

## Why this ordering

The riskiest sub-step is the migration of an existing `settings.dat` containing live `*Script` field overrides set by operators via `/api/discovery/configure`. To minimize that risk we add the new structures **alongside** the old ones (Tasks 1-4), prove the dispatcher works (Tasks 1-4 tests), then bootstrap-and-migrate in one atomic step (Task 5), then cut over the runtime entry point (Task 6), then delete dead code (Task 7). Each phase can be reverted by a `git revert` of one commit.

---

## Task 1: Add `Client.profileName` field + template variable helpers

We need the data shape PR-3 expects (a `profileName` on every Client) and the substitution machinery the dispatcher will call into, **before** we change any execution paths.

**Files:**
- Modify: `mosaicmesh/state.py` (Client class)
- Create: `mosaicmesh/template_vars.py`
- Create: `tests/unit/test_template_vars.py`
- Modify: `tests/unit/test_module_layout.py`

### Step 1.1: Add `Client.profileName` attribute

- [ ] **Edit** `mosaicmesh/state.py`, find the `Client.__init__` block (around line 154) and add the field. **Do not yet remove** the five `*Script` fields — that happens in Task 5.

Locate the line `self.rebootScript = None` and insert after `self.testScript = None`:

```python
        # Selected scripting profile (key into Settings.profiles). None means
        # auto-match has not yet found a matching profile for this device's
        # deviceType (or no profiles exist). The dispatcher treats None as
        # "no scripts to run" and logs a warning.
        self.profileName = None
```

- [ ] **Edit** `mosaicmesh/state.py`, find `migrate_client_objects()` and add a `profileName` backfill at the end of the per-client loop, just before the hostname re-resolve block:

```python
        if not hasattr(client, 'profileName'):
            client.profileName = None
```

- [ ] **Run** `python pytest_runner.py --unit -k client_management -v` — should still pass (no regressions on the Client smoke).

Expected: `tests/unit/test_client_management.py` passes unchanged (Client gains an extra attribute; existing tests don't look at it).

### Step 1.2: Write the failing test for `template_vars.py`

- [ ] **Create** `tests/unit/test_template_vars.py`:

```python
"""Unit tests for mosaicmesh/template_vars.py — SafeDict + build_vars."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import argparse
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Client, ScriptingProfile
from mosaicmesh.template_vars import SafeDict, build_vars


def test_safedict_leaves_unknown_tokens_literal():
    """str.format_map(SafeDict(...)) MUST leave unresolved {tokens} unchanged
    rather than raising KeyError. This is the contract the operator-edited
    profile scripts rely on per spec §7."""
    out = "echo {known} and {unknown}".format_map(SafeDict({"known": "X"}))
    assert out == "echo X and {unknown}"


def test_build_vars_includes_client_fields():
    """build_vars(client, profile) returns a dict with all the spec-§7
    template variables filled from the client + profile objects."""
    c = Client()
    c.clientID = "abc-123"
    c.ip = "192.168.1.50"
    c.friendlyName = "screen1"
    c.displayID = "Default"
    c.cacheMode = "lighttpd-localhost"
    p = ScriptingProfile()
    p.webclip = {"bundleId": "com.apple.webapp-XYZ", "title": "MM"}
    p.launch = {"method": "ssh-then-vnc", "vncPassword": "secret"}
    vars_ = build_vars(c, p, displayUrl="http://1.2.3.4:3000/")
    assert vars_["clientID"] == "abc-123"
    assert vars_["ip"] == "192.168.1.50"
    assert vars_["friendlyName"] == "screen1"
    assert vars_["displayId"] == "Default"
    assert vars_["cacheMode"] == "lighttpd-localhost"
    assert vars_["displayUrl"] == "http://1.2.3.4:3000/"
    assert vars_["webclipBundleId"] == "com.apple.webapp-XYZ"
    assert vars_["webclipTitle"] == "MM"
    assert vars_["vncPassword"] == "secret"


def test_build_vars_handles_missing_profile_fields():
    """A profile with empty/missing webclip or launch dicts must still
    produce a usable substitution map — empty string for absent keys."""
    c = Client()
    c.clientID = "x"
    p = ScriptingProfile()   # default empty dicts
    vars_ = build_vars(c, p, displayUrl="http://x/")
    assert vars_["webclipBundleId"] == ""
    assert vars_["webclipTitle"] == ""
    assert vars_["vncPassword"] == ""


def test_template_substitution_through_safedict():
    """End-to-end: a script template with mixed known + unknown tokens
    substitutes cleanly and leaves unknowns literal."""
    c = Client(); c.ip = "10.0.0.5"
    p = ScriptingProfile()
    p.webclip = {"bundleId": "com.apple.webapp-AAAA", "title": "T"}
    script = "sbdidlaunch '{webclipBundleId}' || uiopen '{displayUrl}'; echo {unknownVar}"
    rendered = script.format_map(SafeDict(build_vars(c, p, displayUrl="http://h/")))
    assert rendered == "sbdidlaunch 'com.apple.webapp-AAAA' || uiopen 'http://h/'; echo {unknownVar}"
```

- [ ] **Run** `python -m pytest tests/unit/test_template_vars.py -c tests/pytest.ini -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'mosaicmesh.template_vars'`.

### Step 1.3: Implement `mosaicmesh/template_vars.py`

- [ ] **Create** `mosaicmesh/template_vars.py`:

```python
"""Template-variable substitution machinery for the ScriptingProfile
dispatcher (PR-3 of the admin-timeline-redesign spec).

Profile script strings (e.g. `profile.scripts["start"]`) contain
literal `{tokens}` such as `{webclipBundleId}` and `{displayUrl}`.
The dispatcher calls `str.format_map(SafeDict(build_vars(...)))` to
substitute them at run time. Unknown tokens are left literal — this
is intentional: operators may use template strings that include
shell variables (`$HOME`, etc.) or other content the substitution
layer should not try to interpret. See spec §7 "Template variables".
"""

__all__ = ["SafeDict", "build_vars"]


class SafeDict(dict):
    """A dict that returns `{key}` (the literal placeholder) on missing
    keys, so `str.format_map(SafeDict(...))` never raises KeyError.

    This is the canonical pattern recommended in PEP 3101 §"Format
    String Syntax" for safe partial substitution.
    """

    def __missing__(self, key):
        return "{" + key + "}"


def build_vars(client, profile, **extra):
    """Construct the substitution dict the dispatcher hands to
    `str.format_map(SafeDict(...))`. Pulls fields from the Client and
    ScriptingProfile per spec §7's table; extra keyword args (typically
    `displayUrl` from server config) merge in last and win on conflict.

    Returns a plain dict — wrap in SafeDict at the call site if you
    want missing-key tolerance during format_map.

    Robust to None / missing nested-dict fields: an unconfigured
    webclip or launch dict yields empty strings rather than raising.
    """
    webclip = getattr(profile, "webclip", None) or {}
    launch = getattr(profile, "launch", None) or {}
    vars_ = {
        "clientID":        getattr(client, "clientID", "") or "",
        "ip":              getattr(client, "ip", "") or "",
        "friendlyName":    getattr(client, "friendlyName", "") or "",
        "displayId":       getattr(client, "displayID", "") or "",
        "cacheMode":       getattr(client, "cacheMode", "") or "",
        "webclipBundleId": webclip.get("bundleId", "") or "",
        "webclipTitle":    webclip.get("title", "") or "",
        "vncPassword":     launch.get("vncPassword", "") or "",
    }
    vars_.update(extra)
    return vars_
```

- [ ] **Run** `python -m pytest tests/unit/test_template_vars.py -c tests/pytest.ini -v`

Expected: 4 tests PASS.

### Step 1.4: Add module-layout sanity check

- [ ] **Edit** `tests/unit/test_module_layout.py`. Find any existing `test_state_classes_importable` or similar smoke and add a section verifying `Client.profileName` defaults to `None` and `mosaicmesh.template_vars` is importable. Append to the file:

```python
def test_client_has_profileName_field():
    """PR-3: Client.profileName replaces the five per-client *Script
    fields. Defaults to None (no profile assigned yet)."""
    from mosaicmesh.state import Client
    c = Client()
    assert hasattr(c, "profileName")
    assert c.profileName is None


def test_template_vars_importable():
    """PR-3 dispatcher uses SafeDict + build_vars from
    mosaicmesh/template_vars.py."""
    from mosaicmesh.template_vars import SafeDict, build_vars
    assert callable(build_vars)
    assert SafeDict({"x": 1})["x"] == 1
    assert SafeDict({"x": 1})["missing"] == "{missing}"
```

- [ ] **Run** `python -m pytest tests/unit/test_module_layout.py -c tests/pytest.ini -v`

Expected: all tests in the file PASS, including the two new ones.

### Step 1.5: Commit

- [ ] **Commit**:

```bash
git add mosaicmesh/state.py mosaicmesh/template_vars.py tests/unit/test_template_vars.py tests/unit/test_module_layout.py
git commit -m "feat(state,template_vars): add Client.profileName + SafeDict substitution helpers

Foundation for the PR-3 ScriptingProfile dispatcher. Client gains a
nullable profileName attribute; mosaicmesh/template_vars.py centralizes
the SafeDict + build_vars helper the dispatcher will call into. No
behavior change yet — the dispatcher itself lands in subsequent tasks.

Backfill in migrate_client_objects ensures Clients loaded from an older
settings.dat get profileName=None on next start.

Part of PR-3 of the admin-timeline-redesign spec."
```

---

## Task 2: Implement launch primitives in `device_scripts.py` (alongside old code)

Add the three launch methods and a `LAUNCH_METHODS` dispatch table to `device_scripts.py`. The existing `_run_device_script`, `_launch_webapp_via_vnc`, and `DEFAULT_DEVICE_SCRIPTS` stay untouched — we wire them up in Task 4 and delete the old code in Task 7.

**Files:**
- Modify: `mosaicmesh/device_scripts.py`
- Create: `tests/unit/test_launch_dispatcher.py`

### Step 2.1: Write the failing test for the three launch primitives

- [ ] **Create** `tests/unit/test_launch_dispatcher.py`:

```python
"""Unit tests for the PR-3 launch dispatcher in mosaicmesh/device_scripts.py.

Each test stubs `asyncio.create_subprocess_exec` (or the VNC pool) and
verifies the dispatcher sends the right command/tap to the right place.
The real subprocess + VNC integration is exercised in a manual smoke
on a live iPad before PR-3 merges.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import argparse
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Client, ScriptingProfile
from mosaicmesh.device_scripts import (
    LAUNCH_METHODS, _exec_ssh, _vnc_tap_sequence, _ssh_then_vnc,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _profile(method, **launch):
    p = ScriptingProfile()
    p.name = "test"
    p.scripts = {"login": "echo LOGIN", "start": "echo START",
                 "stop": "echo STOP", "test": "echo TEST", "reboot": "echo REBOOT"}
    p.launch = {"method": method, **launch}
    p.webclip = {"bundleId": "BID", "title": "T"}
    p.ssh = {"legacyCrypto": True, "user": "root", "keyPath": "/tmp/k"}
    return p


def _client(ip="10.0.0.5"):
    c = Client(); c.ip = ip; c.clientID = "cid"
    return c


def test_launch_methods_table_has_three_entries():
    assert set(LAUNCH_METHODS.keys()) == {"shell", "vnc-tap", "ssh-then-vnc"}
    for k, fn in LAUNCH_METHODS.items():
        assert callable(fn), f"{k} entry is not callable"


def test_exec_ssh_builds_command_with_substituted_template():
    """_exec_ssh runs a script via ssh, substituting template variables
    into the script body before execution."""
    c = _client("192.168.1.99")
    p = _profile("shell")
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"OK", b""))
    fake_proc.returncode = 0
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock()
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=fake_proc)) as exec_mock:
        rc, out = _run(_exec_ssh(c, "echo hello-{ip}",
                                 {"ip": "192.168.1.99"}))
    assert rc == 0
    assert out == "OK"
    args = exec_mock.call_args.args
    assert "ssh" in args[0]
    assert "192.168.1.99" in args[-2]   # user@ip
    assert args[-1] == "echo hello-192.168.1.99"   # substituted script


def test_vnc_tap_sequence_taps_each_coord_in_order():
    """_vnc_tap_sequence iterates launch_cfg['taps'] and calls _do_tap
    for each coordinate."""
    c = _client()
    launch_cfg = {"method": "vnc-tap", "vncPassword": "pw",
                  "taps": [{"fbX": 100, "fbY": 200},
                           {"fbX": 300, "fbY": 400}]}
    proxy = MagicMock()
    server._veency_pool["cid"] = proxy   # pre-seed pool so _get_pooled_vnc returns instantly
    tapped = []
    with patch.object(server, "_get_pooled_vnc",
                      new=AsyncMock(return_value=proxy)), \
         patch.object(server, "_do_tap",
                      side_effect=lambda px, x, y: tapped.append((x, y))):
        ok = _run(_vnc_tap_sequence(c, launch_cfg, {}))
    server._veency_pool.pop("cid", None)
    assert ok is True
    assert tapped == [(100, 200), (300, 400)]


def test_ssh_then_vnc_runs_wakeScript_then_taps():
    """_ssh_then_vnc executes profile.launch['wakeScript'] over SSH
    first (best-effort), then calls _vnc_tap_sequence."""
    c = _client()
    p = _profile("ssh-then-vnc", vncPassword="pw",
                 wakeScript="activator send libactivator.lockscreen.dismiss",
                 taps=[{"fbX": 945, "fbY": 671}])
    fake_proc = MagicMock()
    fake_proc.wait = AsyncMock()
    proxy = MagicMock()
    tapped = []
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=fake_proc)) as exec_mock, \
         patch.object(server, "_get_pooled_vnc",
                      new=AsyncMock(return_value=proxy)), \
         patch.object(server, "_do_tap",
                      side_effect=lambda px, x, y: tapped.append((x, y))), \
         patch("asyncio.sleep", new=AsyncMock()):
        ok = _run(_ssh_then_vnc(c, p, {}))
    assert ok is True
    assert tapped == [(945, 671)]
    # The SSH wake step ran exactly once with the wakeScript body
    assert exec_mock.call_count == 1
    assert exec_mock.call_args.args[-1] == \
        "activator send libactivator.lockscreen.dismiss"


def test_ssh_then_vnc_falls_back_to_ssh_when_tap_fails():
    """If the VNC tap raises, _ssh_then_vnc falls back to running
    profile.scripts['start'] via SSH (the same fallback path the old
    _run_device_script uses today)."""
    c = _client()
    p = _profile("ssh-then-vnc", vncPassword="pw",
                 wakeScript="wake",
                 taps=[{"fbX": 945, "fbY": 671}])
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"FALLBACK_OK", b""))
    fake_proc.returncode = 0
    fake_proc.wait = AsyncMock()
    proxy = MagicMock()
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=fake_proc)) as exec_mock, \
         patch.object(server, "_get_pooled_vnc",
                      new=AsyncMock(return_value=proxy)), \
         patch.object(server, "_do_tap",
                      side_effect=RuntimeError("VNC unreachable")), \
         patch.object(server, "_drop_pooled_vnc", new=AsyncMock()), \
         patch("asyncio.sleep", new=AsyncMock()):
        ok = _run(_ssh_then_vnc(c, p, {}))
    # tap failed, so we fell back to ssh-exec the start script
    assert ok is False or ok == 0
    # The exec_mock should now have TWO calls: the wake step + the fallback start
    assert exec_mock.call_count == 2
```

- [ ] **Run** `python -m pytest tests/unit/test_launch_dispatcher.py -c tests/pytest.ini -v`

Expected: FAIL — `ImportError: cannot import name 'LAUNCH_METHODS' from 'mosaicmesh.device_scripts'`.

### Step 2.2: Add the three primitives + `LAUNCH_METHODS` to `device_scripts.py`

- [ ] **Edit** `mosaicmesh/device_scripts.py`. Append the new dispatcher code at the **end of the file** (after `_run_device_script`). Do **not** remove anything yet — old + new live side-by-side until Task 7.

Append:

```python

# =============================================================================
# PR-3 dispatcher (added alongside the legacy _run_device_script during the
# stacked rollout — Task 7 deletes the legacy path and makes _run_device_script
# call into this dispatcher exclusively).
# =============================================================================

from mosaicmesh.template_vars import SafeDict


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
    loop = asyncio.get_event_loop()
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

    Returns True on a successful tap, the (rc, out) tuple from the fallback
    SSH path when the tap fails, or False when both fail."""
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
LAUNCH_METHODS = {
    "shell":        lambda c, p, v: _exec_ssh(c, p.scripts.get("start", ""), v),
    "vnc-tap":      lambda c, p, v: _vnc_tap_sequence(c, p.launch, v),
    "ssh-then-vnc": lambda c, p, v: _ssh_then_vnc(c, p, v),
}
```

- [ ] **Run** `python -m pytest tests/unit/test_launch_dispatcher.py -c tests/pytest.ini -v`

Expected: all 5 tests PASS.

### Step 2.3: Commit

- [ ] **Commit**:

```bash
git add mosaicmesh/device_scripts.py tests/unit/test_launch_dispatcher.py
git commit -m "feat(device_scripts): add PR-3 launch primitives + LAUNCH_METHODS dispatch table

_exec_ssh, _vnc_tap_sequence, and _ssh_then_vnc are the three primitives
the ScriptingProfile dispatcher (Task 3 of this PR) will route 'start'
actions through. Each follows the existing _run_device_script's discipline
on subprocess kill-on-timeout, pool-drop on failure, and (for ssh-then-vnc)
SSH fallback after a failed tap.

The legacy _run_device_script and _launch_webapp_via_vnc are untouched —
Task 7 deletes them once Task 6 has cut callers over to run_profile_action.

Part of PR-3 of the admin-timeline-redesign spec."
```

---

## Task 3: Add `run_profile_action` — the new public entry point

This is the function `_run_device_script` will become in Task 7. For now it lives alongside.

**Files:**
- Modify: `mosaicmesh/device_scripts.py`
- Create: `tests/unit/test_run_profile_action.py`

### Step 3.1: Write failing tests for `run_profile_action`

- [ ] **Create** `tests/unit/test_run_profile_action.py`:

```python
"""Tests for run_profile_action — the new public dispatcher entry point.

Behavioral contract:
  - Resolves profile via client.profileName -> server.settings.profiles[name]
  - Returns ("no-profile", None) and logs a warning when no profile is set
    or the named profile is missing (no crash, fleet-wide robustness)
  - Routes 'start' through LAUNCH_METHODS[profile.launch['method']]
  - Routes login/stop/test/reboot through _exec_ssh(profile.scripts[which])
  - Substitutes template variables in the script body before execution
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import argparse
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Client, ScriptingProfile, Settings
from mosaicmesh.device_scripts import run_profile_action


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _setup_fleet():
    """Replace server.settings with a fresh Settings containing one Client
    pointing at one profile. Returns (clientKey, profile)."""
    server.settings = Settings()
    p = ScriptingProfile()
    p.name = "ipad1-ios5"
    p.scripts = {"login": "echo LOGIN-{ip}", "start": "echo START-{ip}",
                 "stop":  "echo STOP-{ip}",  "test":  "echo TEST-{ip}",
                 "reboot":"echo REBOOT"}
    p.launch = {"method": "shell"}
    p.webclip = {"bundleId": "BID", "title": "T"}
    server.settings.profiles["ipad1-ios5"] = p
    c = Client(); c.ip = "10.0.0.5"; c.clientID = "abc"
    c.profileName = "ipad1-ios5"
    server.settings.clients["abc"] = c
    return "abc", p


def test_no_profile_assigned_logs_and_returns():
    """A client with profileName=None must NOT crash — return a sentinel."""
    server.settings = Settings()
    c = Client(); c.ip = "10.0.0.5"; c.clientID = "x"
    c.profileName = None
    server.settings.clients["x"] = c
    rc, out = _run(run_profile_action("x", "start"))
    assert rc is None
    assert out == "no-profile"


def test_unknown_profile_name_logs_and_returns():
    """profileName points to a profile that doesn't exist — same robust
    no-crash path as no-profile."""
    server.settings = Settings()
    c = Client(); c.ip = "10.0.0.5"; c.clientID = "x"
    c.profileName = "ghost"
    server.settings.clients["x"] = c
    rc, out = _run(run_profile_action("x", "start"))
    assert rc is None
    assert out == "no-profile"


def test_login_routes_through_exec_ssh_with_substitution():
    ckey, _ = _setup_fleet()
    with patch("mosaicmesh.device_scripts._exec_ssh",
               new=AsyncMock(return_value=(0, "ok"))) as mock_exec:
        rc, out = _run(run_profile_action(ckey, "login"))
    assert rc == 0
    # _exec_ssh was called with the login template (NOT yet substituted —
    # _exec_ssh does the substitution internally)
    args = mock_exec.call_args.args
    assert args[1] == "echo LOGIN-{ip}"
    # vars_ dict contains the right substitution values
    assert args[2]["ip"] == "10.0.0.5"


def test_start_routes_through_LAUNCH_METHODS():
    ckey, p = _setup_fleet()
    p.launch = {"method": "shell"}
    with patch("mosaicmesh.device_scripts._exec_ssh",
               new=AsyncMock(return_value=(0, "ok"))) as mock_exec:
        rc, out = _run(run_profile_action(ckey, "start"))
    assert rc == 0
    # 'shell' launch method calls _exec_ssh(profile.scripts['start'])
    assert mock_exec.call_args.args[1] == "echo START-{ip}"


def test_unknown_launch_method_falls_back_to_exec_ssh_with_start():
    """If profile.launch['method'] is unrecognized, dispatcher falls back to
    executing scripts['start'] via SSH — same as 'shell' method."""
    ckey, p = _setup_fleet()
    p.launch = {"method": "wat"}
    with patch("mosaicmesh.device_scripts._exec_ssh",
               new=AsyncMock(return_value=(0, "ok"))) as mock_exec:
        rc, out = _run(run_profile_action(ckey, "start"))
    assert rc == 0
    assert mock_exec.call_args.args[1] == "echo START-{ip}"
```

- [ ] **Run** `python -m pytest tests/unit/test_run_profile_action.py -c tests/pytest.ini -v`

Expected: FAIL — `ImportError: cannot import name 'run_profile_action'`.

### Step 3.2: Implement `run_profile_action`

- [ ] **Edit** `mosaicmesh/device_scripts.py`. Append after the `LAUNCH_METHODS` dict:

```python


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
    if not client:
        logging.warning("run_profile_action %s %s: no client", client_key, which)
        return (None, "no-client")
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
```

- [ ] **Edit the import line at the top of the new dispatcher section** (added in Task 2's Step 2.2) to also import `build_vars`:

Find:
```python
from mosaicmesh.template_vars import SafeDict
```

Change to:
```python
from mosaicmesh.template_vars import SafeDict, build_vars
```

- [ ] **Run** `python -m pytest tests/unit/test_run_profile_action.py -c tests/pytest.ini -v`

Expected: all 5 tests PASS.

### Step 3.3: Commit

- [ ] **Commit**:

```bash
git add mosaicmesh/device_scripts.py tests/unit/test_run_profile_action.py
git commit -m "feat(device_scripts): add run_profile_action — the PR-3 dispatcher entry point

run_profile_action(client_key, which) is what _run_device_script becomes
in Task 7. Today it lives alongside the legacy code so Task 6's cut-over
is a one-commit rename + caller update, easy to revert.

Robust to missing profile (returns no-profile sentinel, never raises) —
fleet-management code can't allow one mis-configured client to break the
batch loop.

Part of PR-3 of the admin-timeline-redesign spec."
```

---

## Task 4: Bootstrap default profile + migrate Client objects

Create the isolated bootstrap module with the byte-identical default profile literal, then wire it into `migrate_client_objects` so the first server start with empty `settings.profiles` seeds the `ipad1-ios5` profile and migrates all existing Clients.

**Files:**
- Create: `mosaicmesh/profile_bootstrap.py`
- Modify: `mosaicmesh/state.py`
- Create: `tests/unit/test_profile_bootstrap.py`
- Create: `tests/unit/test_client_migration.py`

### Step 4.1: Write the failing test for the default profile content (byte-identical fidelity)

- [ ] **Create** `tests/unit/test_profile_bootstrap.py`:

```python
"""Tests for mosaicmesh/profile_bootstrap.py — the ipad1-ios5 default
profile seed + byte-identical-content guarantees.

The default profile's scripts MUST, after template-variable substitution
against a placeholder client, produce strings identical to the old
DEFAULT_DEVICE_SCRIPTS literal (which is being deleted in Task 7). A
divergence here is the highest-risk change in PR-3 — a one-character
edit to the iPad's login or stop script can take down the fleet.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import argparse
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Settings, Client, ScriptingProfile
from mosaicmesh.template_vars import SafeDict, build_vars
from mosaicmesh.profile_bootstrap import (
    DEFAULT_PROFILE_IPAD1_IOS5,
    seed_default_profile_if_empty,
    migrate_client_script_fields,
)


def test_default_profile_shape():
    """The seeded profile has every script + launch + webclip + ssh field
    the spec §7 'Bootstrap & migration' block requires."""
    p = DEFAULT_PROFILE_IPAD1_IOS5
    assert p.name == "ipad1-ios5"
    assert p.matchDeviceType == "Tablet"
    assert set(p.scripts.keys()) == {"login", "start", "stop", "test", "reboot"}
    assert p.launch["method"] == "ssh-then-vnc"
    assert p.launch["vncPassword"] == "mosaicmesh"
    assert p.launch["taps"] == [{"fbX": 945, "fbY": 671}]
    assert p.webclip["bundleId"] == \
        "com.apple.webapp-4D6F736169634D6573684B696F736B31"
    assert p.ssh["legacyCrypto"] is True


def test_default_profile_login_script_byte_identical_to_legacy():
    """After template substitution against a sample client, the default
    profile's login script equals the legacy DEFAULT_DEVICE_SCRIPTS
    ['loginScript'] literal (which has no template tokens, so vars_ is
    irrelevant — this is a literal-string match)."""
    c = Client(); c.ip = "1.2.3.4"
    vars_ = build_vars(c, DEFAULT_PROFILE_IPAD1_IOS5,
                       displayUrl="http://192.168.1.60:3000/")
    rendered = (DEFAULT_PROFILE_IPAD1_IOS5.scripts["login"]
                .format_map(SafeDict(vars_)))
    expected = (
        "activator send libactivator.lockscreen.dismiss; sleep 1; "
        "activator send switch-off.com.a3tweaks.switch.autolock; "
        "su mobile -c 'defaults write com.apple.springboard SBOrientationLockedActive -bool YES' 2>/dev/null; "
        "su mobile -c 'defaults write com.apple.springboard SBOrientationLockedOrientation -int 1' 2>/dev/null; "
        "echo LOGIN_OK"
    )
    assert rendered == expected


def test_default_profile_start_script_after_substitution():
    c = Client()
    vars_ = build_vars(c, DEFAULT_PROFILE_IPAD1_IOS5,
                       displayUrl="http://192.168.1.60:3000/")
    rendered = (DEFAULT_PROFILE_IPAD1_IOS5.scripts["start"]
                .format_map(SafeDict(vars_)))
    expected = ("sbdidlaunch 'com.apple.webapp-4D6F736169634D6573684B696F736B31' 2>/dev/null"
                " || uiopen 'http://192.168.1.60:3000/'; echo START_OK")
    assert rendered == expected


def test_default_profile_stop_test_reboot_byte_identical():
    c = Client()
    vars_ = build_vars(c, DEFAULT_PROFILE_IPAD1_IOS5,
                       displayUrl="http://192.168.1.60:3000/")
    def render(which):
        return (DEFAULT_PROFILE_IPAD1_IOS5.scripts[which]
                .format_map(SafeDict(vars_)))
    assert render("stop") == (
        "killall Web 2>/dev/null; killall MobileSafari 2>/dev/null; "
        "activator send switch-on.com.a3tweaks.switch.autolock; "
        "activator send libactivator.system.sleepbutton; echo STOP_OK"
    )
    assert render("test") == (
        "killall MobileSafari 2>/dev/null; sleep 1; "
        "uiopen 'http://192.168.1.60:3000/?tdbg'; echo TEST_OK"
    )
    assert render("reboot") == "echo REBOOTING; reboot"


def test_seed_when_empty_creates_profile():
    s = Settings()
    assert s.profiles == {}
    seed_default_profile_if_empty(s)
    assert "ipad1-ios5" in s.profiles
    assert s.profiles["ipad1-ios5"].name == "ipad1-ios5"


def test_seed_is_idempotent():
    """Calling seed twice MUST NOT overwrite an existing profile —
    operators may have edited it post-bootstrap."""
    s = Settings()
    seed_default_profile_if_empty(s)
    s.profiles["ipad1-ios5"].label = "edited-by-operator"
    seed_default_profile_if_empty(s)
    assert s.profiles["ipad1-ios5"].label == "edited-by-operator"


def test_seed_skips_when_other_profiles_exist():
    """If profiles is non-empty (operator has set up custom profiles
    without seeding the default), don't auto-seed — let them decide."""
    s = Settings()
    custom = ScriptingProfile()
    custom.name = "android-tv"
    s.profiles["android-tv"] = custom
    seed_default_profile_if_empty(s)
    assert "ipad1-ios5" not in s.profiles
```

- [ ] **Run** `python -m pytest tests/unit/test_profile_bootstrap.py -c tests/pytest.ini -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'mosaicmesh.profile_bootstrap'`.

### Step 4.2: Implement `mosaicmesh/profile_bootstrap.py`

- [ ] **Create** `mosaicmesh/profile_bootstrap.py`:

```python
"""Default ScriptingProfile content + one-shot migration from the
pre-PR-3 per-Client script fields.

The byte-identical guarantee on the default profile's scripts is the
single highest-risk part of PR-3 (a one-character drift here can brick
the fleet at next 'start' broadcast). All five scripts are pinned by
explicit unit tests in tests/unit/test_profile_bootstrap.py — DO NOT
edit the literals below without updating those tests in the same
commit.

The migration is also one-shot: once Clients are migrated and the old
fields are gone from Client.__init__, jsonpickle can't restore them on
the next start. settings.dat backup at the PR-3 boundary is therefore
mandatory operationally (operator responsibility — documented in the
PR-3 PR description).
"""
import copy
import logging

from mosaicmesh.state import ScriptingProfile

__all__ = [
    "DEFAULT_PROFILE_IPAD1_IOS5",
    "seed_default_profile_if_empty",
    "migrate_client_script_fields",
]


def _make_default_profile():
    p = ScriptingProfile()
    p.name = "ipad1-ios5"
    p.label = "iPad 1 — iOS 5.1.1"
    p.matchDeviceType = "Tablet"
    p.scripts = {
        "login":  ("activator send libactivator.lockscreen.dismiss; sleep 1; "
                   "activator send switch-off.com.a3tweaks.switch.autolock; "
                   "su mobile -c 'defaults write com.apple.springboard SBOrientationLockedActive -bool YES' 2>/dev/null; "
                   "su mobile -c 'defaults write com.apple.springboard SBOrientationLockedOrientation -int 1' 2>/dev/null; "
                   "echo LOGIN_OK"),
        "start":  ("sbdidlaunch '{webclipBundleId}' 2>/dev/null"
                   " || uiopen '{displayUrl}'; echo START_OK"),
        "stop":   ("killall Web 2>/dev/null; killall MobileSafari 2>/dev/null; "
                   "activator send switch-on.com.a3tweaks.switch.autolock; "
                   "activator send libactivator.system.sleepbutton; echo STOP_OK"),
        "test":   ("killall MobileSafari 2>/dev/null; sleep 1; "
                   "uiopen '{displayUrl}?tdbg'; echo TEST_OK"),
        "reboot": "echo REBOOTING; reboot",
    }
    p.launch = {
        "method": "ssh-then-vnc",
        "vncPassword": "mosaicmesh",
        "wakeScript": "activator send libactivator.lockscreen.dismiss",
        "taps": [{"fbX": 945, "fbY": 671}],
    }
    p.webclip = {
        "bundleId": "com.apple.webapp-4D6F736169634D6573684B696F736B31",
        "title":    "MosaicMesh",
    }
    p.ssh = {
        "legacyCrypto": True,
        "user":         "root",
        "keyPath":      "~/.ssh/mosaic_ipad",
    }
    p._serverVersion = 1
    return p


# Module-level singleton for tests that compare-against-content. Use
# seed_default_profile_if_empty() in production — it deep-copies so
# concurrent edits don't corrupt the canonical literal.
DEFAULT_PROFILE_IPAD1_IOS5 = _make_default_profile()


def seed_default_profile_if_empty(settings):
    """If settings.profiles is empty, install a deep copy of the default
    ipad1-ios5 profile. No-op when profiles already contains any entries
    — protects operator-supplied custom profiles AND prevents overwriting
    edits to the default itself on second-boot."""
    if not getattr(settings, "profiles", None):
        settings.profiles = {}
    if settings.profiles:
        return
    settings.profiles["ipad1-ios5"] = copy.deepcopy(DEFAULT_PROFILE_IPAD1_IOS5)
    logging.info("profile-bootstrap: seeded ipad1-ios5 default profile")


def migrate_client_script_fields(settings):
    """One-shot migration for an existing settings.dat:
      - Every Client with profileName=None (or absent) gets the default.
      - The five legacy script attributes (loginScript/startScript/
        stopScript/testScript/rebootScript) are deleted off each Client.

    Idempotent: a Client that already has profileName set is left alone,
    and a Client without legacy script attrs sees the delete-loop no-op."""
    legacy_fields = ("loginScript", "startScript", "stopScript",
                     "testScript", "rebootScript")
    migrated = 0
    for client_key, client in (settings.clients or {}).items():
        if not getattr(client, "profileName", None):
            client.profileName = "ipad1-ios5"
            migrated += 1
        for f in legacy_fields:
            if hasattr(client, f):
                try:
                    delattr(client, f)
                except AttributeError:
                    pass
    if migrated:
        logging.info("profile-bootstrap: migrated %d Client(s) to "
                     "profileName='ipad1-ios5' + cleared legacy script fields",
                     migrated)
```

- [ ] **Run** `python -m pytest tests/unit/test_profile_bootstrap.py -c tests/pytest.ini -v`

Expected: 7 tests PASS.

### Step 4.3: Write the migration test (`test_client_migration.py`)

- [ ] **Create** `tests/unit/test_client_migration.py`:

```python
"""End-to-end migration: a Client object with the pre-PR-3 layout
(loginScript/startScript/etc. attributes) ends up with profileName set
and those legacy attributes stripped after migrate_client_script_fields()."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import argparse
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Settings, Client
from mosaicmesh.profile_bootstrap import migrate_client_script_fields


def _legacy_client():
    """Build a Client with the pre-PR-3 attribute layout (script fields
    set, profileName absent) — simulates what jsonpickle restores from an
    older settings.dat."""
    c = Client()
    c.loginScript  = "old-login"
    c.startScript  = "old-start"
    c.stopScript   = "old-stop"
    c.testScript   = "old-test"
    c.rebootScript = "old-reboot"
    # Simulate pre-PR-3 by removing the new attribute jsonpickle wouldn't have
    if hasattr(c, "profileName"):
        delattr(c, "profileName")
    return c


def test_legacy_client_gets_profileName_set():
    s = Settings()
    s.clients["a"] = _legacy_client()
    migrate_client_script_fields(s)
    assert s.clients["a"].profileName == "ipad1-ios5"


def test_legacy_client_loses_old_script_attrs():
    s = Settings()
    s.clients["a"] = _legacy_client()
    migrate_client_script_fields(s)
    c = s.clients["a"]
    for f in ("loginScript", "startScript", "stopScript",
              "testScript", "rebootScript"):
        assert not hasattr(c, f), f"{f} should be stripped"


def test_migration_is_idempotent():
    """Second call to migrate must not re-overwrite or re-add attributes."""
    s = Settings()
    s.clients["a"] = _legacy_client()
    migrate_client_script_fields(s)
    s.clients["a"].profileName = "custom-override"   # operator edits
    migrate_client_script_fields(s)
    assert s.clients["a"].profileName == "custom-override"


def test_migration_preserves_already_set_profileName():
    """A client that already has profileName set (e.g. via REST POST)
    is left alone."""
    s = Settings()
    c = Client(); c.profileName = "android-tv"
    s.clients["a"] = c
    migrate_client_script_fields(s)
    assert s.clients["a"].profileName == "android-tv"
```

- [ ] **Run** `python -m pytest tests/unit/test_client_migration.py -c tests/pytest.ini -v`

Expected: 4 tests PASS.

### Step 4.4: Wire bootstrap + migration into `migrate_client_objects`

- [ ] **Edit** `mosaicmesh/state.py`, find `migrate_client_objects()` and add a call to the bootstrap+migration at the end of the function (after the per-client loop completes):

Find:
```python
        # Re-attempt resolution for clients that never got a hostname (e.g.
        # resolved blank before DNS was fixed / before the mDNS fallback). The
        # 60s retry throttle keeps perpetually-nameless devices from churning.
        if not getattr(client, 'hostname', ''):
            client.hostnameResolved = False
```

Append after that line, OUTSIDE the per-client loop (at the function level):
```python

    # PR-3 bootstrap: seed the ipad1-ios5 default profile on a settings.dat
    # that pre-dates Settings.profiles, AND migrate every Client object's
    # legacy script fields to the new profileName indirection.
    from mosaicmesh.profile_bootstrap import (
        seed_default_profile_if_empty, migrate_client_script_fields,
    )
    seed_default_profile_if_empty(settings)
    migrate_client_script_fields(settings)
```

- [ ] **Run** `python -m pytest tests/unit -c tests/pytest.ini --tb=no -q | tail -10`

Expected: previous pre-existing failures unchanged (13) + new PR-3 tests pass. No regressions.

### Step 4.5: Commit

- [ ] **Commit**:

```bash
git add mosaicmesh/profile_bootstrap.py mosaicmesh/state.py \
        tests/unit/test_profile_bootstrap.py tests/unit/test_client_migration.py
git commit -m "feat(profile_bootstrap): seed ipad1-ios5 default + migrate legacy Clients

mosaicmesh/profile_bootstrap.py owns the byte-identical-to-legacy
default profile literal (audited by tests/unit/test_profile_bootstrap.py)
and the one-shot Client.profileName backfill + legacy *Script attribute
strip (audited by tests/unit/test_client_migration.py).

Wired into migrate_client_objects, so the next server start with an
existing settings.dat does both in one atomic step. Operators MUST keep
a settings.dat backup at the PR-3 boundary — once the legacy script
fields are gone from Client.__init__ in Task 7, jsonpickle can't restore
them from an older dump.

Part of PR-3 of the admin-timeline-redesign spec."
```

---

## Task 5: Auto-match `profileName` on REGISTER

When a new client connects, auto-assign the first profile whose `matchDeviceType` matches `client.deviceType`. This is the spec §7 "Changes to Client" requirement.

**Files:**
- Modify: `mosaicmesh/api/discovery.py`
- Create: `tests/unit/test_profile_auto_match.py`

### Step 5.1: Write the failing test for auto-match

- [ ] **Create** `tests/unit/test_profile_auto_match.py`:

```python
"""Auto-match profileName on REGISTER per spec §7 'Changes to Client'."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import argparse
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Settings, Client, ScriptingProfile
from mosaicmesh.api.discovery import auto_match_profile


def _profile(name, match):
    p = ScriptingProfile()
    p.name = name; p.matchDeviceType = match
    return p


def test_match_by_device_type_case_insensitive():
    """device_detector emits lowercase ('tablet'); humans write profiles
    with the conventional capitalized form ('Tablet'). The match must
    succeed in both directions."""
    s = Settings()
    s.profiles["ipad"]    = _profile("ipad",    "Tablet")
    s.profiles["android"] = _profile("android", "Mobile")
    c = Client(); c.deviceType = "tablet"   # production lowercase
    assert auto_match_profile(c, s) == "ipad"


def test_match_when_profile_label_is_lowercase():
    """Symmetric case: if a profile is written lowercase 'tablet' it
    still matches a 'Tablet' deviceType."""
    s = Settings()
    s.profiles["ipad"] = _profile("ipad", "tablet")
    c = Client(); c.deviceType = "Tablet"
    assert auto_match_profile(c, s) == "ipad"


def test_no_match_returns_None():
    s = Settings()
    s.profiles["ipad"] = _profile("ipad", "Tablet")
    c = Client(); c.deviceType = "desktop"
    assert auto_match_profile(c, s) is None


def test_empty_matchDeviceType_is_manual_only():
    """A profile with matchDeviceType='' is never auto-assigned."""
    s = Settings()
    s.profiles["custom"] = _profile("custom", "")
    c = Client(); c.deviceType = "Tablet"
    assert auto_match_profile(c, s) is None


def test_does_not_override_already_set_profileName():
    """If client.profileName is already set (operator override), the
    REGISTER auto-match must not change it."""
    s = Settings()
    s.profiles["ipad"] = _profile("ipad", "Tablet")
    c = Client(); c.deviceType = "Tablet"; c.profileName = "custom-override"
    # The helper just returns a candidate; the caller decides. But test
    # the helper's "candidate" output is still the matching profile —
    # the caller is responsible for the not-override semantics.
    assert auto_match_profile(c, s) == "ipad"
```

- [ ] **Run** `python -m pytest tests/unit/test_profile_auto_match.py -c tests/pytest.ini -v`

Expected: FAIL — `ImportError: cannot import name 'auto_match_profile'`.

### Step 5.2: Implement `auto_match_profile` + wire into REGISTER

- [ ] **Edit** `mosaicmesh/api/discovery.py`. Find `auto_configure_client` (the function that sets `client.displayID` based on deviceType — used by the REGISTER handler). Just above it, add:

```python
def auto_match_profile(client, settings):
    """Return the name of the first profile whose matchDeviceType equals
    client.deviceType (case-insensitive), or None if no profile matches.
    A profile with matchDeviceType='' is treated as manual-only and never
    matched.

    Case-insensitive comparison because device_detector emits lowercase
    deviceType ('tablet', 'smartphone', 'desktop') but profile labels
    written by humans through the REST API or admin UI usually capitalize
    ('Tablet'). Spec §7's example default uses 'Tablet'; production
    Client.deviceType is 'tablet'. Normalizing on both sides removes
    the trap.

    Per spec §7: 'assigned at REGISTER from first profile whose
    matchDeviceType matches client.deviceType; admin can override'."""
    dt = (getattr(client, "deviceType", "") or "").lower()
    if not dt:
        return None
    for name, prof in (settings.profiles or {}).items():
        match = (getattr(prof, "matchDeviceType", "") or "").lower()
        if match and match == dt:
            return name
    return None
```

Also export it — find the `__all__` list near the top of the file and add `"auto_match_profile"`.

- [ ] **Find `auto_configure_client`** in the same file. The function does `import server` on its first line and uses `server.settings.X` throughout. Just before the `client.autoConfigured = True` line (near the end), add the auto-match wiring:

```python
    # PR-3: auto-assign a ScriptingProfile on first connect. Only fires
    # when profileName is still None (operator overrides via
    # POST /api/clients/{key}/profile take precedence forever after).
    if not getattr(client, "profileName", None):
        client.profileName = auto_match_profile(client, server.settings)
```

(`auto_match_profile` is defined at module level in the same file, so no import needed. `server` is already in scope from the function's existing `import server` on its first line.)

- [ ] **Run** `python -m pytest tests/unit/test_profile_auto_match.py -c tests/pytest.ini -v`

Expected: 4 tests PASS.

### Step 5.3: Commit

- [ ] **Commit**:

```bash
git add mosaicmesh/api/discovery.py tests/unit/test_profile_auto_match.py
git commit -m "feat(api/discovery): auto-match profileName on REGISTER

A new client whose deviceType matches an existing profile's
matchDeviceType is auto-assigned to that profile at REGISTER time
(via auto_configure_client). Operator overrides via POST
/api/clients/{key}/profile take precedence and are preserved.

Part of PR-3 of the admin-timeline-redesign spec."
```

---

## Task 6: Cut over `_run_device_script` to the new dispatcher

This is the **runtime entry-point flip**. Until now, the legacy `_run_device_script` and `run_profile_action` co-exist. After this task, `_run_device_script` IS `run_profile_action` (renamed for caller compatibility) — and the legacy implementation is gone.

**Files:**
- Modify: `mosaicmesh/device_scripts.py`
- Modify: `tests/unit/test_device_scripts.py` (rewrite — legacy assertions no longer apply)
- Delete: `tests/unit/test_run_profile_action.py` (its checks merge into the rewritten test_device_scripts.py)

### Step 6.1: Rewrite `tests/unit/test_device_scripts.py` against the new dispatcher

- [ ] **Read** the existing `tests/unit/test_device_scripts.py` so you know what to preserve. The smoke shape (build SSH command with legacy crypto + per-device IP) survives; the `DEFAULT_DEVICE_SCRIPTS` / per-client-script-field assertions are replaced.

- [ ] **Replace** the entire contents of `tests/unit/test_device_scripts.py` with:

```python
"""Unit tests for _run_device_script — the public dispatcher entry point
after the PR-3 ScriptingProfile cut-over.

Pre-PR-3 this test file targeted per-Client {login,start,stop,reboot}Script
fields + DEFAULT_DEVICE_SCRIPTS. Those are gone; behavior now flows through
client.profileName -> settings.profiles[name] -> dispatcher."""
import sys, asyncio, argparse
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Settings, Client, ScriptingProfile
from mosaicmesh.profile_bootstrap import DEFAULT_PROFILE_IPAD1_IOS5


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _seeded(ckey="a", ip="10.0.0.5"):
    server.settings = Settings()
    import copy
    server.settings.profiles["ipad1-ios5"] = copy.deepcopy(DEFAULT_PROFILE_IPAD1_IOS5)
    c = Client(); c.clientID = ckey; c.ip = ip
    c.profileName = "ipad1-ios5"
    server.settings.clients[ckey] = c
    return ckey


def test_run_device_script_builds_legacy_ssh_command_for_login():
    """Login goes straight through _exec_ssh — verify the SSH command
    shape (legacy crypto opts, ssh key path, user@ip, substituted script
    body) matches what the iPad-1 sshd expects."""
    ckey = _seeded()
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"LOGIN_OK\n", b""))
    fake_proc.returncode = 0
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock()
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=fake_proc)) as exec_mock:
        rc, out = _run(server._run_device_script(ckey, "login"))
    assert rc == 0
    args = exec_mock.call_args.args
    assert "ssh" in args[0]
    assert "-o" in args and "HostKeyAlgorithms=+ssh-rsa" in args
    assert args[-2] == "root@10.0.0.5"
    # Substituted login script must contain the literal command body (no
    # {tokens} left)
    assert "activator send libactivator.lockscreen.dismiss" in args[-1]
    assert "echo LOGIN_OK" in args[-1]


def test_run_device_script_no_profile_is_noop():
    """A Client with profileName=None returns the sentinel (None, 'no-profile')
    without attempting any subprocess work."""
    server.settings = Settings()
    c = Client(); c.clientID = "x"; c.ip = "10.0.0.5"; c.profileName = None
    server.settings.clients["x"] = c
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock()) as exec_mock:
        rc, out = _run(server._run_device_script("x", "login"))
    assert rc is None
    assert out == "no-profile"
    assert exec_mock.call_count == 0


def test_run_device_script_unknown_profile_is_noop():
    server.settings = Settings()
    c = Client(); c.clientID = "x"; c.ip = "10.0.0.5"
    c.profileName = "ghost-profile"
    server.settings.clients["x"] = c
    rc, out = _run(server._run_device_script("x", "stop"))
    assert rc is None
    assert out == "no-profile"


def test_run_device_script_start_routes_through_ssh_then_vnc_for_default():
    """The default ipad1-ios5 profile uses launch.method='ssh-then-vnc';
    'start' must therefore go through the ssh-then-vnc path (which runs
    the wakeScript over SSH first, then VNC-taps)."""
    ckey = _seeded()
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock()
    proxy = MagicMock()
    tapped = []
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=fake_proc)) as exec_mock, \
         patch.object(server, "_get_pooled_vnc",
                      new=AsyncMock(return_value=proxy)), \
         patch.object(server, "_do_tap",
                      side_effect=lambda px, x, y: tapped.append((x, y))), \
         patch("asyncio.sleep", new=AsyncMock()):
        result = _run(server._run_device_script(ckey, "start"))
    # tap at the default profile's coordinate
    assert tapped == [(945, 671)]
    # wake step ran over SSH exactly once
    assert exec_mock.call_count == 1
    # success path
    rc, out = result
    assert rc == 0
    assert out == "VNC_TAP_OK"


def test_run_device_script_reboot_runs_reboot_template():
    ckey = _seeded()
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"REBOOTING\n", b""))
    fake_proc.returncode = 0
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock()
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=fake_proc)) as exec_mock:
        rc, out = _run(server._run_device_script(ckey, "reboot"))
    assert rc == 0
    # The reboot script template ("echo REBOOTING; reboot") goes through
    # _exec_ssh unchanged (no template tokens).
    assert exec_mock.call_args.args[-1] == "echo REBOOTING; reboot"


def test_run_device_script_via_legacy_broadcast_call_site_unchanged():
    """The legacy mosaicmesh/websocket/legacy.py RUN_SCRIPT handler calls
    `server._run_device_script(k, which)` — same arity and entry point —
    so its call sites need NO change post-PR-3. This is a smoke that the
    re-export through server.py is intact."""
    assert hasattr(server, "_run_device_script")
    assert callable(server._run_device_script)
```

- [ ] **Run** `python -m pytest tests/unit/test_device_scripts.py -c tests/pytest.ini -v` — expect FAIL (the dispatcher cut-over hasn't happened; the SSH command for `login` is still routed through the legacy path which references `client.loginScript`, not the profile).

### Step 6.2: Cut over `_run_device_script` — delete legacy body, alias to `run_profile_action`

- [ ] **Edit** `mosaicmesh/device_scripts.py`. Replace the **entire body** of the legacy `_run_device_script` function (lines ~198-302) with a one-line alias:

Find the entire function:
```python
async def _run_device_script(client_key, which):
    """Run a device's lifecycle script (which in {login,start,stop,reboot}) over
    ...
    [the legacy body]
    """
    [legacy body 100+ lines]
```

Replace with:

```python
async def _run_device_script(client_key, which):
    """Public entry point — delegates to run_profile_action. Kept under the
    old name so existing call sites (mosaicmesh/websocket/legacy.py
    RUN_SCRIPT handler, ad-hoc tests that patch server._run_device_script)
    continue to work without change."""
    return await run_profile_action(client_key, which)
```

- [ ] **Run** `python -m pytest tests/unit/test_device_scripts.py -c tests/pytest.ini -v`

Expected: 6 tests PASS.

### Step 6.3: Delete the now-redundant `test_run_profile_action.py`

The dispatcher checks moved into the cut-over test file.

- [ ] **Delete** the file:

```bash
git rm tests/unit/test_run_profile_action.py
```

- [ ] **Run** `python -m pytest tests/unit -c tests/pytest.ini --tb=no -q | tail -10`

Expected: previous failures unchanged (13). No regressions.

### Step 6.4: Commit

- [ ] **Commit**:

```bash
git add mosaicmesh/device_scripts.py tests/unit/test_device_scripts.py
git rm tests/unit/test_run_profile_action.py
git commit -m "refactor(device_scripts): _run_device_script delegates to run_profile_action

Cut-over commit. The legacy ~100-line _run_device_script body — which
read client.{which}Script fields and dispatched the VNC tap inline — is
replaced by a one-line alias to run_profile_action(). Behavior identical
for any client that has profileName='ipad1-ios5' set (which is all of
them after Task 4's migrate_client_script_fields).

Existing call sites in mosaicmesh/websocket/legacy.py (RUN_SCRIPT) and
tests that patch server._run_device_script remain unchanged.

The legacy primitive _launch_webapp_via_vnc and the DEFAULT_DEVICE_SCRIPTS
literal still exist in device_scripts.py — Task 7 deletes them.

Part of PR-3 of the admin-timeline-redesign spec."
```

---

## Task 7: Delete dead code (legacy primitives + Client script fields)

The cut-over is done; everything still passes tests; now we delete the unreachable legacy code. This is the only Task in PR-3 with a NET deletion in line count.

**Files:**
- Modify: `mosaicmesh/device_scripts.py` (delete legacy primitives + constants)
- Modify: `mosaicmesh/state.py` (delete `_apply_default_scripts` + the five `*Script` Client fields)
- Modify: `mosaicmesh/api/discovery.py` (delete the configure-handler `*Script` block)
- Modify: `server.py` (delete the import + re-export of `DEFAULT_DEVICE_SCRIPTS`, `_apply_default_scripts`)
- Modify: `tests/unit/test_module_layout.py` (delete assertions on the deleted symbols)

### Step 7.1: Delete legacy primitives + constants from `device_scripts.py`

- [ ] **Edit** `mosaicmesh/device_scripts.py`. Delete:
  - `WEBCLIP_BUNDLE_ID = ...` (one line)
  - `DEFAULT_DEVICE_SCRIPTS = { ... }` (~75 lines including its comment block)
  - `WEBAPP_ICON_FBX = 945` and `WEBAPP_ICON_FBY = 671` constants + their comment
  - The entire `async def _launch_webapp_via_vnc(...)` function (~30 lines)

Keep:
  - `SSH_KEY_PATH`, `SSH_USER`, `SSH_LEGACY_OPTS`, `DISPLAY_URL` (still used by the dispatcher)
  - `_drop_pooled_vnc` (still called by `_vnc_tap_sequence`)
  - `_run_device_script` (now a one-line alias)
  - All the PR-3 additions: `_exec_ssh`, `_vnc_tap_sequence`, `_ssh_then_vnc`, `LAUNCH_METHODS`, `run_profile_action`, `build_vars`, `SafeDict` imports.

Update the module docstring at the top of the file to reflect the new responsibility:

```python
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
```

### Step 7.2: Delete the five `*Script` fields + `_apply_default_scripts` from `state.py`

- [ ] **Edit** `mosaicmesh/state.py`. In `Client.__init__`, delete:

```python
        self.loginScript = None
        self.startScript = None
        self.stopScript = None
        self.rebootScript = None
        self.testScript = None
```

- [ ] **Edit** `mosaicmesh/state.py`. Delete the entire `_apply_default_scripts(client)` function.

- [ ] **Edit** `mosaicmesh/state.py`. In `migrate_client_objects`, find and delete the call:

```python
        # Backfill lifecycle-script defaults onto devices registered before the
        # automation existed (their fields are absent/None -> show as null).
        _apply_default_scripts(client)
```

### Step 7.3: Delete the `*Script` configure block from `api/discovery.py`

- [ ] **Edit** `mosaicmesh/api/discovery.py`. Find lines ~429-435:

```python
        # Per-device lifecycle scripts (login/start/stop/reboot/test). ""
        # clears back to the fleet default on next backfill; a non-empty
        # string overrides it.
        for sf in ("loginScript", "startScript", "stopScript", "rebootScript",
                   "testScript"):
            if sf in data:
                setattr(client, sf, data[sf] if data[sf] else None)
```

Delete the entire block.

### Step 7.4: Delete the `server.py` re-export of `DEFAULT_DEVICE_SCRIPTS` and `_apply_default_scripts`

- [ ] **Edit** `server.py`. Find the `from mosaicmesh.device_scripts import (...)` block and remove `DEFAULT_DEVICE_SCRIPTS,`. Find the `from mosaicmesh.state import (...)` block and remove `_apply_default_scripts,`.

### Step 7.5: Update `test_module_layout.py`

- [ ] **Edit** `tests/unit/test_module_layout.py`. Find any reference to `DEFAULT_DEVICE_SCRIPTS`, `_apply_default_scripts`, `loginScript`, `startScript`, `stopScript`, `rebootScript`, `testScript` and delete those assertions. Replace with the PR-3 equivalents:

```python
def test_no_legacy_script_fields_on_client():
    """PR-3 deleted the five per-Client *Script attributes — verify they're
    gone so jsonpickle of an older settings.dat doesn't quietly resurrect them."""
    from mosaicmesh.state import Client
    c = Client()
    for f in ("loginScript", "startScript", "stopScript",
              "testScript", "rebootScript"):
        assert not hasattr(c, f), f"{f} should be deleted in PR-3"


def test_default_profile_is_seeded_in_settings_via_migrate():
    """migrate_client_objects() seeds settings.profiles['ipad1-ios5'] when
    profiles is empty."""
    from mosaicmesh.state import Settings, migrate_client_objects
    import server
    prev = getattr(server, 'settings', None)
    try:
        server.settings = Settings()   # empty profiles dict
        migrate_client_objects()
        assert "ipad1-ios5" in server.settings.profiles
        prof = server.settings.profiles["ipad1-ios5"]
        assert prof.matchDeviceType == "Tablet"
        assert prof.launch["method"] == "ssh-then-vnc"
    finally:
        server.settings = prev
```

### Step 7.6: Run the full unit suite

- [ ] **Run** `python -m pytest tests/unit -c tests/pytest.ini --tb=no -q | tail -10`

Expected: previous failures unchanged (13). All PR-3 tests still pass. No new failures.

### Step 7.7: Commit

- [ ] **Commit**:

```bash
git add mosaicmesh/device_scripts.py mosaicmesh/state.py mosaicmesh/api/discovery.py \
        server.py tests/unit/test_module_layout.py
git commit -m "refactor(device_scripts,state): delete legacy DEFAULT_DEVICE_SCRIPTS + per-Client *Script fields

The cut-over commit's safety net — every caller now flows through
run_profile_action, so the legacy bodies are unreachable. Removed:

  - mosaicmesh/device_scripts.py: DEFAULT_DEVICE_SCRIPTS, WEBCLIP_BUNDLE_ID,
    WEBAPP_ICON_FBX, WEBAPP_ICON_FBY constants; _launch_webapp_via_vnc
    function (~120 lines).
  - mosaicmesh/state.py: Client.{login,start,stop,reboot,test}Script
    attributes; _apply_default_scripts function and its call in
    migrate_client_objects (~15 lines).
  - mosaicmesh/api/discovery.py: the configure-handler block that accepted
    *Script field updates (~7 lines).
  - server.py: DEFAULT_DEVICE_SCRIPTS and _apply_default_scripts re-exports.

Operator impact: settings.dat backup at the PR-3 boundary IS REQUIRED.
Rolling back to a pre-PR-3 server with a PR-3-migrated settings.dat means
the operator loses the (now-stripped) per-Client script overrides.

admin.html's displayKeys map (lines 91-94) still references the dead
loginScript/startScript/stopScript/rebootScript keys — those rows will
render blank in the legacy admin UI until PR-6 removes the table. The
legacy UI is being deleted anyway; not blocking.

Part of PR-3 of the admin-timeline-redesign spec."
```

---

## Task 8: Update `CLAUDE.md` + final verification

Document the new layout. Smoke-test the server still boots cleanly with the migrated settings.dat.

**Files:**
- Modify: `CLAUDE.md`

### Step 8.1: Update CLAUDE.md Architecture + Layout sections

- [ ] **Edit** `CLAUDE.md`. Find the Layout section's `device_scripts.py` bullet:

```markdown
  - `device_scripts.py` — `DEFAULT_DEVICE_SCRIPTS` dict + `_run_device_script` (SSH-based lifecycle script execution) + `_launch_webapp_via_vnc` + `_drop_pooled_vnc`. The spec's PR-3 will replace this module with the ScriptingProfile dispatcher.
```

Replace with:

```markdown
  - `device_scripts.py` — ScriptingProfile dispatcher: `_run_device_script` (alias of `run_profile_action`), three launch primitives (`_exec_ssh`, `_vnc_tap_sequence`, `_ssh_then_vnc`), `LAUNCH_METHODS` dispatch table, SSH constants. The Veency pool itself (`_veency_pool`, `_veency_lock`, `_get_pooled_vnc`, `_do_tap`) still lives in `server.py` — moving it into the dispatcher is a follow-up cleanup.
  - `profile_bootstrap.py` — `DEFAULT_PROFILE_IPAD1_IOS5` (byte-identical-to-legacy content) + `seed_default_profile_if_empty` + `migrate_client_script_fields`. Called once at startup from `migrate_client_objects`.
  - `template_vars.py` — `SafeDict` + `build_vars(client, profile, **extra)`. Profile script strings reference `{webclipBundleId}`, `{displayUrl}`, `{ip}`, etc.; the dispatcher calls `str.format_map(SafeDict(build_vars(...)))` before SSH execution. Unknown tokens stay literal (operator scripts may embed shell variables).
```

- [ ] **Edit** `CLAUDE.md`. Find the Layout section's `state.py` bullet:

```markdown
  - `state.py` — `Settings`, `Client`, `Playlist`, `Schedule`, `PlayMode`, `PlayState`, `Display`, `MediaElement`, `Scripts`; `migrate_client_objects`; `_apply_default_scripts`. The singleton instance `settings = Settings()` stays in `server.py` (for the `server.settings = mock_settings` test pattern).
```

Replace with:

```markdown
  - `state.py` — `Settings`, `Client`, `Playlist`, `Schedule`, `ScriptingProfile`, `PlayMode`, `PlayState`, `Display`, `MediaElement`, `Scripts`; `migrate_client_objects` (also seeds the default profile + migrates legacy Client *Script fields on first boot via `profile_bootstrap`). The singleton instance `settings = Settings()` stays in `server.py` (for the `server.settings = mock_settings` test pattern). Per-Client lifecycle scripts now flow through `client.profileName -> settings.profiles[name]`; the old `loginScript`/`startScript`/`stopScript`/`testScript`/`rebootScript` attributes were removed in PR-3.
```

- [ ] **Edit** `CLAUDE.md`. Append a new paragraph to the Architecture section right after the "REST API surface" paragraph that PR-2 added:

```markdown
**Lifecycle scripts via ScriptingProfile (PR-3).** Each `Client` carries a `profileName` pointing into `settings.profiles[name]`. The profile holds five script templates (login/start/stop/test/reboot), a launch method (`shell` / `vnc-tap` / `ssh-then-vnc`), webclip metadata, and SSH options. `mosaicmesh.device_scripts._run_device_script(client_key, which)` resolves the profile, substitutes template variables (`{webclipBundleId}`, `{displayUrl}`, `{ip}`, etc. via `mosaicmesh.template_vars.SafeDict`), and routes through the dispatcher. The default `ipad1-ios5` profile is seeded at first boot with content byte-identical to the pre-PR-3 `DEFAULT_DEVICE_SCRIPTS` literal.
```

### Step 8.2: Smoke-test the server boot

- [ ] **Run** `python server.py -v` in one terminal. Watch for:
  - No startup traceback.
  - Log line: `profile-bootstrap: seeded ipad1-ios5 default profile` (only on a settings.dat that lacks profiles).
  - Log line: `profile-bootstrap: migrated N Client(s) to profileName='ipad1-ios5'` (only on a settings.dat with un-migrated clients).
  - Server logs `Running on http://...:3000` and stays alive.

- [ ] **In another terminal**, hit the new endpoint to verify profiles are listed:

```bash
curl -s http://localhost:3000/api/profiles | python -m json.tool
```

Expected: returns the seeded `ipad1-ios5` profile with `_serverVersion: 1`.

- [ ] **Verify** a Client has profileName set:

```bash
curl -s http://localhost:3000/api/discovery/devices | python -m json.tool | grep -A 1 profileName
```

Expected: every device shows `"profileName": "ipad1-ios5"` (or whatever the operator has overridden it to).

- [ ] **Stop** the server (`Ctrl+C` in the server terminal).

### Step 8.3: Run the full unit suite one more time

- [ ] **Run** `python -m pytest tests/unit -c tests/pytest.ini --tb=no -q | tail -10`

Expected: 13 pre-existing failures unchanged. The PR-3 test count (~25 new tests) all pass. No regressions.

### Step 8.4: Commit

- [ ] **Commit**:

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): document PR-3 ScriptingProfile dispatcher

CLAUDE.md Architecture + Layout sections updated to describe
mosaicmesh.device_scripts as the profile-driven dispatcher (rather than
the hardcoded-script execution module it was), the new
profile_bootstrap.py + template_vars.py modules, and the removal of
the legacy per-Client *Script fields.

Closes PR-3 of the admin-timeline-redesign spec."
```

---

## Task 9: Manual real-device smoke (operator step — gating PR merge)

Per spec §12 'Pacing guidance': PR-3 is "riskiest for fleet behavior. Needs real-device smoke before merge."

The unit tests in this PR all stub subprocess + VNC. The actual fleet behavior — does the iPad actually launch the webapp on `start`? Does `stop` actually close it? — cannot be verified by unit tests alone.

**This task is NOT executable by an automated agent.** Document it here so the human reviewer knows what to do before merging PR #3 to main.

- [ ] **Boot the server** with the PR-3 branch checked out and a current settings.dat backup taken.
- [ ] **Pick one iPad** (e.g. screen1 / `192.168.1.50`) that's currently online.
- [ ] **Trigger each lifecycle action** via the existing admin UI's RUN_SCRIPT button or:

```bash
# WebSocket RUN_SCRIPT REQUEST — substitute clientKey for your iPad
echo '{"DEST":"server","SRC":"admin","REQUEST":"RUN_SCRIPT","PAYLOAD":{"clientKey":"<KEY>","script":"login"}}'
# Repeat with script ∈ {start, stop, test, reboot}
```

- [ ] **Confirm** each action behaves identically to its pre-PR-3 version:
  - `login` — screen wakes; autolock disabled; orientation locked to portrait.
  - `start` — webapp launches (chrome-less, fullscreen).
  - `stop` — webapp closes; screen sleeps.
  - `test` — webapp relaunches with `?tdbg` query param; debug HUD visible.
  - `reboot` — iPad restarts.
- [ ] **Re-run onboarding** (`tools/onboard_devices.ps1`) on one iPad to confirm the cert/SSH flow still works without the deleted per-Client script fields (the bootstrap writes them — should be a no-op after PR-3 because Settings.profiles already has ipad1-ios5).
- [ ] **Sit for 24 hours** of normal operation (one display cycle's worth of schedule transitions). Confirm no zombie ssh.exe accumulation, no quiet "no profile" warnings on healthy iPads.
- [ ] **Only after the 24h soak** open PR #3 (PR-3) for review.

---

## Self-Review Checklist (run before opening the PR)

- [ ] `python -m pytest tests/unit -c tests/pytest.ini --tb=no -q` shows 13 pre-existing failures + (previous baseline + ~25 new PR-3 tests) passes.
- [ ] `python server.py -v` boots cleanly with a populated settings.dat. Log shows the `profile-bootstrap: ...` messages exactly once (idempotent).
- [ ] `curl -s http://localhost:3000/api/profiles` returns the seeded ipad1-ios5 profile.
- [ ] `git log --oneline feature/pr3-scripting-profile-dispatcher ^feature/pr2-rest-endpoints` shows ~8 task commits (template_vars, launch primitives, run_profile_action, profile_bootstrap, auto_match, cut-over, deletions, CLAUDE.md).
- [ ] `git diff feature/pr2-rest-endpoints..HEAD -- mosaicmesh/device_scripts.py` shows net-negative line count (deletions exceed additions — the rewrite is leaner than the old).
- [ ] **Real-device smoke** in Task 9 has been completed.

---

## Notes for the implementing engineer

1. **The dispatcher's hardest invariant is byte-identical fidelity to the old scripts.** Task 4's tests pin the rendered output of every script against a hand-written literal. If any of those tests fail after editing `_make_default_profile`, you've introduced fleet-behavior drift — fix the literal, not the test.

2. **The cut-over in Task 6 is one commit.** The pre-cut and post-cut state both have a working `_run_device_script`. If real-device smoke in Task 9 reveals a fleet-behavior issue, `git revert` Task 6's commit cleanly restores the legacy path. This is the *purpose* of Task 6 being one commit — preserve that property.

3. **Don't delete `_run_device_script` itself.** It's the entry point for `mosaicmesh/websocket/legacy.py:437`, ad-hoc tests that `patch.object(server, '_run_device_script', ...)`, and any future external integration. After Task 6 it's a one-line alias, but the symbol stays.

4. **The Veency pool migration is explicitly OUT of scope.** `_veency_pool`, `_veency_lock`, `_get_pooled_vnc`, `_do_tap` stay in server.py. Touching them in PR-3 makes the merge riskier without proportionate benefit; they can move in a separate follow-up PR.

5. **The `admin.html` `displayKeys` map references dead keys after PR-3.** Don't fix it here — PR-6 deletes the entire legacy admin UI section.

6. **Settings.dat backup at PR-3 boundary is operator-mandatory.** Once the per-Client `*Script` attrs are gone from `Client.__init__`, jsonpickle can't restore them — a downgrade to pre-PR-3 with a PR-3-migrated settings.dat loses operator-customized scripts. The PR description must call this out.

7. **`run_profile_action` returns mixed types** (`(rc, out)` tuple for SSH paths, `(0, "VNC_TAP_OK")` or `(None, "vnc-tap-failed")` for VNC-only paths, `(None, "no-profile")` sentinel). Callers in `mosaicmesh/websocket/legacy.py:437` use `asyncio.ensure_future` and discard the return value — so the shape variance doesn't matter for them. The cut-over test `test_run_device_script_start_routes_through_ssh_then_vnc_for_default` pins the normalized shape for the dispatcher's own contract.
