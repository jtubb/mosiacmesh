/* mmwsc — WebCore WebSocket transplant (approach C). STEP 1: expose window.WebSocket.
 *
 * iOS-5.1 hard-gates window.WebSocket off (inlined RuntimeEnabledFeatures flag, no symbol/pref).
 * Rather than flip the flag, we INSTALL the real constructor directly at window creation:
 * getDOMConstructor<JSWebSocketConstructor>(exec, global) -> the ctor object, then
 * JSObjectSetProperty(ctx, global, "WebSocket", ctor). All public JSC C API + one known WebCore
 * symbol (mmwscprobe iter5 confirmed all resolve). Deleting the JS bridge + its deadlock.
 *
 * Hook = -[TabDocument webView:didClearWindowObject:forFrame:] (Safari's frame-load delegate;
 * fires before page JS, gives the WebFrame -> globalContext). Webclip hook (UIWebViewWebViewDelegate)
 * added once verified in Safari. Plain ObjC, no classes; symbols via MSFindSymbol (no framework link
 * beyond Foundation) — matches the mmvideo load-gate model.
 *
 * STEP 2 (next): hook WebSocket::connect/send/close -> mmwsconn RFC-6455 (the exposed ctor still
 * speaks the old broken protocol, so the backend transplant is still required). */
#import <Foundation/Foundation.h>
#import <substrate.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <stdio.h>
#import <stdarg.h>

static void mmlog(const char *fmt, ...) {
    static const char *paths[2] = { "/var/mobile/mmwsc.log", "/tmp/mmwsc.log" };
    for (int i = 0; i < 2; i++) { FILE *f = fopen(paths[i], "a");
        if (f) { va_list ap; va_start(ap, fmt); vfprintf(f, fmt, ap); va_end(ap); fclose(f); } }
}

/* JSC C API + the one WebCore symbol, all resolved at %ctor via MSFindSymbol (opaque refs = void*) */
typedef void *(*getctor_t)(void *exec, const void *global);   /* getDOMConstructor<JSWebSocketConstructor> */
typedef void *(*ctxglobal_t)(void *ctx);                      /* JSContextGetGlobalObject -> JSDOMWindowShell* */
typedef void *(*unwrap_t)(void *shell);                       /* JSDOMWindowShell::unwrappedObject -> real JSDOMWindow* */
typedef void *(*strcreate_t)(const char *s);                  /* JSStringCreateWithUTF8CString */
typedef void  (*strrelease_t)(void *s);                       /* JSStringRelease */
typedef void  (*setprop_t)(void *ctx, void *obj, void *name, void *val, unsigned attr, void **exc);
typedef void *(*getprop_t)(void *ctx, void *obj, void *name, void **exc);  /* JSObjectGetProperty (safe read-back) */

static getctor_t   g_getWSCtor;
static ctxglobal_t g_ctxGlobal;
static unwrap_t    g_unwrap;
static strcreate_t g_strCreate;
static strrelease_t g_strRelease;
static setprop_t   g_setProp;
static getprop_t   g_getProp;
static int g_installs;

static void install_websocket(void *ctx) {
    mmlog("[mmwsc] bc: install ctx=%p\n", ctx);
    if (!ctx || !g_getWSCtor || !g_ctxGlobal || !g_setProp || !g_strCreate) { mmlog("[mmwsc] bc: early-out\n"); return; }
    void *shell = g_ctxGlobal(ctx);               /* JSContextGetGlobalObject -> JSDOMWindowShell* (proxy) */
    mmlog("[mmwsc] bc: shell=%p\n", shell);
    if (!shell || !g_unwrap) return;
    void *window = g_unwrap(shell);               /* unwrap the shell -> the REAL JSDOMWindow (a JSDOMGlobalObject) */
    mmlog("[mmwsc] bc: unwrapped window=%p\n", window);
    if (!window) return;
    void *ctor = g_getWSCtor(ctx, window);        /* getDOMConstructor(exec=ctx, real window); returns JSObject* */
    mmlog("[mmwsc] bc: getDOMConstructor -> %p\n", ctor);
    if (!ctor) { mmlog("[mmwsc] getDOMConstructor returned NULL\n"); return; }
    const char *names[2] = { "WebSocket", "MozWebSocket" };
    for (int i = 0; i < 2; i++) {
        void *nm = g_strCreate(names[i]);
        /* set on the SHELL (the JS-visible global) so window.WebSocket resolves */
        if (nm) { g_setProp(ctx, shell, nm, ctor, 0, NULL); g_strRelease(nm); }
        mmlog("[mmwsc] bc: setProp %s done\n", names[i]);
    }
    g_installs++;
    mmlog("[mmwsc] installed window.WebSocket (#%d) ctor=%p window=%p\n", g_installs, ctor, window);
    /* VERIFY JS-visibility SAFELY: read the property back via the same lookup JS uses.
     * (No JSEvaluateScript here — running script during didClearWindowObject re-enters the
     * interpreter mid-window-init and crashes. A property get/set is safe.) */
    if (g_getProp) {
        void *nm = g_strCreate("WebSocket");
        void *got = nm ? g_getProp(ctx, shell, nm, NULL) : NULL;
        if (nm) g_strRelease(nm);
        mmlog("[mmwsc] read-back window.WebSocket=%p (installed ctor=%p, match=%d)\n",
              got, ctor, got == ctor);
    }
}

%hook TabDocument
- (void)webView:(id)wv didClearWindowObject:(id)win forFrame:(id)frame {
    %orig;
    mmlog("[mmwsc] bc: hook fired frame=%p (%s)\n", (void *)frame, frame ? object_getClassName(frame) : "-");
    if (frame) {
        void *ctx = ((void *(*)(id, SEL))objc_msgSend)(frame, sel_registerName("globalContext"));
        mmlog("[mmwsc] bc: globalContext=%p\n", ctx);
        install_websocket(ctx);
    }
}
%end

%ctor {
    g_getWSCtor  = (getctor_t)MSFindSymbol(NULL, "__ZN7WebCore17getDOMConstructorINS_22JSWebSocketConstructorEEEPN3JSC8JSObjectEPNS2_9ExecStateEPKNS_17JSDOMGlobalObjectE");
    g_ctxGlobal  = (ctxglobal_t)MSFindSymbol(NULL, "_JSContextGetGlobalObject");
    g_unwrap     = (unwrap_t)MSFindSymbol(NULL, "__ZN7WebCore16JSDOMWindowShell15unwrappedObjectEv");
    g_strCreate  = (strcreate_t)MSFindSymbol(NULL, "_JSStringCreateWithUTF8CString");
    g_strRelease = (strrelease_t)MSFindSymbol(NULL, "_JSStringRelease");
    g_setProp    = (setprop_t)MSFindSymbol(NULL, "_JSObjectSetProperty");
    g_getProp    = (getprop_t)MSFindSymbol(NULL, "_JSObjectGetProperty");
    mmlog("\n[mmwsc] ctor: getWSCtor=%p ctxGlobal=%p unwrap=%p setProp=%p strCreate=%p\n",
          (void *)g_getWSCtor, (void *)g_ctxGlobal, (void *)g_unwrap, (void *)g_setProp, (void *)g_strCreate);
}
