"""ffmpeg job-runner safety (audit 1.4, 1.5, 1.6, 3.8).

1.4 asyncio.gather(return_exceptions=False) propagates the FIRST exception but
    does NOT cancel its siblings. The caller set the render entry FAILED, then
    each straggler finished and its progress_cb flipped it back to RENDERING --
    where it stuck, with orphaned encodes still burning CPU and writing output.
1.5 `await proc.communicate()` had no timeout: a hung ffmpeg pinned the entry in
    RENDERING and held a concurrency slot for the life of the process.
1.6 The concurrency semaphore was built INSIDE each _encode_group call, so it
    bounded one render rather than the process -- with the queue running 2
    renders the real cap was 2 x _RENDER_CONCURRENCY.
3.8 On cancellation the spawned ffmpeg child was never terminated.
"""
import argparse
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, a=None, n=None: argparse.Namespace(
    Port=3000, Verbose=False)
try:
    import server  # noqa: F401
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh import render as R


class _HangingProc:
    """A subprocess whose communicate() never returns."""

    def __init__(self):
        self.returncode = None
        self.killed = False

    async def communicate(self):
        await asyncio.sleep(3600)

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


class TestFfmpegTimeout:
    async def test_hung_ffmpeg_times_out_and_is_killed(self, monkeypatch):
        proc = _HangingProc()

        async def _spawn(*a, **k):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
        monkeypatch.setattr(R, "_FFMPEG_TIMEOUT_SECONDS", 0.05, raising=False)

        # The outer wait_for is the RED guard: with no timeout in _run_ffmpeg this
        # would hang on the 3600s sleep instead of failing, so bound it here and
        # let an overrun surface as a test failure.
        with pytest.raises(Exception):
            await asyncio.wait_for(
                R._run_ffmpeg(["ffmpeg"], "stuck", asyncio.Semaphore(1)), timeout=2)

        assert proc.killed, "a hung ffmpeg must be killed, not left holding a slot"

    async def test_cancellation_kills_the_child(self, monkeypatch):
        proc = _HangingProc()

        async def _spawn(*a, **k):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
        monkeypatch.setattr(R, "_FFMPEG_TIMEOUT_SECONDS", 3600, raising=False)

        task = asyncio.ensure_future(
            R._run_ffmpeg(["ffmpeg"], "cancelme", asyncio.Semaphore(1)))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert proc.killed, "cancelling the task must not orphan the ffmpeg child"


class TestSiblingCancellation:
    async def test_failure_cancels_siblings(self, monkeypatch):
        finished = []

        async def _fake(cmd, lbl, sem):
            if lbl == "boom":
                raise RuntimeError("ffmpeg failed for boom")
            await asyncio.sleep(0.3)
            finished.append(lbl)

        monkeypatch.setattr(R, "_run_ffmpeg", _fake)

        with pytest.raises(RuntimeError):
            await R._run_video_jobs([(["a"], "boom"), (["b"], "slow")], None)

        await asyncio.sleep(0.4)
        assert finished == [], "a sibling outlived the failed render"

    async def test_progress_is_not_reported_after_a_failure(self, monkeypatch):
        """This is what flipped a FAILED entry back to RENDERING."""
        calls = []

        async def _fake(cmd, lbl, sem):
            if lbl == "boom":
                await asyncio.sleep(0.01)
                raise RuntimeError("ffmpeg failed for boom")
            await asyncio.sleep(0.3)

        monkeypatch.setattr(R, "_run_ffmpeg", _fake)

        with pytest.raises(RuntimeError):
            await R._run_video_jobs(
                [(["a"], "slow"), (["b"], "boom")],
                lambda done, total: calls.append(done))

        await asyncio.sleep(0.4)
        assert calls == [], "progress fired after the render already failed"

    async def test_all_success_reports_progress_for_every_job(self, monkeypatch):
        calls = []

        async def _fake(cmd, lbl, sem):
            await asyncio.sleep(0)

        monkeypatch.setattr(R, "_run_ffmpeg", _fake)
        await R._run_video_jobs(
            [(["a"], "one"), (["b"], "two"), (["c"], "three")],
            lambda done, total: calls.append((done, total)))

        assert [d for d, _ in calls] == [1, 2, 3]
        assert all(t == 3 for _, t in calls)

    async def test_progress_callback_errors_do_not_fail_the_render(self, monkeypatch):
        async def _fake(cmd, lbl, sem):
            await asyncio.sleep(0)

        def _boom(done, total):
            raise ValueError("callback exploded")

        monkeypatch.setattr(R, "_run_ffmpeg", _fake)
        await R._run_video_jobs([(["a"], "one")], _boom)   # must not raise


class TestProcessWideConcurrency:
    async def test_semaphore_is_shared_across_calls(self):
        """The cap must bound the PROCESS, not a single _encode_group call."""
        first = R._get_ffmpeg_sem()
        second = R._get_ffmpeg_sem()
        assert first is second

    async def test_semaphore_capacity_matches_render_concurrency(self):
        sem = R._get_ffmpeg_sem()
        assert sem._value == R._RENDER_CONCURRENCY
