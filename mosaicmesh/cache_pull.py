"""Client-pull cache orchestration: throttled PRECACHE grants + per-client cache
state + the play-gate helper. Pure logic; no aiohttp/SockJS here (server.py wires
the actual sends)."""


class PrecacheWindow:
    """Grants PRECACHE to at most `n` clients at once; advance() releases the next
    as each acks, bounding peak WiFi to n * segment-size. Each active grant carries
    its grant time so sweep_timeouts() can advance past a client that never acks
    (offline / stale client JS with no PRECACHE handler) instead of stalling the
    whole group's precache. `now` is passed in (pure/deterministic — no clock here)."""

    def __init__(self, clients, n=3):
        self._waiting = list(clients)
        self._active = {}          # key -> grant_time (float)
        self._n = max(1, int(n))

    def start(self, now=0.0):
        granted = []
        while self._waiting and len(self._active) < self._n:
            k = self._waiting.pop(0)
            self._active[k] = now
            granted.append(k)
        return granted

    def advance(self, done_key, now=0.0):
        self._active.pop(done_key, None)
        if self._waiting and len(self._active) < self._n:
            k = self._waiting.pop(0)
            self._active[k] = now
            return k
        return None

    def sweep_timeouts(self, now, timeout_s):
        """Advance past any active grant older than `timeout_s` (a client that never
        acked). Returns (timed_out_keys, newly_granted_keys) so the caller can mark the
        timed-out clients failed and PRECACHE the newly-granted ones."""
        stale = [k for k, t in self._active.items() if (now - t) > timeout_s]
        granted = []
        for k in stale:
            nxt = self.advance(k, now)   # drops k, grants the next waiting client
            if nxt is not None:
                granted.append(nxt)
        return stale, granted

    def drained(self):
        return not self._waiting and not self._active
