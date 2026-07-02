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
#import <CoreFoundation/CoreFoundation.h>
#import <substrate.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <stdio.h>
#import <stdarg.h>
#import <string.h>
#include "mmwsconn.h"   /* host-tested RFC-6455 transport (mmws.c/mmws_sm.c/mmwsconn.c) */

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

/* ===================== STEP 2: backend transplant =====================
 * The exposed ctor still speaks WebCore's OLD ws protocol. Intercept WebSocket::connect/send/close
 * and drive our host-tested mmwsconn (RFC-6455) instead; no-op the real WebSocketChannel socket;
 * deliver events by calling the WebSocket's own didConnect/didReceiveMessage/didClose. All on the
 * WebKit main thread (mmwsconn schedules on the current run loop = main), same thread WebCore
 * expects channel callbacks on. */
typedef void (*ws_connect_t)(void *ws, const void *url, int *ec);
typedef bool (*ws_send_t)(void *ws, const void *msg, int *ec);
typedef void (*ws_close_t)(void *ws);
typedef void (*chan_connect_t)(void *chan);
typedef void (*ws_v_t)(void *ws);                       /* didConnect / didReceiveMessageError */
typedef void (*ws_msg_t)(void *ws, const void *str);    /* didReceiveMessage(const String&) */
typedef void (*ws_close_d_t)(void *ws, unsigned long unhandled);   /* didClose(unsigned long) */
typedef CFStringRef (*createcf_t)(const void *str);     /* WTF::String::createCFString() const */
typedef void (*strctor_t)(void *out, const char *cchars);          /* WTF::String::String(const char*) */

static ws_connect_t  o_connect;
static ws_send_t     o_send;
static ws_close_t    o_close;
static chan_connect_t o_chanConnect;
static ws_v_t        f_didConnect;
static ws_msg_t      f_didMsg;
static ws_v_t        f_didMsgErr;
static ws_close_d_t  f_didClose;
static createcf_t    f_createCF;
static strctor_t     f_strCtor;

#define MMWSC_MAXWS 8
static struct { void *ws; MMWSConn *conn; } g_wsmap[MMWSC_MAXWS];
static MMWSConn *wsmap_get(void *ws) { for (int i = 0; i < MMWSC_MAXWS; i++) if (g_wsmap[i].ws == ws) return g_wsmap[i].conn; return 0; }
static void wsmap_put(void *ws, MMWSConn *c) { for (int i = 0; i < MMWSC_MAXWS; i++) if (!g_wsmap[i].ws) { g_wsmap[i].ws = ws; g_wsmap[i].conn = c; return; } }
static void wsmap_del(void *ws) { for (int i = 0; i < MMWSC_MAXWS; i++) if (g_wsmap[i].ws == ws) { g_wsmap[i].ws = 0; g_wsmap[i].conn = 0; return; } }

/* WTF::String& -> C string via createCFString (const method: this=r0=the String*) */
static void str_to_c(const void *wtfstr, char *buf, int bufsz) {
    buf[0] = 0;
    if (!f_createCF || !wtfstr) return;
    CFStringRef cf = f_createCF(wtfstr);
    if (cf) { CFStringGetCString(cf, buf, bufsz, kCFStringEncodingUTF8); CFRelease(cf); }
}

typedef void (*ws_dtor_t)(void *ws);
static ws_dtor_t o_wsDtor;
/* SINGLE teardown owner: when the WebSocket is destroyed (close/stop/navigate/GC all funnel to
 * ~WebSocket), free its mmwsconn + unmap. Guarantees no mmwsconn callback ever fires on a freed
 * ws (dangling ud). Runs on the WebThread, same thread as the callbacks — no race. */
static void teardown_ws(void *ws) {
    MMWSConn *conn = wsmap_get(ws);
    if (conn) { mmlog("[mmwsc/tx] teardown ws=%p conn=%p\n", ws, conn); wsmap_del(ws); mmwsconn_free(conn); }
}
static void h_wsDtor(void *ws) { teardown_ws(ws); if (o_wsDtor) o_wsDtor(ws); }

/* --- delivery: mmwsconn callbacks (ud = the WebSocket*), all on the WebThread run loop.
 * Each guards on wsmap_get(ud): if the ws was already torn down, drop the event. Teardown/free is
 * owned solely by ~WebSocket (h_wsDtor) — the callbacks only DELIVER, never free. --- */
static void cb_open(MMWSConn *c, void *ud) {
    if (!wsmap_get(ud)) return;
    mmlog("[mmwsc/tx] on_open ws=%p -> didConnect\n", ud);
    if (f_didConnect) f_didConnect(ud);
}
static void cb_msg(MMWSConn *c, uint8_t op, const uint8_t *data, size_t len, void *ud) {
    if (!wsmap_get(ud)) return;
    char buf[4096]; size_t n = len < sizeof(buf) - 1 ? len : sizeof(buf) - 1;
    memcpy(buf, data, n); buf[n] = 0;
    mmlog("[mmwsc/tx] on_msg ws=%p op=%d len=%d -> didReceiveMessage\n", ud, op, (int)len);
    if (f_didMsg && f_strCtor) {
        void *s = 0;                       /* a WTF::String is one pointer (StringImpl*) */
        f_strCtor(&s, buf);                /* construct in-place; refs a new StringImpl */
        f_didMsg(ud, &s);                  /* pass const String& */
        /* NOTE: String dtor is inlined (no symbol) -> the StringImpl is intentionally leaked per
         * message for now. Low rate (register/heartbeats); fix by finding StringImpl::deref. */
    }
}
static void cb_close(MMWSConn *c, uint16_t code, void *ud) {
    if (!wsmap_get(ud)) return;
    mmlog("[mmwsc/tx] on_close ws=%p code=%d -> didClose\n", ud, code);
    if (f_didClose) f_didClose(ud, 0);     /* deliver only; ~WebSocket owns free+unmap */
}
static void cb_error(MMWSConn *c, const char *msg, void *ud) {
    if (!wsmap_get(ud)) return;
    mmlog("[mmwsc/tx] on_error ws=%p %s -> didReceiveMessageError\n", ud, msg ? msg : "");
    if (f_didMsgErr) f_didMsgErr(ud);      /* deliver only; ~WebSocket owns free+unmap */
}

/* parse "ws://host[:port][/path]" (wss -> port 443 default, but mmwsconn has no TLS) */
static int parse_ws(const char *url, char *host, int hostsz, int *port, char *path, int pathsz) {
    host[0] = 0; path[0] = '/'; path[1] = 0; *port = 80;
    const char *p = url;
    if (strncmp(p, "ws://", 5) == 0) p += 5;
    else if (strncmp(p, "wss://", 6) == 0) { p += 6; *port = 443; }
    else return -1;
    int i = 0; while (*p && *p != ':' && *p != '/' && i < hostsz - 1) host[i++] = *p++; host[i] = 0;
    if (*p == ':') { p++; *port = 0; while (*p >= '0' && *p <= '9') { *port = *port * 10 + (*p - '0'); p++; } }
    if (*p == '/') { int j = 0; while (*p && j < pathsz - 1) path[j++] = *p++; path[j] = 0; }
    return host[0] ? 0 : -1;
}

/* --- intercepts --- */
static void h_connect(void *ws, const void *url, int *ec) {
    char urlbuf[600]; str_to_c(url, urlbuf, sizeof urlbuf);
    char host[128], path[420]; int port = 80;
    mmlog("[mmwsc/tx] connect ws=%p url=%s\n", ws, urlbuf);
    if (parse_ws(urlbuf, host, sizeof host, &port, path, sizeof path) == 0) {
        mmwsconn_cb cb; cb.on_open = cb_open; cb.on_message = cb_msg; cb.on_close = cb_close; cb.on_error = cb_error; cb.ud = ws;
        MMWSConn *conn = mmwsconn_open(host, port, path, &cb);
        mmlog("[mmwsc/tx] mmwsconn_open(%s,%d,%s) = %p\n", host, port, path, conn);
        if (conn) wsmap_put(ws, conn);
    } else mmlog("[mmwsc/tx] parse_ws FAILED for %s\n", urlbuf);
    /* let WebCore set m_state=CONNECTING + m_url (its channel->connect() is our no-op) */
    if (o_connect) o_connect(ws, url, ec);
}
static bool h_send(void *ws, const void *msg, int *ec) {
    MMWSConn *conn = wsmap_get(ws);
    if (conn) { char buf[4096]; str_to_c(msg, buf, sizeof buf);
        mmwsconn_send_text(conn, (const uint8_t *)buf, strlen(buf)); }
    if (ec) *ec = 0;
    return true;   /* skip the dead channel; report queued */
}
static void h_close(void *ws) {
    mmlog("[mmwsc/tx] close ws=%p (teardown + didClose, NO o_close)\n", ws);
    /* Do NOT call o_close: WebCore's WebSocket::close() drives the half-baked channel beyond the
       methods we no-op'd (close timer / bufferedAmount / etc.) and takes Safari down. Instead tear
       down mmwsconn (closes the socket) and fire the JS 'close' event via didClose directly. */
    teardown_ws(ws);                     /* unmap + mmwsconn_free (closes the socket) */
    if (f_didClose) f_didClose(ws, 0);   /* fire the JS 'close' event */
}
static void h_chanConnect(void *chan) {
    /* NO-OP: never start the old-protocol socket; mmwsconn is the real transport. */
    mmlog("[mmwsc/tx] WebSocketChannel::connect no-op (chan=%p)\n", chan);
}
/* Since connect() is a no-op, the channel's SocketStreamHandle is never created (m_handle NULL).
 * WebCore's close/destroy path (WebSocket::close -> m_channel->close(); ~WebSocket ->
 * m_channel->disconnect()) would NULL-deref it -> SIGSEGV. No-op ALL channel methods that touch the
 * handle; mmwsconn is the real transport, so the channel is inert. */
static void *o_chanClose, *o_chanDisc, *o_chanSend;
static void h_chanNoop(void *chan) { }
static void h_chanSendNoop(void *chan, const void *str) { }

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

    /* --- STEP 2 backend transplant: resolve delivery/string symbols + hook connect/send/close --- */
    f_didConnect = (ws_v_t)MSFindSymbol(NULL, "__ZN7WebCore9WebSocket10didConnectEv");
    f_didMsg     = (ws_msg_t)MSFindSymbol(NULL, "__ZN7WebCore9WebSocket17didReceiveMessageERKN3WTF6StringE");
    f_didMsgErr  = (ws_v_t)MSFindSymbol(NULL, "__ZN7WebCore9WebSocket22didReceiveMessageErrorEv");
    f_didClose   = (ws_close_d_t)MSFindSymbol(NULL, "__ZN7WebCore9WebSocket8didCloseEm");
    f_createCF   = (createcf_t)MSFindSymbol(NULL, "__ZNK3WTF6String14createCFStringEv");
    f_strCtor    = (strctor_t)MSFindSymbol(NULL, "__ZN3WTF6StringC1EPKc");
    void *p;
    p = MSFindSymbol(NULL, "__ZN7WebCore9WebSocket7connectERKN3WTF6StringERi");
    if (p) MSHookFunction(p, (void *)h_connect, (void **)&o_connect);
    p = MSFindSymbol(NULL, "__ZN7WebCore9WebSocket4sendERKN3WTF6StringERi");
    if (p) MSHookFunction(p, (void *)h_send, (void **)&o_send);
    p = MSFindSymbol(NULL, "__ZN7WebCore9WebSocket5closeEv");
    if (p) MSHookFunction(p, (void *)h_close, (void **)&o_close);
    p = MSFindSymbol(NULL, "__ZN7WebCore16WebSocketChannel7connectEv");
    if (p) MSHookFunction(p, (void *)h_chanConnect, (void **)&o_chanConnect);
    p = MSFindSymbol(NULL, "__ZN7WebCore16WebSocketChannel5closeEv");
    if (p) MSHookFunction(p, (void *)h_chanNoop, (void **)&o_chanClose);
    p = MSFindSymbol(NULL, "__ZN7WebCore16WebSocketChannel10disconnectEv");
    if (p) MSHookFunction(p, (void *)h_chanNoop, (void **)&o_chanDisc);
    p = MSFindSymbol(NULL, "__ZN7WebCore16WebSocketChannel4sendERKN3WTF6StringE");
    if (p) MSHookFunction(p, (void *)h_chanSendNoop, (void **)&o_chanSend);
    p = MSFindSymbol(NULL, "__ZN7WebCore9WebSocketD0Ev");   /* ~WebSocket (deleting dtor) */
    if (p) MSHookFunction(p, (void *)h_wsDtor, (void **)&o_wsDtor);
    mmlog("[mmwsc] tx: didConnect=%p didMsg=%p didClose=%p createCF=%p strCtor=%p connectHooked=%d chanHooked=%d\n",
          (void *)f_didConnect, (void *)f_didMsg, (void *)f_didClose, (void *)f_createCF, (void *)f_strCtor,
          o_connect != 0, o_chanConnect != 0);
}
