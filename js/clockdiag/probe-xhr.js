/* js/clockdiag/probe-xhr.js — ES5, iPad-1 Safari 5.1. One NTP round-trip probe
 * over plain XHR to /clockprobe. Uses Date.now() (client epoch ms) for t1/t4 so
 * they mix correctly with the server's epoch t2/t3. cb(sample|null). This is the
 * v1 (XHR) rung of the ground-truth ladder; a probe-ws.js will share this
 * (url, cb) contract for the v2 rung. */
(function (root) {

  function xhrProbe(url, cb) {
    var t1 = (new Date()).getTime();
    var xhr = new XMLHttpRequest();
    xhr.open('GET', url + '?t1=' + t1, true);
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      var t4 = (new Date()).getTime();
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          var r = JSON.parse(xhr.responseText);
          cb({ t1: t1, t2: r.t2, t3: r.t3, t4: t4 });
        } catch (e) { cb(null); }
      } else {
        cb(null);
      }
    };
    try { xhr.send(); } catch (e) { cb(null); }
  }

  root.mmXhrProbe = xhrProbe;

})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
