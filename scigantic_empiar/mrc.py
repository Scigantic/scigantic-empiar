"""MRC/MRCS parsing, file discovery, and NumPy array readers (no plotting)."""
from __future__ import annotations
import os
import struct

import numpy as np

from .reader import entry_url, fast_path, list_files, pread

MRC_EXT = (".mrcs", ".mrc", ".st", ".ali", ".rec", ".mrc.bz2")
# MRC mode -> numpy dtype (the common cryo-EM subset).
MODE_DTYPE = {0: np.int8, 1: np.int16, 2: np.float32, 6: np.uint16, 12: np.float16}


def parse_mrc_header(b: bytes) -> dict:
    """Parse a 1024-byte MRC header. Returns dims, dtype, extended-header size,
    pixel size (Angstrom), and the byte offset where image data begins."""
    nx, ny, nz, mode = struct.unpack("<4i", b[:16])
    nsymbt = struct.unpack("<i", b[92:96])[0]
    mx, my, mz = struct.unpack("<3i", b[28:40])   # grid
    xlen, ylen, zlen = struct.unpack("<3f", b[40:52])  # cell (A)
    apix = (xlen / mx) if mx else 0.0
    dt = np.dtype(MODE_DTYPE.get(mode, np.float32))
    return dict(
        nx=nx, ny=ny, nz=nz, mode=mode, nsymbt=nsymbt, dtype=dt,
        apix=round(apix, 3), frame_bytes=nx * ny * dt.itemsize,
        data0=1024 + nsymbt,
    )


def find_mrc(entry_id, subdir="data", depth=2):
    """First MRC-like file under an entry — checks ``data/`` then up to two
    subdir levels (tomo tilt-series / particle stacks / per-session dirs nest
    their MRCs a couple levels down). Returns a subdir-relative path, or None."""
    entries = list_files(entry_id, subdir)
    hits = [f for f in entries if f.lower().endswith(MRC_EXT)]
    if hits:
        return f"{subdir}/{hits[0]}"
    if depth > 0:
        subs = [f.rstrip("/") for f in entries if "." not in f.rstrip("/")]  # dirs
        for s in subs[:8]:
            r = find_mrc(entry_id, f"{subdir}/{s}", depth - 1)
            if r:
                return r
    return None


def read_mrc(entry_id, filename=None, subdir="data"):
    """Resolve an entry+file to ``(url, filename, header)``, reading only the
    header. ``filename`` may be a path relative to the entry root; if omitted,
    the first MRC found (recursing subdirs) is used."""
    if filename is None:
        rel = find_mrc(entry_id, subdir)
        if not rel:
            raise FileNotFoundError(f"no MRC files under EMPIAR-{entry_id}/{subdir}")
        parts = rel.split("/")
        subdir, filename = "/".join(parts[:-1]), parts[-1]
    fp = fast_path(entry_id)
    url = os.path.join(fp, subdir, filename) if fp else entry_url(entry_id, subdir, filename)
    return url, filename, parse_mrc_header(pread(url, 0, 1024, 1))


def read_mrc_frame(entry_id, filename=None, frame=0, nthreads=8):
    """One 2D frame/slice as a float32 array (+ header)."""
    url, fn, h = read_mrc(entry_id, filename)
    off = h["data0"] + int(frame) * h["frame_bytes"]
    buf = pread(url, off, h["frame_bytes"], nthreads)
    arr = np.frombuffer(buf, dtype=h["dtype"]).astype(np.float32).reshape(h["ny"], h["nx"])
    h["file"] = fn
    return arr, h


def read_mrc_average(entry_id, filename=None, n_frames=4, nthreads=8):
    """Average the first ``n_frames`` — a poor-man's motion-corrected image
    (much cleaner than one raw frame)."""
    url, fn, h = read_mrc(entry_id, filename)
    n = max(1, min(n_frames, h["nz"] or 1))
    buf = pread(url, h["data0"], n * h["frame_bytes"], nthreads)
    stack = np.frombuffer(buf, dtype=h["dtype"]).astype(np.float32).reshape(n, h["ny"], h["nx"])
    h["file"] = fn
    h["n_averaged"] = n
    return stack.mean(0), h


def thumbnail(entry_id, filename=None, size=320, nthreads=8):
    """A small uint8 preview (central strip of the first MRC), reading only ~a
    few MB — used to build catalogs. Returns (uint8 array, header)."""
    url, fn, h = read_mrc(entry_id, filename)
    rows = min(h["ny"], max(size, 256))
    row0 = max(0, (h["ny"] - rows) // 2)
    off = h["data0"] + row0 * h["nx"] * h["dtype"].itemsize
    buf = pread(url, off, rows * h["nx"] * h["dtype"].itemsize, nthreads)
    band = np.frombuffer(buf, dtype=h["dtype"]).astype(np.float32).reshape(rows, h["nx"])
    f = max(1, max(band.shape) // size)
    small = band[::f, ::f]
    lo, hi = np.percentile(small, [2, 98])
    small = np.clip((small - lo) / (hi - lo + 1e-9), 0, 1)
    h["file"] = fn
    return (small * 255).astype(np.uint8), h


def _hann2d(shape):
    return np.outer(np.hanning(shape[0]), np.hanning(shape[1]))


def power_spectrum(arr, bin_to=1024):
    """Windowed, log-scaled, [0,1]-normalised power spectrum (Thon rings)."""
    f = max(1, min(arr.shape) // bin_to)
    a = arr[::f, ::f].astype(np.float32)
    a = (a - a.mean()) / (a.std() + 1e-6)
    a = a * _hann2d(a.shape)
    ps = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(a))))
    lo, hi = np.percentile(ps, [1, 99.5])
    return np.clip((ps - lo) / (hi - lo + 1e-9), 0, 1)


def downsample(a, target=900):
    f = max(1, min(a.shape) // target)
    return a[::f, ::f]
