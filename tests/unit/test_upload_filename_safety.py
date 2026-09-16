"""Upload filename containment (audit 1.8).

upload_handler took the multipart filename straight from the client's
Content-Disposition header and joined it into the upload dir:

    filename = field.filename
    with open(os.path.join(path, filename), 'wb') as f:

os.path.join('cache', '../../server.py') escapes the directory, and on Windows a
drive-absolute filename ('C:/x') discards the join entirely -- an unauthenticated
arbitrary-path write on the LAN. The same value is then handed to
processImage/processVideo/calibrate, so sanitising at the source covers all of them.
"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mosaicmesh.api import media as media_mod

_safe = media_mod._safe_upload_filename


class TestRejectsUnusable:
    def test_none(self):
        assert _safe(None) is None

    def test_empty(self):
        assert _safe("") is None

    def test_whitespace_only(self):
        assert _safe("   ") is None

    def test_dot(self):
        assert _safe(".") is None

    def test_dotdot(self):
        assert _safe("..") is None

    def test_all_dots(self):
        assert _safe("....") is None

    def test_bare_separator(self):
        assert _safe("/") is None


class TestStripsPathComponents:
    def test_posix_traversal(self):
        assert _safe("../../server.py") == "server.py"

    def test_windows_traversal(self):
        assert _safe("..\\..\\server.py") == "server.py"

    def test_posix_absolute(self):
        assert _safe("/etc/passwd") == "passwd"

    def test_windows_drive_absolute(self):
        assert _safe("C:/Windows/evil.png") == "evil.png"

    def test_windows_drive_relative(self):
        assert _safe("C:evil.png") == "evil.png"

    def test_mixed_separators(self):
        assert _safe("a/b\\c/../d.mp4") == "d.mp4"

    def test_strips_nul_and_control_chars(self):
        assert _safe("clip\x00.mp4\n") == "clip.mp4"


class TestPassesThroughLegitimateNames:
    def test_simple(self):
        assert _safe("clip_01.mp4") == "clip_01.mp4"

    def test_spaces_and_unicode(self):
        assert _safe("My Clip \u00e9.png") == "My Clip \u00e9.png"

    def test_leading_dot_is_kept_when_there_is_a_real_name(self):
        assert _safe(".hidden.png") == ".hidden.png"


class _FakeField:
    def __init__(self, filename, chunks):
        self.name = "file"
        self.filename = filename
        self._chunks = list(chunks)

    async def read_chunk(self, size=8192):
        return self._chunks.pop(0) if self._chunks else b""


class _FakeReader:
    def __init__(self, field):
        self._field = field

    async def next(self):
        return self._field


class _FakeRequest:
    """Minimal stand-in for an aiohttp multipart upload request."""

    def __init__(self, dest, field):
        self.match_info = {"dest": dest}
        self._field = field

    async def multipart(self):
        return _FakeReader(self._field)


async def test_handler_contains_traversal_filename(tmp_path, monkeypatch):
    """A path-bearing filename is reduced to its basename, not followed."""
    monkeypatch.chdir(tmp_path)
    resp = await media_mod.upload_handler(
        _FakeRequest("other", _FakeField("../../pwned.txt", [b"payload"])))

    assert resp.status == 200
    assert (tmp_path / "cache" / "pwned.txt").read_bytes() == b"payload"
    assert not (tmp_path.parent / "pwned.txt").exists(), "escaped the upload dir"


async def test_handler_rejects_unusable_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resp = await media_mod.upload_handler(
        _FakeRequest("other", _FakeField("..", [b"payload"])))

    assert resp.status == 400
    assert not (tmp_path.parent / "cache").exists()


def test_result_can_never_escape_the_target_directory(tmp_path):
    """Property: whatever survives sanitisation stays inside the upload dir."""
    hostile = [
        "../../server.py", "..\\..\\server.py", "/etc/passwd", "C:/Windows/evil.png",
        "C:evil.png", "....//x", "a/../../../b", "./../../settings.dat",
        "\\\\unc\\share\\x.png", "sub/dir/file.png",
    ]
    root = os.path.realpath(str(tmp_path))
    for raw in hostile:
        safe = _safe(raw)
        if safe is None:
            continue
        full = os.path.realpath(os.path.join(root, safe))
        assert full.startswith(root + os.sep), f"{raw!r} -> {safe!r} escaped to {full!r}"
        assert os.path.dirname(full) == root, f"{raw!r} -> {safe!r} landed in a subdir"
