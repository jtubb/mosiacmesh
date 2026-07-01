# mmws device bridge — reverse-engineering log

Goal (Layer 3 device side, see DESIGN.md): in the `com.apple.webapp` webclip host,
(1) inject `mmws.js` before SockJS runs, (2) surface `window.__mmwsNative` {open,send,close}
to JS, (3) deliver native->JS via evaluating `window.__mmwsDispatch(...)`. Constraint: the
mmvideo tweak model — plain ObjC (`.x`/`.m`), NO ObjC classes, no C++ unwind (§ mmvideo).

## §1 — target process + web-view class (TODO: fill from on-device inspection)

Questions to answer:
- Does `Web.app` use `UIWebView` (UIKit) or the lower-level `WebView`/`WebFrame` (private
  WebKit)? Determines the injection + native->JS API surface.
- JS injection hook: `-[UIWebView stringByEvaluatingJavaScriptFromString:]` after load, OR the
  frame-load delegate `-webView:didClearWindowObject:forFrame:` (private WebKit) / a WebCore
  page-load point — need it to run BEFORE the page's SockJS constructs its transport.
- URL-scheme interception hook: `-webView:shouldStartLoadWithRequest:navigationType:`
  (UIWebViewDelegate) OR the WebKit resource/policy delegate. Where `mmws://` requests land.
- How to get a reference to the live web view from the tweak (to evaluate JS on it).

## §1 FINDINGS (2026-07-01, from /Applications/Web.app/Web, 49KB armv7 — pulled to host,
`grep -a` since the device has no `strings`)

- **Web-view stack:** `Web.app` uses **UIKit `UIWebView`** (backed by the internal
  `UIWebBrowserView` + private WebKit `WebView`). Links UIKit + WebKit + WebCore +
  JavaScriptCore + WebUI + CFNetwork.
- **App class = `WebAppController`** (the UIApplicationDelegate + web-view host). Other classes:
  `UIWebBrowserView`, `SheetController`, `PrintBrowser`, `CertificateContextWebView`.
- **Interception hook FOUND:** `webView:shouldStartLoadWithRequest:navigationType:` is referenced
  (UIWebViewDelegate) — the app is its own UIWebView delegate (`WebAppController`). This is where
  a `mmws://` navigation lands → intercept + return NO + dispatch to mmwsconn.
- **Injection hook FOUND:** `webView:didClearWindowObject:forFrame:` is referenced (private WebKit
  `WebView` frameLoadDelegate; fires when the JS window is created, BEFORE the page's inline
  scripts) — the place to evaluate `mmws.js` + define `__mmwsNative` so it exists before SockJS.
- **native->JS:** `-[UIWebView stringByEvaluatingJavaScriptFromString:]` (UIKit) — call on the
  web-view instance to run `__mmwsDispatch(...)`.
- Note: no ObjC classes allowed in the tweak (§ mmvideo §11), so JS→native can't use a native
  WebScripting object; the **URL-scheme** path (JS navigates `mmws://`, we hook
  shouldStartLoadWithRequest) is the class-free bridge. All three hooks are ObjC method hooks
  (MSHookMessageEx-style) or the mmvideo MSHookFunction-on-symbol approach — no new class needed.

## §2 HOOK PLAN (to implement + on-device verify next)

1. **Inject** `mmws.js` + a JS `__mmwsNative` shim in `webView:didClearWindowObject:forFrame:`
   (hook the frameLoadDelegate; call `[webView stringByEvaluatingJavaScriptFromString:@MMWS_JS]`
   where the injected `__mmwsNative.{open,send,close}` just do `location`/hidden-iframe navigations
   to `mmws://open/<id>?url=...`, `mmws://send/<id>?d=...`, `mmws://close/<id>?c=...`).
   - OPEN QUESTION: is `didClearWindowObject:forFrame:` implemented by `UIWebBrowserView` (hookable
     class) or deeper in WebKit? Confirm the class + whether it fires per main-frame load. If it's
     awkward, fall back to injecting on the first `shouldStartLoadWithRequest` of the main frame.
2. **Intercept** `mmws://` in `-[WebAppController webView:shouldStartLoadWithRequest:navigationType:]`
   (hook it; if request.URL.scheme == "mmws", parse op/id/args, drive `mmwsconn_*`, return NO;
   else call the original). Need to reach the `UIWebView` for native->JS — it's the delegate
   method's `webView` arg (and/or a `WebAppController` ivar). Keep a global id->MMWSConn + the
   webview ref.
3. **native->JS:** in the mmwsconn_cb callbacks (already main-thread via the run loop), call
   `[webview stringByEvaluatingJavaScriptFromString: @"__mmwsDispatch(id,'message', <json-escaped>)"]`.
   Escape payloads for JS-string safety.

## §3 CONFIRMED ON THE LIVE WEBCLIP (2026-07-01, observe-only probe `tweak/mmwsprobe/`)

Probe hooks ALL classes declaring the two selectors (runtime scan) + logs firing, tagged by
bundle id. Results from **com.apple.webapp / Web.app (pid 256)** — the authoritative webclip:

- **INTERCEPTION hook = `-[WebAppController webView:shouldStartLoadWithRequest:navigationType:]`**
  FIRED: `self=WebAppController nav=5 url=http://192.168.1.60:3000/?tdbg` (the client page load).
  So `mmws://` navigations from JS will land here → intercept (return NO) + drive mmwsconn.
  The `webView` arg is the UIWebView → use it for native->JS `stringByEvaluatingJavaScriptFromString:`.
- **INJECTION hook = `-[UIWebViewWebViewDelegate webView:didClearWindowObject:forFrame:]`**
  FIRED first (before `-[UIWebView …]` and before page JS). The 2nd arg (windowObject) is the
  page's `WebScriptObject` → inject with `[windowObject evaluateWebScript:@MMWS_JS]` (no UIWebView
  ref needed for injection). Webclip declarers: WebDefaultFrameLoadDelegate, UIWebView,
  UIWebViewWebViewDelegate. FIRING order: UIWebViewWebViewDelegate, then UIWebView.
- **native->JS** = `-[UIWebView stringByEvaluatingJavaScriptFromString:]` on the webView from the
  shouldStartLoad hook (cache it), OR re-eval on the cached windowObject.

CONTRAST — Safari (com.apple.mobilesafari, pid 236): injection fires on `TabDocument` (a WebUI
class ABSENT in the webclip) and it does NOT use shouldStartLoadWithRequest at all (drives the
private WebView + WebPolicyDelegate). So Safari data is NOT transferable for these hooks — the
webclip's WebAppController + UIWebViewWebViewDelegate are authoritative. (Probe is harmless/
observe-only; remove `tweak/mmwsprobe/` dylib from the device when the real bridge ships.)

Net: the bridge tweak hooks WebAppController.shouldStartLoad (mmws:// intercept) +
UIWebViewWebViewDelegate.didClearWindowObject (inject mmws.js via evaluateWebScript) + caches the
UIWebView for stringByEvaluatingJavaScriptFromString dispatch. All three confirmed reachable.

## §2 OPEN ITEMS
- Confirm the exact class owning each selector (hook target): `WebAppController` for
  shouldStartLoad; the frameLoadDelegate class for didClearWindowObject. (class-dump the
  __objc sections next, or an on-device observe hook logging `object_getClassName(self)`.)
- Data-size: URL-scheme args cap URL length; large SockJS frames may need chunking or a
  different JS->native channel (e.g. XHR to a `mmws://` host the tweak answers). SockJS control
  msgs are small, so start simple.
- Verify end-to-end on-device: access log flips device IP from `xhr_send` to `.../websocket`.

## §4 STABILITY DEBUGGING (2026-07-01) — bridge connects but is NOT yet production-stable

The bridge does a real `101` upgrade and carries traffic, but does not HOLD. Three distinct
bugs found by breadcrumb-instrumenting the message/socket path (Tweak.x `bc()` +
mmwsconn.c `cbc()` behind `-DMMWSCONN_BC`; breadcrumbs to `/var/mobile/mmws_bc.txt`,
events to `/var/mobile/mmws.log`). Two FIXED, one OPEN:

1. **CRASH (FIXED, commit 273e5ac).** `dispatch_js` called `stringByEvaluatingJavaScriptFromString:`
   SYNCHRONOUSLY from inside the CFStream read callback → re-entered WebKit's JS engine
   mid-network-service → process torn down inside WebKit (NO crash report, NO socket error;
   only dies under message traffic, so a 1-min check passes). FIX: `dispatch_js` builds the JS
   and hands it to `eval_and_free` via `dispatch_async_f(main_queue,…)` — eval on a clean
   main-loop turn. Signature: breadcrumbs balanced afterward, Web.app stays alive.
2. **DEADLOCK/HANG (FIXED, bridge-shim.js).** After #1, the eval ran on the main thread but the
   bridge-shim navigated the send-iframe SYNCHRONOUSLY inside the eval (proven by nesting
   `evalPre→sendPre→sendPost→evalPost`). A received msg → eval (main thread) → `ws.send` →
   iframe `mmws://` → `shouldStartLoad` needs the main thread → but main is blocked in the eval
   waiting on the web thread → HANG (breadcrumb `evalPre` with NO `evalPost`, Web.app alive,
   run loop frozen, client stalls, "video won't start"). FIX: `bridge-shim.js` `nav()` wraps the
   iframe creation in `setTimeout(0)` — the send fires on a fresh JS turn, eval returns first,
   main thread free. Signature: video now plays FULLY THROUGH.
3. **RELOAD-AT-LOOP (OPEN — the remaining bug).** At the playlist/video loop the client
   PAGE-RELOADS (confirmed: a `'boot'` CLIENTLOG tag at the reconnect; NO server `RELOAD`
   broadcast → it's a client SELF-reload, almost certainly its global uncaught-error handler
   tripping on something the bridge does during the loop's PLAY/PREPARE burst). The reload
   tears down the WS with no clean close (balanced breadcrumbs, `OPEN` increments, `CLOSE=0`
   `ERROR=0`), and the fresh page's WS re-attempt is SLOW (~2-3 min) → SockJS falls back to XHR
   in between. Continuity: repeated multi-minute gaps, reconnect every ~loop-period.
   NEXT STEPS: (a) breadcrumb `didClearWindowObject` (page-load) to timestamp the reload vs the
   burst; (b) find the JS error — inspect the client's uncaught-error/self-reload handler
   (`index.html` ~line 348) + wrap the polyfill/bridge-shim in try/catch, log the error;
   (c) figure out why the POST-RELOAD ws re-handshake is slow (orphaned old MMWSConn? the new
   page's `mmws://open` reuses sid=1 and overwrites `g_conns[1]`, leaking the old socket —
   clean up the old conn on a new open, and/or on didClearWindowObject free all g_conns).

### Verification method (use this, not mmws.log OPEN/CLOSE — those mislead)
- `python tools/_ws_continuity.py <client_id> --last-min N` — CLIENTLOG cadence continuity. A
  connected client emits clock-sync every ~30s; any gap > ~75s = it disconnected. This is the
  HONEST stability metric. (mmws.log OPEN=1/CLOSE=0 can mean "held" OR "opened once then died
  silently"; access-log silence can mean "held ws" OR "offline" — both ambiguous. Cadence is not.)
  Bar for "stable": 15+ min, zero gaps, under live playlist traffic.
- `python tools/_ws_timing_profile.py --last-min 12` — per-device clock-sync metrics
  (prec/phStd/sPrec) labeled by actual transport from the access log.

### Baseline timing already captured (for the eventual before/after)
- BEFORE (fleet on XHR, 24 devices, `_ws_before.txt`): prec≈22, phStd≈130, **sPrec≈50**.
- WS signal (screen15 when connected): **sPrec≈24-32** — ~½ the XHR sPrec (sPrec = server-time
  round-trip precision, the transport-bound metric). Promising but not a clean measurement until
  the bridge holds.

### State to resume from
- Current on-device build = MMWS_DEBUG=1 + breadcrumbs (both fixes in). screen15 has it; it
  plays but reloads/disconnects at each loop. XHR is the stable fleet default; NOTHING rolled out.
- DO NOT fleet-onboard the bridge until #3 is fixed and a 15-min continuity soak is clean.
  `tools/mmws.dylib` (onboarding artifact) is a WIP debug build — rebuild clean (MMWS_DEBUG=0,
  drop -DMMWSCONN_BC) before any rollout.

## §5 CORRECTED DIAGNOSIS OF #3 (2026-07-01) — it's the deadlock, not a leak/reload

Chased two wrong sub-hypotheses first (record so nobody re-chases them):
- NOT a JS-error self-reload: `jserror` CLIENTLOG count = 0 across the whole fleet. `window.onerror`
  logs but does not reload.
- NOT (primarily) a memory-leak/jetsam: the accelerating-reload pattern was the OLD buggy builds
  (crash #1 + deadlock #2). On the current build a clean-reboot soak did boot=1, OPEN=1, ERROR=0,
  process ALIVE the whole time — no reload, no jetsam. (RSS measurement dead-ended anyway: iOS-5
  `ps l` doesn't list Web.app, `ps ax` lists it but has no RSS column.)

WHAT IT ACTUALLY IS: **the same main↔web deadlock as #2, only DELAYED — it now hangs after minutes
on a message BURST (the demo PLAY / playlist loop), not after 20s.** Hard evidence from the soak:
breadcrumbs `evalPre=74 / evalPost=73` (one eval entered, never returned), breadcrumb file frozen
at 13:56, CLIENTLOG silent from 13:54 onward (~21 min), yet `Web pid` alive + `OPEN=1`. Classic
hang: process alive, run loop frozen inside `stringByEvaluatingJavaScriptFromString:`.

ROOT (fundamental to the channel design): `stringByEvaluatingJavaScriptFromString:` called from the
MAIN thread BLOCKS main waiting on the WEB thread; a send's iframe→`shouldStartLoad` runs on the
WEB thread and BLOCKS it waiting on MAIN. When a burst has both directions in flight they deadlock.
`setTimeout(0)` on the send (fix #2) only shrank the window; it can't close it.

FIX DIRECTION (next session — a redesign, not a patch): make the native↔JS channel non-blocking in
BOTH directions. Options to try:
  (a) DELIVER inbound msgs without blocking main: don't push via `stringByEvaluatingJavaScriptFromString`
      from a CFStream/main context — instead native buffers inbound frames and the JS polls them
      (a `setInterval` in mmws.js calling a cheap native read via the mmws:// scheme), so no main→web
      blocking call carries payloads.
  (b) or run the eval on the WEB thread (where JS already lives) instead of main.
  (c) or replace the iframe send channel with one that doesn't route through main-thread
      `shouldStartLoad`.
Reassess whether the URL-scheme bridge is even the right substrate — a WebCore SocketStreamHandle
backend transplant (DESIGN.md approach A) sidesteps the whole main↔web JS-bridge threading problem.

TOOL FIX (committed): `tools/_ws_continuity.py` now flags LEADING + TRAILING silence (last sample →
now). It previously only measured gaps BETWEEN samples, so a hung client that stopped emitting read
as "STABLE" — this produced two false "it's stable" calls this session. Always check "silent since
last sample" is < threshold.

## §6 APPROACH C — WebCore WebSocket-backend transplant (chosen 2026-07-01; RE started)

Decision: abandon the URL-scheme JS bridge (fundamentally deadlock-prone, §5). Instead transplant
WebCore's WebSocket network backend so the REAL `window.WebSocket` runs on our host-tested
`mmwsconn` RFC-6455 socket — no eval, no iframe, no main↔web crossing → the deadlock class cannot
exist. WebCore-level ⇒ identical in Safari + webclip. Test in Safari (readable /tmp logs). Symbol
discovery via `tweak/mmwscprobe/` (MSFindSymbol probe, mmvideo method).

### Symbol map (Safari, iOS-5.1 WebKit 534.46, 2026-07-01) — `mmwscprobe`
CLASSES PRESENT (vtable FOUND): `WebCore::WebSocket`, `WebCore::WebSocketChannel`,
`WebCore::SocketStreamHandle`. (ThreadableWebSocketChannel / WebSocketHandshake / WebSocketChannelClient
/ SocketStreamHandleCFNet vtables NOT found — non-polymorphic or differently named.)
HOOKABLE METHODS (FOUND):
- `WebSocket::connect(WTF::String const&, int&)` = `__ZN7WebCore9WebSocket7connectERKN3WTF6StringERi`
- `WebSocket::send(WTF::String const&, int&)`    = `__ZN7WebCore9WebSocket4sendERKN3WTF6StringERi`
- `WebSocketChannel::connect()`                  = `__ZN7WebCore16WebSocketChannel7connectEv`
- `WebSocketChannel::send(WTF::String const&)`   = `__ZN7WebCore16WebSocketChannel4sendERKN3WTF6StringE`
- `WebSocketChannel::close()`                    = `__ZN7WebCore16WebSocketChannel5closeEv`
GATE (expose window.WebSocket) — NOT found (inlined): `RuntimeEnabledFeatures::webSocketEnabled`,
`WebSocket::isAvailable`, `WebSocket::setIsAvailable`. So exposing the constructor is an OPEN
sub-problem (probe the static-bool data symbol next, or patch the DOM-binding feature check).

### Transplant design (hook at the DOM level — get the WebSocket* directly, no ivar RE)
- HOOK `WebSocket::connect(this, url, ec)` → capture (this=client, url), start `mmwsconn_open(url)`,
  skip the real channel connect. `WebSocket::send(this, msg, ec)` → `mmwsconn_send_text`.
  `WebSocket::close(this, …)` → `mmwsconn_close`.
- DELIVER events from mmwsconn callbacks by calling the WebSocket's own client callbacks (they fire
  the JS onopen/onmessage/onerror) — ALL FOUND (iter2):
  - `WebSocket::didConnect()`             = `__ZN7WebCore9WebSocket10didConnectEv`
  - `WebSocket::didReceiveMessage(String)`= `__ZN7WebCore9WebSocket17didReceiveMessageERKN3WTF6StringE`
  - `WebSocket::didReceiveMessageError()` = `__ZN7WebCore9WebSocket22didReceiveMessageErrorEv`
  - `WebSocket::close()`                  = `__ZN7WebCore9WebSocket5closeEv`  (no-arg, FOUND)
  - `didClose(...)` full sig NOT yet matched — low priority (use close() / retry sigs later).
- STRING bridge (iter2, ALL FOUND): `WTF::String::fromUTF8(const char*)`
  `__ZN3WTF6String8fromUTF8EPKc`; `WTF::String(const char*)` ctor `__ZN3WTF6StringC1EPKc`;
  `WTF::String::createCFString()` `__ZNK3WTF6String14createCFStringEv`. So C↔WebCore string passing is solved.
- PREREQUISITE STILL OPEN: expose `window.WebSocket` (the gate). All gate accessors inlined/not-found.
  iter3 re-probes the static-bool DATA with CORRECTED mangling length
  `__ZN7WebCore22RuntimeEnabledFeatures18isWebSocketEnabledE` (18 chars — I mis-counted 21 in iter1/2).
  If that data symbol resolves, MSFindSymbol it + write 1 at %ctor (early, before pages set up their
  window) to expose the constructor. **iter3 result pending — device slow to return from a reboot.**
  If the data symbol is ALSO absent, fallbacks: hook the JSDOMWindow WebSocket-constructor getter, or
  patch the inlined feature check at the binding site.

### Transplant surface = COMPLETE except the gate. Build plan once gate confirmed:
1. Solve the gate (flip isWebSocketEnabled) → verify window.WebSocket is defined in Safari.
2. Hook WebSocket::connect (capture this+url, start mmwsconn, skip real connect) + send + close.
3. Wire mmwsconn callbacks → WebSocket::didConnect / didReceiveMessage(fromUTF8(...)) on the captured
   this. Reuse the host-tested mmws.c/mmws_sm.c/mmwsconn.c (already proven).
4. Soak in Safari with tools/_ws_continuity.py (15+ min, zero gaps) before porting to the webclip.
