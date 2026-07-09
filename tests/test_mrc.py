"""mrc.py — header parsing, file discovery, and NumPy readers."""
import numpy as np

from conftest import make_mrc_header

import scigantic_empiar as se
from scigantic_empiar import mrc


def test_parse_mrc_header():
    h = se.parse_mrc_header(make_mrc_header(nx=4, ny=3, nz=5, mode=2, mx=200, xlen=210.0))
    assert (h["nx"], h["ny"], h["nz"], h["mode"]) == (4, 3, 5, 2)
    assert h["dtype"] == np.float32
    assert h["apix"] == 1.05
    assert h["frame_bytes"] == 4 * 3 * 4          # nx*ny*itemsize
    assert h["data0"] == 1024                     # nsymbt == 0


def test_parse_mrc_header_extended_offset():
    h = se.parse_mrc_header(make_mrc_header(mode=1, nsymbt=512))
    assert h["dtype"] == np.int16                 # mode 1
    assert h["data0"] == 1024 + 512               # extended header shifts data start


def test_find_mrc_recurses_subdirs(monkeypatch):
    listings = {
        "data": ["notes.txt", "micrographs/"],           # no MRC at top level
        "data/micrographs": ["log.txt", "stack_0001.mrc"],  # found one level down
    }
    monkeypatch.setattr(mrc, "list_files", lambda eid, subdir="data": listings[subdir])
    assert se.find_mrc(10406) == "data/micrographs/stack_0001.mrc"


def test_find_mrc_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(mrc, "list_files", lambda eid, subdir="data": ["readme.txt"])
    assert se.find_mrc(1) is None


def test_read_mrc_frame_shapes_and_values(monkeypatch):
    header = make_mrc_header(nx=4, ny=3, nz=5, mode=2)

    def fake_pread(url, off, length, nthreads=8):
        if length == 1024:
            return header                                   # header read
        return np.arange(length // 4, dtype=np.float32).tobytes()  # frame read

    monkeypatch.setattr(mrc, "pread", fake_pread)
    monkeypatch.setattr(mrc, "fast_path", lambda eid: None)

    arr, h = se.read_mrc_frame(10002, filename="m.mrc", frame=0)
    assert arr.shape == (3, 4)                               # (ny, nx)
    assert arr.dtype == np.float32
    assert np.allclose(arr.ravel(), np.arange(12))
    assert h["file"] == "m.mrc"


def test_power_spectrum_and_downsample():
    g = np.outer(np.sin(np.linspace(0, 12, 128)), np.sin(np.linspace(0, 9, 128)))
    ps = se.power_spectrum(g.astype(np.float32))
    assert ps.shape == (128, 128)
    assert ps.min() >= 0.0 and ps.max() <= 1.0              # normalised to [0,1]

    ds = mrc.downsample(g, target=64)
    assert ds.shape == (64, 64)                             # stride-2 decimation
