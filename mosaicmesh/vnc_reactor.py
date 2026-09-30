"""Start Twisted's reactor exactly once, for the Veency/vncdotool VNC path.

WHY THIS EXISTS (measured on-wall 2026-09-30):
A fleet-wide `RUN_SCRIPT start` (24 devices) put this in the server log, twice:

    Exception in thread Twisted Reactor:
    twisted.internet.error.ReactorAlreadyRunning

The reactor is a PROCESS-WIDE SINGLETON that may be started once, but
vncdotool 1.3.0's api.connect() starts it with an unlocked check-then-act:

    if not reactor.running:
        _THREAD = threading.Thread(target=reactor.run, ...)
        _THREAD.name = "Twisted Reactor"
        _THREAD.start()
        observer = PythonLoggingObserver(); observer.start()

`reactor.running` is only set INSIDE reactor.run(), after the thread is
scheduled, and nothing serialises the window. `_get_pooled_vnc` handshakes
outside its own lock on purpose (so one dozing iPad cannot block 23 others), so a
fleet start calls api.connect() 24 times at once: several read False, several
spawn a reactor thread, and every loser's reactor.run() raises inside a thread
where nothing handles it.

WHY IT MATTERS MORE THAN A LOGGED TRACEBACK:
the losing thread dies, so the VNC taps it was going to perform never happen. An
iPad-1 webclip needs one real tap to start video ([[ios5-video-gesture]]), so
those screens just do not launch -- seen as "screen20 did not come back" on
2026-09-29 and 2026-09-30, with NO crash report on the device because the process
never started rather than died. A second RUN_SCRIPT start fixed it each time,
which is exactly what made it look like generic "VNC tap flakiness". It also
clobbers vncdotool's module-global _THREAD (so a later api.shutdown() would join
the wrong thread) and starts a second PythonLoggingObserver, duplicating every
Twisted log line.

WHY NOT JUST SERIALISE api.connect():
that would undo the deliberate concurrency in `_get_pooled_vnc` and make a
24-screen launch pay ~24 sequential RFB handshakes. Instead we start the reactor
ourselves, once, and do not return until `reactor.running` is True -- after which
every api.connect() takes its `if not reactor.running` false branch and never
spawns a thread at all. The handshakes stay parallel; only the one-time start is
serialised.

State is per-instance rather than module-global so that tests need no reset
backdoor in production code; server.py holds a single shared instance.
"""
import logging
import threading
import time


class ReactorStarter:
    """Idempotent, thread-safe starter for a single Twisted reactor."""

    def __init__(self):
        self._lock = threading.Lock()
        self._started = False
        self._thread = None

    @property
    def thread(self):
        """The reactor thread, if this instance started it."""
        return self._thread

    def ensure_started(self, reactor, wait_timeout=10.0, poll=0.005):
        """Guarantee a running reactor before returning.

        Returns True if this call started it, False if it was already up.
        Raises TimeoutError if the reactor does not report `running` in time --
        better than wedging a fleet launch on the server's executor forever.

        The whole lock is held across the wait on purpose: callers after the
        first must not be allowed to observe `running is False` and conclude they
        should start their own, which is precisely vncdotool's bug. The wait is
        milliseconds, and the post-start path takes the lock only briefly.
        """
        with self._lock:
            # `self._started` is the durable guard, NOT `reactor.running`. Twisted
            # forbids restarting a stopped reactor (ReactorNotRestartable), so if
            # `running` goes False later that means "stopped", never "needs
            # starting again" -- reading it as the latter would raise.
            if self._started:
                return False
            # Someone else (vncdotool, via a path that bypasses us) may have won
            # the race. Starting a second one is the bug; leave theirs alone.
            if getattr(reactor, "running", False):
                self._started = True
                return False

            thread = threading.Thread(
                target=reactor.run,
                name="Twisted Reactor",
                # The reactor runs off the main thread: installing signal
                # handlers there raises, and would also steal SIGINT from the
                # aiohttp server.
                kwargs={"installSignalHandlers": False},
            )
            thread.daemon = True
            thread.start()

            deadline = time.monotonic() + float(wait_timeout)
            while not getattr(reactor, "running", False):
                if time.monotonic() > deadline:
                    # Deliberately leave `_started` False: a later caller must be
                    # free to find a reactor that came up late (or to try again),
                    # rather than assume one that is not there.
                    raise TimeoutError(
                        "Twisted reactor did not report running within "
                        "{0}s".format(wait_timeout))
                time.sleep(poll)

            self._started = True
            self._thread = thread
            logging.info("Twisted reactor started for the Veency VNC path")
            return True


# The process-wide instance. One reactor per process, so one starter.
_starter = ReactorStarter()


def ensure_reactor(wait_timeout=10.0):
    """Start the real Twisted reactor once, before any concurrent api.connect().

    Safe to call from any thread and from every VNC code path; cheap after the
    first call. Also starts vncdotool's PythonLoggingObserver once, because
    pre-starting the reactor means vncdotool's own connect() never reaches the
    branch that would have started it, and without it Twisted's internal log
    events (including errors on the VNC connections) go nowhere.
    """
    from twisted.internet import reactor

    started = _starter.ensure_started(reactor, wait_timeout=wait_timeout)
    if started:
        try:
            from twisted.python.log import PythonLoggingObserver
            PythonLoggingObserver().start()
        except Exception as e:                # observability is not worth a failure
            logging.warning("could not attach Twisted log observer: %s", e)
        # Keep api.shutdown() honest: it joins vncdotool's module-global _THREAD,
        # which stays None when we start the reactor instead of it.
        try:
            from vncdotool import api
            if getattr(api, "_THREAD", None) is None:
                api._THREAD = _starter.thread
        except Exception:
            pass
    return started
