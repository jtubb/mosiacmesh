// Read-only: enumerate the WiFi device's property dictionary to find the
// power-save key. Only Create/Copy/Get calls -- nothing is set. -> /tmp/cy.out
// CF refs declared as `id` (toll-free) so no C casts are needed.
var out = "";
function L(s){ out += s + "\n"; }

dlopen("/System/Library/PrivateFrameworks/MobileWiFi.framework/MobileWiFi", 2);

extern "C" id WiFiManagerClientCreate(void *, int);
extern "C" id WiFiManagerClientCopyDevices(id);
extern "C" id WiFiDeviceClientCopyProperty(id, id);
extern "C" id WiFiDeviceClientGetInterfaceName(id);

try {
  var mgr = WiFiManagerClientCreate(0, 0);
  L("mgr = " + (mgr ? "ok" : "nil"));
  var devs = WiFiManagerClientCopyDevices(mgr);
  L("devices = " + (devs ? [devs count] : "nil"));
  var dev = (devs && [devs count] > 0) ? [devs objectAtIndex:0] : null;
  if (dev) {
    var ifn = WiFiDeviceClientGetInterfaceName(dev);
    L("ifname = " + (ifn ? ("" + ifn) : "nil"));
    var props = WiFiDeviceClientCopyProperty(dev, null);
    if (props && [props isKindOfClass:[NSDictionary class]]) {
      L("ALLKEYS = " + [[props allKeys] description]);
      L("FULL = " + [props description]);
    } else {
      L("CopyProperty(null) not a dict (" + (props ? [props description] : "nil") + "); probing candidates:");
      var cand = ["RequestedPower","PowerEnabled","Power","PowerSavingsEnabled",
                  "WiFiPowerSavingsEnabled","PowerSaveEnabled","CurrentPower",
                  "WoWEnabled","ManagedNetworkPower","ScanThrottleEnabled"];
      for (var i = 0; i < cand.length; i++) {
        var v = WiFiDeviceClientCopyProperty(dev, cand[i]);
        L("  " + cand[i] + " = " + (v ? ("" + [v description]) : "nil"));
      }
    }
  }
} catch (e) {
  L("EXC: " + e);
}

[out writeToFile:@"/tmp/cy.out" atomically:NO encoding:4 error:NULL];
