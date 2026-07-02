/* mmws_sm.h — RFC-6455 client connection STATE MACHINE (pure, host-testable).
 *
 * Drives mmws.h's handshake + framing across a byte stream: accumulates received bytes
 * (handshake spread over reads, frames split/coalesced), completes the opening handshake,
 * reassembles frames, and dispatches OPEN/MESSAGE/CLOSE/ERROR events. It performs NO I/O —
 * it emits outbound bytes via a `send` callback (the socket layer writes them) and events
 * via an `event` callback. The CFStream socket (device-only, next layer) just pumps bytes
 * in via mmws_sm_on_recv() and writes whatever `send` hands it. See DESIGN.md.
 *
 * MosaicMesh messages are small JSON, so fixed buffers (no alloc) are fine; oversize -> ERROR.
 */
#ifndef MMWS_SM_H
#define MMWS_SM_H
#include "mmws.h"

#define MMWS_RXCAP 16384   /* inbound reassembly buffer */
#define MMWS_TXCAP 16384   /* max single outbound message (header+payload) */

typedef enum { MMWS_CONNECTING = 0, MMWS_OPEN, MMWS_CLOSING, MMWS_CLOSED, MMWS_ERR } mmws_state;

/* events delivered via mmws_event_fn */
enum { MMWS_EV_OPEN = 1, MMWS_EV_MESSAGE, MMWS_EV_CLOSE, MMWS_EV_ERROR };

/* write `len` bytes to the socket (device layer). */
typedef void (*mmws_send_fn)(void *ud, const uint8_t *bytes, size_t len);
/* deliver an event:
 *   OPEN    — opcode/data/len unused
 *   MESSAGE — opcode = MMWS_OP_TEXT/BINARY/PONG/PING; data/len = payload
 *   CLOSE   — data = 2-byte big-endian close code (len 2), or NULL/0 if none
 *   ERROR   — data = reason string, len = strlen                                    */
typedef void (*mmws_event_fn)(void *ud, int event, uint8_t opcode, const uint8_t *data, size_t len);

typedef struct {
    mmws_state    state;
    char          accept[64];       /* expected Sec-WebSocket-Accept for our sent key */
    uint8_t       rx[MMWS_RXCAP];
    size_t        rxn;
    mmws_send_fn  send;
    mmws_event_fn event;
    void         *ud;
} mmws_sm;

/* Zero-init, compute the accept from rnd_key16, and emit the opening handshake via send().
 * Returns 0 ok, -1 on bad args. State -> CONNECTING. */
int  mmws_sm_start(mmws_sm *sm, const char *host, const char *path,
                   const uint8_t rnd_key16[16],
                   mmws_send_fn send, mmws_event_fn event, void *ud);

/* Feed bytes read from the socket. Completes the handshake (-> OPEN event) then reassembles
 * and dispatches frames (MESSAGE/CLOSE). Returns 0 ok, -1 on protocol error (emits EV_ERROR,
 * state -> MMWS_ERR). Safe to call with len 0 to drain buffered bytes. */
int  mmws_sm_on_recv(mmws_sm *sm, const uint8_t *bytes, size_t len);

/* Frame + send an application message (masked with mask4). opcode = TEXT/BINARY (or PONG for
 * a manual pong). Returns 0 ok, -1 if not OPEN / too big / bad args. */
int  mmws_sm_send_msg(mmws_sm *sm, uint8_t opcode, const uint8_t *data, size_t len,
                      const uint8_t mask4[4]);

/* Send a close frame (masked) and move to CLOSING. */
void mmws_sm_close(mmws_sm *sm, uint16_t code, const uint8_t mask4[4]);

#endif /* MMWS_SM_H */
