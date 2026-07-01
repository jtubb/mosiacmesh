/* mmws.js — ES5 window.WebSocket polyfill for the iOS-5 webclip (Layer 3, JS side).
 *
 * iOS-5.1 WebKit has no usable WebSocket, so SockJS falls back to XHR. This shim installs a
 * working window.WebSocket that routes through a NATIVE RFC-6455 client (mmwsconn.c) via a
 * minimal bridge object `window.__mmwsNative`, which the tweak injects. SockJS then selects
 * the `websocket` transport and uses this.
 *
 * MUST be ES5 (iPad-1 / Safari 5.1): no let/const/arrow/class/Promise. See legacy-ipad-compat.
 *
 * ---- NATIVE BRIDGE CONTRACT (implemented device-side by the tweak) --------------------------
 *   window.__mmwsNative = {
 *     open:  function(id, url)          // start a native RFC-6455 connect; url is ws://host:port/path
 *     send:  function(id, data)         // send a text message (string)
 *     close: function(id, code)         // send close
 *   };
 *   // native -> JS delivery (native calls these on the main thread via evaluate-JS):
 *   window.__mmwsDispatch(id, type, data)   // type: 'open' | 'message' | 'close' | 'error'
 * --------------------------------------------------------------------------------------------
 * The native side maps id -> MMWSConn and translates the mmwsconn_cb callbacks into
 * __mmwsDispatch(...). SockJS only ever sends/receives text frames, so binary is omitted here.
 */
(function (w) {
    if (!w.__mmwsNative || w.__mmwsForceInstall === false) return;   // no bridge -> leave XHR fallback
    if (w.WebSocket && w.WebSocket.__mmws) return;                    // already installed

    var CONNECTING = 0, OPEN = 1, CLOSING = 2, CLOSED = 3;
    var reg = {};       // id -> instance
    var nextId = 1;

    function MMWebSocket(url /*, protocols */) {
        var self = this;
        this.url = String(url);
        this.readyState = CONNECTING;
        this.bufferedAmount = 0;
        this.onopen = null; this.onmessage = null; this.onclose = null; this.onerror = null;
        this._id = nextId++;
        reg[this._id] = this;
        // defer the native open so handlers assigned right after `new WebSocket()` are set first
        setTimeout(function () {
            try { w.__mmwsNative.open(self._id, self.url); }
            catch (e) { self._fail('open failed'); }
        }, 0);
    }
    MMWebSocket.__mmws = true;
    MMWebSocket.CONNECTING = CONNECTING; MMWebSocket.OPEN = OPEN;
    MMWebSocket.CLOSING = CLOSING;       MMWebSocket.CLOSED = CLOSED;

    MMWebSocket.prototype.send = function (data) {
        if (this.readyState !== OPEN) throw new Error('WebSocket is not open');
        w.__mmwsNative.send(this._id, String(data));
        return true;
    };
    MMWebSocket.prototype.close = function (code /*, reason */) {
        if (this.readyState === CLOSED || this.readyState === CLOSING) return;
        this.readyState = CLOSING;
        try { w.__mmwsNative.close(this._id, code || 1000); } catch (e) {}
    };
    MMWebSocket.prototype._fail = function (msg) {
        var ev = { type: 'error', message: msg };
        if (this.onerror) try { this.onerror(ev); } catch (e) {}
        this._closed(1006);
    };
    MMWebSocket.prototype._closed = function (code) {
        if (this.readyState === CLOSED) return;
        this.readyState = CLOSED;
        delete reg[this._id];
        if (this.onclose) try { this.onclose({ type: 'close', code: code || 1000, wasClean: code === 1000 }); } catch (e) {}
    };

    /* native -> JS event pump */
    w.__mmwsDispatch = function (id, type, data) {
        var ws = reg[id];
        if (!ws) return;
        if (type === 'open') {
            ws.readyState = OPEN;
            if (ws.onopen) try { ws.onopen({ type: 'open' }); } catch (e) {}
        } else if (type === 'message') {
            if (ws.onmessage) try { ws.onmessage({ type: 'message', data: data }); } catch (e) {}
        } else if (type === 'close') {
            ws._closed(typeof data === 'number' ? data : 1000);
        } else if (type === 'error') {
            ws._fail(data || 'error');
        }
    };

    w.WebSocket = MMWebSocket;
    if (!w.MozWebSocket) w.MozWebSocket = MMWebSocket;   // SockJS also probes MozWebSocket
})(window);
