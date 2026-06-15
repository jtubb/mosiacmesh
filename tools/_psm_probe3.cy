// Read-only: locate the Apple80211 C API (the direct PSM ioctl path).
// dlopen candidate frameworks, dlsym the key symbols on each handle. -> /tmp/cy.out
var out = "";
function L(s){ out += s + "\n"; }

var paths = [
  "/System/Library/PrivateFrameworks/Apple80211.framework/Apple80211",
  "/System/Library/PrivateFrameworks/IO80211.framework/IO80211",
  "/System/Library/PrivateFrameworks/MobileWiFi.framework/MobileWiFi"
];
var syms = ["Apple80211Open","Apple80211Close","Apple80211BindToInterface",
            "Apple80211GetIntValue","Apple80211SetIntValue","Apple80211GetInfoCopy"];

for (var i = 0; i < paths.length; i++) {
  var h = dlopen(paths[i], 2);
  L("dlopen " + paths[i] + " = " + (h ? "Y" : "n"));
  if (h) {
    for (var j = 0; j < syms.length; j++) {
      var p = null;
      try { p = dlsym(h, syms[j]); } catch (e) { p = null; }
      if (p) L("   " + syms[j] + " = Y");
    }
  }
}

[out writeToFile:@"/tmp/cy.out" atomically:NO encoding:4 error:NULL];
