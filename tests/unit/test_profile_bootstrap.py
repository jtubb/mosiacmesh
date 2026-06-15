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


def test_default_profile_wakeScript_respringings_before_tap():
    """REGRESSION GUARD: the wakeScript MUST respring SpringBoard
    (killall SpringBoard) before the VNC tap fires. Live-fleet
    smoke on 2026-06-05 proved that activator system.homebutton
    alone doesn't reliably return to home-page-1 from Spotlight /
    keyboard / arbitrary app states, causing the tap at (945, 671)
    to land on the wrong icon (typically Game Center). A respring
    is the only mechanism that GUARANTEES a clean home-screen
    state before the tap. Do not remove the killall SpringBoard
    without an alternative that's been smoke-tested against
    Spotlight, lock-screen-with-keyboard, and foreground-app
    starting states."""
    p = DEFAULT_PROFILE_IPAD1_IOS5
    wake = p.launch["wakeScript"]
    assert "killall SpringBoard" in wake, \
        "wakeScript MUST respring SpringBoard before the VNC tap"
    assert "sleep" in wake, \
        "wakeScript MUST sleep after respring so SpringBoard has time to relaunch"
    assert "lockscreen.dismiss" in wake, \
        "wakeScript MUST dismiss the lockscreen in case it auto-locked"


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


def test_seed_produces_independent_copy():
    """The module-level DEFAULT_PROFILE_IPAD1_IOS5 singleton MUST NOT
    share mutable state with the seeded copy in settings.profiles.
    Operator edits to a settings.profiles["ipad1-ios5"].scripts dict
    (or webclip/launch/ssh) must not leak back into the canonical
    literal — otherwise a second server in the same process (or a
    test that runs after another) sees corrupted defaults.

    This test is the safety net for any future edit to
    _make_default_profile() that accidentally shares a reference."""
    s = Settings()
    seed_default_profile_if_empty(s)
    # mutate every mutable nested container in the seeded copy
    s.profiles["ipad1-ios5"].scripts["login"] = "CORRUPTED"
    s.profiles["ipad1-ios5"].launch["taps"].append({"fbX": 1, "fbY": 1})
    s.profiles["ipad1-ios5"].webclip["bundleId"] = "CORRUPTED"
    s.profiles["ipad1-ios5"].ssh["legacyCrypto"] = False
    # canonical literal must be unchanged
    assert DEFAULT_PROFILE_IPAD1_IOS5.scripts["login"].startswith(
        "activator send libactivator.lockscreen.dismiss")
    assert DEFAULT_PROFILE_IPAD1_IOS5.launch["taps"] == [
        {"fbX": 945, "fbY": 671}]
    assert (DEFAULT_PROFILE_IPAD1_IOS5.webclip["bundleId"]
            == "com.apple.webapp-4D6F736169634D6573684B696F736B31")
    assert DEFAULT_PROFILE_IPAD1_IOS5.ssh["legacyCrypto"] is True
