/* mmws.c — pure RFC-6455 client functions (no I/O, no ObjC, no alloc).
 * Host-tested by mmws_test.c. See DESIGN.md. SHA-1 + base64 are self-contained so
 * this stays link-free (CommonCrypto availability under the tweak link model is
 * unverified; these are small and keep the file host-portable). */
#include "mmws.h"
#include <string.h>
#include <stdio.h>

/* ---- SHA-1 (RFC 3174) --------------------------------------------------- */
static uint32_t rol(uint32_t v, int b) { return (v << b) | (v >> (32 - b)); }

static void sha1_block(uint32_t st[5], const uint8_t p[64]) {
    uint32_t w[80];
    for (int i = 0; i < 16; i++)
        w[i] = ((uint32_t)p[i*4] << 24) | ((uint32_t)p[i*4+1] << 16) |
               ((uint32_t)p[i*4+2] << 8) | (uint32_t)p[i*4+3];
    for (int i = 16; i < 80; i++)
        w[i] = rol(w[i-3] ^ w[i-8] ^ w[i-14] ^ w[i-16], 1);
    uint32_t a = st[0], b = st[1], c = st[2], d = st[3], e = st[4];
    for (int i = 0; i < 80; i++) {
        uint32_t f, k;
        if      (i < 20) { f = (b & c) | (~b & d);            k = 0x5A827999; }
        else if (i < 40) { f = b ^ c ^ d;                     k = 0x6ED9EBA1; }
        else if (i < 60) { f = (b & c) | (b & d) | (c & d);   k = 0x8F1BBCDC; }
        else             { f = b ^ c ^ d;                     k = 0xCA62C1D6; }
        uint32_t t = rol(a, 5) + f + e + k + w[i];
        e = d; d = c; c = rol(b, 30); b = a; a = t;
    }
    st[0] += a; st[1] += b; st[2] += c; st[3] += d; st[4] += e;
}

static void sha1(const uint8_t *data, size_t len, uint8_t out[20]) {
    uint32_t st[5] = { 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0 };
    uint8_t blk[64];
    size_t i = 0;
    while (len - i >= 64) { sha1_block(st, data + i); i += 64; }
    size_t rem = len - i;
    memcpy(blk, data + i, rem);
    blk[rem] = 0x80;
    if (rem >= 56) {                       /* not enough room for the length -> extra block */
        memset(blk + rem + 1, 0, 64 - rem - 1);
        sha1_block(st, blk);
        memset(blk, 0, 56);
    } else {
        memset(blk + rem + 1, 0, 56 - rem - 1);
    }
    uint64_t ml = (uint64_t)len * 8;
    for (int j = 0; j < 8; j++) blk[56 + j] = (uint8_t)(ml >> (56 - 8 * j));
    sha1_block(st, blk);
    for (int j = 0; j < 5; j++) {
        out[j*4]   = (uint8_t)(st[j] >> 24); out[j*4+1] = (uint8_t)(st[j] >> 16);
        out[j*4+2] = (uint8_t)(st[j] >> 8);  out[j*4+3] = (uint8_t)st[j];
    }
}

/* ---- base64 encode ------------------------------------------------------ */
static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static int b64enc(const uint8_t *in, size_t n, char *out, size_t outlen) {
    size_t need = ((n + 2) / 3) * 4;
    if (!out || outlen < need + 1) return 0;
    size_t o = 0, i = 0;
    while (i + 3 <= n) {
        uint32_t v = ((uint32_t)in[i] << 16) | ((uint32_t)in[i+1] << 8) | in[i+2];
        out[o++] = B64[(v >> 18) & 63]; out[o++] = B64[(v >> 12) & 63];
        out[o++] = B64[(v >> 6) & 63];  out[o++] = B64[v & 63];
        i += 3;
    }
    size_t rem = n - i;
    if (rem == 1) {
        uint32_t v = (uint32_t)in[i] << 16;
        out[o++] = B64[(v >> 18) & 63]; out[o++] = B64[(v >> 12) & 63];
        out[o++] = '='; out[o++] = '=';
    } else if (rem == 2) {
        uint32_t v = ((uint32_t)in[i] << 16) | ((uint32_t)in[i+1] << 8);
        out[o++] = B64[(v >> 18) & 63]; out[o++] = B64[(v >> 12) & 63];
        out[o++] = B64[(v >> 6) & 63];  out[o++] = '=';
    }
    out[o] = 0;
    return (int)o;
}

/* case-insensitive substring search over a bounded buffer (buf may not be NUL-terminated) */
static const char *mem_ci_find(const char *buf, size_t len, const char *needle) {
    size_t nl = strlen(needle);
    if (nl == 0 || len < nl) return NULL;
    for (size_t i = 0; i + nl <= len; i++) {
        size_t j = 0;
        for (; j < nl; j++) {
            char a = buf[i + j], b = needle[j];
            if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
            if (b >= 'A' && b <= 'Z') b = (char)(b - 'A' + 'a');
            if (a != b) break;
        }
        if (j == nl) return buf + i;
    }
    return NULL;
}

/* ---- handshake ---------------------------------------------------------- */
int mmws_accept_key(const char *key, char *out, size_t outlen) {
    if (!key || !out) return 0;
    size_t kl = strlen(key), gl = strlen(MMWS_GUID);
    char cat[128];
    if (kl + gl >= sizeof cat) return 0;
    memcpy(cat, key, kl);
    memcpy(cat + kl, MMWS_GUID, gl);
    uint8_t dig[20];
    sha1((const uint8_t *)cat, kl + gl, dig);
    return b64enc(dig, 20, out, outlen) ? 1 : 0;
}

int mmws_make_key(const uint8_t rnd16[16], char *out, size_t outlen) {
    if (!rnd16 || !out) return 0;
    return b64enc(rnd16, 16, out, outlen) ? 1 : 0;
}

/* Reject CR/LF so a crafted ua/origin can't inject extra headers into the handshake. */
static int mmws_hdr_safe(const char *s){ for(;*s;s++) if(*s==13||*s==10) return 0; return 1; }
int mmws_build_open_request(const char *host, const char *path,
                            const char *key_b64, const char *ua, const char *origin,
                            char *out, size_t outlen) {
    if (!host || !path || !key_b64 || !out) return 0;
    int n = snprintf(out, outlen,
        "GET %s HTTP/1.1\r\n"
        "Host: %s\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n",
        path, host, key_b64);
    if (n <= 0 || (size_t)n >= outlen) return 0;
    /* Match a browser native WS handshake: browsers always send User-Agent + Origin.
       An empty UA leaves the server unable to classify the device -> it falls to Default. */
    if (ua && ua[0] && mmws_hdr_safe(ua)) { int m = snprintf(out+n, outlen-n, "User-Agent: %s\r\n", ua);
                       if (m <= 0 || (size_t)(n+m) >= outlen) return 0; n += m; }
    if (origin && origin[0] && mmws_hdr_safe(origin)) { int m = snprintf(out+n, outlen-n, "Origin: %s\r\n", origin);
                               if (m <= 0 || (size_t)(n+m) >= outlen) return 0; n += m; }
    { int m = snprintf(out+n, outlen-n, "\r\n");
      if (m <= 0 || (size_t)(n+m) >= outlen) return 0; n += m; }
    return n;
}

int mmws_check_open_response(const char *buf, size_t len, const char *expected_accept) {
    if (!buf) return -1;
    /* need the full header block */
    const char *end = mem_ci_find(buf, len, "\r\n\r\n");
    if (!end) return 0;                                   /* incomplete */
    size_t hlen = (size_t)(end - buf) + 4;
    if (hlen < 12 || strncmp(buf, "HTTP/1.1 101", 12) != 0) return -1;
    if (!mem_ci_find(buf, hlen, "upgrade: websocket"))  return -1;
    if (!mem_ci_find(buf, hlen, "connection: upgrade")) return -1;
    if (expected_accept && *expected_accept) {
        const char *h = mem_ci_find(buf, hlen, "sec-websocket-accept:");
        if (!h) return -1;
        h += strlen("sec-websocket-accept:");
        while (h < end && (*h == ' ' || *h == '\t')) h++;
        size_t al = strlen(expected_accept);
        if ((size_t)(end - h) < al) return -1;
        if (strncmp(h, expected_accept, al) != 0) return -1;
        char after = h[al];                              /* value must end at CR (not a prefix match) */
        if (after != '\r' && after != '\n' && after != ' ' && after != '\t') return -1;
    }
    return 1;
}

/* ---- framing ------------------------------------------------------------ */
size_t mmws_encode_frame(uint8_t opcode, const uint8_t *payload, uint64_t len,
                         const uint8_t mask4[4], uint8_t *out, size_t outlen) {
    if (!out || !mask4 || (len && !payload)) return 0;
    size_t hdr = 2 + 4;                                   /* base + mask */
    if (len >= 126 && len < 65536) hdr += 2;
    else if (len >= 65536)         hdr += 8;
    if (outlen < hdr + len) return 0;
    size_t o = 0;
    out[o++] = 0x80 | (opcode & 0x0f);                    /* FIN=1 */
    if (len < 126) {
        out[o++] = 0x80 | (uint8_t)len;                  /* MASK=1 */
    } else if (len < 65536) {
        out[o++] = 0x80 | 126;
        out[o++] = (uint8_t)(len >> 8); out[o++] = (uint8_t)len;
    } else {
        out[o++] = 0x80 | 127;
        for (int i = 7; i >= 0; i--) out[o++] = (uint8_t)(len >> (i * 8));
    }
    for (int i = 0; i < 4; i++) out[o++] = mask4[i];
    for (uint64_t i = 0; i < len; i++) out[o++] = payload[i] ^ mask4[i & 3];
    return o;
}

int mmws_decode_frame(const uint8_t *buf, size_t len, mmws_frame *f) {
    if (!buf || !f) return -1;
    f->complete = 0;
    if (len < 2) return 1;                                /* need more */
    uint8_t b0 = buf[0], b1 = buf[1];
    if (b0 & 0x70) return -1;                             /* RSV1-3 must be 0 */
    f->fin = (b0 >> 7) & 1;
    f->opcode = b0 & 0x0f;
    if (b1 & 0x80) return -1;                             /* server->client frames are NOT masked */
    uint64_t plen = b1 & 0x7f;
    size_t hdr = 2;
    if (plen == 126) {
        if (len < 4) return 1;
        plen = ((uint64_t)buf[2] << 8) | buf[3];
        hdr = 4;
    } else if (plen == 127) {
        if (len < 10) return 1;
        plen = 0;
        for (int i = 0; i < 8; i++) plen = (plen << 8) | buf[2 + i];
        hdr = 10;
    }
    f->payload_len = plen;
    f->header_len = hdr;
    f->frame_len = hdr + plen;
    if (len < f->frame_len) return 1;                     /* need more */
    f->complete = 1;
    return 1;
}

size_t mmws_close_frame(uint16_t status, const uint8_t mask4[4], uint8_t *out, size_t outlen) {
    uint8_t p[2] = { (uint8_t)(status >> 8), (uint8_t)status };
    return mmws_encode_frame(MMWS_OP_CLOSE, p, 2, mask4, out, outlen);
}
