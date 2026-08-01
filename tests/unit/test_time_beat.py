import json
import server
from mosaicmesh.broadcast import broadcast_all


class _MockManager:
    def __init__(self):
        self.broadcasts = []
    def broadcast(self, encoded):
        self.broadcasts.append(encoded)


def test_broadcast_all_encodes_once_and_broadcasts(monkeypatch):
    mgr = _MockManager()
    monkeypatch.setattr(server, "socketmanager", mgr, raising=False)
    broadcast_all({"REQUEST": "TIMEBEAT", "PAYLOAD": {"t": 1234567890}})
    assert len(mgr.broadcasts) == 1
    decoded = json.loads(mgr.broadcasts[0])
    assert decoded["REQUEST"] == "TIMEBEAT"
    assert decoded["PAYLOAD"]["t"] == 1234567890


def test_broadcast_all_noop_without_manager(monkeypatch):
    monkeypatch.setattr(server, "socketmanager", None, raising=False)
    broadcast_all({"REQUEST": "TIMEBEAT", "PAYLOAD": {"t": 1}})  # must not raise
