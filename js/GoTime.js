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
        _precision: 2e308,
        _history: [],
        _syncInitialTimeouts: [0, 3000, 9000, 18000, 45000],
        _syncInterval: 900000,
        _synchronizing: false,
        _lastSyncTime: null,
        _lastSyncMethod: null,
        _ajaxURL: null,
        _ajaxSampleSize: 1,
        _firstSyncCallbackRan: false,
        _firstSyncCallback: null,
        _onSyncCallback: null,
        _wsCall: null,
        _wsRequestTime: null
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
        var success;
		if (options._wsCall != null) {
			options._wsRequestTime = Date.now();
			success = options._wsCall();
			if (success) {
				options._syncCount++;
				return;
			}
		}
		if (options._ajaxURL != null) {
			success = _ajaxSample();
			if (success) {
				options._syncCount++;
			}
		}
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

    _reviseOffset = function(sample, method) {
        var timestamp;
        if (isNaN(sample.offset) || isNaN(sample.precision)) {
            return;
        }
        timestamp = GoTime.now();
        options._lastSyncTime = timestamp;
        options._lastSyncMethod = method;
        // Add to history
        /*options._history.push({
            Sample: sample,
            Method: method,
            Time: timestamp
        });*/
        // Only update the offset if the precision is improved
        if (sample.precision <= options._precision) {
            options._offset = Math.round(sample.offset);
            options._precision = sample.precision;
        }
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
        var i, len, ref, time;
        if (options._synchronizing === false) {
            options._synchronizing = true;
            ref = options._syncInitialTimeouts;
            for (i = 0, len = ref.length; i < len; i++) {
                time = ref[i];
                // Initial syncs
                setTimeout(_sync, time);
            }
            // Sync repetitively
            setInterval(_sync, options._syncInterval);
        }
    };

	return {
		// Public Getters
		now: function() {
			return getAccurateTimestamp() + options._offset;
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
			if (opts.SyncInitialTimeouts != null) {
				options._syncInitialTimeouts = opts.SyncInitialTimeouts;
			}
			if (opts.SyncInterval != null) {
				options._syncInterval = opts.SyncInterval;
			}
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
	}
    
})();