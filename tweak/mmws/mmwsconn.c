/* mmwsconn.c — CFStream TCP transport driving the mmws_sm state machine (device-only).
 * Compile-checked against the iPhoneOS SDK via compile_device.sh; runtime-verify on-device.
 *
 * ACTOR MODEL (2026-08-01): the socket is serviced on a DEDICATED I/O thread that owns its own
 * CFRunLoop. ALL socket I/O — CFStream create/SetClient/schedule/open, read_cb/write_cb, the
 * mmws_sm callbacks (conn_send/conn_event), writes (mmws_sm_send_msg), unschedule/close/CFRelease,
 * and CFRunLoopStop — happens ON that thread. The main thread only reaches the connection by
 * dispatching work onto the I/O runloop via CFRunLoopPerformBlock + CFRunLoopWakeUp. No CFStream
 * or MMWSConn socket state is touched from two threads at once. This keeps read_cb firing at true
 * frame arrival (off the render-starved main runloop) so receipt can later be stamped jitter-free. */
#include "mmwsconn.h"
#include "mmws_sm.h"
#include <CoreFoundation/CoreFoundation.h>
#include <pthread.h>
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

    /* dedicated I/O thread (the actor). ALL socket ops run on it; main only dispatches. */
    pthread_t          io_thr;
    CFRunLoopRef       io_rl;        /* the io thread's runloop; NULL until published (setup ok) */
    CFRunLoopSourceRef keepalive;    /* no-op source: keeps the runloop alive after unschedule */
    pthread_mutex_t    lk;           /* guards io_rl + setup flags + the dead publish */
    pthread_cond_t     ready;        /* signalled once setup finishes (success OR failure) */
    int                rl_ready;     /* setup finished — mmwsconn_open may return */
    int                setup_ok;     /* streams created + scheduled + mmws_sm_start succeeded */
    int                thr_started;  /* pthread_create succeeded */
    int                term_fired;   /* a terminal (close/error) cb already delivered — fire once */
};

/* open params handed to the io thread; valid only until 'ready' is signalled (open() blocks). */
typedef struct {
    MMWSConn   *c;
    const char *host;
    int         port;
    const char *path;
    const char *ua;
    const char *origin;
} io_args;

static void rand_bytes(uint8_t *p, size_t n) {
    for (size_t i = 0; i < n; i += 4) {
        uint32_t r = arc4random();
        for (int j = 0; j < 4 && i + j < n; j++) p[i + j] = (uint8_t)(r >> (8 * j));
    }
}

/* Unschedule + close the streams (idempotent via `dead`). Runs on the I/O thread only, so
 * CFRunLoopGetCurrent() is the io runloop the streams were scheduled on. */
/* Terminal (close/error) delivery must fire AT MOST ONCE per conn: the state machine can emit a
 * second EV_CLOSE/EV_ERROR in the same on_recv loop (e.g. a tx-overflow on_error during a PING
 * auto-pong, then a buffered CLOSE frame), and Tweak.x turns each terminal cb into a defer_close
 * -> mmwsconn_free, so a double delivery = double free + double join = UAF. term_claim() is the
 * check-and-set gate wrapping EVERY on_close/on_error site. Returns 1 for the first terminal only.
 * Check+set under lk, release, THEN call the cb (never hold lk across the callback). */
static int term_claim(MMWSConn *c) {
    pthread_mutex_lock(&c->lk);
    int first = !c->term_fired;
    c->term_fired = 1;
    pthread_mutex_unlock(&c->lk);
    return first;
}

static void conn_teardown(MMWSConn *c) {
    if (!c) return;
    pthread_mutex_lock(&c->lk);
    if (c->dead) { pthread_mutex_unlock(&c->lk); return; }
    c->dead = 1;
    pthread_mutex_unlock(&c->lk);
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
        if (term_claim(c) && c->cb.on_error) c->cb.on_error(c, "tx overflow", c->cb.ud);
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
        if (term_claim(c) && c->cb.on_close) c->cb.on_close(c, code, c->cb.ud);
        conn_teardown(c);
    } else if (ev == MMWS_EV_ERROR) {
        if (term_claim(c) && c->cb.on_error) c->cb.on_error(c, d ? (const char *)d : "error", c->cb.ud);
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
        else if (n < 0) { if (term_claim(c) && c->cb.on_error) c->cb.on_error(c, "read error", c->cb.ud); conn_teardown(c); }
    } else if (ev == kCFStreamEventErrorOccurred) {
        cbc("Rerr", 0);
        if (term_claim(c) && c->cb.on_error) c->cb.on_error(c, "read stream error", c->cb.ud); conn_teardown(c);
    } else if (ev == kCFStreamEventEndEncountered) {
        cbc("Reof", 0);
        if (term_claim(c) && c->cb.on_close) c->cb.on_close(c, 1006, c->cb.ud); conn_teardown(c);
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
        if (term_claim(c) && c->cb.on_error) c->cb.on_error(c, "write stream error", c->cb.ud); conn_teardown(c);
    }
}

/* no-op runloop source: its mere presence keeps CFRunLoopRun() blocked after the streams are
 * unscheduled, so the io thread stays alive until io_shutdown() calls CFRunLoopStop(). */
static void keepalive_perform(void *info) { (void)info; }

/* Release the streams + keepalive source. Runs on the I/O thread (where they were created). */
static void io_cleanup_streams(MMWSConn *c) {
    conn_teardown(c);                             /* unschedule + close (idempotent via dead) */
    if (c->rs) { CFRelease(c->rs); c->rs = NULL; }
    if (c->ws) { CFRelease(c->ws); c->ws = NULL; }
    if (c->keepalive) {
        CFRunLoopRemoveSource(CFRunLoopGetCurrent(), c->keepalive, kCFRunLoopCommonModes);
        CFRunLoopSourceInvalidate(c->keepalive);
        CFRelease(c->keepalive);
        c->keepalive = NULL;
    }
}

/* Full teardown, performed on the I/O thread from mmwsconn_free(): release everything then stop
 * the runloop so CFRunLoopRun() returns and the thread exits (joined by the caller on main). */
static void io_shutdown(MMWSConn *c) {
    io_cleanup_streams(c);
    CFRunLoopStop(CFRunLoopGetCurrent());
}

/* The I/O thread body: build + schedule the streams on THIS thread's runloop, start the state
 * machine, publish readiness, then service the runloop until io_shutdown() stops it. */
static void *io_thread_main(void *arg) {
    io_args *a = (io_args *)arg;
    MMWSConn *c = a->c;
    int ok = 0;

    CFStringRef h = CFStringCreateWithCString(NULL, a->host, kCFStringEncodingUTF8);
    if (h) {
        CFStreamCreatePairWithSocketToHost(NULL, h, (UInt32)a->port, &c->rs, &c->ws);
        CFRelease(h);
    }
    if (c->rs && c->ws) {
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

        CFRunLoopSourceContext sctx = { 0, c, NULL, NULL, NULL, NULL, NULL, NULL, NULL, keepalive_perform };
        c->keepalive = CFRunLoopSourceCreate(NULL, 0, &sctx);
        if (c->keepalive) CFRunLoopAddSource(rl, c->keepalive, kCFRunLoopCommonModes);

        /* Host header value is host:port; queue the opening handshake (flushed once writable). */
        char hostport[160];
        snprintf(hostport, sizeof hostport, "%s:%d", a->host, a->port);
        uint8_t key16[16]; rand_bytes(key16, 16);
        if (mmws_sm_start(&c->sm, hostport, a->path, a->ua, a->origin,
                          key16, conn_send, conn_event, c) == 0)
            ok = 1;
    }

    /* Publish readiness (+ the runloop ref on success) so mmwsconn_open can return a usable conn.
     * After this point 'a' and the caller's strings must NOT be touched — open() may have returned. */
    pthread_mutex_lock(&c->lk);
    c->io_rl    = ok ? CFRunLoopGetCurrent() : NULL;
    c->setup_ok = ok;
    c->rl_ready = 1;
    pthread_cond_signal(&c->ready);
    pthread_mutex_unlock(&c->lk);

    if (!ok) {
        cbc("ioFail", 0);
        io_cleanup_streams(c);     /* release partial streams on the thread that created them */
        return NULL;               /* mmwsconn_open() joins us and frees c */
    }

    cbc("ioRun", 0);
    CFRunLoopRun();                /* serviced off-main until io_shutdown() calls CFRunLoopStop */
    cbc("ioExit", 0);
    return NULL;
}

/* Destroy the conn's memory. Runs on the caller (main) thread AFTER the io thread has joined,
 * so no socket state is live. rs/ws are normally already NULL (released on the io thread); the
 * CFRelease here only covers the setup-failed-before-thread path. */
static void conn_destroy(MMWSConn *c) {
    if (c->rs) { CFRelease(c->rs); c->rs = NULL; }
    if (c->ws) { CFRelease(c->ws); c->ws = NULL; }
    if (c->keepalive) { CFRelease(c->keepalive); c->keepalive = NULL; }  /* degraded early-exit path */
    pthread_mutex_destroy(&c->lk);
    pthread_cond_destroy(&c->ready);
    free(c);
}

MMWSConn *mmwsconn_open(const char *host, int port, const char *path, const char *ua, const char *origin, const mmwsconn_cb *cb) {
    if (!host || !path || !cb) return NULL;
    MMWSConn *c = (MMWSConn *)calloc(1, sizeof *c);
    if (!c) return NULL;
    c->cb = *cb;
    if (pthread_mutex_init(&c->lk, NULL) != 0) { free(c); return NULL; }
    if (pthread_cond_init(&c->ready, NULL) != 0) { pthread_mutex_destroy(&c->lk); free(c); return NULL; }

    /* Hand the open params to the io thread. `a` stays valid because open() blocks on `ready`
     * until the io thread has consumed it (streams built + mmws_sm_start done). */
    io_args a;
    a.c = c; a.host = host; a.port = port; a.path = path; a.ua = ua; a.origin = origin;
    if (pthread_create(&c->io_thr, NULL, io_thread_main, &a) != 0) {
        pthread_cond_destroy(&c->ready); pthread_mutex_destroy(&c->lk); free(c);
        return NULL;
    }
    c->thr_started = 1;

    pthread_mutex_lock(&c->lk);
    while (!c->rl_ready) pthread_cond_wait(&c->ready, &c->lk);
    int ok = c->setup_ok;
    pthread_mutex_unlock(&c->lk);

    if (!ok) {                     /* setup failed on the io thread — reap it, then free */
        pthread_join(c->io_thr, NULL);
        conn_destroy(c);
        return NULL;
    }
    return c;
}

/* Marshal a send onto the io thread: copy the payload, hand it to the io runloop, mask + frame
 * there. mmws_sm_send_msg (which touches the streams + tx queue) must run ON the io thread. */
static int conn_marshal_send(MMWSConn *c, uint8_t opcode, const uint8_t *data, size_t len) {
    if (!c) return -1;
    pthread_mutex_lock(&c->lk);
    CFRunLoopRef rl = c->io_rl;
    int dead = c->dead;
    pthread_mutex_unlock(&c->lk);
    if (!rl || dead) return -1;                   /* torn-down / never-opened conn: drop */

    uint8_t *copy = NULL;
    if (len) {
        copy = (uint8_t *)malloc(len);
        if (!copy) return -1;
        memcpy(copy, data, len);
    }
    size_t n = len;
    CFRunLoopPerformBlock(rl, kCFRunLoopCommonModes, ^{
        if (!c->dead) {                           /* authoritative check — same thread as teardown */
            uint8_t m[4]; rand_bytes(m, 4);
            mmws_sm_send_msg(&c->sm, opcode, copy, n, m);
        }
        if (copy) free(copy);
    });
    CFRunLoopWakeUp(rl);
    return 0;
}

int mmwsconn_send_text(MMWSConn *c, const uint8_t *data, size_t len) {
    return conn_marshal_send(c, MMWS_OP_TEXT, data, len);
}

int mmwsconn_send_binary(MMWSConn *c, const uint8_t *data, size_t len) {
    return conn_marshal_send(c, MMWS_OP_BINARY, data, len);
}

void mmwsconn_close(MMWSConn *c, uint16_t code) {
    if (!c) return;
    pthread_mutex_lock(&c->lk);
    CFRunLoopRef rl = c->io_rl;
    int dead = c->dead;
    pthread_mutex_unlock(&c->lk);
    if (!rl || dead) return;
    CFRunLoopPerformBlock(rl, kCFRunLoopCommonModes, ^{
        if (!c->dead) {
            uint8_t m[4]; rand_bytes(m, 4);
            mmws_sm_close(&c->sm, code, m);
        }
    });
    CFRunLoopWakeUp(rl);
}

void mmwsconn_free(MMWSConn *c) {
    if (!c) return;
    pthread_mutex_lock(&c->lk);
    CFRunLoopRef rl = c->io_rl;
    pthread_mutex_unlock(&c->lk);
    if (rl) {
        /* tear down + stop the runloop ON the io thread, then join it (never join from the io
         * thread itself — this always runs on main, where Tweak.x defers mmwsconn_free). */
        CFRunLoopPerformBlock(rl, kCFRunLoopCommonModes, ^{ io_shutdown(c); });
        CFRunLoopWakeUp(rl);
        pthread_join(c->io_thr, NULL);
    } else if (c->thr_started) {
        pthread_join(c->io_thr, NULL);   /* thread never published a runloop; reap it */
    }
    conn_destroy(c);
}
