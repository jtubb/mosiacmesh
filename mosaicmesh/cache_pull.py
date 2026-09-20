"""Client-pull cache orchestration: throttled PRECACHE grants + per-client cache
state + the play-gate helper. Pure logic; no aiohttp/SockJS here (server.py wires
the actual sends)."""


class PrecacheWindow:
    """Grants PRECACHE to at most `n` clients at once; advance() releases the next
    as each acks, bounding peak WiFi to n * segment-size. Each active grant carries
    its grant time so sweep_timeouts() can advance past a client that never acks
    (offline / stale client JS with no PRECACHE handler) instead of stalling the
    whole group's precache. `now` is passed in (pure/deterministic — no clock here)."""

    def __init__(self, clients, n=3, timeout_for=None):
        self._waiting = list(clients)
        self._active = {}          # key -> (grant_time, timeout_s or None)
        self._n = max(1, int(n))
        # timeout_for(key) -> seconds for THAT client's grant. The ack deadline
        # has to scale with the bytes being moved: a flat 20s was sized for ~11MB
        # segments and made a 93MB one structurally impossible to complete --
        # every grant timed out, freeing the slot and provoking a re-issue that
        # restarted the download from zero. None keeps the flat fallback that
        # sweep_timeouts(timeout_s=...) supplies.
        self._timeout_for = timeout_for

    def _grant(self, key, now):
        t = None
        if self._timeout_for is not None:
            try:
                t = float(self._timeout_for(key))
            except Exception:
                t = None
        self._active[key] = (now, t)

    def start(self, now=0.0):
        granted = []
        while self._waiting and len(self._active) < self._n:
            k = self._waiting.pop(0)
            self._grant(k, now)
            granted.append(k)
        return granted

    def advance(self, done_key, now=0.0):
        self._active.pop(done_key, None)
        if self._waiting and len(self._active) < self._n:
            k = self._waiting.pop(0)
            self._grant(k, now)
            return k
        return None

    def sweep_timeouts(self, now, timeout_s=None):
        """Advance past any active grant past ITS OWN deadline (a client that never
        acked). Returns (timed_out_keys, newly_granted_keys) so the caller can mark the
        timed-out clients failed and PRECACHE the newly-granted ones.

        Each grant carries the deadline computed when it was issued, so a client
        granted into a freed slot gets its own, not the one the previous holder
        had. `timeout_s` is the fallback for grants with no per-grant value."""
        stale = [k for k, (t, per) in self._active.items()
                 if (now - t) > (per if per is not None else (timeout_s or 0.0))]
        granted = []
        for k in stale:
            nxt = self.advance(k, now)   # drops k, grants the next waiting client
            if nxt is not None:
                granted.append(nxt)
        return stale, granted

    def drained(self):
        return not self._waiting and not self._active
