/* Host tests for mmws_sm.c — the RFC-6455 connection state machine.
 * Exercises the stateful bits the pure fns can't: split handshakes, frames split across
 * reads, multiple frames per read, handshake+frame coalesced, close, outbound framing. */
#include "mmws_sm.h"
#include <stdio.h>
#include <string.h>
#include <stdint.h>

static int fails = 0, tests = 0;
#define CHECK(cond, msg) do { tests++; if (!(cond)) { printf("FAIL: %s\n", msg); fails++; } \
                              else printf("  ok: %s\n", msg); } while (0)

typedef struct {
    uint8_t  sent[16384]; size_t sent_n;
    int      open, msg, close, err;
    uint8_t  last_op;
    uint8_t  last_msg[4096]; size_t last_msg_n;
    uint16_t close_code;
    char     errmsg[256];
} tctx;

static void t_send(void *ud, const uint8_t *b, size_t n) {
    tctx *c = ud; if (c->sent_n + n <= sizeof c->sent) { memcpy(c->sent + c->sent_n, b, n); c->sent_n += n; }
}
static void t_event(void *ud, int ev, uint8_t op, const uint8_t *d, size_t n) {
    tctx *c = ud;
    if (ev == MMWS_EV_OPEN) c->open++;
    else if (ev == MMWS_EV_MESSAGE) {
        c->msg++; c->last_op = op;
        c->last_msg_n = n < sizeof c->last_msg ? n : sizeof c->last_msg;
        if (d) memcpy(c->last_msg, d, c->last_msg_n);
    } else if (ev == MMWS_EV_CLOSE) { c->close++; c->close_code = (n >= 2 && d) ? ((d[0] << 8) | d[1]) : 0; }
    else if (ev == MMWS_EV_ERROR) { c->err++; size_t k = n < sizeof c->errmsg - 1 ? n : sizeof c->errmsg - 1; if (d) memcpy(c->errmsg, d, k); c->errmsg[k] = 0; }
}

/* start an sm with a fixed key; return the expected accept in `acc` for building responses */
static void start_conn(mmws_sm *sm, tctx *c, char acc[64]) {
    memset(c, 0, sizeof *c);
    uint8_t rnd[16]; for (int i = 0; i < 16; i++) rnd[i] = (uint8_t)i;
    char key[32]; mmws_make_key(rnd, key, sizeof key);
    mmws_accept_key(key, acc, 64);
    mmws_sm_start(sm, "h:3000", "/sockjs/x/y/websocket", NULL, NULL, rnd, t_send, t_event, c);
}
static int build_resp(char *out, size_t outlen, const char *acc) {
    return snprintf(out, outlen,
        "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
        "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n", acc);
}

int main(void) {
    mmws_sm sm; tctx c; char acc[64];

    /* 1) start emits a valid opening handshake */
    start_conn(&sm, &c, acc);
    CHECK(c.sent_n > 0, "start emitted handshake bytes");
    CHECK(memmem(c.sent, c.sent_n, "GET /sockjs/x/y/websocket HTTP/1.1", 33) != NULL, "handshake has request line");
    CHECK(memmem(c.sent, c.sent_n, "Sec-WebSocket-Key: AAECAwQFBgcICQoLDA0ODw==", 43) != NULL, "handshake has our key");

    /* 2) full handshake response in one recv -> OPEN */
    char resp[256]; int rl = build_resp(resp, sizeof resp, acc);
    CHECK(mmws_sm_on_recv(&sm, (uint8_t *)resp, rl) == 0 && c.open == 1 && sm.state == MMWS_OPEN, "handshake in one chunk -> OPEN");

    /* 3) split handshake across two recvs -> OPEN only after completion */
    start_conn(&sm, &c, acc); build_resp(resp, sizeof resp, acc); rl = (int)strlen(resp);
    int half = rl / 2;
    mmws_sm_on_recv(&sm, (uint8_t *)resp, half);
    CHECK(c.open == 0 && sm.state == MMWS_CONNECTING, "split handshake: no OPEN after first half");
    mmws_sm_on_recv(&sm, (uint8_t *)resp + half, rl - half);
    CHECK(c.open == 1 && sm.state == MMWS_OPEN, "split handshake: OPEN after second half");

    /* 4) a server TEXT frame "hi" (unmasked) -> MESSAGE */
    uint8_t hi[] = { 0x81, 0x02, 'h', 'i' };
    mmws_sm_on_recv(&sm, hi, sizeof hi);
    CHECK(c.msg == 1 && c.last_op == MMWS_OP_TEXT && c.last_msg_n == 2 && memcmp(c.last_msg, "hi", 2) == 0, "TEXT frame -> MESSAGE 'hi'");

    /* 5) a TEXT frame split across two recvs -> one MESSAGE after completion */
    c.msg = 0;
    uint8_t f5[] = { 0x81, 0x03, 'a', 'b', 'c' };
    mmws_sm_on_recv(&sm, f5, 2);
    CHECK(c.msg == 0, "split frame: no MESSAGE after header only");
    mmws_sm_on_recv(&sm, f5 + 2, 3);
    CHECK(c.msg == 1 && c.last_msg_n == 3 && memcmp(c.last_msg, "abc", 3) == 0, "split frame: MESSAGE after body");

    /* 6) two frames coalesced in one recv -> two MESSAGEs */
    c.msg = 0;
    uint8_t two[] = { 0x81, 0x01, 'x', 0x81, 0x01, 'y' };
    mmws_sm_on_recv(&sm, two, sizeof two);
    CHECK(c.msg == 2 && c.last_msg_n == 1 && c.last_msg[0] == 'y', "two coalesced frames -> two MESSAGEs");

    /* 7) handshake + first frame coalesced in one recv -> OPEN then MESSAGE */
    start_conn(&sm, &c, acc); build_resp(resp, sizeof resp, acc);
    uint8_t combo[512]; size_t cl = strlen(resp);
    memcpy(combo, resp, cl);
    uint8_t fr[] = { 0x81, 0x02, 'o', 'k' }; memcpy(combo + cl, fr, 4); cl += 4;
    mmws_sm_on_recv(&sm, combo, cl);
    CHECK(c.open == 1 && c.msg == 1 && memcmp(c.last_msg, "ok", 2) == 0, "handshake+frame coalesced -> OPEN + MESSAGE");

    /* 8) close frame -> CLOSE with code 1000 */
    uint8_t cls[] = { 0x88, 0x02, 0x03, 0xE8 };   /* opcode 8, len 2, code 1000 */
    mmws_sm_on_recv(&sm, cls, sizeof cls);
    CHECK(c.close == 1 && c.close_code == 1000 && sm.state == MMWS_CLOSED, "CLOSE frame -> CLOSE code 1000");

    /* 9) send_msg frames + masks correctly (decode the captured bytes back) */
    start_conn(&sm, &c, acc); build_resp(resp, sizeof resp, acc); mmws_sm_on_recv(&sm, (uint8_t *)resp, strlen(resp));
    c.sent_n = 0;                                  /* clear the handshake bytes */
    uint8_t mask[4] = { 0x11, 0x22, 0x33, 0x44 };
    CHECK(mmws_sm_send_msg(&sm, MMWS_OP_TEXT, (uint8_t *)"REGISTER", 8, mask) == 0, "send_msg returns 0");
    CHECK(c.sent_n == 2 + 4 + 8, "send_msg framed len (hdr+mask+payload)");
    CHECK((c.sent[0] & 0x0f) == MMWS_OP_TEXT && (c.sent[0] & 0x80) && (c.sent[1] & 0x80), "send_msg FIN+opcode+MASK");
    char dec[16]; for (int i = 0; i < 8; i++) dec[i] = c.sent[6 + i] ^ mask[i & 3];
    CHECK(memcmp(dec, "REGISTER", 8) == 0, "send_msg payload unmasks to REGISTER");

    /* 10) bad handshake (wrong accept) -> ERROR */
    start_conn(&sm, &c, acc);
    const char *bad = "HTTP/1.1 101 x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: WRONG\r\n\r\n";
    CHECK(mmws_sm_on_recv(&sm, (uint8_t *)bad, strlen(bad)) == -1 && c.err == 1 && sm.state == MMWS_ERR, "bad handshake -> ERROR");

    printf("\n%d/%d checks passed\n", tests - fails, tests);
    return fails ? 1 : 0;
}
