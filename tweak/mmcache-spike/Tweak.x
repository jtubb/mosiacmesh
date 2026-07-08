/* mmcache-spike Tweak.x — SPIKE (throwaway) to de-risk Plan 2's iOS-5 cache bridge.
 * Proves: a JS mmcache:// iframe nav reaches a WebAppController hook (coexisting with mmws's
 * hook via %orig chaining), an NSData download writes /var/mobile/Media/mmcache/<token>.mp4,
 * and window.__mmCacheDone fires back into JS. file:// autoplay itself is already proven
 * (mmvideo). Mirrors tweak/mmws/Tweak.x load-safe model: plain ObjC (.x), NO ObjC classes,
 * runtime messaging only (objc_msgSend), NO Blocks (dispatch_async_f + C ctx), Foundation-only.
 * iOS-5 has NO NSURLSession -> NSData dataWithContentsOfURL: on a background queue. */
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

static id g_webview;   /* the UIWebView, retained, for native->JS dispatch */

static void spklog(const char *fmt, ...) {
    FILE *f = fopen("/var/mobile/mmcache_spike.log", "a");
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

/* ---- percent-decode (from mmws) ---------------------------------------------------------- */
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

/* ---- background download worker (dispatch_async_f target; NO Blocks) --------------------- */
typedef struct { char token[128]; char url[600]; } dlctx;

static void mm_download(void *p) {
    dlctx *c = (dlctx *)p;
    id url = ((id (*)(id, SEL, id))objc_msgSend)(
        (id)objc_getClass("NSURL"), sel_registerName("URLWithString:"), nsstr(c->url));
    id data = url ? ((id (*)(id, SEL, id))objc_msgSend)(
        (id)objc_getClass("NSData"), sel_registerName("dataWithContentsOfURL:"), url) : (id)0;
    if (!data) { spklog("[spk] download FAILED token=%s url=%s\n", c->token, c->url);
                 dispatch_fail(c->token, "net"); free(c); return; }
    /* ensure the cache dir exists */
    id fm = ((id (*)(id, SEL))objc_msgSend)(
        (id)objc_getClass("NSFileManager"), sel_registerName("defaultManager"));
    ((int (*)(id, SEL, id, int, id, id))objc_msgSend)(
        fm, sel_registerName("createDirectoryAtPath:withIntermediateDirectories:attributes:error:"),
        nsstr("/var/mobile/Media/mmcache"), 1, (id)0, (id)0);
    char pathc[320];
    snprintf(pathc, sizeof pathc, "/var/mobile/Media/mmcache/%s.mp4", c->token);
    int ok = ((int (*)(id, SEL, id, int))objc_msgSend)(
        data, sel_registerName("writeToFile:atomically:"), nsstr(pathc), 1);
    unsigned long bytes = ok ? ((unsigned long (*)(id, SEL))objc_msgSend)(
        data, sel_registerName("length")) : 0UL;
    spklog("[spk] download %s token=%s bytes=%lu -> %s\n", ok ? "OK" : "WRITEFAIL", c->token, bytes, pathc);
    if (ok) dispatch_done(c->token, (long)bytes); else dispatch_fail(c->token, "write");
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
    if (strncmp(op, "fetch", 5) == 0) {
        char urlbuf[600]; urlbuf[0] = 0;
        { const char *a = strstr(query, "url="); if (a) { a += 4; const char *e = strchr(a, '&');
            size_t l = e ? (size_t)(e - a) : strlen(a); pct_decode(a, l, urlbuf, sizeof urlbuf); } }
        dlctx *c = (dlctx *)malloc(sizeof(dlctx));
        if (!c) return;
        strncpy(c->token, token, sizeof c->token - 1); c->token[sizeof c->token - 1] = 0;
        strncpy(c->url, urlbuf, sizeof c->url - 1); c->url[sizeof c->url - 1] = 0;
        spklog("[spk] fetch token=%s url=%s\n", c->token, c->url);
        dispatch_async_f(dispatch_get_global_queue(0, 0), c, mm_download);
    } else if (strncmp(op, "evict", 5) == 0) {
        char pathc[320];
        snprintf(pathc, sizeof pathc, "/var/mobile/Media/mmcache/%s.mp4", token);
        unlink(pathc);
        spklog("[spk] evict token=%s\n", token);
    }
}

/* ---- hook (mirrors mmws; %orig chains so mmws:// + real navs still work) ----------------- */
%hook WebAppController
- (BOOL)webView:(id)wv shouldStartLoadWithRequest:(NSURLRequest *)req navigationType:(int)nt {
    NSURL *u = [req URL];
    NSString *scheme = [u scheme];
    if (scheme && [scheme caseInsensitiveCompare:@"mmcache"] == NSOrderedSame) {
        if (!g_webview) { g_webview = [wv retain]; spklog("[spk] webview captured\n"); }
        handle_mmcache([[u absoluteString] UTF8String]);
        return NO;
    }
    return %orig;
}
%end
