import types
from mosaicmesh.websocket.legacy import client_warmable

def _c(**kw):
    c = types.SimpleNamespace(deviceBrand="", deviceType="", osName="", osVersion="", engine="")
    for k, v in kw.items(): setattr(c, k, v)
    return c

def test_legacy_ipad_not_warmable():
    # iOS 5 Safari 5 / old WebKit => the transplant can't double-decode
    assert client_warmable(_c(deviceType="tablet", osName="iOS", osVersion="5.1", engine="WebKit")) is False

def test_modern_ipad_warmable():
    assert client_warmable(_c(deviceType="tablet", osName="iOS", osVersion="15.0", engine="WebKit")) is True

def test_desktop_warmable():
    assert client_warmable(_c(deviceType="desktop", osName="Windows", osVersion="10", engine="Blink")) is True

def test_missing_fields_default_warmable():
    # unknown device => assume modern (warmable); the client no-ops safely if it can't
    assert client_warmable(_c()) is True
