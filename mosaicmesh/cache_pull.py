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


class CacheState:
    """Per-client cached token (one live token per client). Ack-driven; replaces
    push-progress polling."""

    def __init__(self):
        self._cached = {}   # client_key -> token
        self._failed = {}   # client_key -> token

    def record_cached(self, client, token):
        self._cached[client] = token
        if self._failed.get(client) == token:
            del self._failed[client]

    def record_failed(self, client, token):
        self._failed[client] = token

    def is_cached(self, client, token):
        return self._cached.get(client) == token

    def cached_clients(self, clients, token):
        return [c for c in clients if self._cached.get(c) == token]
