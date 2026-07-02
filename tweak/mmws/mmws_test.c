/* Host unit tests for mmws.c — pure RFC-6455 client functions.
 * Build+run: tweak/mmws/test.sh (WSL gcc). No device needed.
 * Vectors: RFC 6455 §1.3 (accept key) + §5.7 (masked/unmasked "Hello" frames). */
#include "mmws.h"
#include <stdio.h>
#include <string.h>
#include <stdint.h>

static int fails = 0, tests = 0;
#define CHECK(cond, msg) do { tests++; if (!(cond)) { printf("FAIL: %s\n", msg); fails++; } \
                              else printf("  ok: %s\n", msg); } while (0)

int main(void) {
    /* 1) Sec-WebSocket-Accept — the canonical RFC 6455 §1.3 vector */
    char acc[64];
    CHECK(mmws_accept_key("dGhlIHNhbXBsZSBub25jZQ==", acc, sizeof acc) == 1, "accept_key returns 1");
    CHECK(strcmp(acc, "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=") == 0, "accept_key matches RFC vector");
    CHECK(mmws_accept_key("dGhlIHNhbXBsZSBub25jZQ==", acc, 4) == 0, "accept_key short buffer -> 0");

    /* 2) make_key — base64 of 16 raw bytes 0x00..0x0f */
    uint8_t rnd[16]; for (int i = 0; i < 16; i++) rnd[i] = (uint8_t)i;
    char key[32];
    CHECK(mmws_make_key(rnd, key, sizeof key) == 1, "make_key returns 1");
    CHECK(strcmp(key, "AAECAwQFBgcICQoLDA0ODw==") == 0, "make_key base64 vector");

    /* 3) encode_frame — RFC 6455 §5.7 masked text "Hello" */
    uint8_t mask[4] = { 0x37, 0xfa, 0x21, 0x3d };
    uint8_t out[64];
    size_t n = mmws_encode_frame(MMWS_OP_TEXT, (const uint8_t *)"Hello", 5, mask, out, sizeof out);
    uint8_t exp[] = { 0x81, 0x85, 0x37, 0xfa, 0x21, 0x3d, 0x7f, 0x9f, 0x4d, 0x51, 0x58 };
    CHECK(n == 11, "encode_frame masked len == 11");
    CHECK(memcmp(out, exp, 11) == 0, "encode_frame matches RFC masked Hello");
    CHECK(mmws_encode_frame(MMWS_OP_TEXT, (const uint8_t *)"Hello", 5, mask, out, 4) == 0, "encode short buffer -> 0");

    /* 3b) encode 16-bit length path (payload 200) — needs a 208-byte buffer */
    uint8_t big[300]; for (int i = 0; i < 200; i++) big[i] = (uint8_t)i;
    uint8_t out2[300];
    n = mmws_encode_frame(MMWS_OP_BINARY, big, 200, mask, out2, sizeof out2);
    CHECK(n == 2 + 2 + 4 + 200, "encode 16-bit len total bytes");
    CHECK(out2[1] == (0x80 | 126) && out2[2] == 0x00 && out2[3] == 0xC8, "encode 16-bit len header (200)");
    CHECK((uint8_t)(out2[8] ^ mask[0]) == big[0] && (uint8_t)(out2[8 + 199] ^ mask[199 & 3]) == big[199],
          "encode 16-bit payload masked correctly");

    /* 4) decode_frame — unmasked server text "Hello" */
    uint8_t srv[] = { 0x81, 0x05, 'H', 'e', 'l', 'l', 'o' };
    mmws_frame f;
    CHECK(mmws_decode_frame(srv, sizeof srv, &f) == 1 && f.complete, "decode complete");
    CHECK(f.fin == 1 && f.opcode == MMWS_OP_TEXT && f.payload_len == 5 &&
          f.header_len == 2 && f.frame_len == 7, "decode fields");
    CHECK(memcmp(srv + f.header_len, "Hello", 5) == 0, "decode payload offset");
    CHECK(mmws_decode_frame(srv, 3, &f) == 1 && !f.complete, "decode partial -> incomplete");
    uint8_t masked_srv[] = { 0x81, 0x85, 0, 0, 0, 0, 0 };
    CHECK(mmws_decode_frame(masked_srv, sizeof masked_srv, &f) == -1, "decode masked server -> error");
    uint8_t rsv[] = { 0xC1, 0x00 };   /* RSV1 set */
    CHECK(mmws_decode_frame(rsv, sizeof rsv, &f) == -1, "decode reserved bit -> error");
    uint8_t len16[] = { 0x82, 126, 0x01, 0x00 };  /* binary, len 256, header only */
    CHECK(mmws_decode_frame(len16, 4, &f) == 1 && !f.complete &&
          f.payload_len == 256 && f.header_len == 4, "decode 16-bit len header");

    /* 5) build_open_request */
    char req[256];
    int rl = mmws_build_open_request("192.168.1.60:3000", "/sockjs/x/y/websocket", "abc123==", req, sizeof req);
    CHECK(rl > 0, "build_open_request > 0");
    CHECK(strstr(req, "GET /sockjs/x/y/websocket HTTP/1.1\r\n") != NULL, "req: request line");
    CHECK(strstr(req, "Host: 192.168.1.60:3000\r\n") != NULL, "req: Host");
    CHECK(strstr(req, "Upgrade: websocket\r\n") != NULL, "req: Upgrade");
    CHECK(strstr(req, "Sec-WebSocket-Key: abc123==\r\n") != NULL, "req: Key");
    CHECK(strstr(req, "Sec-WebSocket-Version: 13\r\n") != NULL, "req: Version 13");
    CHECK(strstr(req, "\r\n\r\n") != NULL, "req: header terminator");

    /* 6) check_open_response */
    const char *ok = "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                     "Connection: Upgrade\r\nSec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n";
    CHECK(mmws_check_open_response(ok, strlen(ok), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=") == 1, "response valid -> 1");
    CHECK(mmws_check_open_response("HTTP/1.1 101 x\r\nUpgrade: websocket\r\n", 36, "x") == 0, "response incomplete -> 0");
    const char *badacc = "HTTP/1.1 101 x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         "Sec-WebSocket-Accept: WRONG\r\n\r\n";
    CHECK(mmws_check_open_response(badacc, strlen(badacc), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=") == -1, "response bad accept -> -1");
    const char *not101 = "HTTP/1.1 400 Bad Request\r\n\r\n";
    CHECK(mmws_check_open_response(not101, strlen(not101), "x") == -1, "response non-101 -> -1");

    /* 7) close_frame */
    uint8_t cl[16];
    size_t cn = mmws_close_frame(1000, mask, cl, sizeof cl);
    CHECK(cn == 8, "close_frame len == 8 (2 hdr + 4 mask + 2 payload)");
    CHECK((cl[0] & 0x0f) == MMWS_OP_CLOSE && (cl[0] & 0x80), "close_frame opcode + FIN");
    CHECK((uint8_t)(cl[6] ^ mask[0]) == 0x03 && (uint8_t)(cl[7] ^ mask[1]) == 0xE8, "close_frame status 1000 (masked)");

    printf("\n%d/%d checks passed\n", tests - fails, tests);
    return fails ? 1 : 0;
}
