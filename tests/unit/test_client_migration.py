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
