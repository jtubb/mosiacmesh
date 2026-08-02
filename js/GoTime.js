/*if(window.performance) {
	if (window.performance.now) {
		console.log("Using high performance timer");
		getAccurateTimestamp = function() { return window.performance.now(); };
	} else {
		if (window.performance.webkitNow) {
			console.log("Using webkit high performance timer");
			getAccurateTimestamp = function() { return window.performance.webkitNow(); };
		}
	}
} else {
    console.log("Using low performance timer");
    getAccurateTimestamp = function() { return new Date().getTime(); };
}*/

getAccurateTimestamp = function() { return new Date().getTime(); };

var ProgrammableTimer = (function f() {
	var options = {
		_target: getAccurateTimestamp(),
		_interval: 1000,
		_callback: null,
		_stopped: false,
		_frame: 0,
		_drift_history: [],
		_drift_history_samples: 5,
		_drift_correction: 0,
		_tolerance: 50,
		_synced: false
	};
	
	_calcdrift = function(arr){
	  // Calculate drift correction.

	  /*
	  In this example I've used a simple median.
	  You can use other methods, but it's important not to use an average. 
	  If the user switches tabs and back, an average would put far too much
	  weight on the outlier.
	  */

	  var values = arr.concat(); // copy array so it isn't mutated
	  
	  values.sort(function(a,b){
		return a-b;
	  });
	  if(values.length ===0) return 0;
	  var half = Math.floor(values.length / 2);
	  if (values.length % 2) return values[half];
	  var median = (values[half - 1] + values[half]) / 2.0;
	  
	  return median;
	};
	
	_tick = function(){
		if (options._stopped) return;

		var currentTime = GoTime.now();
		var dt = currentTime - options._target;
		
		if (dt <= (options._interval*1.2)) {
			options._drift_history.push(dt+options._drift_correction);
			options._drift_correction = _calcdrift(options._drift_history);
			if(options._drift_history.length >= options._drift_history_samples)
			{
				options._drift_history.shift();
				options._synced = false;
				if((dt-options._interval)<options._tolerance)
				{
					options._synced = true;
				}
			}
		}
		
		//var currentInterval = (options._target += options._interval) - currentTime;

		//setTimeout(_tick, currentInterval);
		options._frame = Math.max(0, options._interval - dt - options._drift_correction);
		
		setTimeout(_tick, Math.max(0, options._interval - dt - options._drift_correction));
		if(options._callback)
		{
			options._callback(currentTime, options._target, options._frame);
		}
		options._target += options._interval;
	};
	
	return {
		// Public functions
		setup: function(interval, callback) {
			options._target = GoTime.now();     // target time for the next frame
			options._interval = interval;    // the milliseconds between ticks
			options._callback = callback;
			options._stopped = false;
			options._frame = 0;
		},

		tick: function() {
            _tick();
		},
		
		stop: function() { 
			options._stopped = true; 
			return options._frame 
		},
		
		isSynced: function() { 
			return options._synced 
		},
		
		target: function(target) { 
			options._target = target; 
		},

		setCallback: function(replacement) { 
			options._callback = replacement 
		}
	}
})();

var GoTime = (function f() {
    var options = {
        _syncCount: 0,
        _offset: 0,
        _skew: 0,             // oscillator skew (ppm fraction); 0 => now() identical to today
        _skewRef: 0,          // raw-local anchor for the skew rate term
        _skewMinSamples: 6,
        _skewMinSpanMs: 60000,
        _skewDeadbandPpm: 2e-6,
        _precision: 2e308,
        _lastAcceptTime: null,   // when the offset was last locked (for ?tdbg age only)
        _history: [],
        _syncInterval: 900000,
        _fastSyncInterval: 1000,
        _fastSyncJitterMs: 150,
        _syncPrecisionTargetMs: 40,
        _syncPrecisionStreak: 2,
        _fastSyncCapMs: 60000,
        _syncPhase: 'fast',
        _syncStreak: 0,
        _fastStartMs: null,
        _syncSampleReturned: false,
        _syncTimer: null,
        _synchronizing: false,
        _lastSyncTime: null,
        _lastSyncMethod: null,
        _ajaxURL: null,
        _ajaxSampleSize: 1,
        _firstSyncCallbackRan: false,
        _firstSyncCallback: null,
        _onSyncCallback: null,
        _wsCall: null,
        _wsRequestTime: null,
        _steerSamples: [],
        _steering: false,
        _lastSteerAt: null,
        _lastSteerTarget: null,
        _steerDeadbandMs: 33,
        _steerSnapMs: 500,
        _steerCapMsPerSec: 15,
        _steerWindowMs: 120000,
        _steerMinSamples: 3,
        _steerPrecisionFloorMs: 60,
        _lastBeatMs: null,
        _beatPrecisionMs: 12,
        _beatStaleMs: 6000,
        _beatRecvTolMs: 3000
    };
    
	// Private Methods
    _ajaxSample = function() {
        var req, requestTime;
        req = new XMLHttpRequest();
        req.open("GET", options._ajaxURL);
        req.onreadystatechange = function() {
            var responseTime, sample, serverTime;
            responseTime = Date.now();
            if (req.readyState === 4) {
                if (req.status === 200) {
                    serverTime = _dateFromService(req.responseText);
                    sample = _calculateOffset(requestTime, responseTime, serverTime);
                    _reviseOffset(sample, "ajax");
                }
            }
        };
        requestTime = Date.now();
        req.send();
        return true;
    };

    _sync = function() {
        // Beat suppression: when the server's broadcast clock beat is enabled and
        // fresh, the beat already feeds the steer via beatReceived -- skip the
        // /time (ws/ajax) round-trip entirely so it doesn't re-dilute the
        // _steerSamples ring with a coarser sample, and just re-arm a staleness
        // re-check so /time resumes automatically if beats stop. window is absent
        // in node (tests), so this is inert there and existing cadence is unchanged.
        var beatOn = (typeof window !== 'undefined' && window._mmClockBeat);
        if (_beatSuppressesPoll(!!beatOn, options._lastBeatMs, getAccurateTimestamp(), options._beatStaleMs)) {
            // Worst-case resume latency from "beats stop" to "first /time poll" is up
            // to ~2x _beatStaleMs (this re-arm is phase-independent of when beats
            // actually stopped). Acceptable: the clock is carried by offset+skew in
            // the interim and isSynced() is unaffected -- it never freezes.
            if (options._syncTimer != null) { clearTimeout(options._syncTimer); }
            options._syncTimer = setTimeout(_sync, options._beatStaleMs);
            return;
        }
        var success = false;
		if (options._wsCall != null) {
			options._wsRequestTime = Date.now();
			if (options._wsCall()) { options._syncCount++; success = true; }
		}
		if (!success && options._ajaxURL != null) {
			if (_ajaxSample()) { options._syncCount++; }
		}
		_scheduleAdaptiveSync();
    };

    // Arm the next _sync. Reads the last cycle's sample precision (a large sentinel
    // if no sample returned, so a dropped/coarse sample keeps us fast), asks the
    // pure _nextSyncDelay for the next delay+phase, and applies per-client jitter in
    // the fast phase only. clearTimeout-before-arm keeps exactly one timer live even
    // when resync() fires overlapping _sync calls.
    _scheduleAdaptiveSync = function() {
        var nowMs = Date.now();
        if (options._fastStartMs == null) { options._fastStartMs = nowMs; }
        var effPrec = options._syncSampleReturned ? options._precision : 2e308;
        var d = _nextSyncDelay({
            phase: options._syncPhase,
            precision: effPrec,
            streak: options._syncStreak,
            fastElapsedMs: nowMs - options._fastStartMs,
            opts: {
                SyncInterval: options._syncInterval,
                FastSyncInterval: options._fastSyncInterval,
                SyncPrecisionTargetMs: options._syncPrecisionTargetMs,
                SyncPrecisionStreak: options._syncPrecisionStreak,
                FastSyncCapMs: options._fastSyncCapMs
            }
        });
        options._syncPhase = d.phase;
        options._syncStreak = d.streak;
        options._syncSampleReturned = false;
        var delay = d.delayMs;
        if (d.phase === 'fast') { delay += Math.floor(Math.random() * options._fastSyncJitterMs); }
        if (options._syncTimer != null) { clearTimeout(options._syncTimer); }
        options._syncTimer = setTimeout(_sync, delay);
    };

    _calculateOffset = function(requestTime, responseTime, serverTime) {
        var duration, oneway;
        duration = responseTime - requestTime;
        oneway = duration / 2;
        return {
            offset: serverTime - requestTime - oneway,
            precision: oneway
        };
    };

    // Naive one-way offset from a broadcast beat: NO per-screen ToF correction
    // (that asymmetry error is exactly what we're eliminating). Transit stays a
    // common-mode lag. precision is a fixed nominal (broadcast transit is low +
    // common-mode), since a one-way beat has no RTT to measure.
    _beatSample = function (serverSendMs, localRecv, precisionMs) {
        return { offset: serverSendMs - localRecv, precision: precisionMs };
    };
    // Should the /time poll be suppressed this cycle? Yes only when beat mode is
    // enabled AND a beat arrived within staleMs (else fall back to /time).
    _beatSuppressesPoll = function (enabled, lastBeatMs, nowMs, staleMs) {
        if (!enabled || lastBeatMs === null || lastBeatMs === undefined) { return false; }
        return (nowMs - lastBeatMs) < staleMs;
    };

    // Choose localRecv for a beat: prefer a native receipt stamp (recvTs, epoch-ms
    // from mmws' off-main I/O thread) when it's a finite value close to now; else
    // fall back to the main-thread clock read. toleranceMs rejects stale/garbage stamps.
    _pickRecv = function (recvTs, nowTs, toleranceMs) {
        if (typeof recvTs === 'number' && isFinite(recvTs) &&
            Math.abs(nowTs - recvTs) <= toleranceMs) { return recvTs; }
        return nowTs;
    };

    // Pure hybrid correction step: deadband (ignore tiny error) / bounded slew /
    // snap (large error only). dtMs = wall ms since the previous step. opts:
    // {deadbandMs, snapMs, capMsPerSec}. Returns the new offset (float).
    _steerStep = function(offset, target, dtMs, opts) {
        var dead = (opts && opts.deadbandMs != null) ? opts.deadbandMs : 33;
        var snap = (opts && opts.snapMs != null) ? opts.snapMs : 500;
        var cap = (opts && opts.capMsPerSec != null) ? opts.capMsPerSec : 15;
        var err = target - offset;
        var aerr = err < 0 ? -err : err;
        if (aerr <= dead) { return offset; }
        if (aerr >= snap) { return target; }
        var maxMove = cap * (dtMs / 1000);
        var move = err;
        if (move > maxMove) { move = maxMove; }
        else if (move < -maxMove) { move = -maxMove; }
        return offset + move;
    };

    // Pure robust target offset: median of recent (within windowMs) samples whose
    // precision passes a RELATIVE gate (max(2*bestInWindow, precisionFloorMs)), so a
    // single PSM-jittery high-RTT sample can't move it. Returns null when fewer than
    // minSamples pass. samples: [{offset, precision, t}]. nowMs: GoTime.now().
    _robustTarget = function(samples, nowMs, opts) {
        var windowMs = (opts && opts.windowMs != null) ? opts.windowMs : 120000;
        var minS = (opts && opts.minSamples != null) ? opts.minSamples : 3;
        var floor = (opts && opts.precisionFloorMs != null) ? opts.precisionFloorMs : 60;
        var i, recent = [];
        for (i = 0; i < samples.length; i++) {
            if (samples[i].t >= nowMs - windowMs) { recent.push(samples[i]); }
        }
        if (recent.length === 0) { return null; }
        var best = recent[0].precision;
        for (i = 1; i < recent.length; i++) {
            if (recent[i].precision < best) { best = recent[i].precision; }
        }
        var gate = 2 * best;
        if (gate < floor) { gate = floor; }
        var kept = [];
        for (i = 0; i < recent.length; i++) {
            if (recent[i].precision <= gate) { kept.push(recent[i].offset); }
        }
        if (kept.length < minS) { return null; }
        kept.sort(function(a, b) { return a - b; });
        var h = Math.floor(kept.length / 2);
        return (kept.length % 2) ? kept[h] : (kept[h - 1] + kept[h]) / 2;
    };

    // Reference-frame note (M1 follow-up): `t` on a sample is now-space (GoTime.now() at revise
    // time, i.e. skew-corrected server-clock space), while `_skewRef` is anchored in RAW-local
    // space (getAccurateTimestamp()) -- see _applySkew/_steerTick. Mixing now-space `t` with
    // raw-space `skewRef` in the skew fit/de-trend produces a bounded residual of order
    // skew*offset (sub-ms normally; can reach tens of ms only when the locked offset itself is
    // very large, e.g. an hour-scale clock error -- not expected on this fleet). `tRaw` (added to
    // samples in _reviseOffset) is the raw-local capture of the SAME sample, so the slope fit and
    // de-trend below use it instead of `t` to stay consistent with `skewRef`'s frame. Falls back
    // to `t` when a sample has no `tRaw` (older/hand-built sample objects, e.g. existing unit
    // tests) so behavior for those is unchanged. `t` itself is NEVER modified -- _robustTarget's
    // windowing/recency logic still reads `t` in now-space, unchanged.
    _sampleRawT = function(sample) {
        return (sample.tRaw != null) ? sample.tRaw : sample.t;
    };

    // Robust oscillator-skew estimate: the Theil-Sen slope (median of pairwise slopes) of
    // offset-vs-time over the recent precision-gated samples. Median-of-slopes rejects PSM
    // jitter outliers (mirrors _robustTarget's robust-median philosophy). Returns the slope
    // (ppm fraction, e.g. 22e-6) or null unless >=skewMinSamples gated samples span
    // >=skewMinSpanMs of time (a slope over a short baseline is noise). Pure.
    _skewFit = function(samples, nowMs, opts) {
        var windowMs = (opts && opts.windowMs != null) ? opts.windowMs : 120000;
        var minS = (opts && opts.skewMinSamples != null) ? opts.skewMinSamples : 6;
        var minSpan = (opts && opts.skewMinSpanMs != null) ? opts.skewMinSpanMs : 60000;
        var floor = (opts && opts.precisionFloorMs != null) ? opts.precisionFloorMs : 60;
        var i, j, recent = [];
        for (i = 0; i < samples.length; i++) {
            if (samples[i].t >= nowMs - windowMs) { recent.push(samples[i]); }
        }
        if (recent.length === 0) { return null; }
        var best = recent[0].precision;
        for (i = 1; i < recent.length; i++) { if (recent[i].precision < best) { best = recent[i].precision; } }
        var gate = 2 * best; if (gate < floor) { gate = floor; }
        var kept = [];
        for (i = 0; i < recent.length; i++) { if (recent[i].precision <= gate) { kept.push(recent[i]); } }
        if (kept.length < minS) { return null; }
        var tmin = kept[0].t, tmax = kept[0].t;
        for (i = 1; i < kept.length; i++) {
            if (kept[i].t < tmin) { tmin = kept[i].t; }
            if (kept[i].t > tmax) { tmax = kept[i].t; }
        }
        if (tmax - tmin < minSpan) { return null; }
        var slopes = [];
        for (i = 0; i < kept.length; i++) {
            for (j = i + 1; j < kept.length; j++) {
                // Slope denominator uses tRaw (raw-local space, matching _skewRef) -- see the
                // reference-frame note above _sampleRawT. The window/span checks above stay on
                // `t` unchanged.
                var dt = _sampleRawT(kept[j]) - _sampleRawT(kept[i]);
                if (dt !== 0) { slopes.push((kept[j].offset - kept[i].offset) / dt); }
            }
        }
        if (slopes.length === 0) { return null; }
        slopes.sort(function(a, b) { return a - b; });
        var h = Math.floor(slopes.length / 2);
        return (slopes.length % 2) ? slopes[h] : (slopes[h - 1] + slopes[h]) / 2;
    };

    // Update the clock model {offset, skew, skewRef} for a new skew estimate WITHOUT jumping
    // now(). At the update instant rawNow, fold the accumulated old rate term into offset and
    // re-anchor skewRef to rawNow, so now(rawNow) is unchanged and thereafter advances at the
    // new (clamped) rate. Pure. maxPpm guards a bad fit from running the clock away.
    _applySkew = function(model, newSkew, rawNow) {
        var maxPpm = 300e-6;
        var s = newSkew;
        if (s > maxPpm) { s = maxPpm; } else if (s < -maxPpm) { s = -maxPpm; }
        var newOffset = model.offset + model.skew * (rawNow - model.skewRef);
        return { offset: newOffset, skew: s, skewRef: rawNow };
    };

    // Remove the skew trend from offset samples so the offset steer chases the residual, not the
    // drift the skew term already corrects (else the two double-correct and the error accumulates).
    // De-trends using tRaw (raw-local space, matching skewRef -- see the reference-frame note
    // above _sampleRawT); falls back to `t` for samples without a tRaw. `t` itself is passed
    // through unchanged on the output (still now-space; _robustTarget needs it for windowing).
    // Fresh array; input untouched. Pure.
    _deskewSamples = function(samples, skew, skewRef) {
        var out = [], i;
        for (i = 0; i < samples.length; i++) {
            out.push({ offset: samples[i].offset - skew * (_sampleRawT(samples[i]) - skewRef),
                       precision: samples[i].precision, t: samples[i].t });
        }
        return out;
    };

    // One maintenance iteration: pull _offset toward the robust target via _steerStep.
    // Flips _steering true the first time a target is available, transferring offset
    // ownership from the ratchet to the slew. dtMs is real local elapsed time, so a
    // skipped beat can't over-correct beyond cap*dt. getAccurateTimestamp() is the raw
    // (offset-free) local clock, so dt is immune to offset changes.
    _steerTick = function() {
        var nowLocal = getAccurateTimestamp();
        var dtMs = (options._lastSteerAt == null) ? 1000 : (nowLocal - options._lastSteerAt);
        options._lastSteerAt = nowLocal;
        if (dtMs <= 0) { return; }
        var opts = {
            deadbandMs: options._steerDeadbandMs, snapMs: options._steerSnapMs,
            capMsPerSec: options._steerCapMsPerSec, windowMs: options._steerWindowMs,
            minSamples: options._steerMinSamples, precisionFloorMs: options._steerPrecisionFloorMs,
            skewMinSamples: options._skewMinSamples, skewMinSpanMs: options._skewMinSpanMs
        };
        // Frequency discipline: re-fit skew once the offset has locked; fold on meaningful change.
        if (options._steering) {
            var newSkew = _skewFit(options._steerSamples, GoTime.now(), opts);
            if (newSkew !== null) {
                // Clamp BEFORE the deadband compare, matching the clamp _applySkew applies to the
                // stored skew -- otherwise a (theoretically-possible) >300ppm fit would keep
                // re-folding every beat forever (harmless -- each fold preserves now() continuity --
                // but wasted work + needless skewRef re-anchoring).
                var maxSkewPpm = 300e-6;
                var clampedSkew = newSkew;
                if (clampedSkew > maxSkewPpm) { clampedSkew = maxSkewPpm; }
                else if (clampedSkew < -maxSkewPpm) { clampedSkew = -maxSkewPpm; }
                var dead = (options._skewDeadbandPpm != null) ? options._skewDeadbandPpm : 2e-6;
                if (Math.abs(clampedSkew - options._skew) > dead) {
                    var m = _applySkew({ offset: options._offset, skew: options._skew, skewRef: options._skewRef },
                                       newSkew, nowLocal);
                    options._offset = m.offset; options._skew = m.skew; options._skewRef = m.skewRef;
                }
            }
        }
        // Offset steer chases the DE-SKEWED residual (skew owns rate, steer owns offset).
        var target = _robustTarget(_deskewSamples(options._steerSamples, options._skew, options._skewRef),
                                   GoTime.now(), opts);
        if (target === null) { return; }
        options._steering = true;
        options._lastSteerTarget = target;
        options._offset = Math.round(_steerStep(options._offset, target, dtMs, opts));
    };

    _reviseOffset = function(sample, method) {
        var timestamp, rawNow;
        if (isNaN(sample.offset) || isNaN(sample.precision)) {
            return;
        }
        options._syncSampleReturned = true;
        timestamp = GoTime.now();
        // rawNow is the RAW-local (offset-free) capture of this sample's arrival, in the same
        // space as _skewRef (see _skewFit/_deskewSamples). `t` (now-space) is kept unchanged --
        // _robustTarget's windowing/recency logic depends on it -- tRaw only feeds the skew fit.
        rawNow = getAccurateTimestamp();
        options._lastSyncTime = timestamp;
        options._lastSyncMethod = method;
        options._steerSamples.push({ offset: sample.offset, precision: sample.precision, t: timestamp, tRaw: rawNow });
        while (options._steerSamples.length > 64) { options._steerSamples.shift(); }
        // Add to history
        /*options._history.push({
            Sample: sample,
            Method: method,
            Time: timestamp
        });*/
        // Monotonic precision ratchet (the original, field-proven design): hold the best
        // low-RTT offset we've measured. Ongoing oscillator drift is corrected by
        // ProgrammableTimer's median drift loop at the BEAT level — NOT by re-locking the
        // offset. The decaying re-lock that briefly replaced this chased fresh samples and
        // on PSM-jittery iPad-1 radios kept re-locking onto 90-190ms-RTT samples, moving
        // the offset out from under a beat that was already settled. A stale-but-precise
        // offset is fine here: the beat absorbs the drift. (_lastAcceptTime is retained
        // only so ?tdbg can report offset age; nothing gates on it anymore.)
        if (sample.precision <= options._precision) {
            options._precision = sample.precision;
            options._lastAcceptTime = timestamp;
            // Initial lock only: once steering owns the offset, a late better-precision
            // sample must NOT step it (would defeat the smooth slew).
            if (!options._steering) { options._offset = Math.round(sample.offset); }
        }
        // Beat samples carry a fixed 12ms precision that may never beat a /time
        // sample's precision ratchet, so the branch above can skip them forever --
        // freezing _lastAcceptTime (and therefore msSinceAccept()/?tdbg accAge) at
        // minutes-old even while beats keep arriving every ~2s. Refresh the accept
        // time for every accepted beat regardless, WITHOUT touching options._precision
        // (that ratchet still governs _robustTarget weighting + readyVerdict).
        if (method === "beat") { options._lastAcceptTime = timestamp; }
        if (!options._firstSyncCallbackRan && (options._firstSyncCallback != null)) {
            options._firstSyncCallbackRan = true;
            return options._firstSyncCallback(timestamp, method, sample.offset, sample.precision);
        } else if (options._onSyncCallback != null) {
            return options._onSyncCallback(timestamp, method, sample.offset, sample.precision);
        }
    };

    _dateFromService = function(text) {
        return new Date(parseInt(text));
    };
	
    _setupSync = function() {
        if (options._synchronizing === false) {
            options._synchronizing = true;
            options._syncPhase = 'fast';
            options._syncStreak = 0;
            options._fastStartMs = null;
            options._syncSampleReturned = false;
            // Initial short kick (preserves the old 500ms first-sample latency); every
            // sample after this is scheduled adaptively by _sync -> _scheduleAdaptiveSync.
            options._syncTimer = setTimeout(_sync, 500);
        }
    };

    // Pure fast->slow cadence decision. Given the current phase, the effective
    // precision of the last cycle's sample (a large sentinel when none returned),
    // the good-sample streak so far, and ms elapsed in the fast phase, return the
    // next {delayMs, phase, streak}. No Date / Math.random / timers here — the
    // scheduler wrapper owns those. See docs/superpowers/specs/2026-07-10-sync-cadence-adaptive-design.md
    _nextSyncDelay = function(state) {
        var o = state.opts;
        if (state.phase === 'slow') {
            return { delayMs: o.SyncInterval, phase: 'slow', streak: state.streak };
        }
        var nextStreak = (state.precision <= o.SyncPrecisionTargetMs) ? (state.streak + 1) : 0;
        if (nextStreak >= o.SyncPrecisionStreak) {
            return { delayMs: o.SyncInterval, phase: 'slow', streak: nextStreak };
        }
        if (state.fastElapsedMs >= o.FastSyncCapMs) {
            return { delayMs: o.SyncInterval, phase: 'slow', streak: nextStreak };
        }
        return { delayMs: o.FastSyncInterval, phase: 'fast', streak: nextStreak };
    };

	return {
		// Public Getters
		now: function() {
			var raw = getAccurateTimestamp();
			return raw + options._offset + options._skew * (raw - options._skewRef);
		},

		getOffset: function() {
			return options._offset;
		},

		getPrecision: function() {
			return options._precision;
		},

		getLastMethod: function() {
			return options._lastSyncMethod;
		},

		getSyncCount: function() {
			return options._syncCount;
		},

		getHistory: function() {
			return options._history;
		},

		// Setters
		setOptions: function(opts) {
			if (opts.AjaxURL != null) {
				options._ajaxURL = opts.AjaxURL;
			}
			if (opts.SyncInterval != null) {
				options._syncInterval = opts.SyncInterval;
			}
			if (opts.FastSyncInterval != null) { options._fastSyncInterval = opts.FastSyncInterval; }
			if (opts.FastSyncJitterMs != null) { options._fastSyncJitterMs = opts.FastSyncJitterMs; }
			if (opts.SyncPrecisionTargetMs != null) { options._syncPrecisionTargetMs = opts.SyncPrecisionTargetMs; }
			if (opts.SyncPrecisionStreak != null) { options._syncPrecisionStreak = opts.SyncPrecisionStreak; }
			if (opts.FastSyncCapMs != null) { options._fastSyncCapMs = opts.FastSyncCapMs; }
            if (opts.SteerDeadbandMs != null) { options._steerDeadbandMs = opts.SteerDeadbandMs; }
            if (opts.SteerSnapMs != null) { options._steerSnapMs = opts.SteerSnapMs; }
            if (opts.SteerCapMsPerSec != null) { options._steerCapMsPerSec = opts.SteerCapMsPerSec; }
            if (opts.SteerWindowMs != null) { options._steerWindowMs = opts.SteerWindowMs; }
            if (opts.SteerMinSamples != null) { options._steerMinSamples = opts.SteerMinSamples; }
            if (opts.SteerPrecisionFloorMs != null) { options._steerPrecisionFloorMs = opts.SteerPrecisionFloorMs; }
			if (opts.OnSync != null) {
				options._onSyncCallback = opts.OnSync;
			}
			if (opts.WhenSynced != null) {
				options._firstSyncCallback = opts.WhenSynced;
			}
			return _setupSync();
		},

		// Callbacks
		wsSend: function(callback) {
			return options._wsCall = callback;
		},

		wsReceived: function(serverTimeString) {
			var responseTime, sample, serverTime;
			responseTime = Date.now();
			serverTime = _dateFromService(serverTimeString);
			sample = _calculateOffset(options._wsRequestTime, responseTime, serverTime);
			return _reviseOffset(sample, "websocket");
		},

		// Server broadcast clock beat: {REQUEST:"TIMEBEAT", PAYLOAD:{t:<serverMs>}}.
		// Message routing that calls this is a separate task; this just consumes
		// the server-send timestamp once handed to it. recvTs is OPTIONAL: a native
		// receipt stamp (epoch-ms) from mmws' off-main I/O thread, free of
		// main-thread scheduling jitter. When absent/implausible, falls back to the
		// main-thread clock read (the original one-arg behavior is unchanged).
		beatReceived: function (serverSendMs, recvTs) {
			var nowTs = getAccurateTimestamp();   // SAME clock fn now() uses
			var localRecv = _pickRecv(recvTs, nowTs, options._beatRecvTolMs);
			options._lastBeatMs = nowTs;   // freshness/failsafe uses the local read, not recvTs
			var sample = _beatSample(serverSendMs, localRecv, options._beatPrecisionMs);
			return _reviseOffset(sample, "beat");
		},

		// Force fresh clock samples now (used on PREPARE so the offset isn't
		// up-to-15-min stale). Fires n _sync() calls spaced spacingMs apart.
		// Under window._mmClockBeat these _sync calls suppress (see _sync) while a
		// beat is fresh -- PREPARE then relies on the ~2s beat cadence to keep the
		// offset fresh rather than this /time burst. Intended, not a regression.
		resync: function(n, spacingMs) {
			n = n || 4; spacingMs = spacingMs || 400;
			for (var i = 0; i < n; i++) { setTimeout(_sync, i * spacingMs); }
		},

		// Age (ms) of the currently-locked offset sample; Infinity if none yet.
		msSinceAccept: function() {
			return (options._lastAcceptTime == null) ? Infinity : (GoTime.now() - options._lastAcceptTime);
		},

		// PURE clock-ready decision (no closure state) so it is unit-testable:
		// offset must be fresh (accAgeMs) AND precise (precisionMs) AND the beat
		// stable (phaseStd) AND centered (phaseMean). Thresholds overridable.
		readyVerdict: function(precisionMs, accAgeMs, phaseStd, phaseMean, opts) {
			opts = opts || {};
			var maxPrec = (opts.maxPrecisionMs != null) ? opts.maxPrecisionMs : 50;
			var maxAge  = (opts.maxAgeMs       != null) ? opts.maxAgeMs       : 30000;
			var maxStd  = (opts.maxStdMs       != null) ? opts.maxStdMs       : 10;
			var maxMean = (opts.maxMeanMs      != null) ? opts.maxMeanMs      : 20;
			return (precisionMs <= maxPrec) && (accAgeMs <= maxAge) &&
			       (phaseStd <= maxStd) && (Math.abs(phaseMean) <= maxMean);
		},

		getSteerState: function() {
            return { steering: options._steering, samples: options._steerSamples.length,
                     offset: options._offset, target: options._lastSteerTarget,
                     skew: options._skew };
        },

		// DIAGNOSTIC (?tdbg): why is steering engaged or not? Distinguishes
		// "samples not arriving" (recent==0 -> cadence too sparse) from "samples
		// arriving but filtered" (recent>0, kept<minS -> precision gate too tight
		// / RTT too high). Mirrors _robustTarget's window+gate math read-only.
		steerDebug: function() {
			var nowMs = getAccurateTimestamp() + options._offset;
			var win = (options._steerWindowMs != null) ? options._steerWindowMs : 120000;
			var floor = (options._steerPrecisionFloorMs != null) ? options._steerPrecisionFloorMs : 60;
			var minS = (options._steerMinSamples != null) ? options._steerMinSamples : 3;
			var s = options._steerSamples || [], i, recent = [];
			for (i = 0; i < s.length; i++) { if (s[i].t >= nowMs - win) { recent.push(s[i]); } }
			var best = null;
			for (i = 0; i < recent.length; i++) { if (best === null || recent[i].precision < best) { best = recent[i].precision; } }
			var gate = (best === null) ? null : Math.max(2 * best, floor);
			var kept = 0;
			if (gate !== null) { for (i = 0; i < recent.length; i++) { if (recent[i].precision <= gate) { kept++; } } }
			return { n: s.length, recent: recent.length, kept: kept,
			         best: (best === null ? null : Math.round(best)),
			         gate: (gate === null ? null : Math.round(gate)), minS: minS, win: win,
			         steering: !!options._steering, syncCount: options._syncCount,
			         interval: options._syncInterval };
		},

		steerTick: function() { return _steerTick(); },
		_steerStep: _steerStep,
		_robustTarget: _robustTarget,
		_skewFit: _skewFit,
		_applySkew: _applySkew,
		_deskewSamples: _deskewSamples,
		_steerTick: function() { return _steerTick(); },
		_reviseOffset: function(sample, method) { return _reviseOffset(sample, method); },
		_applyModelForTest: function(offset, skew, ref) {
			options._offset = offset; options._skew = skew; options._skewRef = ref;
		},
		_nextSyncDelay: _nextSyncDelay,
		_beatSample: _beatSample,
		_beatSuppressesPoll: _beatSuppressesPoll,
		_pickRecv: _pickRecv
	}

})();