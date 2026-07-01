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
