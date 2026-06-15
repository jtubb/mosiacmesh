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
from mosaicmesh.api.discovery import auto_match_profile, auto_configure_client


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


def test_helper_returns_candidate_regardless_of_existing_profileName():
    """auto_match_profile is a pure helper — it returns the candidate
    that WOULD be assigned, leaving the not-override decision to the
    caller (auto_configure_client). The wiring guard is exercised by
    test_auto_configure_preserves_existing_profileName below."""
    s = Settings()
    s.profiles["ipad"] = _profile("ipad", "Tablet")
    c = Client(); c.deviceType = "Tablet"; c.profileName = "custom-override"
    assert auto_match_profile(c, s) == "ipad"


# ---------------------------------------------------------------------------
# Integration tests for the auto_configure_client wiring (Step 5.2c). These
# exercise the wired-up guard end-to-end so a refactor that breaks the
# insertion point or the `if not getattr(...)` check fails loudly.
# ---------------------------------------------------------------------------


def _setup(deviceType="Tablet", clientID="abc"):
    """Replace server.settings with a fresh Settings + one Display and
    return (client_key, client)."""
    server.settings = Settings()
    server.settings.profiles["ipad1-ios5"] = _profile("ipad1-ios5", "Tablet")
    c = Client()
    c.clientID = clientID
    c.deviceType = deviceType
    c.deviceModel = "iPad"
    c.deviceBrand = "Apple"
    return clientID, c


def test_auto_configure_sets_profileName_on_first_connect():
    """auto_configure_client must assign client.profileName from
    auto_match_profile when the client has no override."""
    key, c = _setup(deviceType="tablet")
    assert c.profileName is None
    auto_configure_client(key, c)
    assert c.profileName == "ipad1-ios5"


def test_auto_configure_preserves_existing_profileName():
    """Operator overrides (set via POST /api/clients/{key}/profile)
    must survive a subsequent REGISTER — the auto-match guard checks
    `if not getattr(client, 'profileName', None)`."""
    key, c = _setup(deviceType="tablet")
    c.profileName = "custom-override"
    auto_configure_client(key, c)
    assert c.profileName == "custom-override"


def test_auto_configure_leaves_profileName_None_when_no_match():
    """Device whose deviceType doesn't match any profile keeps
    profileName=None. The client will still be auto-configured
    (displayID, capabilities, etc.) but the dispatcher will warn on
    every script invocation until an operator assigns a profile."""
    key, c = _setup(deviceType="desktop")
    auto_configure_client(key, c)
    assert c.profileName is None
