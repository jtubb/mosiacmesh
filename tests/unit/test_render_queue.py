# tests/unit/test_render_queue.py
import sys
from pathlib import Path
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

import asyncio
import pytest
from mosaicmesh import render_queue as Q


@pytest.fixture(autouse=True)
def clean_queue():
    Q._pending.clear()
    getattr(Q, "_dirty", set()).clear()
    for t in list(Q._debounce_tasks.values()):
        t.cancel()
    Q._debounce_tasks.clear()
    Q._sem = None
    yield
    Q._pending.clear()
    getattr(Q, "_dirty", set()).clear()
    Q._sem = None


def test_enqueue_idempotent(monkeypatch):
    runs = []
    async def _fake_render(name, did):
        runs.append((name, did))
        await asyncio.sleep(0)
    monkeypatch.setattr("mosaicmesh.render.render_playlist_for_group_async", _fake_render)

    async def _go():
        assert Q.enqueue("P", "G1") is True
        assert Q.enqueue("P", "G1") is False   # already pending → deduped
        await asyncio.sleep(0.05)
    asyncio.run(_go())
    assert runs == [("P", "G1")]


def test_queue_depth_counts_queued(monkeypatch):
    async def _slow(name, did):
        await asyncio.sleep(0.2)
    monkeypatch.setattr("mosaicmesh.render.render_playlist_for_group_async", _slow)
    monkeypatch.setattr(Q, "_QUEUE_CONCURRENCY", 1, raising=False)

    async def _go():
        Q._sem = None  # rebuild semaphore at the new concurrency
        Q.enqueue("A", "G1")
        Q.enqueue("B", "G1")
        await asyncio.sleep(0.02)
        # With concurrency 1, one is RENDERING, one still QUEUED.
        assert Q.queue_depth() >= 1
        await asyncio.sleep(0.5)
    asyncio.run(_go())


def test_parse_ffmpeg_progress_line():
    from mosaicmesh import render as R
    assert R._parse_ffmpeg_progress_line("out_time_ms=2000000") == ("out_time_ms", 2000000)
    assert R._parse_ffmpeg_progress_line("progress=end") == ("progress", "end")
    assert R._parse_ffmpeg_progress_line("garbage") is None


def test_edit_during_render_triggers_a_rerun(monkeypatch):
    """Audit 1.3: a playlist edited WHILE its render is in flight was dropped.

    enqueue() returned False because the key was still in _pending, so no new job
    was scheduled -- but the in-flight render had already snapshotted the OLD
    token and finished by writing READY with it. is_playlist_ready then compared
    the current token against that stale one and returned False forever, so the
    UI showed READY while PLAY stayed gated. Nothing re-enqueued.
    """
    runs = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def _fake_render(name, did):
        runs.append((name, did))
        if len(runs) == 1:
            started.set()
            await release.wait()

    monkeypatch.setattr("mosaicmesh.render.render_playlist_for_group_async", _fake_render)

    async def _go():
        assert Q.enqueue("P", "G1") is True
        await asyncio.wait_for(started.wait(), timeout=1)
        # The edit lands mid-render: no NEW task right now (one is running)...
        assert Q.enqueue("P", "G1") is False
        release.set()
        await asyncio.sleep(0.1)
        # ...but the newer token MUST be picked up once the in-flight job ends.
        assert runs == [("P", "G1"), ("P", "G1")], "mid-flight edit was dropped"

    asyncio.run(_go())


def test_rerun_happens_only_once_per_edit(monkeypatch):
    """The re-run must not loop forever on a single mid-flight edit."""
    runs = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def _fake_render(name, did):
        runs.append((name, did))
        if len(runs) == 1:
            started.set()
            await release.wait()

    monkeypatch.setattr("mosaicmesh.render.render_playlist_for_group_async", _fake_render)

    async def _go():
        Q.enqueue("P", "G1")
        await asyncio.wait_for(started.wait(), timeout=1)
        Q.enqueue("P", "G1")
        Q.enqueue("P", "G1")   # two edits during the same render coalesce
        release.set()
        await asyncio.sleep(0.15)
        assert len(runs) == 2
        assert Q._pending == {}

    asyncio.run(_go())


def test_debounce_coalesces(monkeypatch):
    calls = []
    monkeypatch.setattr("mosaicmesh.render.enqueue_playlist_for_eligible_groups",
                        lambda name: calls.append(name))
    monkeypatch.setattr(Q, "DEBOUNCE_SECONDS", 0.05, raising=False)

    async def _go():
        Q.schedule_autorender("P")
        await asyncio.sleep(0.02)
        Q.schedule_autorender("P")   # resets the timer
        await asyncio.sleep(0.02)
        assert calls == []           # not fired yet
        await asyncio.sleep(0.1)
        assert calls == ["P"]        # fired once
    asyncio.run(_go())
