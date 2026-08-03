/* js/clockdiag/render.js — ES5, iPad-1 Safari 5.1. Renders the clock-diagnostic
 * stats into a big, readable, color-coded panel. DOM only, no analysis logic. */
(function (root) {

  var COLOR = { PASS: '#1a7f37', WARN: '#9a6700', FAIL: '#cf222e', 'N/A': '#888' };

  function r1(x) { return (typeof x === 'number') ? (Math.round(x * 10) / 10) : x; }

  function verdictRow(label, v) {
    if (!v) { return ''; }
    var c = COLOR[v.verdict] || '#444';
    return '<tr><td>' + label + '</td>' +
           '<td style="text-align:right;font-weight:bold">' + r1(v.value) + ' ' + v.unit + '</td>' +
           '<td style="color:' + c + ';font-weight:bold">' + v.verdict + '</td></tr>';
  }
  function rawRow(label, val) {
    return '<tr><td>' + label + '</td><td colspan="2" style="text-align:right">' + val + '</td></tr>';
  }

  // el: container; stats/verdicts from analysis; extra: {progress,total,done,
  // timerJitterMs, clockSource, failed}
  function render(el, stats, verdicts, extra) {
    extra = extra || {};
    var worst = 'PASS', k;
    for (k in verdicts) {
      if (verdicts[k].verdict === 'FAIL') { worst = 'FAIL'; }
      else if (verdicts[k].verdict === 'WARN' && worst !== 'FAIL') { worst = 'WARN'; }
    }

    var html = '';
    html += '<div style="font:16px/1.4 -apple-system,Helvetica,Arial,sans-serif;max-width:640px;margin:0 auto;padding:12px">';
    html += '<h2 style="margin:0 0 4px">Clock / comms qualification</h2>';
    html += '<div style="color:#666;font-size:13px">' +
            'probes ' + (extra.progress || 0) + '/' + (extra.total || '?') +
            (extra.failed ? ('  ·  failed ' + extra.failed) : '') +
            (extra.done ? '  ·  DONE' : '  ·  running…') + '</div>';

    if (stats && stats.n) {
      html += '<div style="margin:10px 0;padding:8px;border-radius:6px;background:' +
              (COLOR[worst] || '#444') + ';color:#fff;font-weight:bold;font-size:18px;text-align:center">' +
              'OVERALL: ' + worst + '</div>';
      html += '<table style="width:100%;border-collapse:collapse" border="0" cellpadding="6">';
      html += '<tr style="border-bottom:1px solid #ccc"><th align="left">metric</th><th align="right">value</th><th align="left">verdict</th></tr>';
      html += verdictRow('RTT jitter (path)', verdicts.rttJitter);
      html += verdictRow('offset jitter', verdicts.offsetJitter);
      html += verdictRow('oscillator skew', verdicts.skew);
      html += verdictRow('up/down jitter imbalance', verdicts.jitterImbalance);
      // client-side (rendered as verdicts if provided by the page)
      if (extra.timerVerdict) { html += verdictRow('main-thread timer jitter', extra.timerVerdict); }
      html += '<tr><td colspan="3" style="height:6px"></td></tr>';
      html += rawRow('RTT min / median / p90 / max',
                     r1(stats.rtt.min) + ' / ' + r1(stats.rtt.median) + ' / ' + r1(stats.rtt.p90) + ' / ' + r1(stats.rtt.max) + ' ms');
      html += rawRow('offset (best, min-RTT)', r1(stats.offset) + ' ms');
      html += rawRow('one-way jitter up / down', r1(stats.jitter.up) + ' / ' + r1(stats.jitter.down) + ' ms');
      if (stats.skew) {
        html += rawRow('skew fit: window / clean / drift vs noise',
          r1(stats.skew.windowMs / 1000) + 's / ' + stats.skew.cleanN + ' pts / ' +
          r1(stats.skew.driftMs) + ' vs ' + r1(stats.skew.residMs) + ' ms' +
          (stats.skew.confident ? ' (trusted)' : ' (run longer)'));
      }
      html += rawRow('clock source', extra.clockSource || '?');
      html += '</table>';
      html += '<div style="margin-top:6px;font-size:11px;color:#888">Note: steady one-way path asymmetry is absorbed into the offset and is not measurable from round-trips.</div>';
      // copyable raw JSON
      var dump = { stats: stats, verdicts: verdicts, timerJitterMs: extra.timerJitterMs, clockSource: extra.clockSource };
      html += '<textarea readonly style="width:100%;height:90px;margin-top:8px;font:11px monospace" onclick="this.select()">' +
              _json(dump) + '</textarea>';
    }
    html += '</div>';
    el.innerHTML = html;
  }

  // tiny stable JSON (avoid JSON.stringify formatting differences; Safari 5.1 has JSON)
  function _json(o) { try { return JSON.stringify(o); } catch (e) { return '' + o; } }

  root.mmClockRender = render;

})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
