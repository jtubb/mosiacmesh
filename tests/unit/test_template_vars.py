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
