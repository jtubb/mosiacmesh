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

## §2 OPEN ITEMS
- Confirm the exact class owning each selector (hook target): `WebAppController` for
  shouldStartLoad; the frameLoadDelegate class for didClearWindowObject. (class-dump the
  __objc sections next, or an on-device observe hook logging `object_getClassName(self)`.)
- Data-size: URL-scheme args cap URL length; large SockJS frames may need chunking or a
  different JS->native channel (e.g. XHR to a `mmws://` host the tweak answers). SockJS control
  msgs are small, so start simple.
- Verify end-to-end on-device: access log flips device IP from `xhr_send` to `.../websocket`.
