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
        # Kill any foreground app (Web.app, Safari) then respring
        # SpringBoard — only reliable way to GUARANTEE the iPad is
        # showing home-page-1 with the dock visible at tap time.
        # activator's system.homebutton was unreliable: from Spotlight,
        # an app, or the lockscreen-with-keyboard state it didn't
        # always land on home-page-1, so (fbX, fbY) tapped the wrong
        # icon (typically Game Center at iconLists[0][8]).
        # sleep 4 waits for SpringBoard to relaunch and load icons
        # (~3s observed); lockscreen.dismiss at the end wakes the
        # screen in case it auto-locked during the respring.
        # Veency dies with SpringBoard, so the dispatcher's
        # _ssh_then_vnc drops the pooled VNC connection AFTER this
        # wakeScript completes — see mosaicmesh.device_scripts.
        "wakeScript": ("killall Web 2>/dev/null; "
                       "killall MobileSafari 2>/dev/null; "
                       "killall SpringBoard; "
                       "sleep 4; "
                       "activator send libactivator.lockscreen.dismiss"),
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
