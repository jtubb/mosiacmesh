"""Per-(client, tag) sampler for the CLIENTLOG firehose.

WHY (measured 2026-10-15): mm_server.err reached 2.52 GB in ~2.5 days and the
server died with it; 16.85 GB of logs had piled up across 54 files. The volume is
arithmetic, not a leak -- 24 screens x ~1 CLIENTLOG/s x 86400 s x ~500 B is about
1 GB/day -- and legacy.py logged every one at WARNING unconditionally.

On a freshly rotated log, with playback STOPPED, one tag was 83.8% of the file:

    clock-sync         317 lines   230,821 bytes   83.8%
    boot                44 lines    34,142 bytes   12.4%
    clock-sync-first     4 lines     2,963 bytes    1.1%

A SAMPLER, NOT A FILTER, and only on the tags nothing measures:
  - `drift` and `hb` carry the (t_ms, ct) pairs synctool fits, and `elapsed`,
    which clockspread fits. Both average out 22-214 ms of one-sided uplink
    jitter by having MANY samples per window; thinning them would directly
    degrade the clock and video numbers this project runs on.
  - `clock-sync` is read by synctool ONLY as `latest_any`, a liveness
    reference. One line every few seconds serves that exactly as well as one
    per second, at a tenth of the bytes.

UNKNOWN TAGS DEFAULT TO FULL RATE. A diagnostic added later must not be silently
swallowed by a throttle written before it existed. And suppression is COUNTED so
the caller can record that it happened -- a log that quietly drops lines is how
the mmws lifetime counts came to mislead for months.
"""
import time


class ClientlogThrottle:
    """Decides whether a given client's CLIENTLOG line should be written.

    Pure except for its own state: `now_s` is supplied so the decision is
    testable and so one sweep judges every client against the same instant.
    """

    def __init__(self, interval_s=10.0, throttled=None, max_keys=4096):
        self.interval_s = float(interval_s)
        # Only tags named here are sampled. Everything else, known or not,
        # passes at full rate.
        self.throttled = set(throttled or ())
        self.max_keys = int(max_keys)
        self._last = {}                 # (key, tag) -> last emitted now_s
        self.suppressed_total = 0       # lifetime, never reset
        self.suppressed_since_report = 0  # since the last take_suppressed()

    def should_log(self, key, tag, now_s=None):
        if now_s is None:
            now_s = time.time()
        if self.interval_s <= 0:
            return True                 # operator switched it off
        if tag is None or tag not in self.throttled:
            return True
        k = (key, tag)
        last = self._last.get(k)
        # `now < last` means the clock stepped backwards (NTP, or a DST shift on
        # the Windows host). A naive `now - last >= interval` would then suppress
        # until wall time caught up, which could be an hour.
        if last is None or now_s < last or (now_s - last) >= self.interval_s:
            if len(self._last) >= self.max_keys and k not in self._last:
                # SRC arrives off the wire, so a bogus or spoofed value must not
                # be able to grow this map without bound. Dropping the oldest
                # entry costs at most one extra line for a real client.
                oldest = min(self._last, key=lambda x: self._last[x])
                del self._last[oldest]
            self._last[k] = now_s
            return True
        self.suppressed_total += 1
        self.suppressed_since_report += 1
        return False

    def take_suppressed(self):
        """Suppressed count since the last call; resets that window only."""
        n = self.suppressed_since_report
        self.suppressed_since_report = 0
        return n

    def tracked_keys(self):
        return len(self._last)
