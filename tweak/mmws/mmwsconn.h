/* mmwsconn.h — device-side CFStream TCP transport that drives the mmws_sm state machine.
 *
 * This is the ONLY I/O layer: a non-blocking CFStream socket to the server, run-loop
 * scheduled, pumping bytes into mmws_sm_on_recv() and writing whatever the state machine's
 * send callback emits. Supplies arc4random masks. Auto-pongs PINGs. All protocol correctness
 * lives in the host-tested mmws.c / mmws_sm.c below it. Runtime-verify on-device via the
 * server access log (mm_live.err) showing a real .../websocket 101 upgrade. See DESIGN.md.
 */
#ifndef MMWSCONN_H
#define MMWSCONN_H
#include <stddef.h>
#include <stdint.h>

typedef struct MMWSConn MMWSConn;

typedef struct {
    void (*on_open)   (MMWSConn *c, void *ud);
    void (*on_message)(MMWSConn *c, uint8_t opcode, const uint8_t *data, size_t len, void *ud);
    void (*on_close)  (MMWSConn *c, uint16_t code, void *ud);
    void (*on_error)  (MMWSConn *c, const char *msg, void *ud);
    void *ud;
} mmwsconn_cb;

/* Open a WebSocket to ws://host:port<path>. Schedules on the CURRENT run loop; callbacks fire
 * on that run loop's thread. Returns NULL on immediate failure. `cb` is copied. */
MMWSConn *mmwsconn_open(const char *host, int port, const char *path, const char *ua, const char *origin, const mmwsconn_cb *cb);

/* Send an application message (masked). 0 ok, -1 if not open / too big. */
int  mmwsconn_send_text  (MMWSConn *c, const uint8_t *data, size_t len);
int  mmwsconn_send_binary(MMWSConn *c, const uint8_t *data, size_t len);

/* Send a close frame (does not free — wait for on_close, then mmwsconn_free). */
void mmwsconn_close(MMWSConn *c, uint16_t code);

/* Unschedule/close the streams and free. Safe after on_close/on_error. */
void mmwsconn_free(MMWSConn *c);

#endif /* MMWSCONN_H */
