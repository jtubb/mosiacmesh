"""Always-encode-for-device: fit helper, transcode cmd, FULL render path."""
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


def test_fit_within_downscales_keeping_aspect():
    assert R._fit_within(1920, 1080, (1280, 720)) == (1280, 720)

def test_fit_within_portrait():
    w, h = R._fit_within(1080, 1920, (1280, 720))
    assert h == 720 and w % 2 == 0 and w <= 1280 and abs(w/h - 1080/1920) < 0.02

def test_fit_within_no_upscale():
    assert R._fit_within(640, 480, (1280, 720)) == (640, 480)

def test_fit_within_even_dims():
    w, h = R._fit_within(1001, 333, (1280, 720))
    assert w % 2 == 0 and h % 2 == 0
