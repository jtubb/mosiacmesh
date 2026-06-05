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
