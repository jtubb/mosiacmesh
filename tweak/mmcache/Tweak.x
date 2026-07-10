/* mmcache Tweak.x — production iOS-5 client-pull cache bridge (Plan 2 Task 4). Promoted from
 * tweak/mmcache-spike after the on-device spike PASS (tweak/mmcache-spike/SPIKE-FINDINGS.md).
 * Standalone tweak: coexists with mmws (ws bridge) + mmvideo (video transplant) — all hook
 * different things via %orig chaining; proven to load clean together. Mirrors mmws's load-safe
 * model: plain ObjC (.x), NO ObjC classes, runtime messaging only (objc_msgSend), NO Blocks
 * (dispatch_async_f + C ctx), Foundation only. iOS-5 has NO NSURLSession -> NSData.
 *   - JS->native: mmcache://fetch?token=&url=  and  mmcache://evict?token=   (WebAppController hook)
 *   - native->JS: window.__mmCacheDone(token,bytes) / __mmCacheFail(token,reason)
 *   - readiness:  window.__mmCacheReady=true set at didClearWindowObject so the display client
 *                 registers the mmvideo backend (index.html gates on it).
 * Downloads to /var/mobile/Media/MosaicMeshCache/<token>.mp4 — the dir mmvideo's mm_url_to_path
 * maps http://127.0.0.1:8080/<name> to; the client plays via that 127.0.0.1:8080 src (a raw
 * file:// is cross-origin-blocked). See [[ios5-cached-video-url-convention]]. */
#import <Foundation/Foundation.h>
#import <substrate.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <dispatch/dispatch.h>
#import <string.h>
#import <stdlib.h>
#import <stdint.h>
#import <stdio.h>
#import <stdarg.h>
#import <unistd.h>

#define MM_CACHE_DIR "/var/mobile/Media/MosaicMeshCache"   /* no trailing slash; appended below */

static id g_webview;   /* the UIWebView, retained, for native->JS dispatch */

#define MMCACHE_DEBUG 1
static void mmclog(const char *fmt, ...) {
    if (!MMCACHE_DEBUG) return;
    FILE *f = fopen("/var/mobile/mmcache.log", "a");
    if (f) { va_list ap; va_start(ap, fmt); vfprintf(f, fmt, ap); va_end(ap); fclose(f); }
}

static id nsstr(const char *s) {
    return ((id (*)(id, SEL, const char *))objc_msgSend)(
        (id)objc_getClass("NSString"), sel_registerName("stringWithUTF8String:"), s);
}

/* native -> JS eval on the main queue (never from a background thread) */
static void eval_and_free(void *ctx) {
    char *js = (char *)ctx;
    if (js) {
        if (g_webview) {
            id s = nsstr(js);
            if (s) ((id (*)(id, SEL, id))objc_msgSend)(
                g_webview, sel_registerName("stringByEvaluatingJavaScriptFromString:"), s);
        }
        free(js);
    }
}
static void dispatch_done(const char *token, long bytes) {
    char *buf = (char *)malloc(320);
    if (!buf) return;
    snprintf(buf, 320, "if(window.__mmCacheDone)window.__mmCacheDone('%s',%ld)", token, bytes);
    dispatch_async_f(dispatch_get_main_queue(), buf, eval_and_free);
}
static void dispatch_fail(const char *token, const char *reason) {
    char *buf = (char *)malloc(320);
    if (!buf) return;
    snprintf(buf, 320, "if(window.__mmCacheFail)window.__mmCacheFail('%s','%s')", token, reason);
    dispatch_async_f(dispatch_get_main_queue(), buf, eval_and_free);
}

/* ---- percent-decode ---------------------------------------------------------------------- */
static int hexv(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
static size_t pct_decode(const char *s, size_t n, char *out, size_t cap) {
    size_t o = 0;
    for (size_t i = 0; i < n && o + 1 < cap; i++) {
        if (s[i] == '%' && i + 2 < n) {
            int h = hexv(s[i + 1]), l = hexv(s[i + 2]);
            if (h >= 0 && l >= 0) { out[o++] = (char)(h * 16 + l); i += 2; continue; }
        }
        out[o++] = (s[i] == '+') ? ' ' : s[i];
    }
    out[o] = 0;
    return o;
}
/* reject a token that would escape the cache dir (path-traversal / separators) */
static int token_is_safe(const char *t) {
    if (!*t) return 0;
    for (const char *p = t; *p; ++p) { if (*p == '/' || *p == '\\') return 0; }
    if (strstr(t, "..")) return 0;
    return 1;
}

/* ---- background download worker (dispatch_async_f target; NO Blocks) --------------------- */
typedef struct { char token[128]; char url[600]; } dlctx;

static void mm_download(void *p) {
    dlctx *c = (dlctx *)p;
    id url = ((id (*)(id, SEL, id))objc_msgSend)(
        (id)objc_getClass("NSURL"), sel_registerName("URLWithString:"), nsstr(c->url));
    if (!url) { mmclog("[mmcache] bad url token=%s\n", c->token);
                dispatch_fail(c->token, "url"); free(c); return; }
    /* iOS-5 has NO NSURLSession. dataWithContentsOfURL: hides the response, so a
       truncated transfer returns partial data (non-nil, no error) that we used to cache
       + ack CACHED -> verr=3 on playback. sendSynchronousRequest gives us the response
       (Content-Length) so we can verify completeness BEFORE writing. */
    id req = ((id (*)(id, SEL, id))objc_msgSend)(
        (id)objc_getClass("NSURLRequest"), sel_registerName("requestWithURL:"), url);
    id resp = (id)0, err = (id)0;
    id data = req ? ((id (*)(id, SEL, id, id *, id *))objc_msgSend)(
        (id)objc_getClass("NSURLConnection"),
        sel_registerName("sendSynchronousRequest:returningResponse:error:"),
        req, &resp, &err) : (id)0;
    if (!data) { mmclog("[mmcache] download FAILED token=%s\n", c->token);
                 dispatch_fail(c->token, "net"); free(c); return; }
    /* HTTP status must be 200 (reject 206 partial / 4xx / 5xx). */
    int status = 0;
    if (resp && ((int (*)(id, SEL, id))objc_msgSend)(
            resp, sel_registerName("isKindOfClass:"), (id)objc_getClass("NSHTTPURLResponse")))
        status = ((int (*)(id, SEL))objc_msgSend)(resp, sel_registerName("statusCode"));
    if (status != 200) { mmclog("[mmcache] bad status=%d token=%s\n", status, c->token);
                         dispatch_fail(c->token, "http"); free(c); return; }
    /* Completeness: downloaded length must equal the response's Content-Length. */
    long long expect = ((long long (*)(id, SEL))objc_msgSend)(
        resp, sel_registerName("expectedContentLength"));
    unsigned long got = ((unsigned long (*)(id, SEL))objc_msgSend)(
        data, sel_registerName("length"));
    if (expect <= 0 || (long long)got != expect) {
        mmclog("[mmcache] TRUNCATED token=%s got=%lu expect=%lld\n", c->token, got, expect);
        dispatch_fail(c->token, "len"); free(c); return;
    }
    /* Verified complete -> write + ack CACHED. */
    id fm = ((id (*)(id, SEL))objc_msgSend)(
        (id)objc_getClass("NSFileManager"), sel_registerName("defaultManager"));
    ((int (*)(id, SEL, id, int, id, id))objc_msgSend)(
        fm, sel_registerName("createDirectoryAtPath:withIntermediateDirectories:attributes:error:"),
        nsstr(MM_CACHE_DIR), 1, (id)0, (id)0);
    char pathc[320];
    snprintf(pathc, sizeof pathc, "%s/%s.mp4", MM_CACHE_DIR, c->token);
    int ok = ((int (*)(id, SEL, id, int))objc_msgSend)(
        data, sel_registerName("writeToFile:atomically:"), nsstr(pathc), 1);
    mmclog("[mmcache] %s token=%s bytes=%lu -> %s\n", ok ? "OK" : "WRITEFAIL", c->token, got, pathc);
    if (ok) dispatch_done(c->token, (long)got); else dispatch_fail(c->token, "write");
    free(c);
}

/* ---- mmcache:// scheme parsing (JS -> native) -------------------------------------------- */
static void handle_mmcache(const char *url) {
    if (!url || strncmp(url, "mmcache://", 10) != 0) return;
    const char *p = url + 10;
    char op[16]; int oi = 0;
    while (*p && *p != '?' && oi < 15) op[oi++] = *p++;
    op[oi] = 0;
    const char *query = (*p == '?') ? p + 1 : "";
    char token[128]; token[0] = 0;
    { const char *a = strstr(query, "token="); if (a) { a += 6; const char *e = strchr(a, '&');
        size_t l = e ? (size_t)(e - a) : strlen(a); pct_decode(a, l, token, sizeof token); } }
    if (!token_is_safe(token)) { mmclog("[mmcache] unsafe token rejected\n"); return; }
    if (strncmp(op, "fetch", 5) == 0) {
        char urlbuf[600]; urlbuf[0] = 0;
        { const char *a = strstr(query, "url="); if (a) { a += 4; const char *e = strchr(a, '&');
            size_t l = e ? (size_t)(e - a) : strlen(a); pct_decode(a, l, urlbuf, sizeof urlbuf); } }
        dlctx *c = (dlctx *)malloc(sizeof(dlctx));
        if (!c) return;
        strncpy(c->token, token, sizeof c->token - 1); c->token[sizeof c->token - 1] = 0;
        strncpy(c->url, urlbuf, sizeof c->url - 1); c->url[sizeof c->url - 1] = 0;
        mmclog("[mmcache] fetch token=%s url=%s\n", c->token, c->url);
        dispatch_async_f(dispatch_get_global_queue(0, 0), c, mm_download);
    } else if (strncmp(op, "evict", 5) == 0) {
        char pathc[320];
        snprintf(pathc, sizeof pathc, "%s/%s.mp4", MM_CACHE_DIR, token);
        unlink(pathc);
        mmclog("[mmcache] evict token=%s\n", token);
    }
}

/* ---- hooks (mirror mmws: same delegate classes, %orig chains) ---------------------------- */
%hook WebAppController
- (BOOL)webView:(id)wv shouldStartLoadWithRequest:(NSURLRequest *)req navigationType:(int)nt {
    NSURL *u = [req URL];
    NSString *scheme = [u scheme];
    if (scheme && [scheme caseInsensitiveCompare:@"mmcache"] == NSOrderedSame) {
        if (!g_webview) { g_webview = [wv retain]; }
        handle_mmcache([[u absoluteString] UTF8String]);
        return NO;
    }
    return %orig;
}
%end

%hook UIWebViewWebViewDelegate
- (void)webView:(id)wv didClearWindowObject:(id)win forFrame:(id)frame {
    if (!g_webview && wv) g_webview = [wv retain];
    if (win) {
        id s = nsstr("window.__mmCacheReady=true");
        if (s) ((id (*)(id, SEL, id))objc_msgSend)(win, sel_registerName("evaluateWebScript:"), s);
    }
    %orig;
}
%end
