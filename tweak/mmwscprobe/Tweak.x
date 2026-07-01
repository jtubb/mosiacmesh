/* mmwscprobe — WebCore WebSocket-backend symbol discovery (approach C, step 1).
 * Observe-only: MSFindSymbol-probes candidate WebCore WebSocket/SocketStream symbols and logs
 * which resolve, mapping the transplant surface. Safari-first (com.apple.mobilesafari) so the
 * log at /tmp is readable (Web.app's /tmp is sandboxed). Plain ObjC, no classes — mmvideo model.
 *
 * Reading the map: vtables (__ZTV...) present => the class exists on-device. connect/send/close
 * methods present => hookable transplant points. The plan is to route WebCore::WebSocket (or its
 * channel) through our host-tested mmwsconn RFC-6455 socket, deleting the JS bridge + its deadlock. */
#import <substrate.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <stdio.h>
#import <stdarg.h>
#import <stdlib.h>
#import <unistd.h>

static char g_tag[64] = "unknown";
static void compute_tag(void) {
    Class nsb = objc_getClass("NSBundle"); if (!nsb) return;
    id mb = ((id (*)(id, SEL))objc_msgSend)((id)nsb, sel_registerName("mainBundle")); if (!mb) return;
    id bid = ((id (*)(id, SEL))objc_msgSend)(mb, sel_registerName("bundleIdentifier")); if (!bid) return;
    const char *s = ((const char *(*)(id, SEL))objc_msgSend)(bid, sel_registerName("UTF8String")); if (!s) return;
    int j = 0; for (int i = 0; s[i] && j < 63; i++) { char c = s[i];
        int ok = (c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='.'||c=='-'; g_tag[j++] = ok?c:'_'; }
    g_tag[j] = 0;
}
static void plog(const char *fmt, ...) {
    char pa[128], pb[128];
    snprintf(pa, sizeof pa, "/var/mobile/mmwsc-%s.log", g_tag);
    snprintf(pb, sizeof pb, "/tmp/mmwsc-%s.log", g_tag);
    const char *paths[2] = { pa, pb };
    for (int i = 0; i < 2; i++) { FILE *f = fopen(paths[i], "a");
        if (f) { va_list ap; va_start(ap, fmt); vfprintf(f, fmt, ap); va_end(ap); fclose(f); } }
}
static void probe(const char *sym) {
    void *p = MSFindSymbol(NULL, sym);
    plog("  %-74s %s\n", sym, p ? "FOUND" : "-");
}

%ctor {
    compute_tag();
    plog("\n[mmwscprobe] ==== WebCore WebSocket symbol discovery  pid=%d bundle=%s ====\n", getpid(), g_tag);

    plog("-- class vtables (present => class exists on-device) --\n");
    probe("__ZTVN7WebCore9WebSocketE");
    probe("__ZTVN7WebCore16WebSocketChannelE");
    probe("__ZTVN7WebCore26ThreadableWebSocketChannelE");
    probe("__ZTVN7WebCore18WebSocketHandshakeE");
    probe("__ZTVN7WebCore18SocketStreamHandleE");
    probe("__ZTVN7WebCore23WebSocketChannelClientE");

    plog("-- enable gates (already known: setIsAvailable inlined) --\n");
    probe("__ZN7WebCore22RuntimeEnabledFeatures16webSocketEnabledEv");
    probe("__ZN7WebCore9WebSocket11isAvailableEv");
    probe("__ZN7WebCore9WebSocket14setIsAvailableEb");

    plog("-- WebSocket (DOM) connect/send/close (guessed signatures) --\n");
    probe("__ZN7WebCore9WebSocket7connectERKN3WTF6StringERi");
    probe("__ZN7WebCore9WebSocket7connectERKN3WTF6StringERNS_13ExceptionCodeE");
    probe("__ZN7WebCore9WebSocket4sendERKN3WTF6StringERi");
    probe("__ZN7WebCore9WebSocket5closeE");
    probe("__ZN7WebCore9WebSocket5closeEiRKN3WTF6StringERi");

    plog("-- WebSocketChannel connect/send/close/fail --\n");
    probe("__ZN7WebCore16WebSocketChannel7connectEv");
    probe("__ZN7WebCore16WebSocketChannel4sendERKN3WTF6StringE");
    probe("__ZN7WebCore16WebSocketChannel4sendERKNS_4BlobE");
    probe("__ZN7WebCore16WebSocketChannel5closeEv");
    probe("__ZN7WebCore16WebSocketChannel4failERKN3WTF6StringE");
    probe("__ZN7WebCore16WebSocketChannel7didOpenEv");
    probe("__ZN7WebCore16WebSocketChannel19didReceiveSocketDataEPKci");

    plog("-- SocketStreamHandle (CFNet backend) --\n");
    probe("__ZN7WebCore18SocketStreamHandle5closeEv");
    probe("__ZN7WebCore18SocketStreamHandle8sendDataEPKci");
    probe("__ZN7WebCore25SocketStreamHandleCFNet5closeEv");
    probe("__ZTVN7WebCore25SocketStreamHandleCFNetE");

    plog("-- WebSocketHandshake --\n");
    probe("__ZN7WebCore18WebSocketHandshake21clientHandshakeMessageEv");
    probe("__ZNK7WebCore18WebSocketHandshake21clientHandshakeMessageEv");
    probe("__ZN7WebCore18WebSocketHandshake17readServerHandshakeEPKcj");

    plog("-- ITER2: WebSocket event callbacks (fire JS onopen/onmessage/onclose) --\n");
    probe("__ZN7WebCore9WebSocket10didConnectEv");
    probe("__ZN7WebCore9WebSocket17didReceiveMessageERKN3WTF6StringE");
    probe("__ZN7WebCore9WebSocket22didReceiveMessageErrorEv");
    probe("__ZN7WebCore9WebSocket20didReceiveBinaryDataEN3WTF6VectorIcLj0ENS1_15CrashOnOverflowEEE");
    probe("__ZN7WebCore9WebSocket8didCloseEji");
    probe("__ZN7WebCore9WebSocket8didCloseEjNS_9WebSocket31ClosingHandshakeCompletionStatusEt");
    probe("__ZN7WebCore9WebSocket8didCloseEi");
    probe("__ZN7WebCore9WebSocket24didStartClosingHandshakeEv");

    plog("-- ITER2: WebSocket::close variants --\n");
    probe("__ZN7WebCore9WebSocket5closeEi");
    probe("__ZN7WebCore9WebSocket5closeEv");
    probe("__ZN7WebCore9WebSocket5closeEiRKN3WTF6StringERi");

    plog("-- ITER2: enable-gate data symbol + setter --\n");
    probe("__ZN7WebCore22RuntimeEnabledFeatures21isWebSocketEnabledE");
    probe("__ZN7WebCore22RuntimeEnabledFeatures19setWebSocketEnabledEb");
    probe("__ZN7WebCore22RuntimeEnabledFeatures25s_isWebSocketEnabledE");

    plog("-- ITER2: WTF::String helpers (build a WTF::String from a C url) --\n");
    probe("__ZN3WTF6String8fromUTF8EPKc");
    probe("__ZN3WTF6String8fromUTF8EPKcm");
    probe("__ZNK3WTF6String14createCFStringEv");
    probe("__ZN3WTF6StringC1EPKc");
    probe("__ZN3WTF6StringC1EPKcj");

    plog("-- ITER3: gate data with CORRECTED mangling lengths --\n");
    /* isWebSocketEnabled = 18 chars (I mis-counted 21 before). The static bool to flip. */
    probe("__ZN7WebCore22RuntimeEnabledFeatures18isWebSocketEnabledE");
    probe("__ZN7WebCore22RuntimeEnabledFeatures20s_isWebSocketEnabledE");
    /* accessor with corrected form / const */
    probe("__ZN7WebCore22RuntimeEnabledFeatures16webSocketEnabledEv");
    /* some builds gate under a different flag name */
    probe("__ZN7WebCore22RuntimeEnabledFeatures22isWebSocketEnabledFlagE");
    /* WebSocket static availability bool (member) */
    probe("__ZN7WebCore9WebSocket13s_isAvailableE");
    probe("__ZN7WebCore9WebSocket11s_availableE");

    plog("-- ITER3: didClose (WebKit 534 full signature attempts) --\n");
    probe("__ZN7WebCore9WebSocket8didCloseEmNS_9WebSocket31ClosingHandshakeCompletionStatusEtRKN3WTF6StringE");
    probe("__ZN7WebCore9WebSocket8didCloseEjNS_9WebSocket31ClosingHandshakeCompletionStatusEtRKN3WTF6StringE");
    probe("__ZN7WebCore9WebSocket8didCloseEt");

    plog("[mmwscprobe] done.\n");
}
