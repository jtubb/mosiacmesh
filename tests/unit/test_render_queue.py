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
    for t in list(Q._debounce_tasks.values()):
        t.cancel()
    Q._debounce_tasks.clear()
    Q._sem = None
    yield
    Q._pending.clear()
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
