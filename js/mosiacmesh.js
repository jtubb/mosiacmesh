var udid;

function getset_cookie(cname, cvalue, days)
{
	if (typeof days === "undefined" || days === null) {
		days = 365
	}
 
    // If cookie exists return it's value
    var name = cname + "=";
    var decodedCookie = decodeURIComponent(document.cookie);
    var ca = decodedCookie.split(';');
    for(var i = 0; i <ca.length; i++) {
        var c = ca[i];
        while (c.charAt(0) == ' ') {
            c = c.substring(1);
        }
        if (c.indexOf(name) == 0) {
            return c.substring(name.length, c.length);
        }
    }
    
    const d = new Date();
    d.setTime(d.getTime() + (365*24*60*60*1000));
    var expires = "expires="+ d.toUTCString();
    document.cookie = cname + "=" + cvalue + ";" + expires + ";path=/;SameSite=Strict";
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
        var control = $('#log');
        control.html(control.html() + msg + '<br/>');
        control.scrollTop(control.scrollTop() + 1000);
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
            if(data_obj.DEST == udid)
            {
                if(data_obj.REQUEST == "TIME")
                {
                    GoTime.wsReceived(data_obj.PAYLOAD);
                }
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
            sock.send(generateMessage("SRV","TIME","null"));
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
        var msg = '';

        if (sock == null || sock.readyState != SockJS.OPEN) {
            $('#status').text('disconnected');
            $('#connect').text('connect');
        } else {
            $('#status').text('connected (' + sock.protocol + ')');
            $('#connect').text('Disconnect');
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
        $('#sync-table').html("<tr><td>" + (new Date(t)).toLocaleTimeString() + "</td><td>" + method + "</td><td>" + offset + "ms</td><td>" + precision + "ms</td></tr>")
    }

    function updateData(t, method, off, precision) {
        $('#local').text(getAccurateTimestamp());
        $('#server').text(GoTime.now());

        $('#offset').text(GoTime.getOffset());
        $('#precision').text(GoTime.getPrecision());

        appendHistory(t, method, off, precision);
    }
	
	function goTimeSync(t, method, off, precision) {
		ProgrammableTimer.target(Math.round(GoTime.now()/1000)*1000); // Start on the even second
		ProgrammableTimer.tick();
        $('#local').text(getAccurateTimestamp());
        $('#server').text(GoTime.now());

        $('#offset').text(GoTime.getOffset());
        $('#precision').text(GoTime.getPrecision());
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