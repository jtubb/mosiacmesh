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
#import "mmcachefile.h"    /* mm_cache_path / mm_cache_usable_size — fetch fast path (audit 1.15) */
#import "mmrange.h"        /* mm_next_range / mm_parse_content_range — chunked download (audit 1.13) */

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

/* CHUNKED, CONSTANT-MEMORY DOWNLOAD (audit 1.13).
   The previous implementation used one sendSynchronousRequest: for the whole body,
   which materializes the entire segment as an in-memory NSData. On a 247 MB iPad-1
   with Web.app idling ~22 MB that capped a segment at ~15-20 MB, and therefore
   capped CLIP LENGTH at roughly 2 minutes: a 10-minute render produced 150 MB per
   screen, which would jetsam the webclip -- and nothing relaunches a dead webclip.

   The textbook fix (async NSURLConnection delegate appending didReceiveData:) is
   NOT available here: a delegate is an object, and this tweak must define NO ObjC
   class (a static class SIGKILLs the dylib load on iOS 5.1; objc_allocateClassPair
   does not flat-bind -- REFINDINGS 11/12). So we keep the synchronous call and ask
   for one MM_RANGE_CHUNK window at a time, appending each to a temp file. Peak RAM
   is one chunk whatever the file size, with no delegate and no new linker symbols.
   The server already serves 206 range replies (media_handler does this for iPad-1
   video seeking), so the transport is production-exercised.

   COMPLETENESS is preserved, and the fast path in handle_mmcache depends on it:
   bytes accumulate in a TEMP file and are renamed into the cache dir only once the
   total matches what the server advertised, so a partial file can never appear
   there. Everything here goes through objc_msgSend on already-bound classes, so no
   libSystem symbol is added -- an earlier cut that called stat() SIGKILLed the load
   (see mmcachefile.h). */
static void mm_download(void *p) {
    dlctx *c = (dlctx *)p;
    id url = ((id (*)(id, SEL, id))objc_msgSend)(
        (id)objc_getClass("NSURL"), sel_registerName("URLWithString:"), nsstr(c->url));
    if (!url) { mmclog("[mmcache] bad url token=%s\n", c->token);
                dispatch_fail(c->token, "url"); free(c); return; }

    id fm = ((id (*)(id, SEL))objc_msgSend)(
        (id)objc_getClass("NSFileManager"), sel_registerName("defaultManager"));
    ((int (*)(id, SEL, id, int, id, id))objc_msgSend)(
        fm, sel_registerName("createDirectoryAtPath:withIntermediateDirectories:attributes:error:"),
        nsstr(MM_CACHE_DIR), 1, (id)0, (id)0);

    char tmpc[320], pathc[320];
    snprintf(tmpc,  sizeof tmpc,  "%s/.%s.part", MM_CACHE_DIR, c->token);
    snprintf(pathc, sizeof pathc, "%s/%s.mp4",   MM_CACHE_DIR, c->token);
    /* Start clean: a leftover .part from an interrupted pull must never be mistaken
       for progress. */
    ((int (*)(id, SEL, id, id))objc_msgSend)(
        fm, sel_registerName("removeItemAtPath:error:"), nsstr(tmpc), (id)0);
    ((int (*)(id, SEL, id, id, id))objc_msgSend)(
        fm, sel_registerName("createFileAtPath:contents:attributes:"), nsstr(tmpc), (id)0, (id)0);
    id fh = ((id (*)(id, SEL, id))objc_msgSend)(
        (id)objc_getClass("NSFileHandle"),
        sel_registerName("fileHandleForWritingAtPath:"), nsstr(tmpc));
    if (!fh) { mmclog("[mmcache] cannot open %s\n", tmpc);
               dispatch_fail(c->token, "open"); free(c); return; }

    long long total = -1, got = 0, rs = 0, re = 0;
    int guard = 0, ok = 1, failed = 0;
    while (guard++ < 4096) {
        char hdr[64];
        /* First pass the total is unknown, so ask for one chunk from the current
           offset and learn the real total from the reply's Content-Range. */
        long long cap = (total > 0) ? total : (got + MM_RANGE_CHUNK);
        if (!mm_next_range(cap, got, MM_RANGE_CHUNK, &rs, &re)) { break; }
        if (!mm_range_header(hdr, sizeof hdr, rs, re)) { ok = 0; break; }

        id req = ((id (*)(id, SEL, id))objc_msgSend)(
            (id)objc_getClass("NSMutableURLRequest"), sel_registerName("requestWithURL:"), url);
        if (!req) { ok = 0; break; }
        ((void (*)(id, SEL, id, id))objc_msgSend)(
            req, sel_registerName("setValue:forHTTPHeaderField:"), nsstr(hdr), nsstr("Range"));

        id resp = (id)0, err = (id)0;
        id data = ((id (*)(id, SEL, id, id *, id *))objc_msgSend)(
            (id)objc_getClass("NSURLConnection"),
            sel_registerName("sendSynchronousRequest:returningResponse:error:"), req, &resp, &err);
        if (!data || !resp) {
            mmclog("[mmcache] chunk net fail token=%s at %lld\n", c->token, got);
            ok = 0; failed = 1; dispatch_fail(c->token, "net"); break;
        }

        int status = 0;
        if (((int (*)(id, SEL, id))objc_msgSend)(
                resp, sel_registerName("isKindOfClass:"), (id)objc_getClass("NSHTTPURLResponse")))
            status = ((int (*)(id, SEL))objc_msgSend)(resp, sel_registerName("statusCode"));
        /* 206 only. A 200 means the server ignored Range and is handing us the whole
           body in RAM -- precisely what this rewrite exists to avoid. */
        if (status != 206) {
            mmclog("[mmcache] no range support (status=%d) token=%s\n", status, c->token);
            ok = 0; failed = 1; dispatch_fail(c->token, "norange"); break;
        }

        if (total < 0) {
            id hdrs = ((id (*)(id, SEL))objc_msgSend)(resp, sel_registerName("allHeaderFields"));
            id cr = hdrs ? ((id (*)(id, SEL, id))objc_msgSend)(
                        hdrs, sel_registerName("objectForKey:"), @"Content-Range") : (id)0;
            if (!cr && hdrs) cr = ((id (*)(id, SEL, id))objc_msgSend)(
                        hdrs, sel_registerName("objectForKey:"), @"content-range");
            const char *crs = cr ? ((const char *(*)(id, SEL))objc_msgSend)(
                        cr, sel_registerName("UTF8String")) : (const char *)0;
            long long a = 0, b = 0;
            if (!crs || !mm_parse_content_range(crs, &a, &b, &total)) {
                mmclog("[mmcache] unusable Content-Range token=%s\n", c->token);
                ok = 0; failed = 1; dispatch_fail(c->token, "range"); break;
            }
        }

        unsigned long n = ((unsigned long (*)(id, SEL))objc_msgSend)(
            data, sel_registerName("length"));
        if (!n) {
            mmclog("[mmcache] empty chunk token=%s at %lld\n", c->token, got);
            ok = 0; failed = 1; dispatch_fail(c->token, "short"); break;
        }
        ((void (*)(id, SEL, id))objc_msgSend)(fh, sel_registerName("writeData:"), data);
        got += (long long)n;
        if (total > 0 && got >= total) { break; }
    }
    ((void (*)(id, SEL))objc_msgSend)(fh, sel_registerName("closeFile"));

    if (ok && total > 0 && got == total) {
        ((int (*)(id, SEL, id, id))objc_msgSend)(
            fm, sel_registerName("removeItemAtPath:error:"), nsstr(pathc), (id)0);
        int moved = ((int (*)(id, SEL, id, id, id))objc_msgSend)(
            fm, sel_registerName("moveItemAtPath:toPath:error:"),
            nsstr(tmpc), nsstr(pathc), (id)0);
        mmclog("[mmcache] %s token=%s bytes=%lld chunks=%d -> %s\n",
               moved ? "OK" : "MOVEFAIL", c->token, got, guard, pathc);
        if (moved) dispatch_done(c->token, (long)got);
        else       dispatch_fail(c->token, "write");
    } else {
        if (!failed) {
            mmclog("[mmcache] TRUNCATED token=%s got=%lld expect=%lld\n", c->token, got, total);
            dispatch_fail(c->token, "len");
        }
        ((int (*)(id, SEL, id, id))objc_msgSend)(
            fm, sel_registerName("removeItemAtPath:error:"), nsstr(tmpc), (id)0);
    }
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
    if (strncmp(op, "clearall", 8) == 0) {
        /* Wipe the whole cache dir (no token). Remove the dir, then recreate it empty so
           lighttpd on :8080 still has a docroot. Foundation-only, runtime messaging. */
        id fm = ((id (*)(id, SEL))objc_msgSend)(
            (id)objc_getClass("NSFileManager"), sel_registerName("defaultManager"));
        ((int (*)(id, SEL, id, id *))objc_msgSend)(
            fm, sel_registerName("removeItemAtPath:error:"), nsstr(MM_CACHE_DIR), (id *)0);
        ((int (*)(id, SEL, id, int, id, id *))objc_msgSend)(
            fm, sel_registerName("createDirectoryAtPath:withIntermediateDirectories:attributes:error:"),
            nsstr(MM_CACHE_DIR), 1, (id)0, (id *)0);
        mmclog("[mmcache] clearall -> wiped %s\n", MM_CACHE_DIR);
        return;
    }
    char token[128]; token[0] = 0;
    { const char *a = strstr(query, "token="); if (a) { a += 6; const char *e = strchr(a, '&');
        size_t l = e ? (size_t)(e - a) : strlen(a); pct_decode(a, l, token, sizeof token); } }
    if (!token_is_safe(token)) { mmclog("[mmcache] unsafe token rejected\n"); return; }
    if (strncmp(op, "fetch", 5) == 0) {
        /* FAST PATH (audit 1.15): the segment may already be on disk from a previous
           page session. The JS _present map is per-page-load and nothing enumerates
           the cache dir at startup, so WITHOUT this every reload re-downloaded every
           segment -- ~440 MB across the fleet at 19 MB segments, throttled 3-at-a-time,
           12+ minutes of screens streaming centrally or hanging into wd-dead.
           Presence is sufficient evidence of validity: mm_download below rejects any
           transfer whose length != expectedContentLength BEFORE writing and writes
           atomically, so a partial file cannot exist here. mm_cached_size additionally
           treats a zero-byte file as a miss -- the one shape that could exist without
           having gone through that verified write. */
        {
            char have[320];
            long sz = 0;
            if (mm_cache_path(MM_CACHE_DIR, token, have, sizeof have)) {
                /* Facts via NSFileManager, NOT stat(): `_stat` is unbindable on the
                   5.1-era libSystem and SIGKILLed the load (see mmcachefile.h). Every
                   symbol used here — objc_getClass/sel_registerName/objc_msgSend and
                   the CFConstantString class behind @"..." — is already bound by this
                   dylib, so this adds nothing new for dyld to resolve.
                   The attribute KEYS are spelled as literals on purpose: referencing
                   NSFileSize/NSFileType/NSFileTypeRegular would pull in new undefined
                   symbols. Their documented values are exactly these strings. */
                id fm = ((id (*)(id, SEL))objc_msgSend)(
                    (id)objc_getClass("NSFileManager"), sel_registerName("defaultManager"));
                id attrs = fm ? ((id (*)(id, SEL, id, id *))objc_msgSend)(
                    fm, sel_registerName("attributesOfItemAtPath:error:"), nsstr(have), (id *)0)
                    : (id)0;
                if (attrs) {
                    id type = ((id (*)(id, SEL, id))objc_msgSend)(
                        attrs, sel_registerName("objectForKey:"), @"NSFileType");
                    int is_regular = type && ((int (*)(id, SEL, id))objc_msgSend)(
                        type, sel_registerName("isEqualToString:"), @"NSFileTypeRegular");
                    id num = ((id (*)(id, SEL, id))objc_msgSend)(
                        attrs, sel_registerName("objectForKey:"), @"NSFileSize");
                    long long bytes = num ? ((long long (*)(id, SEL))objc_msgSend)(
                        num, sel_registerName("longLongValue")) : 0LL;
                    sz = mm_cache_usable_size(1, is_regular, bytes);
                }
            }
            if (sz > 0) {
                mmclog("[mmcache] HIT token=%s bytes=%ld (already on disk, no download)\n",
                       token, sz);
                dispatch_done(token, sz);
                return;
            }
        }
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
