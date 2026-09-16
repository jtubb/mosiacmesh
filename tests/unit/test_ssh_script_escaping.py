"""Shell-escaping of values substituted into SSH script templates (audit 1.11).

_exec_ssh does `script_template.format_map(SafeDict(vars_))` and hands the result
to ssh as the remote command -- which the REMOTE SHELL re-parses. Substituted
values were not escaped, so a value containing a quote broke the command and a
crafted one executed as root.

Escaping strategy: the templates wrap variables in SINGLE quotes
    sbdidlaunch '{webclipBundleId}' 2>/dev/null || uiopen '{displayUrl}'
    uiopen '{displayUrl}?tdbg'
so a substituted value always lands inside a single-quoted context, and the
correct escape there is  '  ->  '\\''  .

shlex.quote is WRONG here and measurably worse: nested inside the template's own
quotes it leaves $(...) and `...` payloads executable and breaks plain
apostrophes. (Verified against a real shell across benign and hostile inputs.)
Note `'{displayUrl}?tdbg'` has a suffix inside the quotes, which also rules out
"strip the template quotes and shlex.quote instead".
"""
import argparse
import asyncio
import copy
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh import device_scripts
from mosaicmesh.state import Settings, Client
from mosaicmesh.profile_bootstrap import DEFAULT_PROFILE_IPAD1_IOS5

ESC = "'\\''"          # the 4 characters:  ' \ ' '
HOSTILE = "x'; touch PWNED; echo '"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _seeded(bundle_id):
    server.settings = Settings()
    prof = copy.deepcopy(DEFAULT_PROFILE_IPAD1_IOS5)
    prof.webclip["bundleId"] = bundle_id
    server.settings.profiles["ipad1-ios5"] = prof
    c = Client()
    c.clientID = "a"
    c.ip = "10.0.0.5"
    c.profileName = "ipad1-ios5"
    server.settings.clients["a"] = c
    return "a"


def _render(bundle_id, which="start"):
    """Return the remote command string _exec_ssh would hand to ssh."""
    ckey = _seeded(bundle_id)
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"START_OK\n", b""))
    proc.returncode = 0
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=proc)) as exec_mock:
        _run(server._run_device_script(ckey, which))
    return exec_mock.call_args.args[-1]


class TestSqEscape:
    def test_plain_value_untouched(self):
        assert device_scripts._sq_escape("com.apple.webapp") == "com.apple.webapp"

    def test_single_quote_is_escaped(self):
        assert device_scripts._sq_escape("Jon's iPad") == "Jon" + ESC + "s iPad"

    def test_multiple_quotes_all_escaped(self):
        assert device_scripts._sq_escape("a'b'c") == "a" + ESC + "b" + ESC + "c"

    def test_shell_metacharacters_need_no_escaping_inside_quotes(self):
        # $, `, ; and & are inert inside single quotes -- only ' can break out,
        # so escaping ONLY the quote is both necessary and sufficient here.
        v = "x$(id)`id`;&|y"
        assert device_scripts._sq_escape(v) == v

    def test_non_string_values_survive(self):
        assert device_scripts._sq_escape(1234) == "1234"
        assert device_scripts._sq_escape(None) == ""


class TestRenderedCommand:
    def test_benign_value_renders_exactly_as_before(self):
        """Regression guard: normal values must be byte-identical to today."""
        script = _render("com.apple.webapp")
        assert "sbdidlaunch 'com.apple.webapp'" in script

    def test_hostile_value_cannot_break_out_of_the_quotes(self):
        script = _render(HOSTILE)
        # The raw payload must NOT appear -- that form terminates the quote.
        assert "sbdidlaunch 'x'; touch PWNED" not in script
        # It must appear in escaped form instead.
        assert "x" + ESC + "; touch PWNED; echo " + ESC in script

    def test_apostrophe_name_does_not_break_the_command(self):
        script = _render("Jon's iPad")
        assert "Jon" + ESC + "s iPad" in script
        assert "'Jon's iPad'" not in script


def test_rendered_command_has_balanced_single_quotes():
    """A crafted value must not leave the command's quoting unbalanced."""
    for value in ("com.apple.webapp", "Jon's iPad", HOSTILE, "a'b", "'''"):
        script = _render(value)
        # Count quotes outside of backslash-escapes: a balanced single-quoted
        # command has an even number of unescaped quotes.
        unescaped = script.replace("\\'", "")
        assert unescaped.count("'") % 2 == 0, f"{value!r} unbalanced: {script!r}"
