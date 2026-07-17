"""meshCells is sent ONCE at the payload level, not per item — a per-item copy
overflowed the mmws 128KB ws rx buffer and regressed the fleet to XHR."""
import types
import numpy as np
import pytest
import server
from mosaicmesh import render, calibration
from mosaicmesh.state import Display, Client, MediaElement, PlayMode


def _mesh_display_and_client():
    d = Display()
    d.displayID = "G"
    d.boundingBox = [0, 0, 1000, 800]
    d.meshGlobal = [1000, 800]
    d.meshGlobalRect = [1000, 800]
    d.meshGrid = [2, 2]
    d.meshCells = [[[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]],
                   [[0.5, 0.0], [1.0, 0.0], [1.0, 0.5], [0.5, 0.5]]]
    # three mesh SCRIPT items
    els = []
    for i in range(3):
        me = MediaElement()
        me.id = "i%d" % i
        me.file = "bouncingBalls"
        me.playmode = PlayMode.SCRIPT
        me.scriptSpan = "mesh"
        me.duration = 10
        els.append(me)
    d.mediaElements = els
    c = Client()
    c.displayID = "G"
    c.measuredPerimeter = np.array([[0, 0], [500, 0], [500, 400], [0, 400]])
    c.meshCellQuad = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
    return d, c


def test_per_client_items_no_meshcells(monkeypatch):
    monkeypatch.setattr(calibration, "MESH_RECTIFY", True)
    d, c = _mesh_display_and_client()
    items = render._per_client_items(d, "k", c)
    assert len(items) == 3
    for it in items:
        assert "meshCells" not in it, "meshCells must NOT be attached per-item"
        # small per-item mesh fields stay
        assert "meshGlobal" in it and "meshQuad" in it


def test_mesh_cells_payload_is_fresh_grid():
    d, _ = _mesh_display_and_client()
    mc = render._mesh_cells_payload(d)
    assert mc == [[[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]],
                  [[0.5, 0.0], [1.0, 0.0], [1.0, 0.5], [0.5, 0.5]]]
    assert mc is not d.meshCells  # fresh list, not the stored ref


def test_mesh_cells_payload_none_without_grid():
    d = Display(); d.meshCells = None
    assert render._mesh_cells_payload(d) is None


def test_payload_size_is_O_one_grid(monkeypatch):
    """The grid appears ONCE regardless of item count (guards against regression)."""
    import json
    monkeypatch.setattr(calibration, "MESH_RECTIFY", True)
    d, c = _mesh_display_and_client()
    items = render._per_client_items(d, "k", c)
    grid = render._mesh_cells_payload(d)
    payload = {"items": items, "meshCells": grid}
    blob = json.dumps(payload)
    # the grid's own JSON appears exactly once in the payload -- not once per item
    # (the old per-item attach would have made this 3). NOTE: counting raw "0.5"
    # substrings is unreliable here because the retained per-item meshQuad fields
    # legitimately reuse some of the same coordinate values as the mesh grid.
    assert blob.count(json.dumps(grid)) == 1
    assert json.dumps(items).count("meshCells") == 0
