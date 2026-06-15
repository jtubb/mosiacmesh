// Read-only cycript probe: discover which WiFi power-save symbols exist in
// MobileWiFi on this iOS 5.1.1 device. Writes a report to /tmp/cy.out.
// Changes NOTHING (only dlopen/dlsym lookups + a getter attempt).
var out = "";
function line(s){ out += s + "\n"; }

var h = dlopen("/System/Library/PrivateFrameworks/MobileWiFi.framework/MobileWiFi", 2);
line("dlopen MobileWiFi = " + (h ? "Y" : "n"));

var syms = [
  "Apple80211Open", "Apple80211Close", "Apple80211BindToInterface",
  "Apple80211GetIntValue", "Apple80211SetIntValue",
  "Apple80211GetPowerSavingMode", "Apple80211SetPowerSavingMode",
  "Apple80211CopyValue", "Apple80211GetInfoCopy",
  "WiFiManagerClientCreate", "WiFiManagerClientCopyDevices",
  "WiFiDeviceClientCopyProperty", "WiFiDeviceClientSetProperty",
  "WiFiDeviceClientGetInterfaceName"
];
for (var i = 0; i < syms.length; i++) {
  var p = null;
  try { p = dlsym(h, syms[i]); } catch (e) { p = null; }
  line(syms[i] + " = " + (p ? "Y" : "n"));
}

[out writeToFile:@"/tmp/cy.out" atomically:NO encoding:4 error:NULL];
