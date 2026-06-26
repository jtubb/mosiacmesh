"""Regression tests for the 2026-06-26 settings clobber.

Two defenses are verified here:
  1. Test isolation: persistence.SETTINGS_PATH is redirected to a per-test tmp file
     (tests/conftest.py autouse fixture), so no test can write the repo's live
     settings.dat. The original bug: REST/render tests called the real save against an
     empty mock Settings and the relative "settings.dat" path resolved to the cwd.
  2. Empty-overwrite guard: save_settings_incremental refuses to overwrite a populated
     settings.dat with a fresh/empty Settings (a stray instance / accidental test write).
"""
import jsonpickle
import jsonpickle.ext.numpy as _jn
_jn.register_handlers()

import mosaicmesh.persistence as persistence
from mosaicmesh.state import Settings, Display


def _set_mem_settings(monkeypatch, s):
    import server
    monkeypatch.setattr(server, "settings", s, raising=False)
    persistence.last_settings_hash = None   # force a write attempt


def test_settings_path_is_isolated_during_tests():
    """The autouse conftest fixture must redirect writes away from the repo file."""
    from pathlib import Path
    assert persistence.SETTINGS_PATH != Path("settings.dat"), \
        "tests must not target the repo's live settings.dat"


def test_save_refuses_empty_overwrite_of_populated_file(monkeypatch):
    p = persistence.SETTINGS_PATH                      # per-test tmp path
    populated = Settings()
    populated.displays["OEB Sign 1"] = Display()
    p.write_text(jsonpickle.encode(populated, unpicklable=True), encoding="utf-8")

    _set_mem_settings(monkeypatch, Settings())         # in-memory is fresh/empty
    persistence.save_settings_incremental()            # must REFUSE

    after = jsonpickle.decode(p.read_text(encoding="utf-8"))
    assert "OEB Sign 1" in (getattr(after, "displays", {}) or {}), \
        "empty state must NOT overwrite a populated settings.dat"


def test_save_writes_populated_state(monkeypatch):
    p = persistence.SETTINGS_PATH
    s = Settings()
    s.displays["NEW"] = Display()
    _set_mem_settings(monkeypatch, s)
    persistence.save_settings_incremental()

    assert p.exists()
    after = jsonpickle.decode(p.read_text(encoding="utf-8"))
    assert "NEW" in (getattr(after, "displays", {}) or {})


def test_save_allows_empty_when_no_existing_file(monkeypatch):
    """A genuine fresh first run (no existing file) should still persist."""
    p = persistence.SETTINGS_PATH
    if p.exists():
        p.unlink()
    _set_mem_settings(monkeypatch, Settings())
    persistence.save_settings_incremental()
    assert p.exists(), "fresh first-run with no existing file should still save"


def test_settings_is_empty_helper():
    assert persistence._settings_is_empty(Settings()) is True
    s = Settings()
    s.clients["abc"] = object()
    assert persistence._settings_is_empty(s) is False
