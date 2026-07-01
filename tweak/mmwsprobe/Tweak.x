/* mmwsprobe — observe-only Layer-3 bridge RE probe.
 * Confirms, on the LIVE webclip, which concrete classes implement the two bridge hook
 * selectors and that they actually fire (and when). No behavior change — logs + calls original.
 * Plain ObjC (.x), no ObjC classes, no float math -> no mmbuiltins; matches the mmvideo
 * load-gate model (REFINDINGS §8/§13). Discovered facts feed the real mmws bridge tweak. */
#import <substrate.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <stdio.h>
#import <stdarg.h>
#import <stdlib.h>
#import <unistd.h>

/* per-process tag (sanitized bundle id) so the webclip's log is separate from Safari's */
static char g_tag[64] = "unknown";

static void compute_tag(void) {
    Class nsb = objc_getClass("NSBundle");
    if (!nsb) return;
    id mb = ((id (*)(id, SEL))objc_msgSend)((id)nsb, sel_registerName("mainBundle"));
    if (!mb) return;
    id bid = ((id (*)(id, SEL))objc_msgSend)(mb, sel_registerName("bundleIdentifier"));
    if (!bid) return;
    const char *s = ((const char *(*)(id, SEL))objc_msgSend)(bid, sel_registerName("UTF8String"));
    if (!s) return;
    int j = 0;
    for (int i = 0; s[i] && j < 63; i++) {
        char c = s[i];
        int ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '.' || c == '-';
        g_tag[j++] = ok ? c : '_';
    }
    g_tag[j] = 0;
}

static void plog(const char *fmt, ...) {
    /* tag the filename by bundle id; write to both /var/mobile (webclip-writable) and /tmp. */
    char pa[128], pb[128];
    snprintf(pa, sizeof pa, "/var/mobile/mmwsprobe-%s.log", g_tag);
    snprintf(pb, sizeof pb, "/tmp/mmwsprobe-%s.log", g_tag);
    const char *paths[2] = { pa, pb };
    for (int i = 0; i < 2; i++) {
        FILE *f = fopen(paths[i], "a");
        if (f) { va_list ap; va_start(ap, fmt); vfprintf(f, fmt, ap); va_end(ap); fclose(f); }
    }
}

#define MAXH 16
static Class g_should_cls[MAXH]; static IMP g_should_orig[MAXH]; static int g_should_n = 0;
static Class g_clear_cls[MAXH];  static IMP g_clear_orig[MAXH];  static int g_clear_n = 0;

static const char *url_of(id request) {
    if (!request) return "(nil req)";
    id url = ((id (*)(id, SEL))objc_msgSend)(request, sel_registerName("URL"));
    if (!url) return "(nil url)";
    id abs = ((id (*)(id, SEL))objc_msgSend)(url, sel_registerName("absoluteString"));
    if (!abs) return "(nil abs)";
    const char *s = ((const char *(*)(id, SEL))objc_msgSend)(abs, sel_registerName("UTF8String"));
    return s ? s : "(nil utf8)";
}

static char h_should(id self, SEL _cmd, id webView, id request, int navType) {
    plog("[mmwsprobe] shouldStartLoad self=%s nav=%d url=%s\n",
         object_getClassName(self), navType, url_of(request));
    Class c = object_getClass(self);
    for (int i = 0; i < g_should_n; i++)
        if (g_should_cls[i] == c)
            return ((char (*)(id, SEL, id, id, int))g_should_orig[i])(self, _cmd, webView, request, navType);
    return 1; /* default allow — should never reach */
}

static void h_clear(id self, SEL _cmd, id webView, id windowObj, id frame) {
    plog("[mmwsprobe] didClearWindowObject self=%s\n", object_getClassName(self));
    Class c = object_getClass(self);
    for (int i = 0; i < g_clear_n; i++)
        if (g_clear_cls[i] == c) {
            ((void (*)(id, SEL, id, id, id))g_clear_orig[i])(self, _cmd, webView, windowObj, frame);
            return;
        }
}

static int class_declares(Class c, SEL sel) {
    unsigned int cnt = 0;
    Method *ml = class_copyMethodList(c, &cnt);
    int found = 0;
    if (ml) { for (unsigned i = 0; i < cnt; i++) if (method_getName(ml[i]) == sel) { found = 1; break; } free(ml); }
    return found;
}

%ctor {
    compute_tag();
    plog("\n[mmwsprobe] ==== ctor pid=%d bundle=%s prog=%s ====\n",
         getpid(), g_tag, getprogname() ? getprogname() : "?");
    SEL sShould = sel_registerName("webView:shouldStartLoadWithRequest:navigationType:");
    SEL sClear  = sel_registerName("webView:didClearWindowObject:forFrame:");
    int n = objc_getClassList(NULL, 0);
    Class *classes = (Class *)malloc(sizeof(Class) * n);
    if (!classes) { plog("[mmwsprobe] malloc failed\n"); return; }
    n = objc_getClassList(classes, n);
    for (int i = 0; i < n; i++) {
        Class c = classes[i];
        if (g_should_n < MAXH && class_declares(c, sShould)) {
            plog("[mmwsprobe] DECLARES shouldStartLoad: %s\n", class_getName(c));
            g_should_cls[g_should_n] = c;
            MSHookMessageEx(c, sShould, (IMP)h_should, (IMP *)&g_should_orig[g_should_n]);
            g_should_n++;
        }
        if (g_clear_n < MAXH && class_declares(c, sClear)) {
            plog("[mmwsprobe] DECLARES didClearWindowObject: %s\n", class_getName(c));
            g_clear_cls[g_clear_n] = c;
            MSHookMessageEx(c, sClear, (IMP)h_clear, (IMP *)&g_clear_orig[g_clear_n]);
            g_clear_n++;
        }
    }
    free(classes);
    plog("[mmwsprobe] scan done: hooked %d shouldStartLoad + %d didClearWindowObject class(es)\n",
         g_should_n, g_clear_n);
}
