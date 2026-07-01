# mmws — native RFC-6455 WebSocket for iOS-5.1 WebKit

## Why

iOS-5.1 WebKit (Safari + the `com.apple.webapp` webclip UIWebView) has **no usable
WebSocket**. The built-in one is the disabled/old-protocol (Hixie-76-era) implementation
that does **not** interoperate with MosaicMesh's RFC-6455 aiohttp/SockJS server — forcing it
on (`RuntimeEnabledFeatures::webSocketEnabled` / `WebSocket::isAvailable` → true) makes SockJS
commit to the ws transport and fail ("cannot connect"). So the whole fleet runs **SockJS over
XHR polling** (`xhr_streaming` + `xhr_send`). See memory `ios5-websocket-transport`.

XHR works today, so this is an **optimization** (lower latency/overhead for the sync wall),
not a blocker. The only way to real ws on this WebKit is to **supply our own RFC-6455 client**
in native code and expose it to JS as `window.WebSocket`.

## Architecture

Two layers, built bottom-up (same discipline as the video transplant `tweak/mmvideo/`: pure
host-testable functions first, then device wiring):

1. **Pure RFC-6455 protocol functions** (`mmws.h`, no I/O, no ObjC) — the "client functions":
   - `mmws_accept_key()` — compute `Sec-WebSocket-Accept` (SHA-1(key + GUID) → base64).
   - `mmws_build_open_request()` — the client HTTP Upgrade handshake bytes.
   - `mmws_check_open_response()` — validate the server's `101` + the Accept header.
   - `mmws_encode_frame()` — RFC-6455 frame; **client frames MUST be masked** (4-byte key).
   - `mmws_decode_frame()` — parse a frame (FIN/opcode/masked/len 7|16|64-bit) from a buffer.
   - `mmws_close_frame()` — build a close frame (opcode 0x8 + status code).
   These are pure C → **host-unit-tested** before any device work (like `mmurl.h`).

2. **Native transport + JS exposure** (device):
   - **Transport:** a non-blocking TCP socket via `CFStream`/`CFSocket` (present on iOS-5.1),
     driving the pure functions: open handshake → framed send/receive → ping/pong → close.
   - **JS exposure — pick one at impl time:**
     - **(A) DOM-backend transplant** (mirrors mmvideo): hook WebCore's `WebSocket` /
       `SocketStreamHandle` backend so the real `window.WebSocket` uses our socket. Keeps the
       page API native; needs RE of the WebSocket backend symbols (analogous to the
       `MediaPlayerPrivateiPhone` hunt). Also requires flipping `isAvailable()` true so the
       constructor is exposed — but ONLY if our backend replaces the broken one first.
     - **(B) JS polyfill + native bridge** (likely simpler): DON'T enable the built-in ws.
       Inject a JS shim defining `window.WebSocket` that calls a native bridge (e.g. a custom
       URL-scheme handler or a hooked JS-callable) which runs the native RFC-6455 client. The
       page/SockJS then sees a working `window.WebSocket`. No WebCore-backend RE; the bridge
       is the main unknown (how to surface a native callable to the UIWebView JS on iOS-5).

   Recommendation: prototype **(B)** first (less RE, contained), fall back to **(A)** if the
   JS↔native bridge on iOS-5 UIWebView proves too limited.

## Build / test plan (bottom-up, host first)

- [ ] `mmws.h` pure functions + a host test (`node`/C harness) with RFC-6455 vectors
      (the canonical `dGhlIHNhbXBsZSBub25jZQ==` → `s3pPLMBiTxaQ9kYGzzhZRbK+xOo=` accept vector;
      a masked "Hello" frame `0x81 0x85 …`; fragmented + 16/64-bit length cases).
- [ ] Native CFStream socket wrapper driving the pure functions against the real server
      (`ws://192.168.1.60:3000/sockjs/<srv>/<sess>/websocket`), validated with the live access
      log (`mm_live.err`) showing a real `…/websocket` 101 upgrade for the device IP.
- [x] JS polyfill written — `mmws.js` (ES5 `window.WebSocket` shim, routes through the native
      bridge contract `window.__mmwsNative` {open/send/close} + `window.__mmwsDispatch` for
      native->JS events; also sets `MozWebSocket` since SockJS probes it).
- [ ] **Bridge RE (device, the remaining unknown)** — in the `com.apple.webapp` process:
      (1) INJECT `mmws.js` into every page before SockJS runs (hook WebCore page-load / evaluate
          the script, or inject via the UIWebView).
      (2) SURFACE `__mmwsNative` to JS. iOS-5 UIWebView has no WKScriptMessageHandler and no
          JSContext access (iOS7+), so the classic path is a **custom URL scheme**: open/send/close
          navigate a hidden iframe to `mmws://…`; hook the webclip's resource-load / UIWebView
          delegate to intercept `mmws://` and call `mmwsconn_*`. (Alternative: RE a WebCore JS
          binding to add a native global — more work.)
      (3) DELIVER native->JS events: call `stringByEvaluatingJavaScriptFromString:@"__mmwsDispatch(…)"`
          on the main thread from the `mmwsconn_cb` callbacks — needs a ref to the page's UIWebView
          (find it in Web.app).
- [x] **Bridge tweak built + VERIFIED WORKING on the live webclip (2026-07-01).** `Tweak.x` hooks
      WebAppController.shouldStartLoad (mmws:// intercept) + UIWebViewWebViewDelegate.didClearWindowObject
      (inject bridge-shim.js+mmws.js via evaluateWebScript). Access log `mm_live.err` showed
      `GET /sockjs/250/kex4am4z/websocket HTTP/1.1" 101` from the device — a real RFC-6455 upgrade,
      NO xhr_send fallback, app sending REGISTER/CLIENTLOG over the socket. mmwsbuiltins.c provides
      __udivsi3/__umodsi3 (base64 /3; no libclang_rt). Logos gotcha: '\\'/'\'' char literals crash
      its tokenizer — use numeric 0x5C/0x27.
- [ ] REMAINING: clean redeploy (diagnostics gated behind MMWS_DEBUG=0) + fleet rollout via
      tools/onboard_devices.ps1 (install mmws.dylib+plist alongside mmvideo).

## Constraints / gotchas (carried from this session)

- **Filter must be `com.apple.webapp`** (lowercase — the webclip host bundle id; `com.apple.WebApp`
  never matched). If mmws ships as its own tweak, its filter needs the same two bundles.
- **Toolchain:** WSL Ubuntu + Theos, target `iphone:clang:9.3:5.1`, ARCHS=armv7; build via
  PowerShell `wsl` (NOT the Bash tool — mangles `/mnt/c`). Plain ObjC (`.x`/`.m`), NO ObjC
  classes / C++ unwind (load-crashes iOS-5, see mmvideo REFINDINGS §8/§11); provide any
  compiler-rt builtins the toolchain omits (`mmbuiltins.c` pattern).
- **SHA-1 + base64:** implement in pure C (don't rely on CommonCrypto symbol availability under
  the tweak's link model until verified) — small, and keeps `mmws.h` host-testable.
- **Measurement:** transport truth = server access-log SockJS URL path, NOT the CLIENTLOG `'m'`
  field (which lies). Read `mm_live.err`.
- **Don't force the built-in ws on** — it breaks SockJS. mmws must REPLACE the transport, then
  (approach A only) enable the constructor.
