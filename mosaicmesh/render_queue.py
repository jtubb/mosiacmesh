"""Bounded background render queue + per-playlist save debounce.

Decouples "a (playlist, group) needs rendering" from the ffmpeg work so a
fleet-wide save or a calibrate can enqueue N jobs without spawning N
simultaneous encodes. Lives apart from render.py so that module stays focused
on the encode itself.

- enqueue(name, group): idempotent; schedules a render under a concurrency cap.
- schedule_autorender(name): debounced; after DEBOUNCE_SECONDS of quiet, enqueues
  the playlist against every calibrated group.
"""
import asyncio
import logging
import os

_QUEUE_CONCURRENCY = int(os.environ.get("MMRENDER_QUEUE_CONCURRENCY") or 2)
DEBOUNCE_SECONDS = int(os.environ.get("MMRENDER_DEBOUNCE") or 60)

_pending = {}          # (name, group) -> "QUEUED" | "RENDERING"
_sem = None            # asyncio.Semaphore, lazily bound to the running loop
_debounce_tasks = {}   # name -> asyncio.Task


def _get_sem():
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_QUEUE_CONCURRENCY)
    return _sem


def enqueue(playlist_name, display_id):
    """Idempotent enqueue of a (playlist, group) render. No-op if already
    queued/in-flight, or if there is no running event loop (a synchronous /
    non-async caller has nothing to schedule onto). Returns True iff a new job
    was scheduled."""
    key = (playlist_name, display_id)
    if key in _pending:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _pending[key] = "QUEUED"
    loop.create_task(_run(playlist_name, display_id))
    return True


async def _run(playlist_name, display_id):
    from mosaicmesh import render as R
    key = (playlist_name, display_id)
    async with _get_sem():
        _pending[key] = "RENDERING"
        try:
            await R.render_playlist_for_group_async(playlist_name, display_id)
        except Exception as e:
            logging.error("render_queue job %s failed: %s", key, e)
        finally:
            _pending.pop(key, None)


def queue_depth():
    """Number of jobs still waiting (not yet started)."""
    return sum(1 for v in _pending.values() if v == "QUEUED")


def schedule_autorender(playlist_name):
    """Debounced auto-render: (re)start a DEBOUNCE_SECONDS timer for this
    playlist. On fire, enqueue it for every calibrated group. A later call
    within the window resets the timer (coalesces a burst of edits). No-op if
    there is no running event loop (synchronous / non-async caller)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    old = _debounce_tasks.get(playlist_name)
    if old and not old.done():
        old.cancel()
    _debounce_tasks[playlist_name] = loop.create_task(_debounce_fire(playlist_name))


async def _debounce_fire(playlist_name):
    try:
        await asyncio.sleep(DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        if _debounce_tasks.get(playlist_name) is asyncio.current_task():
            _debounce_tasks.pop(playlist_name, None)
        return
    _debounce_tasks.pop(playlist_name, None)
    from mosaicmesh import render as R
    R.enqueue_playlist_for_eligible_groups(playlist_name)
