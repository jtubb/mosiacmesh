import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args
class _MockArgs:
    Port = 3000
    Verbose = False
argparse.ArgumentParser.parse_args = lambda self, a=None, n=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig
import pytest
from mosaicmesh import render as R


def test_pull_url_for_seg_key_segment():
    assert R.pull_url_for_seg_key("ck1", "abc123_0") == "/media/ck1/videos/seg_abc123_0.mp4"

def test_pull_url_for_seg_key_full():
    assert R.pull_url_for_seg_key("ck1", "full_abc123_2") == "/media/server/videos/full_abc123_2.mp4"

def test_pull_url_for_seg_key_reexported_on_server():
    assert server.pull_url_for_seg_key("ck1", "abc123_0") == "/media/ck1/videos/seg_abc123_0.mp4"
