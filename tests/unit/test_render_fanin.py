"""T1.2 SEGMENT single-decode fan-in: command builder + golden equivalence.

The fan-in (build_ffmpeg_perspective_fanin_cmd) decodes a source ONCE, splits to
N branches, and warps each to its screen — replacing N ffmpeg processes that each
decode the same source. The per-screen output must be equivalent to the
per-process path. Equivalence is by construction (both reuse _perspective_scale_vf
+ _segment_output_opts); the golden test proves it empirically by comparing the
FILTERED frames (framemd5, pre-encode) so libx264's multi-thread encode
nondeterminism can't mask a real warp-graph difference.
"""
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args
class _MockArgs:
    Port = 3000
    Verbose = False
argparse.ArgumentParser.parse_args = lambda self, a=None, n=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

import mosaicmesh.render as r

FFMPEG = shutil.which("ffmpeg")


def _branch(sp, w, h, **extra):
    b = {"out_path": "o.mp4", "src_points": sp, "out_w": w, "out_h": h}
    b.update(extra)
    return b


# --------------------------- pure builder tests ---------------------------

def test_fanin_one_decode_n_outputs():
    brs = [_branch([[0, 0], [10, 0], [10, 10], [0, 10]], 64, 48),
           _branch([[1, 1], [9, 1], [9, 9], [1, 9]], 32, 24)]
    brs[0]["out_path"] = "o0.mp4"
    brs[1]["out_path"] = "o1.mp4"
    cmd = r.build_ffmpeg_perspective_fanin_cmd("src.mp4", brs)
    assert cmd.count("-i") == 1, "source decoded exactly once"
    assert cmd.count("-filter_complex") == 1
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert fc.startswith("[0:v]split=2[s0][s1];")
    assert "[v0]" in fc and "[v1]" in fc
    # both outputs present, each mapped from its own branch label
    assert cmd.count("o0.mp4") == 1 and cmd.count("o1.mp4") == 1
    assert "[v0]" in cmd and "[v1]" in cmd
    # [v0] is mapped immediately before o0's encode opts (branch order preserved)
    assert cmd[cmd.index("o0.mp4")] == "o0.mp4" and cmd.index("[v0]") < cmd.index("o0.mp4")
    assert cmd.index("[v1]") < cmd.index("o1.mp4") and cmd.index("o0.mp4") < cmd.index("[v1]")
    # per-output encode opts carried through for BOTH outputs
    assert cmd.count("+faststart") == 2 and cmd.count("baseline") == 2


def test_fanin_branch_filter_equals_per_process_filter():
    """Each branch's filter chain is byte-identical to the per-process -vf — this
    is the by-construction equivalence guarantee."""
    sp, w, h = [[2, 3], [101, 4], [99, 98], [1, 97]], 200, 150
    evf = ["fade=t=out:st=1:d=0.5"]
    per_process = r.build_ffmpeg_perspective_cmd("s.mp4", "o.mp4", sp, w, h,
                                                 extra_video_filters=evf)
    vf_pp = per_process[per_process.index("-vf") + 1]
    graph = r.build_ffmpeg_perspective_fanin_cmd(
        "s.mp4", [_branch(sp, w, h, out_path="o0.mp4", evf=evf),
                  _branch([[0, 0], [9, 0], [9, 9], [0, 9]], 20, 20, out_path="o1.mp4")])
    fcs = graph[graph.index("-filter_complex") + 1]
    assert "[s0]%s[v0]" % vf_pp in fcs, "branch 0 filter must match per-process -vf exactly"


def test_fanin_single_branch_delegates_to_per_process():
    one = r.build_ffmpeg_perspective_fanin_cmd(
        "s.mp4", [_branch([[0, 0], [9, 0], [9, 9], [0, 9]], 20, 20, out_path="solo.mp4")])
    assert "-vf" in one and "-filter_complex" not in one


def test_fanin_empty_raises():
    with pytest.raises(ValueError):
        r.build_ffmpeg_perspective_fanin_cmd("s.mp4", [])


def test_fanin_per_branch_audio_filter():
    brs = [_branch([[0, 0], [9, 0], [9, 9], [0, 9]], 20, 20, out_path="o0.mp4",
                   eaf=["afade=t=out:st=2:d=1"]),
           _branch([[0, 0], [9, 0], [9, 9], [0, 9]], 20, 20, out_path="o1.mp4")]
    cmd = r.build_ffmpeg_perspective_fanin_cmd("s.mp4", brs)
    assert "-af" in cmd and "afade=t=out:st=2:d=1" in cmd
    # source audio mapped per output (optional ? so silent sources don't fail)
    assert cmd.count("0:a?") == 2


# --------------------------- golden equivalence ---------------------------

@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_golden_fanin_filtered_frames_bit_identical(tmp_path):
    """The decisive gate: fan-in branch j's FILTERED frames must be byte-identical
    to the per-process path's screen-j frames (same decode + same filter chain).
    Compared pre-encode via framemd5 so encoder nondeterminism is out of scope."""
    src = tmp_path / "src.mp4"
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi",
         "-i", "testsrc=size=320x240:rate=10:duration=1",
         "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True)

    branches = [
        _branch([[0, 0], [320, 0], [320, 240], [0, 240]], 160, 120),
        _branch([[20, 10], [300, 5], [310, 230], [15, 235]], 128, 96),
    ]

    def _md5_lines(path):
        return [ln for ln in path.read_text().splitlines() if not ln.startswith("#")]

    # Per-process: each branch's filtered frames -> framemd5
    pp = []
    for j, b in enumerate(branches):
        vf = r._perspective_scale_vf(b["src_points"], b["out_w"], b["out_h"])
        out = tmp_path / ("pp%d.md5" % j)
        subprocess.run([FFMPEG, "-y", "-i", str(src), "-vf", vf, "-an",
                        "-f", "framemd5", str(out)], check=True, capture_output=True)
        pp.append(_md5_lines(out))

    # Fan-in: one decode, split, framemd5 each branch
    n = len(branches)
    parts = ["[0:v]split=%d%s" % (n, "".join("[s%d]" % j for j in range(n)))]
    for j, b in enumerate(branches):
        parts.append("[s%d]%s[v%d]" % (
            j, r._perspective_scale_vf(b["src_points"], b["out_w"], b["out_h"]), j))
    cmd = [FFMPEG, "-y", "-i", str(src), "-filter_complex", ";".join(parts)]
    fi_outs = []
    for j in range(n):
        o = tmp_path / ("fi%d.md5" % j)
        cmd += ["-map", "[v%d]" % j, "-an", "-f", "framemd5", str(o)]
        fi_outs.append(o)
    subprocess.run(cmd, check=True, capture_output=True)

    for j in range(n):
        assert _md5_lines(fi_outs[j]) == pp[j], \
            "fan-in branch %d filtered frames differ from per-process" % j


# --------------------------- _encode_group wiring ---------------------------

def _setup_seg_group(monkeypatch, n_clients):
    """A SEGMENT video group with n calibrated clients; ffmpeg + push mocked."""
    from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode
    monkeypatch.setattr(server, "settings", Settings(), raising=False)
    d = Display(); d.boundingBox = [0, 0, 100, 100]
    server.settings.displays["G"] = d
    for j in range(n_clients):
        c = Client(); c.displayID = "G"; c.deviceWidth = 160; c.deviceHeight = 120
        c.measuredPerimeter = [10 * j, 0, 50 + 10 * j, 0, 50 + 10 * j, 50, 10 * j, 50]
        server.settings.clients["c%d" % j] = c
    me = MediaElement(); me.id = 0; me.file = "/media/server/videos/a.mp4"
    me.playmode = PlayMode.SEGMENT; me.duration = 5
    monkeypatch.setattr(r, "resolve_media_path", lambda f: "/abs/a.mp4")
    monkeypatch.setattr(r, "get_video_dimensions", lambda p: (640, 480))
    return me


def _capture_jobs(monkeypatch):
    cmds = []
    async def _cap(cmd, label, sem):
        cmds.append(cmd)
    monkeypatch.setattr(r, "_run_ffmpeg", _cap)
    return cmds


def test_encode_group_segment_fanin_default_on(monkeypatch):
    """Default (env unset) is fan-in ON: ONE shared decode for all screens."""
    me = _setup_seg_group(monkeypatch, 3)
    cmds = _capture_jobs(monkeypatch)
    monkeypatch.delenv("MM_RENDER_FANIN", raising=False)       # default -> ON
    monkeypatch.delenv("MM_RENDER_FANIN_CAP", raising=False)   # default cap 8 >= 3
    asyncio.run(r._encode_group([me], "G", "tok"))
    assert len(cmds) == 1, "default ON: ONE fan-in ffmpeg (single shared decode)"
    fc = cmds[0]
    assert fc.count("-i") == 1 and "-filter_complex" in fc
    assert "split=3" in fc[fc.index("-filter_complex") + 1]
    assert sum("seg_tok_0.mp4" in a for a in fc) == 3, "all 3 screen outputs present"


def test_encode_group_segment_kill_switch_forces_per_process(monkeypatch):
    """MM_RENDER_FANIN=0 is the kill-switch back to one ffmpeg per screen."""
    me = _setup_seg_group(monkeypatch, 3)
    cmds = _capture_jobs(monkeypatch)
    monkeypatch.setenv("MM_RENDER_FANIN", "0")
    asyncio.run(r._encode_group([me], "G", "tok"))
    assert len(cmds) == 3, "kill-switch: one ffmpeg per screen (3 decodes)"
    assert all(c.count("-i") == 1 and "-vf" in c and "-filter_complex" not in c
               for c in cmds)


def test_encode_group_segment_fanin_respects_cap(monkeypatch):
    me = _setup_seg_group(monkeypatch, 3)
    cmds = _capture_jobs(monkeypatch)
    monkeypatch.setenv("MM_RENDER_FANIN", "1")
    monkeypatch.setenv("MM_RENDER_FANIN_CAP", "2")            # 3 screens -> chunks of 2 + 1
    asyncio.run(r._encode_group([me], "G", "tok"))
    assert len(cmds) == 2, "cap=2 over 3 screens -> 2 fan-in jobs (2 decodes, not 3)"
    splits = sorted(
        int(c[c.index("-filter_complex") + 1].split("split=")[1][0]) if "-filter_complex" in c else 1
        for c in cmds)
    assert splits == [1, 2], "one chunk of 2 (split=2) + one of 1 (delegates, no split)"
