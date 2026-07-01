/* mmws bridge shim — defines window.__mmwsNative to reach native via the mmws:// URL scheme.
 * The tweak injects THIS then mmws.js (which defines window.WebSocket) in didClearWindowObject,
 * before the page's SockJS runs. ES5 only (iPad-1 / Safari 5.1). See mmws/DESIGN.md + REFINDINGS.
 *
 * JS->native: navigate a hidden iframe to mmws://<op>/<id>?<args>. The tweak's hook on
 * -[WebAppController webView:shouldStartLoadWithRequest:navigationType:] intercepts it (returns
 * NO) and drives mmwsconn_*. native->JS comes back via window.__mmwsDispatch (defined by mmws.js). */
(function (w) {
    if (w.__mmwsNative) return;
    function nav(op, id, args) {
        var q = '';
        for (var k in args) if (args.hasOwnProperty(k)) q += (q ? '&' : '') + k + '=' + encodeURIComponent(args[k]);
        var u = 'mmws://' + op + '/' + id + (q ? ('?' + q) : '');
        var f = document.createElement('iframe');
        f.style.display = 'none';
        f.src = u;
        (document.documentElement || document.body).appendChild(f);
        setTimeout(function () { if (f.parentNode) f.parentNode.removeChild(f); }, 0);
    }
    w.__mmwsNative = {
        open:  function (id, url)  { nav('open',  id, { url: url }); },
        send:  function (id, data) { nav('send',  id, { d: data }); },
        close: function (id, code) { nav('close', id, { c: code }); }
    };
})(window);
