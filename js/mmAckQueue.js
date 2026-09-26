// js/mmAckQueue.js — ES5. A cache ack must not be lost because the transport
// happened to be down when it was raised.
//
// WHY THIS EXISTS (measured on-wall 2026-09-26):
//   index.html wired mmCache.onAck as
//
//       if (sock && sock.readyState === SockJS.OPEN) { sock.send(...); }
//
//   with no else, no queue and no retry. An ack raised while the socket was
//   momentarily not OPEN was dropped silently and forever.
//
//   It failed ASYMMETRICALLY, which is why it hid: a DOWNLOAD ack fires minutes
//   after page load, when the socket is certainly open, so it lands. A HIT ack
//   (segment already on disk) fires milliseconds after the grant arrives, so it
//   is the one that vanishes. sign1screen23 logged
//       [mmcache] HIT token=seg_4298b787efa0_0 bytes=66665122 (already on disk)
//   while /api/discovery/devices still reported propagationPercent 0.0 and
//   cachedSegments without that token.
//
//   The damage is not one stale field. A terminal ack is what calls
//   PrecacheWindow.advance(), and reconcile_group_cache() returns early while
//   `display_id in precache_windows` -- so ONE dropped ack freezes the whole
//   group's cache self-heal until that grant's size-aware deadline expires (up
//   to PRECACHE_ACK_MAX = 1800s). A completed pull becomes a half-hour stall for
//   every other screen in the group. See [[play-races-cache-propagation]] for the
//   downstream symptom: a screen whose cache the server does not know about is
//   routed to central streaming and goes dark on a playlist switch.
//
// SCOPE: this survives a transport blip, not a page reload -- the queue is in
//   memory. The durable answer is for the client to enumerate its cache dir at
//   startup and report the inventory, so the server's record is self-correcting
//   instead of depending on catching every single ack. That is a larger design
//   change (the tweak currently exposes no enumeration) and is tracked separately.
(function (root) {
    'use strict';

    var DEFAULT_CAP = 64;

    // Requests that are FACTS about what the device holds, and so are worth
    // replaying. CACHE_PROGRESS is deliberately absent: it is liveness, and a
    // progress report replayed after the fact would refresh a grant deadline on
    // evidence that has since expired -- the opposite of what the stall timeout
    // is for. Dropping one costs nothing; another follows every few MB.
    function isDurable(req) {
        return req === 'CACHED' || req === 'CACHE_FAILED';
    }

    // sendFn(req, payload)  — performs the actual send; may throw.
    // isOpenFn()            — true when the transport can carry a message now.
    // cap                   — max retained acks (default 64).
    function mmAckQueue(sendFn, isOpenFn, cap) {
        var pending = [];
        var limit = cap || DEFAULT_CAP;

        // Drain oldest-first. Stop on the FIRST failure and leave that ack at the
        // head: readyState can report OPEN and the send still throw if the socket
        // dies between the check and the call, so shifting before a confirmed send
        // would lose the ack for precisely the reason this module exists.
        function flush() {
            while (pending.length && isOpenFn()) {
                var a = pending[0];
                try { sendFn(a.req, a.payload); }
                catch (e) { return; }          /* keep it; retry on the next tick */
                pending.shift();
            }
        }

        function push(req, payload) {
            if (!isDurable(req)) {
                // Fire-and-forget. Never retained, never replayed.
                if (isOpenFn()) { try { sendFn(req, payload); } catch (e) {} }
                return;
            }
            // Bounded: an unbounded queue on a device that has been offline for
            // hours is a leak on a 247MB iPad-1 whose webclip is already
            // jetsam-adjacent. When something must go it is the OLDEST -- the
            // newest ack is the current truth about what the device holds.
            if (pending.length >= limit) { pending.shift(); }
            pending.push({ req: req, payload: payload });
            flush();
        }

        return {
            push: push,
            flush: flush,
            length: function () { return pending.length; }
        };
    }

    root.mmAckQueue = mmAckQueue;
    if (typeof module !== 'undefined' && module.exports) { module.exports = { mmAckQueue: mmAckQueue }; }
})(typeof window !== 'undefined' ? window : global);
