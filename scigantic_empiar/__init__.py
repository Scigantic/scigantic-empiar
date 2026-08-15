"""scigantic_empiar — explore the EMPIAR archive from a Scigantic notebook.

The whole archive (~3,000 entries / 8.9 PiB) is lazily mounted at
``$SCIGANTIC_MOUNT_PATH`` (``/mnt/http-archive/data``) over EBI's public
autoindex. That mount is a single stream (~1.5 MB/s from us-east-1), so for
*previews* we read the same bytes over parallel HTTP range requests directly
from EBI, which aggregates to ~5-10 MB/s. 8-way is a sensible default, but not
because EBI throttles past it: measured, stalls occur at 1, 8 and 12 readers
alike and 16-way completed fine. The real behaviour is that ~3% of requests hang
rather than slow down, so a SHORT timeout matters more than the thread count
(12s beat 60s by 2.5x on wall clock). Heavy/repeated compute should use a
mirrored entry in S3
(``add_to_fast_workspace``) — 1.5 MB/s is fine to *look*, not to reprocess a
260 GB set.

Two tiers that work today, one API:
  * catalog / search  — metadata across ALL entries (``EmpiarCatalog``)
  * live preview       — pull a frame from ANY entry in seconds (``preview``)

A third tier, mirroring an entry to S3 for full-speed compute, is designed but
NOT implemented: see ``add_to_fast_workspace``. Moving 100 GB - 6 TB out of
EMPIAR belongs on Globus (their preferred bulk channel), not on the HTTPS
endpoint they document for datasets under four gigabytes.

Nothing here is a locked widget; every function is a few lines you can read,
copy, and bend.
"""
from __future__ import annotations
import os, io, struct, math, functools
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from scigantic_headers import decode_mrc_header

from ._search import expand_query, match_score, passes_filters, field_text as _field_text

__version__ = "0.3.0"

__all__ = [
    "MOUNT", "entry_url", "pread", "list_files", "read_mrc",
    "read_mrc_frame", "read_mrc_average", "power_spectrum", "preview", "thumbnail",
    "EmpiarClient", "EmpiarCatalog", "add_to_fast_workspace", "fast_path",
    "expand_query", "match_score",
    "__version__",
]

# ── locations ──────────────────────────────────────────────────────────────
MOUNT = os.environ.get("SCIGANTIC_MOUNT_PATH", "/mnt/http-archive/data")
EBI = "https://ftp.ebi.ac.uk/empiar/world_availability"
API = "https://www.ebi.ac.uk/empiar/api/entry"
# Catalog index (per-entry metadata + thumbnail) produced by the onboarding
# batch job; overridable so a demo can point at a staging copy.
CATALOG_URL = os.environ.get(
    "SCIGANTIC_EMPIAR_CATALOG",
    "https://scigantic-empiar-catalog.s3.amazonaws.com/catalog.json",
)
# S3 fast-workspace bucket for mirrored entries (full-speed compute).
FAST_BUCKET = os.environ.get("SCIGANTIC_EMPIAR_FAST_BUCKET", "scigantic-empiar-fast")
FAST_MNT = os.environ.get("SCIGANTIC_EMPIAR_FAST_MNT", "/mnt/empiar-fast")

_UA = {"User-Agent": "Scigantic-empiar/1.0 (+https://scigantic.com; mailto:support@scigantic.com)"}
_session = requests.Session()
_session.headers.update(_UA)


def entry_url(entry_id, *parts) -> str:
    """EBI HTTPS URL for an entry (optionally a file under it)."""
    eid = str(entry_id).replace("EMPIAR-", "").lstrip("0") or "0"
    tail = "/".join(str(p).strip("/") for p in parts if p is not None)
    return f"{EBI}/{eid}/" + (tail if tail else "")


def fast_path(entry_id) -> str | None:
    """Local path to a mirrored (fast) copy of an entry, or None if not mirrored."""
    eid = str(entry_id).replace("EMPIAR-", "")
    p = os.path.join(FAST_MNT, eid)
    return p if os.path.isdir(p) else None


# ── parallel range reader (the fast lane over EBI) ─────────────────────────
# EBI serves range requests in about a second, but a small share of them are
# accepted and then never served. Measured over 30 sequential 1 MB reads:
# median 1.17s, p90 2.02s, and 1 in 30 hung past 100s. It is not load-related —
# the same stall rate shows up at 1, 8, 12 and 16 concurrent connections, so
# backing off does not help. Abandoning a stalled request and asking again does.
#
# The old 60s timeout with 2 retries meant one unlucky read could burn 180s of
# pure waiting, which is what turned single entries into 3-10 minute affairs and
# made the pacer read a coin flip as the archive struggling.
_READ_TIMEOUT_S = float(os.environ.get("SCIGANTIC_EMPIAR_READ_TIMEOUT", "12"))


def _get_range(url, start, end, retries=3):
    """One range read, abandoning stalls quickly rather than waiting them out.

    The timeout is a stall detector, not a patience setting: at p90 = 2s, 12s is
    six times the slowest healthy request, so anything past it is the pathology
    rather than a slow success.
    """
    last = None
    for a in range(retries + 1):
        try:
            r = _session.get(url, headers={"Range": f"bytes={start}-{end}"},
                             timeout=_READ_TIMEOUT_S)
            r.raise_for_status()
            return r.content
        except Exception as exc:
            last = exc
            if a == retries:
                raise
    raise last  # unreachable, kept so the contract is explicit


def pread(url, offset, length, nthreads=8) -> bytes:
    """Read ``length`` bytes at ``offset`` via ``nthreads`` parallel range GETs.

    ~4x faster than a single stream against EBI; 8 is the sweet spot (EBI
    throttles more connections). Falls back to a mounted local read for
    ``file://`` / plain paths.
    """
    if url.startswith("/") or url.startswith("file:"):
        with open(url.replace("file://", ""), "rb") as fh:
            fh.seek(offset); return fh.read(length)
    if length <= 0:
        return b""
    n = max(1, min(nthreads, math.ceil(length / (1 << 20))))
    step = math.ceil(length / n)
    spans, o = [], offset
    while o < offset + length:
        e = min(o + step, offset + length) - 1
        spans.append((o, e)); o = e + 1
    if len(spans) == 1:
        return _get_range(url, *spans[0])
    with ThreadPoolExecutor(max_workers=len(spans)) as ex:
        parts = list(ex.map(lambda s: _get_range(url, s[0], s[1]), spans))
    return b"".join(parts)


def list_files(entry_id, subdir="data"):
    """Filenames under an entry's data dir (from the mount if present, else EBI)."""
    eid = str(entry_id).replace("EMPIAR-", "")
    local = os.path.join(MOUNT, eid, subdir)
    if os.path.isdir(local):
        return sorted(os.listdir(local))
    # parse the EBI autoindex
    html = _session.get(entry_url(eid, subdir) + "/", timeout=30).text
    import re
    out = [m for m in re.findall(r'href="([^"?/][^"]*)"', html) if not m.startswith("..")]
    return sorted(set(out))


# ── MRC / MRCS ─────────────────────────────────────────────────────────────
_MODE = {0: np.int8, 1: np.int16, 2: np.float32, 6: np.uint16, 12: np.float16}


def parse_mrc_header(b: bytes) -> dict:
    """Parse an MRC header into the dict this module's readers expect.

    The byte-offset parsing is delegated to scigantic-headers (the shared
    decoder, single source of truth for the MRC layout; strict=False keeps the
    historical permissive behavior — no 'MAP ' stamp requirement, no dimension
    validation — for the pre-2014 files EMPIAR still serves). The numpy dtype
    and the reader contract (dtype as np.dtype, apix=0.0 when unknown, data0,
    frame_bytes) stay here because they are specific to how the readers below
    slice the pixel data.
    """
    d = decode_mrc_header(b, strict=False)
    if d is None:  # too short to hold a header — same failure as the old struct.error
        raise ValueError("buffer too small for an MRC header")
    f = d.fields
    dt = np.dtype(_MODE.get(f["mode"], np.float32))
    apix = f["pixelSizeA"] if f["pixelSizeA"] is not None else 0.0
    return dict(nx=f["nx"], ny=f["ny"], nz=f["nz"], mode=f["mode"],
                nsymbt=f["dataOffset"] - 1024, dtype=dt, apix=apix,
                frame_bytes=f["nx"] * f["ny"] * dt.itemsize, data0=f["dataOffset"])


_MRC_EXT = (".mrcs", ".mrc", ".st", ".ali", ".rec", ".mrc.bz2")
_TIFF_EXT = (".tif", ".tiff", ".tif.bz2")

# Ceiling on a single TIFF page decode (raw bytes x2, for the float32 copy).
# Generous enough for any real micrograph — a 4096x4096 uint16 frame needs
# ~64 MB — while refusing the rare montage that would take the process down.
MAX_DECODE_BYTES = int(os.environ.get("SCIGANTIC_EMPIAR_MAX_DECODE_MB", "1024")) << 20


def preview_imagesets(entry_id):
    """Entry imagesets ordered by how well each represents the study.

    EMPIAR tells us exactly where the images live (`directory`) and what they
    are (`category`, `data_format`); blind directory walking does not. Walking
    picked EMPIAR-10309's 150x150 picked-particle stack over its 4096x4096
    micrographs, and spent seconds recursing EMPIAR-10288 for MRCs that do not
    exist there at all (it is a TIFF entry).

    Micrographs first, then tomography, then anything else, and larger frames
    ahead of smaller within each — a picked-particle box is a poor thumbnail
    for a study even when it is the only thing readable.
    """
    try:
        sets = EmpiarClient().entry(entry_id).get("imagesets") or []
    except Exception:
        return []
    if isinstance(sets, dict):
        sets = [sets]

    def rank(s):
        cat = str(s.get("category") or "").lower()
        if "picked particle" in cat:
            kind = 2
        elif "micrograph" in cat:
            kind = 0
        else:
            kind = 1  # tilt series / tomograms / reconstructions
        try:
            area = int(s.get("image_width") or 0) * int(s.get("image_height") or 0)
        except (TypeError, ValueError):
            area = 0
        return (kind, -area)

    return sorted((s for s in sets if isinstance(s, dict)), key=rank)


def _imageset_dir(imageset):
    """The entry-relative directory an imageset's files live in."""
    d = str(imageset.get("directory") or "").strip().strip("/")
    return d or "data"


def find_image(entry_id, exts=_MRC_EXT):
    """Best file of the given extensions for an entry, as an entry-relative path.

    Consults EMPIAR's own imageset metadata first, then falls back to the
    historical directory walk for entries whose metadata is missing or wrong.
    """
    seen = set()
    for s in preview_imagesets(entry_id):
        d = _imageset_dir(s)
        if d in seen:
            continue
        seen.add(d)
        try:
            hits = [f for f in list_files(entry_id, d) if f.lower().endswith(exts)]
        except Exception:
            continue
        if hits:
            return f"{d}/{hits[0]}"
    if "data" not in seen:
        r = _walk_for(entry_id, "data", exts, 2)
        if r:
            return r
    return _walk_for(entry_id, "data", exts, 2) if not seen else None


def _walk_for(entry_id, subdir, exts, depth):
    try:
        entries = list_files(entry_id, subdir)
    except Exception:
        return None
    hits = [f for f in entries if f.lower().endswith(exts)]
    if hits:
        return f"{subdir}/{hits[0]}"
    if depth > 0:
        subs = [f.rstrip("/") for f in entries if "." not in f.rstrip("/")]  # dirs
        for s in subs[:8]:
            r = _walk_for(entry_id, f"{subdir}/{s}", exts, depth - 1)
            if r:
                return r
    return None


def find_mrc(entry_id, subdir="data", depth=2):
    """First MRC-like file under an entry, as an entry-relative path, or None.

    Kept for the historical signature (callers pass an explicit subdir); the
    metadata-first path is `find_image`.
    """
    if subdir == "data" and depth == 2:
        return find_image(entry_id, _MRC_EXT)
    return _walk_for(entry_id, subdir, _MRC_EXT, depth)


class RangeFile(io.RawIOBase):
    """A seekable file over HTTP range requests.

    TIFF is the single most common format in EMPIAR (~55% of entries), and the
    MRC reader cannot touch it, so more than half the archive had no preview at
    all. tifffile needs random access rather than a stream, but only actually
    touches the header, the IFD and the first page's strips — reading one frame
    of a 1.4 GB movie pulls about 20 MB.
    """
    # Reads go straight through, deliberately. An 8 MB read-ahead buffer was
    # tried and measured 4.6x SLOWER (EMPIAR-10309: 42s -> 195s): tifffile seeks
    # scattered strip offsets rather than streaming, so nearly every read missed
    # the buffer and refetched 8 MB to serve a few KB. Read-ahead is the wrong
    # shape for this access pattern.
    def __init__(self, url, size):
        self.url, self.size, self.pos, self.bytes_read = url, size, 0, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, max(0, self.size - self.pos))
        if n <= 0:
            return b""
        out = pread(self.url, self.pos, n, nthreads=8)
        self.pos += len(out)
        self.bytes_read += len(out)
        return out

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


def _remote_size(url):
    if url.startswith("/") or url.startswith("file:"):
        return os.path.getsize(url.replace("file://", ""))
    r = _session.head(url, allow_redirects=True, timeout=30)
    r.raise_for_status()
    return int(r.headers.get("Content-Length") or 0)


def read_tiff_frame(entry_id, filename=None, frame=0):
    """First frame of a TIFF/multiframe-TIFF entry as float32 (+ a header dict
    shaped like the MRC readers', so callers can treat the two the same)."""
    import tifffile
    if filename is None:
        rel = find_image(entry_id, _TIFF_EXT)
        if not rel:
            raise FileNotFoundError(f"no TIFF files under EMPIAR-{entry_id}")
        parts = rel.split("/")
        subdir, filename = "/".join(parts[:-1]), parts[-1]
    else:
        parts = filename.split("/")
        subdir, filename = "/".join(parts[:-1]) or "data", parts[-1]
    fp = fast_path(entry_id)
    url = os.path.join(fp, subdir, filename) if fp else entry_url(entry_id, subdir, filename)
    size = _remote_size(url)
    if not size:
        raise OSError(f"could not size {url}")
    with tifffile.TiffFile(RangeFile(url, size)) as tf:
        page = tf.pages[min(frame, len(tf.pages) - 1)]
        # Size the decode BEFORE doing it. page.asarray() materialises the whole
        # page, and .astype(float32) copies it again, so one oversized montage
        # can outgrow the machine — an unbounded backfill was OOM-killed at
        # entry ~300 of 3,025 because of exactly this, taking the whole run with
        # it. shape/dtype are read from the IFD without touching pixel data, so
        # the check is free. Raising here costs one skipped thumbnail instead of
        # the process.
        try:
            need = int(np.prod(page.shape)) * np.dtype(page.dtype).itemsize * 2
        except Exception:
            need = 0
        if need and need > MAX_DECODE_BYTES:
            raise MemoryError(
                f"page {tuple(page.shape)} {page.dtype} needs ~{need/(1<<20):.0f} MB "
                f"to decode (cap {MAX_DECODE_BYTES/(1<<20):.0f} MB)")
        arr = np.asarray(page.asarray()).astype(np.float32)
    if arr.ndim > 2:
        arr = arr[0]
    return arr, dict(nx=arr.shape[1], ny=arr.shape[0], nz=1, mode=None, apix=0.0,
                     dtype=arr.dtype, file=filename, format="TIFF")


def read_mrc(entry_id, filename=None, subdir="data"):
    """Resolve an entry+file to a (url, header) pair, reading only the header.
    ``filename`` may be a path relative to the entry root; if omitted, the first
    MRC found (recursing one subdir level) is used."""
    if filename is None:
        rel = find_mrc(entry_id, subdir)
        if not rel:
            raise FileNotFoundError(f"no MRC files under EMPIAR-{entry_id}/{subdir}")
        parts = rel.split("/"); subdir, filename = "/".join(parts[:-1]), parts[-1]
    fp = fast_path(entry_id)
    url = os.path.join(fp, subdir, filename) if fp else entry_url(entry_id, subdir, filename)
    hdr = pread(url, 0, 1024, 1)
    return url, filename, parse_mrc_header(hdr)


def read_mrc_frame(entry_id, filename=None, frame=0, nthreads=8):
    """One 2D frame/slice as a float32 array (+ header)."""
    url, fn, h = read_mrc(entry_id, filename)
    off = h["data0"] + int(frame) * h["frame_bytes"]
    buf = pread(url, off, h["frame_bytes"], nthreads)
    arr = np.frombuffer(buf, dtype=h["dtype"]).astype(np.float32).reshape(h["ny"], h["nx"])
    h["file"] = fn
    return arr, h


def read_mrc_average(entry_id, filename=None, n_frames=8, nthreads=8):
    """Average the first ``n_frames`` (a poor-man's motion-corrected image —
    much cleaner micrograph + Thon rings than a single raw frame)."""
    url, fn, h = read_mrc(entry_id, filename)
    n = max(1, min(n_frames, h["nz"] or 1))
    buf = pread(url, h["data0"], n * h["frame_bytes"], nthreads)
    stack = np.frombuffer(buf, dtype=h["dtype"]).astype(np.float32).reshape(n, h["ny"], h["nx"])
    h["file"] = fn; h["n_averaged"] = n
    return stack.mean(0), h


def _stretch(arr, size):
    """Downsample to ~`size` on each axis and contrast-stretch to uint8.

    Per-axis factors, not one factor from the longest side. The MRC path reads a
    ~320-row strip spanning the full width, so a single factor taken from the
    width collapsed the rows too: a 320x7420 strip became a 14x323 sliver, which
    is what the eight thumbnails already in the bucket look like. Scaling each
    axis independently turns the same bytes into a readable ~320x320 preview.
    """
    fy = max(1, arr.shape[0] // size)
    fx = max(1, arr.shape[1] // size)
    small = arr[::fy, ::fx]
    lo, hi = np.percentile(small, [2, 98])
    small = np.clip((small - lo) / (hi - lo + 1e-9), 0, 1)
    return (small * 255).astype(np.uint8)


def _mrc_strip(entry_id, rel, size, nthreads):
    """Central strip of an MRC, without decoding the whole frame."""
    parts = rel.split("/")
    subdir, filename = "/".join(parts[:-1]), parts[-1]
    fp = fast_path(entry_id)
    url = os.path.join(fp, subdir, filename) if fp else entry_url(entry_id, subdir, filename)
    h = parse_mrc_header(pread(url, 0, 1024, 1))
    rows = min(h["ny"], max(size, 256))
    row0 = max(0, (h["ny"] - rows) // 2)
    off = h["data0"] + row0 * h["nx"] * h["dtype"].itemsize
    buf = pread(url, off, rows * h["nx"] * h["dtype"].itemsize, nthreads)
    band = np.frombuffer(buf, dtype=h["dtype"]).astype(np.float32).reshape(rows, h["nx"])
    h["file"] = filename
    h["format"] = "MRC"
    return band, h


def thumbnail(entry_id, filename=None, size=320, nthreads=8):
    """A small 2D uint8 preview of an entry, reading only a few MB.

    Walks the entry's imagesets in representativeness order and renders the
    first one it can actually read, MRC or TIFF. Choosing the *imageset* before
    the *format* is the important part: trying MRC first and falling back to
    TIFF sounds equivalent but is not — EMPIAR-10309's micrographs are 4096x4096
    TIFF while its picked particles are 150x150 MRCS, so format-first returns a
    150x150 smudge for a study that has a perfectly good micrograph.
    """
    if filename is not None:
        try:
            band, h = _mrc_strip(entry_id, filename, size, nthreads)
            return _stretch(band, size), h
        except Exception:
            arr, h = read_tiff_frame(entry_id, filename)
            return _stretch(arr, size), h

    errors = []
    for s in preview_imagesets(entry_id) or [{}]:
        d = _imageset_dir(s)
        try:
            names = list_files(entry_id, d)
        except Exception as exc:
            errors.append(f"{d}: {exc}")
            continue
        mrcs = [f for f in names if f.lower().endswith(_MRC_EXT)]
        tiffs = [f for f in names if f.lower().endswith(_TIFF_EXT)]
        if mrcs:
            try:
                band, h = _mrc_strip(entry_id, f"{d}/{mrcs[0]}", size, nthreads)
                return _stretch(band, size), h
            except Exception as exc:
                errors.append(f"{d}/{mrcs[0]}: {exc}")
        if tiffs:
            try:
                arr, h = read_tiff_frame(entry_id, f"{d}/{tiffs[0]}")
                return _stretch(arr, size), h
            except Exception as exc:
                errors.append(f"{d}/{tiffs[0]}: {exc}")

    # Metadata gave us nothing readable — fall back to the historical walk.
    for exts, reader in ((_MRC_EXT, "mrc"), (_TIFF_EXT, "tiff")):
        rel = _walk_for(entry_id, "data", exts, 2)
        if not rel:
            continue
        try:
            if reader == "mrc":
                band, h = _mrc_strip(entry_id, rel, size, nthreads)
                return _stretch(band, size), h
            arr, h = read_tiff_frame(entry_id, rel)
            return _stretch(arr, size), h
        except Exception as exc:
            errors.append(f"{rel}: {exc}")
    raise FileNotFoundError(
        f"no readable image for EMPIAR-{entry_id}" + (f" ({errors[0]})" if errors else ""))


def _hann2d(shape):
    wy = np.hanning(shape[0]); wx = np.hanning(shape[1])
    return np.outer(wy, wx)


def power_spectrum(arr, bin_to=1024):
    """Windowed, log-scaled power spectrum (Thon rings) at a reasonable size."""
    f = max(1, min(arr.shape) // bin_to)
    a = arr[::f, ::f].astype(np.float32)
    a = (a - a.mean()) / (a.std() + 1e-6)
    a = a * _hann2d(a.shape)
    ps = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(a))))
    lo, hi = np.percentile(ps, [1, 99.5])
    return np.clip((ps - lo) / (hi - lo + 1e-9), 0, 1)


def preview(entry_id, filename=None, average=False, n_frames=4, cmap="gray",
            apix=None, nthreads=8, figsize=(11, 5.3)):
    """Render a micrograph (or tomogram slice) + its power spectrum inline.

    Reads only what it needs over parallel range requests — a single frame is a
    few seconds even on a multi-hundred-GB entry, nothing downloaded to disk.
    Pass ``average=True`` for a cleaner (but heavier) mean-of-frames image.
    """
    import matplotlib.pyplot as plt
    eid = str(entry_id).replace("EMPIAR-", "")
    try:
        if average:
            img, h = read_mrc_average(entry_id, filename, n_frames, nthreads)
            sub = f"mean of {h.get('n_averaged', 1)} frames"
        else:
            try:
                img, h = read_mrc_frame(entry_id, filename, 0, nthreads)
            except Exception:
                # ~55% of EMPIAR is TIFF, which the MRC reader cannot open. Those
                # entries used to fall through to the "couldn't auto-locate"
                # message even though the data is perfectly readable.
                img, h = read_tiff_frame(entry_id, filename)
            sub = "frame 0"
    except Exception as e:
        # No auto-locatable MRC, a missing/renamed file (404), or a transient
        # read error — don't crash the notebook; show metadata + how to drill in.
        try:
            s = EmpiarClient().summary(eid)
            print(f"EMPIAR-{eid}: {s.get('title', '')}  ({s.get('size', '?')}, {s.get('format', '?')})")
        except Exception:
            pass
        print(f"Couldn't auto-locate an MRC to preview ({e}).")
        print(f"Explore the layout:  list_files({eid})  then  list_files({eid}, 'data/<subdir>')")
        print(f"Preview a specific file:  preview({eid}, filename='<subdir>/<file>.mrc')")
        return None
    # Header pixel size is often blank; fall back to the EMPIAR API.
    if not apix:
        apix = h["apix"] if h["apix"] else None
    px = f", {apix} Å/px" if apix else ""
    disp = img
    f = max(1, min(disp.shape) // 900)
    disp = disp[::f, ::f]
    lo, hi = np.percentile(disp, [2, 98])
    fig, ax = plt.subplots(1, 2, figsize=figsize)
    ax[0].imshow(np.clip(disp, lo, hi), cmap=cmap)
    ax[0].set_title(f"EMPIAR-{str(entry_id).replace('EMPIAR-','')} · {h['file']}\n"
                    f"{h['nx']}×{h['ny']}{px} · {sub}", fontsize=9)
    ax[0].axis("off")
    ax[1].imshow(power_spectrum(img), cmap="magma")
    ax[1].set_title("power spectrum (FFT) — Thon rings", fontsize=9); ax[1].axis("off")
    fig.tight_layout()
    return fig


# ── EMPIAR metadata + catalog ──────────────────────────────────────────────
class EmpiarClient:
    """Per-entry metadata from EMPIAR's REST API (cached)."""
    @functools.lru_cache(maxsize=4096)
    def entry(self, entry_id):
        eid = str(entry_id).replace("EMPIAR-", "")
        r = _session.get(f"{API}/{eid}/", timeout=30); r.raise_for_status()
        d = r.json()
        e = d.get(f"EMPIAR-{eid}") or (list(d.values())[0] if d else {})
        return e if isinstance(e, dict) else {}

    def summary(self, entry_id):
        e = self.entry(entry_id)
        iss = e.get("imagesets") or [{}]
        i0 = iss[0] if isinstance(iss[0], dict) else {}
        return dict(
            id=str(entry_id).replace("EMPIAR-", ""),
            title=e.get("title", ""),
            size=e.get("dataset_size", ""),
            format=i0.get("data_format") or i0.get("header_format"),
            category=i0.get("category"),
            release_date=e.get("release_date"),
            doi=e.get("entry_doi"),
        )


class EmpiarCatalog:
    """Searchable catalog across the whole archive.

    Loads a pre-built index (metadata per entry, plus a thumbnail where one has
    been rendered) so search/filter over all ~3,000 entries is instant, with no
    live reads. Falls back to the live mount listing when no index is reachable.

    The index carries the EMDB-derived scientific vocabulary (sample name,
    protein chains, organism, resolution, per-chain molecular weight, half-map
    and mask availability), because that is what people actually search and
    filter on. EMPIAR's own titles describe the experiment, not the molecule.
    """
    def __init__(self, url=CATALOG_URL):
        self.url = url
        self._df = None

    def load(self):
        import pandas as pd
        if self._df is not None:
            return self._df
        try:
            self._df = pd.DataFrame(_session.get(self.url, timeout=30).json())
        except Exception:
            ids = sorted(os.listdir(MOUNT)) if os.path.isdir(MOUNT) else []
            self._df = pd.DataFrame({"id": ids})
        return self._df

    def search(self, query=None, method=None, max_gb=None, limit=50, *,
               min_gb=None, max_res=None, min_res=None,
               max_chain_kda=None, min_chain_kda=None, complex_kda_max=None,
               organism=None, half_maps=None, mask=None, has_emdb=None,
               sort="relevance"):
        """Find entries by scientific content, not just dataset title.

        query           free text — matched across title, sample name, protein
                        and organism names, method and accessions, with synonym
                        expansion (so "GPCR" and "cryoET" work).
        method          imageset category substring (e.g. "micrographs").
        min_gb/max_gb   dataset size bounds.
        min_res/max_res reconstruction resolution in Å (lower is better).
        max_chain_kda   largest single polymer, in kDa. This is the filter for
                        "a receptor under 100 kDa" — the assembled complex is
                        almost always heavier than the molecule of interest.
        complex_kda_max total assembly weight bound, when you do mean the complex.
        organism        source organism substring (e.g. "Homo sapiens").
        half_maps/mask  require deposited half-maps / a mask (True), or exclude
                        them (False). Needed for FSC work and postprocessing.
        has_emdb        require (or exclude) an EMDB cross-reference.
        sort            "relevance" (default), "size", "resolution", or "id".

        Returns a DataFrame, so .head()/.to_csv()/plotting all work as usual.
        """
        df = self.load().copy()
        if df.empty:
            return df
        records = df.to_dict("records")
        terms = expand_query(query)
        # The literal query, so a record that actually matches what the user
        # typed sorts above one pulled in only by synonym expansion.
        primary = str(query or "").strip().lower() or None
        hits = [
            r for r in records
            if (not terms or match_score(r, terms, primary) > 0)
            and passes_filters(
                r, method=method, organism=organism, max_gb=max_gb, min_gb=min_gb,
                max_res=max_res, min_res=min_res, max_chain_kda=max_chain_kda,
                min_chain_kda=min_chain_kda, complex_kda_max=complex_kda_max,
                half_maps=half_maps, mask=mask, has_emdb=has_emdb)
        ]
        if sort == "relevance" and terms:
            hits.sort(key=lambda r: -match_score(r, terms, primary))
        elif sort == "size":
            hits.sort(key=lambda r: -(r.get("size_gb") or 0))
        elif sort == "resolution":
            hits.sort(key=lambda r: (r.get("resolution_a") is None, r.get("resolution_a") or 0))
        elif sort == "id":
            hits.sort(key=lambda r: int(r.get("id", 0) or 0))

        import pandas as pd
        out = pd.DataFrame(hits[:limit] if limit else hits)
        if out.empty:
            return out
        # Lead with what a structural biologist scans for; keep everything else.
        preferred = ["id", "title", "sample_name", "resolution_a", "max_chain_kda",
                     "complex_kda", "size_gb", "n_images", "has_half_maps", "has_mask",
                     "emdb_ids", "organisms", "method"]
        cols = [c for c in preferred if c in out.columns]
        return out[cols + [c for c in out.columns if c not in cols]]

    def gallery(self, df=None, cols=4):
        """Render a gallery (HTML) for a set of entries.

        Cards lead with the EMDB structure rendering where one exists (90% of
        entries cross-reference a map), falling back to a raw micrograph
        thumbnail, then to a text-only card. The label under each image says
        which it is: a structure rendering shows the finished molecule and tells
        you nothing about the raw data's quality, whereas a micrograph shows ice,
        contrast and particle density but costs an MRC read to produce. Both are
        useful; conflating them is not.

        A card without any image still has to be worth reading, so it leads with
        the deposited sample name rather than the study title and carries
        resolution / size / map availability.
        """
        from IPython.display import HTML
        import html as _html
        df = self.load() if df is None else df
        cells = []
        for _, r in df.iterrows():
            def g(key, default=""):
                v = r.get(key, default)
                # A missing column in a partial index reads back as NaN.
                return default if v is None or v != v else v

            eid = g("id")
            # Two different pictures, never conflated. structure_image_url is the
            # EMDB rendering of the finished map — what the molecule looks like,
            # published by EMDB and free to show. thumbnail_url would be a real
            # micrograph from this entry's raw data — what the DATA looks like.
            # They answer different questions, so the card says which it is.
            structure, thumb = g("structure_image_url"), g("thumbnail_url")
            if structure:
                art = (f'<img src="{_html.escape(str(structure))}" title="EMDB structure"'
                       ' style="width:100%;border-radius:6px;background:#fff">'
                       '<div style="font:9px sans-serif;color:#aaa;margin-top:2px">'
                       'EMDB structure</div>')
            elif thumb:
                art = (f'<img src="{_html.escape(str(thumb))}" title="micrograph"'
                       ' style="width:100%;border-radius:6px">'
                       '<div style="font:9px sans-serif;color:#aaa;margin-top:2px">'
                       'micrograph</div>')
            else:
                art = ('<div style="height:120px;border-radius:6px;background:#f2f2f2;'
                       'display:flex;align-items:center;justify-content:center;color:#aaa;'
                       f'font:10px sans-serif">preview({eid})</div>')

            label = str(g("sample_name") or g("title"))
            bits = []
            res = g("resolution_a", None)
            if res:
                bits.append(f"{float(res):.2f} &Aring;")
            if g("size"):
                bits.append(_html.escape(str(g("size"))))
            if g("has_half_maps"):
                bits.append("half-maps")
            if g("has_mask"):
                bits.append("mask")

            cells.append(
                f'<div style="width:{100 // cols - 2}%;display:inline-block;vertical-align:top;'
                f'margin:1%;font:11px sans-serif">{art}'
                f'<b>EMPIAR-{_html.escape(str(eid))}</b><br>{_html.escape(label[:70])}'
                f'<br><span style="color:#888">{" &middot; ".join(bits)}</span></div>')
        return HTML("<div>" + "".join(cells) + "</div>")


# ── fast workspace (mirror to S3 for full-speed compute) ───────────────────
def add_to_fast_workspace(entry_id):
    """Check whether an entry has an S3-mirrored copy available for full-speed compute.

    Returns the local path if the entry is already mirrored, otherwise None.

    NOTE: on-demand mirroring is not implemented. This used to print "Requested
    fast mirror ... copy runs in the background" and return a path that did not
    exist, so a caller had every reason to believe a transfer had started and to
    wait for data that was never coming. Nothing was ever queued.

    Mirroring an entry means moving 100 GB to 6 TB out of EMPIAR. EMPIAR
    documents HTTPS for datasets "no more than four gigabytes" and steers bulk
    transfer to Globus (preferred) or Aspera, so the honest implementation is a
    Globus transfer to an S3 destination, not an rclone pull over HTTPS.
    Until that exists, say so rather than imply a queue.
    """
    eid = str(entry_id).replace("EMPIAR-", "")
    p = fast_path(eid)
    if p:
        print(f"EMPIAR-{eid} is in the fast workspace at {p}")
        return p
    print(f"EMPIAR-{eid} is not mirrored, and on-demand mirroring is not available yet.")
    print(f"  Nothing has been queued by this call.")
    print(f"  To work with it now: preview({eid}) or read_mrc_frame({eid}) stream")
    print(f"  directly from EBI without downloading the dataset.")
    print(f"  For the full dataset, use EMPIAR's own bulk channels (Globus preferred):")
    print(f"  https://www.ebi.ac.uk/empiar/{('EMPIAR-' + eid)}/")
    return None
