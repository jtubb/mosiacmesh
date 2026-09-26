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

    def touch(self, key, now=0.0, stall_s=None):
        """Record PROGRESS on an active grant. Returns False if `key` is not
        currently granted (unknown, or already completed).

        Two effects, both needed:
          - resets the grant's clock, so a transfer that is visibly moving is
            never mistaken for a dead one however long it takes;
          - once `stall_s` is given, replaces the whole-file deadline with that
            much shorter no-progress window, so a client that DIES mid-transfer
            frees its slot in seconds instead of minutes.

        A client that never reports progress is never touched and keeps its
        original deadline, so old client JS behaves exactly as before -- absence
        of progress is the backward-compatible fallback, which is why this needs
        no capability flag."""
        if key not in self._active:
            return False
        _t, per = self._active[key]
        self._active[key] = (now, per if stall_s is None else float(stall_s))
        return True

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

    def snapshot(self, now, timeout_s=None):
        """Read-only view of this window, for the /debug/precache endpoint.

        WHY THIS IS NEEDED: `precache_windows` lives only in memory with no
        accessor, and `_send_precache` logs at INFO while the server runs at
        WARNING. On 2026-09-26 two canaries sat with an empty cache and
        propagationPercent 0.0 while not one grant reached them for 14 minutes,
        and every candidate explanation -- reconcile disabled, window wedged,
        grant delivered into a stale SockJS session -- fitted the evidence
        equally well because none of them could be checked. This makes the
        window's actual state answerable instead of arguable.

        Ages and deadlines are reported PER GRANT because that is where the
        subtlety is: two grants issued in the same instant can have very
        different deadlines (size-aware) and a progress `touch` swaps one for the
        much shorter stall window. `timeout_s` is the same flat fallback
        sweep_timeouts takes, so the view can never disagree with the sweep.

        Must not mutate: a debug handler being polled cannot be allowed to change
        fleet behaviour."""
        active = []
        overdue = 0
        for k, (t, per) in self._active.items():
            deadline = per if per is not None else (timeout_s if timeout_s is not None else 0.0)
            age = now - t
            is_overdue = age > deadline
            if is_overdue:
                overdue += 1
            active.append({
                "key": k,
                "ageS": round(age, 1),
                "deadlineS": round(float(deadline), 1),
                "overdue": is_overdue,
            })
        active.sort(key=lambda e: e["key"])
        return {
            "n": self._n,
            "active": active,
            "waiting": list(self._waiting),
            "overdueCount": overdue,
            "drained": self.drained(),
        }
