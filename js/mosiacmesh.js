var udid;

// Cache DOM selectors for better performance
var domCache = {};
function getCachedElement(selector) {
    if (!domCache[selector]) {
        domCache[selector] = $(selector);
    }
    return domCache[selector];
}

// Cookie cache for better performance  
var cookieCache = null;
var cookieCacheTime = 0;
var COOKIE_CACHE_TTL = 5000; // 5 seconds

function getset_cookie(cname, cvalue, days)
{
	if (typeof days === "undefined" || days === null) {
		days = 365
	}
 
	// Use cached cookie parsing for better performance
	var now = Date.now();
	if (!cookieCache || (now - cookieCacheTime) > COOKIE_CACHE_TTL) {
		cookieCache = {};
		var decodedCookie = decodeURIComponent(document.cookie);
		var ca = decodedCookie.split(';');
		for(var i = 0; i < ca.length; i++) {
			var c = ca[i].trim(); // Use trim() instead of manual loop
			var equalIndex = c.indexOf('=');
			if (equalIndex > 0) {
				var key = c.substring(0, equalIndex);
				var value = c.substring(equalIndex + 1);
				cookieCache[key] = value;
			}
		}
		cookieCacheTime = now;
	}
	
	// Check if cookie exists in cache
	if (cookieCache[cname]) {
		return cookieCache[cname];
	}
	
	// Set new cookie and update cache
	const d = new Date();
	d.setTime(d.getTime() + (365*24*60*60*1000));
	var expires = "expires="+ d.toUTCString();
	document.cookie = cname + "=" + cvalue + ";" + expires + ";path=/;SameSite=Strict";
	
	// Update cache with new value
	if (cookieCache) {
		cookieCache[cname] = cvalue;
	}
	
	return cvalue;
}

function getUDID() {
	if(udid==null)
	{
		udid = Math.random().toString(36).slice(2);
	}
	udid = getset_cookie("clientID",udid);
	return udid;
}


	var sock = null;    
	
	getUDID();

	function log(msg) {
		var logElement = getCachedElement('#log');
		if(logElement.length > 0)
		{
			// Use array join for better string concatenation performance
			var currentContent = logElement.html();
			logElement.html(currentContent + msg + '<br/>');
			logElement.scrollTop(logElement.scrollTop() + 1000);
		}
		else
		{
			console.log(msg);
		}
	}

	function mosiacMeshConnect(callback) {
		sock_callback = callback
		mosiacMeshDisconnect();

		sock = new SockJS('http://' + window.location.host + '/sockjs/', [], {
			debug: true,
			transports: [ "websocket", "xhr-streaming", "iframe-eventsource", "iframe-htmlfile", "xhr-polling", "iframe-xhr-polling", "jsonp-polling" ]
		});

		log('connecting...');

		sock.onopen = function() {
			log('connected.');
			sock.send(generateMessage("SRV","REGISTER",{"width": screen.width, "height": screen.height}));
			update_ui();
		};

		sock.onmessage = function(msg) {
			log('Received: ' + msg.data);
			data_obj = JSON.parse(msg.data.replace("'",""));
			if(data_obj.REQUEST == "SERVERTIME")
			{
				GoTime.wsReceived(data_obj.PAYLOAD);
			}
			if(data_obj.REQUEST == "RELOAD")
		    {
				location.reload(true);
			}
			if(sock_callback != null)
			{
				sock_callback(data_obj);
			}
		};

		sock.onclose = function() {
			log('Disconnected.');
			sock = null;
			update_ui();
			//ProgrammableTimer.stop();
		};
	}

	// Set options before first GoTime use
	GoTime.setOptions({
		AjaxURL: "/time",
		WhenSynced: updateData, // Is called for the first sync
		OnSync: goTimeSync, // Calls on ever sync starting with the second sync
		SyncInitialTimeouts: [500, 3000, 9000, 15000],
		SyncInterval: 900000 // Set this often for demo purposes only
	});


	GoTime.wsSend(function() {
		if (sock !== null && sock.readyState === SockJS.OPEN) {
			sock.send(generateMessage("SRV","SERVERTIME","null"));
		return true
	}
	return false
	});

	function mosiacMeshDisconnect() {
		if (sock != null) {
			log('Disconnecting...');

			sock.close();
			sock = null;

			update_ui();
		}
	}

	function update_ui() {
		var statusElement = getCachedElement('#status');
		var connectElement = getCachedElement('#connect');

		if (sock == null || sock.readyState != SockJS.OPEN) {
			statusElement.text('disconnected');
			connectElement.text('connect');
		} else {
			statusElement.text('connected (' + sock.protocol + ')');
			connectElement.text('Disconnect');
		}
	}

	$('#fullscreen').click(function() {
		fullScreen();
	});
	
	$('#connect').click(function() {
		if (sock == null) {
			connect();
		} else {
			disconnect();
		}

		update_ui();
		return false;
	});

	function appendHistory(t, method, offset, precision) {
		var syncTable = getCachedElement('#sync-table');
		var rowHtml = "<tr><td>" + (new Date(t)).toLocaleTimeString() + "</td><td>" + method + "</td><td>" + offset + "ms</td><td>" + precision + "ms</td></tr>";
		syncTable.html(rowHtml);
	}

	function updateData(t, method, off, precision) {
		var localElement = getCachedElement('#local');
		var serverElement = getCachedElement('#server');
		var offsetElement = getCachedElement('#offset');
		var precisionElement = getCachedElement('#precision');
		
		localElement.text(getAccurateTimestamp());
		serverElement.text(GoTime.now());
		offsetElement.text(GoTime.getOffset());
		precisionElement.text(GoTime.getPrecision());

		appendHistory(t, method, off, precision);
	}
	
	function goTimeSync(t, method, off, precision) {
		ProgrammableTimer.target(Math.round(GoTime.now()/1000)*1000); // Start on the even second
		ProgrammableTimer.tick();
		
		var localElement = getCachedElement('#local');
		var serverElement = getCachedElement('#server');
		var offsetElement = getCachedElement('#offset');
		var precisionElement = getCachedElement('#precision');
		
		localElement.text(getAccurateTimestamp());
		serverElement.text(GoTime.now());
		offsetElement.text(GoTime.getOffset());
		precisionElement.text(GoTime.getPrecision());
	}

	function generateMessage(dest, request, payload)
	{
		message = {"SRC": udid, "DEST": dest, "REQUEST": request, "PAYLOAD":payload}
		return JSON.stringify(message);
	}
	
	//connect();


function isFullScreen() { 
  return Boolean(
	document.fullscreenElement ||
	document.webkitFullscreenElement ||
	document.mozFullScreenElement ||
	document.msFullscreenElement
  );
}

function fullScreen(el) { 
  // Use a guard clause to exit out of the function immediately
  if (isFullScreen()) return false;
  // Set a default value for your element parameter
  if (el === undefined) el = document.documentElement; 
  // Test for the existence of document.fullscreenEnabled instead of requestFullscreen()
  if (document.fullscreenEnabled) { 
	el.requestFullscreen();
  } else if (document.webkitFullscreenEnabled) {
	el.webkitRequestFullscreen();
  } else if (document.mozFullScreenEnabled) {
	el.mozRequestFullScreen();
  } else if (document.msFullscreenEnabled) {
	el.msRequestFullscreen();
  }
}