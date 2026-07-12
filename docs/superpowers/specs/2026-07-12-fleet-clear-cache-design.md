# Fleet "Clear cache" for a display group — design

**Date:** 2026-07-12
**Status:** Approved (design), pending plan
**Files:** `js/mmCache.js` (new `clear`), `js/mmCacheBackendModern.js` + `js/mmCacheBackendMmvideo.js` (new `backend.clear`), `tweak/mmcache/Tweak.x` (new `mmcache://clearall`), `mosaicmesh/websocket/legacy.py` (new `CLEAR_CACHE` handler), `index.html` (client `CLEAR_CACHE` dispatch), `js/timeline/modals/confirm-modal.js` (new generic confirm on `modal-shell`), `js/timeline/fleet/fleet-view.js` (+ its template: a `clearCache()` button), tests. Admin + client + tweak; deploy = admin/server immediate, tweak staged.

## Problem

Operators have no way to clear the cached render segments on a display group's devices. When a
device holds stale, partial, or superseded segments (e.g. after the historical
[[mmcache-supersede-evicts-siblings]] bug, a bad render, or just to reclaim space), the only recourse
today is manual per-device SSH `rm`. There is no Fleet-management control, and no uniform mechanism
across the two cache backends.

## Goal

Add a **"Clear cache" button** to the Fleet group detail that wipes the cached segments on every
device in the selected display group and clears the server's per-client `cachedSegments` record, so
the group starts fresh and re-pulls on next play. Do it through the existing `mmCache` backend
abstraction so both cache backends are handled uniformly.

## Non-goals

- **No re-pull trigger.** After clearing, devices re-populate on next play via the existing pipeline
  (auto-render → cache-reconcile → the arm-recache poll from [[wall-verr3-is-mmvideo-not-cache]]).
  Adding an immediate re-pull is YAGNI and risks a fleet-scale central herd ([[full-video-wifi-bound]]).
- **No stop-playback.** Clearing mid-play is safe (unix unlink of an open file lets the current clip
  finish; the next arm re-pulls). We do not stop or restart playback.
- **No clearing of server-side rendered assets** (`media/<key>/videos/seg_*`). Those are the source
  the devices pull FROM; they are managed by the render pipeline + `sweep_orphan_render_assets`, not
  this feature. This clears the *device* cache + the server's *record* of it only.
- **No per-client ack aggregation UI.** The button reports the server's `{count}` (devices targeted),
  matching `RELOAD`/`RUN_SCRIPT`. A native `__mmCacheCleared` ack is optional plumbing, not surfaced.
- ES5 only for `js/mmCache.js`, the two backends, and `index.html` (iPad-1 / iOS 5.1):
  no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`. [[legacy-ipad-compat]]
  (`js/timeline/fleet/fleet-view.js` is admin-side Alpine/ESM and may use modern JS.)

## Design

### 1. `mmCache.clear()` + a `backend.clear()` interface method (`js/mmCache.js`)

Add `clear` to the backend interface (alongside `fetchToCache`/`localSrc`/`evict`/`has`/`size`) and a
coordinator method that delegates then resets the JS bookkeeping:

```js
mmCache.clear = function (onDone, onFail) {
  var b = mmCache.backend;
  function done() { mmCache._tokens = {}; mmCache._order = []; if (onDone) { onDone(); } }
  if (!b || !b.clear) { done(); return; }           // no backend / old build -> just reset JS state
  b.clear(done, function (reason) { if (onFail) { onFail(reason); } });
};
```

`_tokens`/`_order` are reset on success so `mmCache.state(token)` returns `'none'` for every prior
token (the record now matches the wiped disk).

### 2. `backend.clear()` per backend

**Modern (`js/mmCacheBackendModern.js`)** — the Cache API is persistent + enumerable across sessions,
so one call is a complete wipe:

```js
clear: function (onDone, onFail) {
  var cs = _caches();
  if (!cs) { onFail(onFail ? 'no-cache-api' : undefined); return; }
  cs['delete'](CACHE_NAME).then(function () { _present = {}; if (onDone) { onDone(); } })
    ['catch'](function () { if (onFail) { onFail('delete-failed'); } });
}
```

**mmvideo (`js/mmCacheBackendMmvideo.js`)** — JS can't enumerate the on-disk dir, so it delegates to a
native wipe via the same hidden-iframe nav pattern as `fetch`/`evict`:

```js
clear: function (onDone, onFail) {
  _present = {};
  _nav('mmcache://clearall');
  if (onDone) { onDone(); }        // fire-and-forget; native wipe is best-effort (see ack note)
}
```

(An optional native `__mmCacheCleared` callback can later make this await completion; for v1 it is
fire-and-forget, consistent with how `evict` already works.)

### 3. `mmcache://clearall` native handler (`tweak/mmcache/Tweak.x`)

Add a branch alongside the existing `fetch`/`evict` URL handling: enumerate the
`/var/mobile/Media/MosaicMeshCache/` directory and remove its files (keep the directory itself so
lighttpd on `:8080` still has a docroot), e.g. `[NSFileManager removeItemAtPath:file]` per entry (or
remove the dir then recreate it empty). The dir holds only pushed cache files
(`seg_*`/`full_*`/`ind_*`), so wiping all contents is correct. Optionally call back
`__mmCacheCleared` via `stringByEvaluatingJavaScriptFromString` (mirrors `__mmCacheDone`).

### 4. `CLEAR_CACHE` server handler (`mosaicmesh/websocket/legacy.py`)

Mirror the `RELOAD` handler's scope logic (clientKey / displayID / all). For the display-group case:

```python
elif(msg["REQUEST"] == "CLEAR_CACHE"):
    payload = msg.get("PAYLOAD") or {}
    client_key = payload.get("clientKey")
    display_id = payload.get("displayID")
    if client_key:
        keys = [client_key] if client_key in server.settings.clients else []
    elif display_id:
        keys = [k for k, c in server.settings.clients.items()
                if getattr(c, "displayID", None) == display_id]
    else:
        keys = list(server.settings.clients.keys())
    for k in keys:
        broadcast_to_client(k, {"REQUEST": "CLEAR_CACHE", "PAYLOAD": "NONE"})
        c = server.settings.clients.get(k)
        cs = getattr(c, "cachedSegments", None) if c is not None else None
        if isinstance(cs, set):
            cs.clear()
        elif c is not None:
            c.cachedSegments = set()
    logging.warning("CLEAR_CACHE -> %d device(s)", len(keys))
    response["PAYLOAD"] = {"status": "SUCCESS", "count": len(keys)}
```

Clearing the server record immediately is essential: otherwise `_resolve_media_url` keeps routing the
device to a now-deleted `http://127.0.0.1:8080/seg_...` (404). With the record cleared, the device
serves/plays via the normal path and re-pulls when the pipeline next pushes/args.

### 5. Client `CLEAR_CACHE` dispatch (`index.html`)

In the message dispatch (near the `PRECACHE`/`STOP`/`PAUSE` branches), add:

```js
else if (data_obj.REQUEST === 'CLEAR_CACHE' && window.mmCache) {
  mmCache.clear();
  if (sock && typeof SockJS !== 'undefined' && sock.readyState === SockJS.OPEN) {
    sock.send(generateMessage('SRV', 'CLIENTLOG', { msg: 'mmcache-cleared' }));
  }
}
```

### 6. Confirm + Fleet UI button

**`fleet-confirm.js` is NOT a generic confirm** — it is coupled to `RUN_SCRIPT` (`ACTION_LABELS`,
`sendFrame` → RUN_SCRIPT). Its reusable primitive is `modals/modal-shell.js`'s `openModal({title,
contentEl})` / `closeModal()`, which its private `showConfirm` builds on. So we add a small **generic
confirm helper** on that same scaffold (the honest "reuse") rather than reshaping `fireFleetAction`:

**New `js/timeline/modals/confirm-modal.js`** — `confirmModal({ title, message, confirmLabel, danger,
onConfirm })`: builds a `<p>` message + a `.mm-form-actions` row (Cancel `btn-ghost` → `closeModal()`;
Confirm `btn-primary` [+ `mm-fleet-confirm-danger` when `danger`] → `closeModal()` then `onConfirm()`),
and calls `openModal({ title, contentEl })`. Mirrors `fleet-confirm.js` `showConfirm` (lines 57–98)
but parameterized. (This helper is reusable by future destructive actions too.)

**`clearCache()` in `fleet-view.js`** — a direct sibling of `reloadGroup()`:

```js
clearCache() {
  const id = this.selectedGroupId;
  if (!id) return;
  const count = (this.$store.mm.displays || []).filter(d => d.displayID === id).length;
  confirmModal({
    title: `Clear cache (group "${id}")`,
    message: `Clear cached video on ${count} device${count === 1 ? '' : 's'} in "${id}"? They'll re-pull on next play.`,
    confirmLabel: `Clear ${count} device${count === 1 ? '' : 's'}`,
    danger: true,
    onConfirm: () => {
      if (typeof window.sock === 'undefined' || typeof window.generateMessage !== 'function') {
        this.$store.mm.toast('SockJS not available; reload the page.', 'error'); return;
      }
      try {
        window.sock.send(window.generateMessage('SRV', 'CLEAR_CACHE', { displayID: id }));
        this.$store.mm.toast(`Cache clear sent to "${id}" (${count} device${count === 1 ? '' : 's'}).`, 'info');
      } catch (e) {
        this.$store.mm.toast(`Failed to send clear: ${e?.message || e}`, 'error');
      }
    },
  });
}
```

Import `confirmModal` in `fleet-view.js` (alongside the existing `fireFleetAction` import) and add a
**Clear cache** button to the group-actions row next to Reload in the component's template (the same
markup block that renders the Reload button).

## Error handling / edge cases

- **Old-tweak mmvideo device** (no `mmcache://clearall` handler yet): `_nav` is a no-op on-device, so
  the on-disk wipe doesn't happen, but `mmCache._tokens`/`_order` still reset and the server record
  still clears. Safe partial behavior; the device re-pulls anyway once the record is empty. Full wipe
  lands after the staged tweak redeploy.
- **Offline device in the group:** the broadcast doesn't reach it (SockJS), but its server-side
  `cachedSegments` is still cleared, so the record is honest; the device re-syncs on reconnect.
- **Clear during active playback:** current clip keeps playing (open file handle); the next arm hits
  the now-missing seg and the arm-recache poll re-pulls it. No black-out of the current clip.
- **`caches` unavailable (Modern over plain http)**: `clear` calls `onFail('no-cache-api')`; the JS
  reset still runs via `mmCache.clear`'s `done`. Consistent with the backend never having registered.
- **`cachedSegments` not a set** (older Client): coerce to `set()` (the handler assigns a fresh set).

## Testing

- **Node `--test`** (`tests/unit/js/`):
  - `mmCache.clear()` delegates to `backend.clear` and resets `_tokens`/`_order`; no-backend path just
    resets + calls `onDone`.
  - Modern `backend.clear()` calls `caches.delete('mm-seg')` and clears `_present` (mock `caches`).
  - mmvideo `backend.clear()` triggers a `mmcache://clearall` nav (mock `_nav`/`document`) and clears
    `_present`.
  - `confirmModal({onConfirm})`: renders via a mocked `openModal`; clicking the Confirm button calls
    `onConfirm` + `closeModal`, clicking Cancel calls only `closeModal` (matches the existing
    `modal-shell`/`fleet-confirm` test style if present, else a jsdom-free DOM-node assertion).
- **Python unit** (`tests/unit/`, mirroring `test_cache_pull_msg.py`): `CLEAR_CACHE {displayID}`
  broadcasts a `CLEAR_CACHE` to each group member and empties each member's `cachedSegments`;
  `{count}` equals the group size; a non-member's cache is untouched.
- **On-wall sign-off:** with a group holding cached segs, click Clear cache → confirm the
  `MosaicMeshCache` dir is emptied on a device (SSH `ls`), the server `cachedSegments` is empty, and
  the group re-pulls + plays on next PLAY (metric: `verr` clear + `rs>=2` + `ct` advancing — NOT
  `elapsed`; [[wall-verr3-is-mmvideo-not-cache]]). Verify in the webclip, not Safari
  ([[onwall-signoff-needs-webclip-not-safari]]).

## Deploy

- **Admin JS + server** (`fleet-view.js`, `legacy.py`, `index.html`, `mmCache.js`, both backends):
  ship immediately; a server restart + served-file refresh picks them up. Client JS reaches devices on
  their next reload.
- **Tweak** (`mmcache://clearall`): rebuild the dylib + **staged** redeploy to the fleet (single/
  small-batch, never a burst — [[fleet-ssh-no-burst]]). Until then, mmvideo `clear()` is a safe no-op
  on old-tweak devices (server record still clears; Modern clients fully functional).
