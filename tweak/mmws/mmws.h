/* mmws.h — pure RFC-6455 (WebSocket) client functions for the iOS-5.1 transplant.
 *
 * NO I/O, NO ObjC, NO allocation — caller supplies buffers. This is the host-unit-testable
 * core (build a `node --test` / C harness against the RFC-6455 vectors before any device work,
 * exactly like tweak/mmvideo/mmurl.h). The native CFStream socket + JS exposure layer sit ON
 * TOP of these and are device-only. See DESIGN.md.
 *
 * STATUS: signatures only — implementations (mmws.c) + host tests are the next step.
 * Reference: RFC 6455. Client frames MUST be masked (§5.3). Handshake accept per §1.3.
 */
#ifndef MMWS_H
#define MMWS_H
#include <stddef.h>
#include <stdint.h>

/* WebSocket opcodes (RFC 6455 §5.2) */
#define MMWS_OP_CONT   0x0
#define MMWS_OP_TEXT   0x1
#define MMWS_OP_BINARY 0x2
#define MMWS_OP_CLOSE  0x8
#define MMWS_OP_PING   0x9
#define MMWS_OP_PONG   0xA

/* The magic GUID appended to Sec-WebSocket-Key before SHA-1 (RFC 6455 §1.3). */
#define MMWS_GUID "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

/* ---- handshake ---------------------------------------------------------- */

/* Compute Sec-WebSocket-Accept from the client's Sec-WebSocket-Key:
 *   base64( SHA1( key + MMWS_GUID ) ).  `key` is the base64 nonce the client sent.
 * Writes a NUL-terminated 28-char base64 string to `out` (needs >= 29 bytes).
 * Returns 1 on success, 0 on bad args / short buffer.
 * TEST VECTOR: key "dGhlIHNhbXBsZSBub25jZQ==" -> "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=". */
int mmws_accept_key(const char *key, char *out, size_t outlen);

/* Generate a random 16-byte nonce, base64-encode it into `out` (needs >= 25 bytes,
 * NUL-terminated). `rnd16` supplies the 16 raw random bytes (caller's RNG — keep this
 * pure/deterministic for tests by passing fixed bytes). Returns 1/0. */
int mmws_make_key(const uint8_t rnd16[16], char *out, size_t outlen);

/* Build the client opening handshake (HTTP/1.1 Upgrade) into `out` (NUL-terminated).
 * Produces the request line + Host/Upgrade/Connection/Sec-WebSocket-Key/Version(13) headers
 * for `path` on `host` (host may include :port). `key_b64` from mmws_make_key.
 * Returns bytes written (excl. NUL), or 0 on short buffer. */
int mmws_build_open_request(const char *host, const char *path,
                            const char *key_b64, const char *ua, const char *origin,
                            char *out, size_t outlen);

/* Validate the server's opening-handshake response held in `buf` (len bytes).
 * Checks "HTTP/1.1 101", Upgrade: websocket, Connection: Upgrade, and
 * Sec-WebSocket-Accept == expected_accept (from mmws_accept_key on the sent key).
 * Returns: 1 = valid 101 handshake; 0 = not-yet-complete (no header terminator seen);
 *         -1 = invalid (wrong status / bad accept). */
int mmws_check_open_response(const char *buf, size_t len, const char *expected_accept);

/* ---- framing ------------------------------------------------------------ */

/* Encode ONE client->server frame (always FIN=1, always masked per §5.3) into `out`.
 * `mask4` = the 4-byte masking key (caller-supplied random; fixed for tests). Payload is
 * masked in place of the output. Returns total frame bytes written, or 0 on short buffer.
 * Header is 2 + {0|2|8} length bytes + 4 mask bytes, then masked payload. */
size_t mmws_encode_frame(uint8_t opcode, const uint8_t *payload, uint64_t len,
                         const uint8_t mask4[4], uint8_t *out, size_t outlen);

/* Result of decoding one server->client frame. Server frames are NOT masked. */
typedef struct {
    int      complete;      /* 1 = a full frame was parsed from buf; 0 = need more bytes  */
    int      fin;           /* FIN bit                                                     */
    uint8_t  opcode;        /* MMWS_OP_*                                                   */
    uint64_t payload_len;   /* decoded payload length                                      */
    size_t   header_len;    /* bytes of frame header (offset to payload within buf)        */
    size_t   frame_len;     /* header_len + payload_len (advance the read cursor by this)  */
} mmws_frame;

/* Parse one frame from `buf` (len available bytes) into `f`. Sets f.complete=0 if the
 * buffer doesn't yet hold the full header+payload (caller reads more, retries). The payload
 * bytes live at buf + f.header_len (unmasked, since server frames are unmasked). Returns 1
 * if parsing proceeded (check f.complete), -1 on a protocol error (e.g. masked server frame,
 * reserved bits set). */
int mmws_decode_frame(const uint8_t *buf, size_t len, mmws_frame *f);

/* Build a close frame (opcode 0x8) with a 2-byte big-endian status code (e.g. 1000 normal),
 * masked, into `out`. Returns bytes written or 0. */
size_t mmws_close_frame(uint16_t status, const uint8_t mask4[4], uint8_t *out, size_t outlen);

#endif /* MMWS_H */
