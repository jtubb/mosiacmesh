"""Unit tests for mosaicmesh.vnc_reactor -- starting the Twisted reactor exactly once.

WHY THIS EXISTS (measured on-wall 2026-09-30):
A fleet-wide `RUN_SCRIPT start` (24 devices) produced, twice, in the server log:

    Exception in thread Twisted Reactor:
    twisted.internet.error.ReactorAlreadyRunning

The cause is a check-then-act race inside vncdotool 1.3.0's api.connect():

    if not reactor.running:
        _THREAD = threading.Thread(target=reactor.run, ...)
        _THREAD.name = "Twisted Reactor"
        _THREAD.start()
        observer = PythonLoggingObserver(); observer.start()

`reactor.running` only becomes True *inside* reactor.run(), after the thread is
scheduled. There is no lock. So two concurrent api.connect() calls both read
False, both spawn a reactor thread, and the loser's reactor.run() raises -- in a
thread, where nothing handles it.

The damage is not just a logged traceback:
  - the losing thread dies, so whichever screens its VNC taps belonged to are
    never tapped, and a webclip that needs a real tap to start video simply does
    not launch. Observed as "screen20 did not come back" on 2026-09-29 and
    2026-09-30 with NO crash log on the device (the process never started, it did
    not die), fixed only by a second RUN_SCRIPT start.
  - vncdotool's module-global _THREAD is clobbered by the loser, so a later
    api.shutdown() would join the wrong thread.
  - a second PythonLoggingObserver is started, duplicating every Twisted log line.

The fix is NOT to serialize api.connect(): `_get_pooled_vnc` deliberately
handshakes outside its lock so one dozing iPad cannot block 23 others, and a
serial fleet start would cost ~24s of handshakes. Instead start the reactor once
ourselves and WAIT until reactor.running is True, so every subsequent
api.connect() takes the `if not reactor.running` false branch and never spawns a
thread at all. Concurrency of the handshakes is preserved.
"""
import threading
import time

import pytest

from mosaicmesh.vnc_reactor import ReactorStarter


class FakeReactor:
    """Stands in for twisted.internet.reactor, modelling its TWO flags.

    This distinction is the whole bug, so the fake has to be faithful to it.
    twisted.internet.base.ReactorBase.startRunning() guards on `self._started`,
    which it sets SYNCHRONOUSLY, and only sets the public `running` later, after
    firing the startup event:

        if self._started:      raise error.ReactorAlreadyRunning()
        if self._startedBefore: raise error.ReactorNotRestartable()
        self._started = True
        ...
        self.running = True

    vncdotool polls the PUBLIC `running`, so the race window is the gap between
    the first caller entering run() and `running` becoming visible. Collapsing the
    two flags into one (an earlier cut of this fake did) makes the double-start
    unreachable and the assertion vacuous -- confirmed by replaying vncdotool's
    unlocked starter against it and seeing 24 run() calls with no raise.
    """

    def __init__(self, start_delay=0.02):
        self.running = False            # public: what vncdotool polls
        self._started = False           # internal: what the guard checks
        self._started_before = False    # internal: blocks a restart
        self.run_calls = 0
        self.double_start = False
        self.not_restartable = False
        self.signal_handlers_arg = None
        self._start_delay = start_delay
        self._counter_lock = threading.Lock()
        self._stop = threading.Event()

    def run(self, installSignalHandlers=True):
        with self._counter_lock:
            self.run_calls += 1
            if self._started:
                self.double_start = True
                raise RuntimeError("Reactor already running")
            if self._started_before:
                self.not_restartable = True
                raise RuntimeError("Reactor not restartable")
            self._started = True        # set synchronously, like Twisted
        self.signal_handlers_arg = installSignalHandlers
        # The race window: `running` is not visible yet, so every caller polling
        # it still sees False.
        time.sleep(self._start_delay)
        self.running = True
        while not self._stop.is_set():       # a real reactor blocks forever
            time.sleep(0.005)

    def stop(self):
        self._stop.set()
        self.running = False
        self._started = False
        self._started_before = True


@pytest.fixture
def fake():
    r = FakeReactor()
    yield r
    r.stop()


def test_first_call_starts_the_reactor_once(fake):
    s = ReactorStarter()
    assert s.ensure_started(fake, wait_timeout=5) is True, "the first caller starts it"
    assert fake.run_calls == 1
    assert fake.running is True


def test_returns_only_after_the_reactor_is_actually_running(fake):
    # THE PROPERTY THE WHOLE FIX RESTS ON. vncdotool decides whether to spawn its
    # own reactor thread by reading reactor.running. If we returned before that
    # flag was set, the very next api.connect() would spawn a second thread and we
    # would have reproduced the bug we are fixing.
    s = ReactorStarter()
    s.ensure_started(fake, wait_timeout=5)
    assert fake.running is True


def test_signal_handlers_are_not_installed(fake):
    # The reactor runs on a non-main thread; installing signal handlers there
    # raises, and it would also steal SIGINT from the aiohttp server.
    s = ReactorStarter()
    s.ensure_started(fake, wait_timeout=5)
    assert fake.signal_handlers_arg is False


def test_THE_BUG_concurrent_callers_start_it_exactly_once(fake):
    # 24 threads, mirroring a fleet-wide RUN_SCRIPT start. Pre-fix this is where
    # two of them both saw running=False and the loser raised
    # ReactorAlreadyRunning, killing its taps.
    s = ReactorStarter()
    results = []
    errors = []
    barrier = threading.Barrier(24)

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(s.ensure_started(fake, wait_timeout=5))
        except Exception as e:               # recording, not handling
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == [], "no caller may fail: {0}".format(errors)
    assert fake.run_calls == 1, "reactor.run must be called once, got {0}".format(fake.run_calls)
    assert fake.double_start is False, "a second run() while running is the bug"
    assert results.count(True) == 1, "exactly one caller reports having started it"
    assert results.count(False) == 23, "the rest must report it was already up"
    # ...and every caller observed a running reactor before returning.
    assert fake.running is True


def test_second_sequential_call_is_a_cheap_no_op(fake):
    s = ReactorStarter()
    s.ensure_started(fake, wait_timeout=5)
    t0 = time.monotonic()
    assert s.ensure_started(fake, wait_timeout=5) is False
    assert time.monotonic() - t0 < 0.05, "the hot path must not poll or sleep"
    assert fake.run_calls == 1


def test_does_not_start_a_reactor_someone_else_already_started(fake):
    # vncdotool may win the race and start it first (e.g. a code path that calls
    # api.connect without going through _get_pooled_vnc). Starting a second one
    # is the bug, so an externally-running reactor must be left alone.
    fake.running = True
    s = ReactorStarter()
    assert s.ensure_started(fake, wait_timeout=5) is False
    assert fake.run_calls == 0


def test_a_stopped_reactor_is_never_restarted(fake):
    # Twisted forbids restarting a stopped reactor (ReactorNotRestartable), so
    # running going False later must NOT be read as "needs starting again".
    # Only a durable "we started it" flag is safe here, not reactor.running.
    s = ReactorStarter()
    s.ensure_started(fake, wait_timeout=5)
    assert fake.run_calls == 1
    fake.stop()                               # reactor stopped underneath us
    assert fake.running is False
    assert s.ensure_started(fake, wait_timeout=5) is False
    assert fake.run_calls == 1, "must not attempt a restart"
    assert fake.not_restartable is False, "a restart attempt would have raised"


def test_times_out_instead_of_hanging_forever():
    # A reactor that never comes up must not wedge the caller: this runs inside
    # _get_pooled_vnc, on the server's executor, during a fleet launch.
    class NeverStarts(FakeReactor):
        def run(self, installSignalHandlers=True):
            with self._counter_lock:
                self.run_calls += 1
                if self._started:
                    self.double_start = True
                    raise RuntimeError("Reactor already running")
                self._started = True
            while not self._stop.is_set():
                time.sleep(0.005)             # never sets the public `running`

    r = NeverStarts()
    try:
        s = ReactorStarter()
        with pytest.raises(TimeoutError):
            s.ensure_started(r, wait_timeout=0.15)
    finally:
        r.stop()


def test_a_timeout_does_not_wedge_later_callers():
    # After a failed start the flag must not be left saying "started", or every
    # later api.connect() would assume a reactor that is not there.
    r = FakeReactor(start_delay=0.30)
    try:
        s = ReactorStarter()
        with pytest.raises(TimeoutError):
            s.ensure_started(r, wait_timeout=0.05)
        # it did come up eventually; the next caller must see that and succeed
        time.sleep(0.40)
        assert r.running is True
        assert s.ensure_started(r, wait_timeout=5) is False
    finally:
        r.stop()


def test_each_starter_is_independent_no_test_only_reset_hook(fake):
    # State is per-instance rather than module-global so tests need no reset
    # backdoor in production code; server.py holds one shared instance.
    a, b = ReactorStarter(), ReactorStarter()
    assert a.ensure_started(fake, wait_timeout=5) is True
    assert b.ensure_started(fake, wait_timeout=5) is False   # sees running=True
    assert fake.run_calls == 1
