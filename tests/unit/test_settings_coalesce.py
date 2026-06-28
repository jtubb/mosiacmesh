"""Tests for the coalesced-save flag (request_save / flush_pending_save).

High-frequency churn paths (cache-probe, canvas-report, render-status) call
request_save() — an O(1) flag set — instead of encoding the whole Settings
inline. The process() loop calls flush_pending_save() once per cycle, so a
fleet-wide burst of N churn saves collapses into ONE jsonpickle encode rather
than N back-to-back encodes that each freeze the event loop.

These tests assert the coalescing contract WITHOUT touching disk: the real
save primitive is replaced by a counter so we can verify call-collapsing and
the dirty-flag lifecycle directly.
"""
import mosaicmesh.persistence as persistence


def _install_counter(monkeypatch):
    """Replace the real encode/write primitive with a call counter."""
    calls = {"n": 0}
    monkeypatch.setattr(persistence, "save_settings_incremental",
                        lambda: calls.__setitem__("n", calls["n"] + 1))
    persistence._save_requested = False        # clean slate per test
    return calls


def test_flush_noop_when_nothing_requested(monkeypatch):
    calls = _install_counter(monkeypatch)
    assert persistence.flush_pending_save() is False
    assert calls["n"] == 0, "no request -> no encode"


def test_single_request_flushes_once(monkeypatch):
    calls = _install_counter(monkeypatch)
    persistence.request_save()
    assert persistence.flush_pending_save() is True
    assert calls["n"] == 1


def test_many_requests_coalesce_into_one_flush(monkeypatch):
    """The whole point: N churn saves in a window -> ONE encode at flush."""
    calls = _install_counter(monkeypatch)
    for _ in range(200):                       # a 200-device boot/calibration burst
        persistence.request_save()
    persistence.flush_pending_save()
    assert calls["n"] == 1, "200 requests must collapse to a single encode"


def test_flag_clears_after_flush(monkeypatch):
    calls = _install_counter(monkeypatch)
    persistence.request_save()
    persistence.flush_pending_save()           # writes, clears flag
    assert persistence.flush_pending_save() is False   # nothing pending now
    assert calls["n"] == 1, "a cleared flag must not re-flush"


def test_new_request_after_flush_flushes_again(monkeypatch):
    calls = _install_counter(monkeypatch)
    persistence.request_save()
    persistence.flush_pending_save()
    persistence.request_save()                 # a later churn event
    persistence.flush_pending_save()
    assert calls["n"] == 2, "each dirty window flushes once"
