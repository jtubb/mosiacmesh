"""Memory + thread leak instrumentation.

A lightweight process snapshot (RSS/VMS, thread counts, OS handles, and Python
object-type counts) plus a one-line *growth* log that reports which object types
and threads GREW since the previous call — the signal for locating a leak.

`growth_line()` is emitted periodically as `MEMWATCH ...` from the server's
process() loop; `snapshot()` backs the on-demand `/debug/memory` endpoint. Pure
stdlib + psutil (already a dependency); imports nothing from `server`, so it's
safe to import at module top with no circular-import risk.
"""
import gc
import os
import threading
import collections

try:
    import psutil
    _PROC = psutil.Process(os.getpid())
except Exception:                       # psutil missing or process handle denied
    _PROC = None

_prev_types = None                      # previous top-type counts, for growth deltas


def _type_counts(top=30):
    """The `top` most common live Python object types by instance count."""
    c = collections.Counter()
    for o in gc.get_objects():
        c[type(o).__name__] += 1
    return dict(c.most_common(top))


def _thread_names():
    """Thread counts by normalized name ('Thread-12' -> 'Thread-N') so a leaking
    pool of numbered threads collapses into one growing bucket instead of noise."""
    c = collections.Counter()
    for t in threading.enumerate():
        parts = t.name.rsplit('-', 1)
        base = parts[0] + '-N' if len(parts) == 2 and parts[1].isdigit() else t.name
        c[base] += 1
    return dict(c)


def snapshot(full=True):
    """Full process snapshot as a JSON-safe dict."""
    d = {
        'threads': threading.active_count(),
        'thread_names': _thread_names(),
        'gc_objects': len(gc.get_objects()),
        'gc_counts': list(gc.get_count()),
        'gc_garbage': len(gc.garbage),
    }
    if _PROC is not None:
        try:
            with _PROC.oneshot():
                mi = _PROC.memory_info()
                d['rss_mb'] = round(mi.rss / 1048576.0, 1)
                d['vms_mb'] = round(mi.vms / 1048576.0, 1)
                try:
                    d['handles'] = _PROC.num_handles()      # Windows
                except Exception:
                    try:
                        d['fds'] = _PROC.num_fds()          # POSIX
                    except Exception:
                        pass
        except Exception:
            pass
    if full:
        d['top_types'] = _type_counts()
    return d


def growth_line():
    """One compact log line with deltas vs the previous call — reveals what leaks.

    Reports RSS/threads/objects/handles, plus the object types that grew the most
    and the top thread buckets. Called once per interval from process().
    """
    global _prev_types
    snap = snapshot(full=True)
    cur = snap['top_types']
    grow = []
    if _prev_types is not None:
        deltas = {k: cur[k] - _prev_types.get(k, 0) for k in cur}
        grow = sorted(((k, v) for k, v in deltas.items() if v > 0),
                      key=lambda kv: -kv[1])[:6]
    _prev_types = cur
    tn = sorted(snap['thread_names'].items(), key=lambda kv: -kv[1])[:5]
    return (
        "MEMWATCH rss=%sMB vms=%sMB threads=%d objs=%d handles=%s garbage=%d | "
        "growers: %s | threads: %s" % (
            snap.get('rss_mb', '?'), snap.get('vms_mb', '?'), snap['threads'],
            snap['gc_objects'], snap.get('handles', snap.get('fds', '?')),
            snap['gc_garbage'],
            ', '.join('%s+%d' % (k, v) for k, v in grow) or '(baseline)',
            ', '.join('%s=%d' % (k, v) for k, v in tn),
        )
    )
