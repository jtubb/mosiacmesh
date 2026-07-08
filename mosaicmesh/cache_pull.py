"""Client-pull cache orchestration: throttled PRECACHE grants + per-client cache
state + the play-gate helper. Pure logic; no aiohttp/SockJS here (server.py wires
the actual sends)."""


class PrecacheWindow:
    """Grants PRECACHE to at most `n` clients at once; advance() releases the next
    as each acks, bounding peak WiFi to n * segment-size."""

    def __init__(self, clients, n=3):
        self._waiting = list(clients)
        self._active = set()
        self._n = max(1, int(n))

    def start(self):
        granted = []
        while self._waiting and len(self._active) < self._n:
            k = self._waiting.pop(0)
            self._active.add(k)
            granted.append(k)
        return granted

    def advance(self, done_key):
        self._active.discard(done_key)
        if self._waiting and len(self._active) < self._n:
            k = self._waiting.pop(0)
            self._active.add(k)
            return k
        return None

    def drained(self):
        return not self._waiting and not self._active
