/* mmws_sm.c — RFC-6455 client connection state machine. Pure; host-tested by mmws_sm_test.c. */
#include "mmws_sm.h"
#include <string.h>
#include <stdio.h>

static void emit_error(mmws_sm *sm, const char *msg) {
    sm->state = MMWS_ERR;
    if (sm->event) sm->event(sm->ud, MMWS_EV_ERROR, 0, (const uint8_t *)msg, msg ? strlen(msg) : 0);
}

/* drop the first k bytes of the rx buffer */
static void rx_consume(mmws_sm *sm, size_t k) {
    if (k >= sm->rxn) { sm->rxn = 0; return; }
    memmove(sm->rx, sm->rx + k, sm->rxn - k);
    sm->rxn -= k;
}

int mmws_sm_start(mmws_sm *sm, const char *host, const char *path, const uint8_t rnd16[16],
                  mmws_send_fn send, mmws_event_fn event, void *ud) {
    if (!sm || !host || !path || !rnd16) return -1;
    memset(sm, 0, sizeof *sm);
    sm->send = send; sm->event = event; sm->ud = ud; sm->state = MMWS_CONNECTING;
    char key[32];
    if (!mmws_make_key(rnd16, key, sizeof key)) return -1;
    if (!mmws_accept_key(key, sm->accept, sizeof sm->accept)) return -1;
    char req[600];
    int n = mmws_build_open_request(host, path, key, req, sizeof req);
    if (n <= 0) return -1;
    if (sm->send) sm->send(sm->ud, (const uint8_t *)req, (size_t)n);
    return 0;
}

int mmws_sm_on_recv(mmws_sm *sm, const uint8_t *bytes, size_t len) {
    if (!sm) return -1;
    if (sm->state == MMWS_CLOSED || sm->state == MMWS_ERR) return -1;
    if (len) {
        if (sm->rxn + len > MMWS_RXCAP) {
            static char ov[80];
            snprintf(ov, sizeof ov, "rx overflow rxn=%u len=%u cap=%u",
                     (unsigned)sm->rxn, (unsigned)len, (unsigned)MMWS_RXCAP);
            emit_error(sm, ov);
            return -1;
        }
        memcpy(sm->rx + sm->rxn, bytes, len);
        sm->rxn += len;
    }

    /* opening handshake */
    if (sm->state == MMWS_CONNECTING) {
        int r = mmws_check_open_response((const char *)sm->rx, sm->rxn, sm->accept);
        if (r == 0) return 0;                                  /* need more */
        if (r < 0) { emit_error(sm, "bad handshake"); return -1; }
        size_t hlen = 0;                                       /* consume through the CRLFCRLF */
        for (size_t i = 0; i + 4 <= sm->rxn; i++)
            if (memcmp(sm->rx + i, "\r\n\r\n", 4) == 0) { hlen = i + 4; break; }
        rx_consume(sm, hlen);
        sm->state = MMWS_OPEN;
        if (sm->event) sm->event(sm->ud, MMWS_EV_OPEN, 0, NULL, 0);
    }

    /* frames (may be several buffered; a message may still be partial) */
    while (sm->state == MMWS_OPEN || sm->state == MMWS_CLOSING) {
        mmws_frame f;
        int d = mmws_decode_frame(sm->rx, sm->rxn, &f);
        if (d < 0) { emit_error(sm, "bad frame"); return -1; }
        if (!f.complete) break;                                /* need more */
        const uint8_t *payload = sm->rx + f.header_len;
        if (f.opcode == MMWS_OP_CLOSE) {
            uint8_t code[2] = { 0, 0 };
            int have = (f.payload_len >= 2);
            if (have) { code[0] = payload[0]; code[1] = payload[1]; }
            sm->state = MMWS_CLOSED;
            if (sm->event) sm->event(sm->ud, MMWS_EV_CLOSE, 0, have ? code : NULL, have ? 2 : 0);
            rx_consume(sm, (size_t)f.frame_len);
            break;
        }
        /* TEXT/BINARY/CONT/PING/PONG -> deliver payload (caller pongs on PING) */
        if (sm->event) sm->event(sm->ud, MMWS_EV_MESSAGE, f.opcode, payload, (size_t)f.payload_len);
        rx_consume(sm, (size_t)f.frame_len);
    }
    return 0;
}

int mmws_sm_send_msg(mmws_sm *sm, uint8_t opcode, const uint8_t *data, size_t len,
                     const uint8_t mask4[4]) {
    if (!sm || sm->state != MMWS_OPEN || !mask4) return -1;
    if (len + 14 > MMWS_TXCAP) return -1;                      /* too big for a single frame here */
    uint8_t buf[MMWS_TXCAP];
    size_t n = mmws_encode_frame(opcode, data, len, mask4, buf, sizeof buf);
    if (!n) return -1;
    if (sm->send) sm->send(sm->ud, buf, n);
    return 0;
}

void mmws_sm_close(mmws_sm *sm, uint16_t code, const uint8_t mask4[4]) {
    if (!sm || (sm->state != MMWS_OPEN) || !mask4) return;
    uint8_t buf[16];
    size_t n = mmws_close_frame(code, mask4, buf, sizeof buf);
    if (n && sm->send) sm->send(sm->ud, buf, n);
    sm->state = MMWS_CLOSING;
}
