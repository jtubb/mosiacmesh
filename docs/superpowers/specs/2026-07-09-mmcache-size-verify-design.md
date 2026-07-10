# mmcache download size-verification — design

**Date:** 2026-07-09
**Status:** approved (brainstorming)

## Problem

The client-pull cache tweak (`tweak/mmcache/Tweak.x`, `mm_download()`) downloads a
segment with `[NSData dataWithContentsOfURL:]`, writes whatever it gets, and acks
**CACHED** — with **no verification that the download is complete.**
`dataWithContentsOfURL:` returns **truncated data (non-nil, no error)** when a transfer
is interrupted (WiFi contention, connection drop). The result: a partial `.mp4` is
cached, marked ready, and served locally by `_per_client_items` (`127.0.0.1:8080`), and
the iPad-1 hits **`verr=3` (MEDIA_ERR_DECODE)** — the screen never starts playing.

The server source is a valid full file (ffprobe: H.264 Constrained Baseline / 3.1 /
768×1024); only the *client's cached copy* is truncated. Root-caused 2026-07-09 after the
cache fill-reconcile pulled ~48 segments fleet-wide under load and left ~17/24 screens
stuck at `verr=3`. (The reconcile is now paused behind `MM_CACHE_RECONCILE`, default OFF,
until this is fixed.)

## Goal

A truncated/incomplete download becomes a clean **`CACHE_FAILED`** (→ the client streams
central + can retry) instead of a poisoned local copy. The cache only ever holds
byte-complete files.

## The change — `mm_download()` in `tweak/mmcache/Tweak.x`

Replace the completeness-blind `dataWithContentsOfURL:` with a request that exposes the
response, and verify before writing:

1. Build an `NSURLRequest` from the URL (via `objc_msgSend`, no ARC — the tweak's style).
2. `[NSURLConnection sendSynchronousRequest:returningResponse:error:]` (available on
   iOS-5) → returns `NSData *data` and fills an `NSURLResponse *` out-param.
3. **Guards (strict):**
   - `data` is non-nil.
   - the response is an `NSHTTPURLResponse` with **`statusCode == 200`** (reject 206
     partial, 4xx, 5xx).
   - **`expectedContentLength`** is known (`> 0`, not `NSURLResponseUnknownLength` / -1).
   - **`[data length] == expectedContentLength`** (the completeness check).
4. **PASS** → `createDirectory` + `writeToFile:atomically:` + `dispatch_done(token, bytes)`
   (→ `__mmCacheDone` → CACHED).
5. **FAIL** (any guard) → do **NOT** write the file + `dispatch_fail(token, reason)`
   (→ `__mmCacheFail` → CACHE_FAILED). Reasons: `"net"` (nil), `"http"` (bad status),
   `"len"` (unknown/mismatched length).

Because verification happens **before** `writeToFile:`, no partial file is ever written —
no cleanup needed. The `dlctx` struct, the `mmcache://fetch` parsing, the dispatch
helpers, and the `evict` path are unchanged.

## iOS-5 API notes

- `NSURLConnection sendSynchronousRequest:returningResponse:error:` is a class method with
  an `NSURLResponse **` out-param — reached via `objc_msgSend` cast to
  `(id (*)(id, SEL, id, id*, id*))`. Present in iOS-5.1 (deprecated only much later).
- `NSURLResponse` `expectedContentLength` returns `long long` (`-1` =
  `NSURLResponseUnknownLength`).
- `NSHTTPURLResponse` `statusCode` returns `NSInteger`.
- Foundation only — no new frameworks; the Makefile's `mmcache_FRAMEWORKS = Foundation`
  is sufficient (`NSURLConnection`/`NSURLRequest`/`NSHTTPURLResponse` are Foundation).

## Build

Build via the existing WSL Ubuntu theos toolchain (`jtubb@/home/jtubb/theos`) using
`tweak/mmcache/build.sh` (copies sources to `~/mmcache`, `make`). The build is gated on the
script's existing `nm` invariants:
- **No C++ unwind symbols** (`_Unwind*`, `gxx_personality`) — a static-init/unwind symbol
  SIGKILLs the tweak at load on iOS-5.
- **No `OBJC_CLASS_$` defined** — a static ObjC class SIGKILLs load; the tweak stays
  Foundation-via-`objc_msgSend` only.
- Undefined symbols limited to libSystem / ObjC runtime.

The added `NSURLConnection` calls are all `objc_msgSend` (no new ObjC class, no C++), so
they preserve these invariants.

## Testing

Native tweak code — no unit-test harness (consistent with the other tweaks).
Verification gate:
1. **Build gate:** `build.sh` completes and the `nm` checks pass (clean, load-safe dylib).
2. **Post-deploy on-device smoke (ONE iPad):**
   - a normal pull acks **CACHED** and plays the local copy (no `verr`);
   - a truncated pull (e.g. induced by killing WiFi mid-pull, or a deliberately short
     server response) acks **CACHE_FAILED** and the client streams central — **no `verr=3`,
     no poisoned cache entry**.

## Out of scope

- The paced fleet redeploy (scp `mmcache.dylib` + plist per device → respring) — a separate
  operation, sequential + paced per the no-burst-SSH rule.
- Re-enabling the reconcile (`MM_CACHE_RECONCILE`) — only after this fix is deployed +
  verified on-device at scale.
- Any server / `mmCache.js` / PRECACHE-protocol / `_per_client_items` change.
- The sync-cadence (1s-until-settled) question — unrelated, separate.
