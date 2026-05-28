import sys, time
from pathlib import Path
import argparse
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig


def test_prepare_state_and_constants_exist():
    assert hasattr(server.PlayState, "PREPARING")
    assert server.RELEASE_LEAD_MS > 0
    assert server.PREPARE_TIMEOUT_MS > 0
    d = server.Display()
    assert d.prepareId is None
    assert d.readyClients == set()
    assert d.prepareDeadline == 0
