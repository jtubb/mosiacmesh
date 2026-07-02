/* mmwsconn.c — CFStream TCP transport driving the mmws_sm state machine (device-only).
 * Compile-checked against the iPhoneOS SDK via compile_device.sh; runtime-verify on-device. */
#include "mmwsconn.h"
#include "mmws_sm.h"
#include <CoreFoundation/CoreFoundation.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#define MMWSCONN_TXCAP 32768

/* breadcrumb into the same file as Tweak.x's bc() — locate a stall in the CFStream callbacks */
#ifdef MMWSCONN_BC
static void cbc(const char *s, long n) {
    FILE *f = fopen("/var/mobile/mmws_bc.txt", "a");
    if (f) { fprintf(f, "%s %ld\n", s, n); fclose(f); }
}
#else
#define cbc(s, n) ((void)0)
#endif

struct MMWSConn {
    CFReadStreamRef  rs;
    CFWriteStreamRef ws;
    mmws_sm          sm;
    mmwsconn_cb      cb;
    uint8_t          tx[MMWSCONN_TXCAP];   /* outbound queue (compacted) */
    size_t           tx_off, tx_len;
    int              w_ready;              /* write stream can accept bytes */
    int              dead;
};

static void rand_bytes(uint8_t *p, size_t n) {
    for (size_t i = 0; i < n; i += 4) {
        uint32_t r = arc4random();
        for (int j = 0; j < 4 && i + j < n; j++) p[i + j] = (uint8_t)(r >> (8 * j));
    }
}

static void conn_teardown(MMWSConn *c) {
    if (!c || c->dead) return;
    c->dead = 1;
    CFRunLoopRef rl = CFRunLoopGetCurrent();
    if (c->rs) { CFReadStreamUnscheduleFromRunLoop(c->rs, rl, kCFRunLoopCommonModes); CFReadStreamClose(c->rs); }
    if (c->ws) { CFWriteStreamUnscheduleFromRunLoop(c->ws, rl, kCFRunLoopCommonModes); CFWriteStreamClose(c->ws); }
}

static void conn_flush(MMWSConn *c) {
    while (c->tx_off < c->tx_len && c->w_ready && !c->dead) {
        CFIndex w = CFWriteStreamWrite(c->ws, c->tx + c->tx_off, (CFIndex)(c->tx_len - c->tx_off));
        if (w > 0) c->tx_off += (size_t)w;
        else { c->w_ready = 0; cbc("Wblock", (long)(c->tx_len - c->tx_off)); break; }  /* would block */
    }
    if (c->tx_off >= c->tx_len) c->tx_off = c->tx_len = 0;
}

/* mmws_sm send callback: queue bytes (compacting) then try to flush */
static void conn_send(void *ud, const uint8_t *b, size_t n) {
    MMWSConn *c = (MMWSConn *)ud;
    if (c->dead) return;
    if (c->tx_off > 0) {                          /* compact */
        memmove(c->tx, c->tx + c->tx_off, c->tx_len - c->tx_off);
        c->tx_len -= c->tx_off; c->tx_off = 0;
    }
    if (c->tx_len + n > MMWSCONN_TXCAP) {
        if (c->cb.on_error) c->cb.on_error(c, "tx overflow", c->cb.ud);
        conn_teardown(c); return;
    }
    memcpy(c->tx + c->tx_len, b, n);
    c->tx_len += n;
    conn_flush(c);
}

/* mmws_sm event callback */
static void conn_event(void *ud, int ev, uint8_t op, const uint8_t *d, size_t n) {
    MMWSConn *c = (MMWSConn *)ud;
    if (ev == MMWS_EV_OPEN) {
        if (c->cb.on_open) c->cb.on_open(c, c->cb.ud);
    } else if (ev == MMWS_EV_MESSAGE) {
        if (op == MMWS_OP_PING) {                 /* auto-pong with the same payload */
            uint8_t m[4]; rand_bytes(m, 4);
            mmws_sm_send_msg(&c->sm, MMWS_OP_PONG, d, n, m);
        } else if (op == MMWS_OP_PONG) {
            /* keepalive ack — ignore */
        } else if (c->cb.on_message) {
            c->cb.on_message(c, op, d, n, c->cb.ud);
        }
    } else if (ev == MMWS_EV_CLOSE) {
        uint16_t code = (n >= 2 && d) ? (uint16_t)((d[0] << 8) | d[1]) : 1005;
        if (c->cb.on_close) c->cb.on_close(c, code, c->cb.ud);
        conn_teardown(c);
    } else if (ev == MMWS_EV_ERROR) {
        if (c->cb.on_error) c->cb.on_error(c, d ? (const char *)d : "error", c->cb.ud);
        conn_teardown(c);
    }
}

static void read_cb(CFReadStreamRef s, CFStreamEventType ev, void *info) {
    MMWSConn *c = (MMWSConn *)info;
    if (c->dead) return;
    if (ev == kCFStreamEventHasBytesAvailable) {
        uint8_t buf[4096];
        CFIndex n = CFReadStreamRead(s, buf, (CFIndex)sizeof buf);
        cbc("R", (long)n);
        if (n > 0) mmws_sm_on_recv(&c->sm, buf, (size_t)n);
        else if (n < 0) { if (c->cb.on_error) c->cb.on_error(c, "read error", c->cb.ud); conn_teardown(c); }
    } else if (ev == kCFStreamEventErrorOccurred) {
        cbc("Rerr", 0);
        if (c->cb.on_error) c->cb.on_error(c, "read stream error", c->cb.ud); conn_teardown(c);
    } else if (ev == kCFStreamEventEndEncountered) {
        cbc("Reof", 0);
        if (c->cb.on_close) c->cb.on_close(c, 1006, c->cb.ud); conn_teardown(c);
    } else { cbc("Rev", (long)ev); }
}

static void write_cb(CFWriteStreamRef s, CFStreamEventType ev, void *info) {
    (void)s;
    MMWSConn *c = (MMWSConn *)info;
    if (c->dead) return;
    if (ev == kCFStreamEventCanAcceptBytes) { cbc("Wok", (long)(c->tx_len - c->tx_off)); c->w_ready = 1; conn_flush(c); }
    else if (ev == kCFStreamEventOpenCompleted) { cbc("Wopen", 0); }
    else if (ev == kCFStreamEventErrorOccurred) {
        cbc("Werr", 0);
        if (c->cb.on_error) c->cb.on_error(c, "write stream error", c->cb.ud); conn_teardown(c);
    }
}

MMWSConn *mmwsconn_open(const char *host, int port, const char *path, const mmwsconn_cb *cb) {
    if (!host || !path || !cb) return NULL;
    MMWSConn *c = (MMWSConn *)calloc(1, sizeof *c);
    if (!c) return NULL;
    c->cb = *cb;

    CFStringRef h = CFStringCreateWithCString(NULL, host, kCFStringEncodingUTF8);
    if (!h) { free(c); return NULL; }
    CFStreamCreatePairWithSocketToHost(NULL, h, (UInt32)port, &c->rs, &c->ws);
    CFRelease(h);
    if (!c->rs || !c->ws) { if (c->rs) CFRelease(c->rs); if (c->ws) CFRelease(c->ws); free(c); return NULL; }

    CFStreamClientContext ctx = { 0, c, NULL, NULL, NULL };
    CFReadStreamSetClient(c->rs,
        kCFStreamEventHasBytesAvailable | kCFStreamEventErrorOccurred | kCFStreamEventEndEncountered,
        read_cb, &ctx);
    CFWriteStreamSetClient(c->ws,
        kCFStreamEventCanAcceptBytes | kCFStreamEventOpenCompleted | kCFStreamEventErrorOccurred,
        write_cb, &ctx);
    CFRunLoopRef rl = CFRunLoopGetCurrent();
    CFReadStreamScheduleWithRunLoop(c->rs, rl, kCFRunLoopCommonModes);
    CFWriteStreamScheduleWithRunLoop(c->ws, rl, kCFRunLoopCommonModes);
    CFReadStreamOpen(c->rs);
    CFWriteStreamOpen(c->ws);

    /* Host header value is host:port; queue the opening handshake (flushed once writable). */
    char hostport[160];
    snprintf(hostport, sizeof hostport, "%s:%d", host, port);
    uint8_t key16[16]; rand_bytes(key16, 16);
    if (mmws_sm_start(&c->sm, hostport, path, key16, conn_send, conn_event, c) != 0) {
        mmwsconn_free(c); return NULL;
    }
    return c;
}

int mmwsconn_send_text(MMWSConn *c, const uint8_t *data, size_t len) {
    if (!c || c->dead) return -1;
    uint8_t m[4]; rand_bytes(m, 4);
    return mmws_sm_send_msg(&c->sm, MMWS_OP_TEXT, data, len, m);
}

int mmwsconn_send_binary(MMWSConn *c, const uint8_t *data, size_t len) {
    if (!c || c->dead) return -1;
    uint8_t m[4]; rand_bytes(m, 4);
    return mmws_sm_send_msg(&c->sm, MMWS_OP_BINARY, data, len, m);
}

void mmwsconn_close(MMWSConn *c, uint16_t code) {
    if (!c || c->dead) return;
    uint8_t m[4]; rand_bytes(m, 4);
    mmws_sm_close(&c->sm, code, m);
}

void mmwsconn_free(MMWSConn *c) {
    if (!c) return;
    conn_teardown(c);
    if (c->rs) CFRelease(c->rs);
    if (c->ws) CFRelease(c->ws);
    free(c);
}
