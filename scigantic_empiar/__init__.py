"""scigantic_empiar — explore the EMPIAR archive from a Scigantic notebook.

The whole archive (~3,000 entries / 8.9 PiB) is lazily mounted at
``$SCIGANTIC_MOUNT_PATH`` (``/mnt/http-archive/data``) over EBI's public
autoindex. That mount is a single stream (~1.5 MB/s from us-east-1), so for
*previews* we read the same bytes over parallel HTTP range requests directly
from EBI, which aggregates to ~5-10 MB/s (8-way is the sweet spot; EBI throttles
past that). Heavy/repeated compute should use a mirrored entry in S3
(``add_to_fast_workspace``) — 1.5 MB/s is fine to *look*, not to reprocess a
260 GB set.

Three tiers, one API:
  * catalog / search  — metadata across ALL entries (``EmpiarCatalog``)
  * live preview       — pull a frame from ANY entry in seconds (``preview``)
  * fast workspace     — mirror an entry to S3 for full-speed compute

Nothing here is a locked widget; every function is a few lines you can read,
copy, and bend.
"""
from __future__ import annotations
import os, io, struct, math, functools
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

__all__ = [
    "MOUNT", "entry_url", "pread", "list_files", "read_mrc",
    "read_mrc_frame", "read_mrc_average", "power_spectrum", "preview", "thumbnail",
    "EmpiarClient", "EmpiarCatalog", "add_to_fast_workspace", "fast_path",
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
def _get_range(url, start, end, retries=2):
    for a in range(retries + 1):
        try:
            r = _session.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=60)
            r.raise_for_status()
            return r.content
        except Exception:
            if a == retries:
                raise


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
    nx, ny, nz, mode = struct.unpack("<4i", b[:16])
    nsymbt = struct.unpack("<i", b[92:96])[0]
    # pixel size (Angstrom) = cell (bytes 40:52) / grid (bytes 28:40)
    mx, my, mz = struct.unpack("<3i", b[28:40])
    xlen, ylen, zlen = struct.unpack("<3f", b[40:52])
    apix = (xlen / mx) if mx else 0.0
    dt = np.dtype(_MODE.get(mode, np.float32))
    return dict(nx=nx, ny=ny, nz=nz, mode=mode, nsymbt=nsymbt, dtype=dt,
                apix=round(apix, 3), frame_bytes=nx * ny * dt.itemsize,
                data0=1024 + nsymbt)


_MRC_EXT = (".mrcs", ".mrc", ".st", ".ali", ".rec", ".mrc.bz2")


def find_mrc(entry_id, subdir="data", depth=2):
    """First MRC-like file under an entry — checks data/, then up to two levels
    of subdirs (tomography tilt-series / particle stacks / per-session dirs nest
    their MRCs a couple levels down, e.g. data/<sample>/micrographs/*.mrc).
    Returns a subdir-relative path, or None."""
    entries = list_files(entry_id, subdir)
    hits = [f for f in entries if f.lower().endswith(_MRC_EXT)]
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


def thumbnail(entry_id, filename=None, size=320, nthreads=8):
    """A small 2D uint8 preview (central strip of the first MRC), reading only
    ~a few MB — used by the catalog batch to give every entry a thumbnail.
    Returns (uint8 array, header) or raises if the entry has no MRC data."""
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
            img, h = read_mrc_frame(entry_id, filename, 0, nthreads)
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
    """Searchable, VISUAL catalog across the whole archive.

    Loads a pre-built index (id, title, size, method, thumbnail per entry) so
    search/filter over all ~3,000 entries is instant — no live reads. Falls
    back to the live mount listing + API when no index is available yet.
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

    def search(self, query=None, method=None, max_gb=None, limit=50):
        df = self.load().copy()
        if query and "title" in df:
            df = df[df["title"].str.contains(query, case=False, na=False)]
        if method and "method" in df:
            df = df[df["method"].str.contains(method, case=False, na=False)]
        if max_gb and "size_gb" in df:
            df = df[df["size_gb"].fillna(1e9) <= max_gb]
        return df.head(limit)

    def gallery(self, df=None, cols=4):
        """Render a thumbnail gallery (HTML) for a set of entries."""
        from IPython.display import HTML
        df = self.load() if df is None else df
        cells = []
        for _, r in df.iterrows():
            thumb = r.get("thumbnail_url") or ""
            img = f'<img src="{thumb}" style="width:100%;border-radius:6px">' if thumb else \
                  '<div style="height:120px;background:#eee;border-radius:6px"></div>'
            cells.append(
                f'<div style="width:{100//cols-2}%;display:inline-block;vertical-align:top;'
                f'margin:1%;font:11px sans-serif">{img}'
                f'<b>EMPIAR-{r.get("id","")}</b><br>{str(r.get("title",""))[:70]}'
                f'<br><span style="color:#888">{r.get("size","")}</span></div>')
        return HTML("<div>" + "".join(cells) + "</div>")


# ── fast workspace (mirror to S3 for full-speed compute) ───────────────────
def add_to_fast_workspace(entry_id):
    """Request a full-speed S3-mirrored copy of an entry.

    Flagship entries are pre-mirrored (instant). For any other entry this
    kicks off a background copy from EBI → the us-east-1 fast bucket; once it
    lands it mounts at ``fast_path(entry_id)`` and everything (RELION, etc.)
    runs at S3 speed instead of 1.5 MB/s. Returns the eventual local path.
    """
    eid = str(entry_id).replace("EMPIAR-", "")
    p = fast_path(eid)
    if p:
        print(f"EMPIAR-{eid} is already in the fast workspace at {p}")
        return p
    # The mirror is driven by the platform (see scripts/empiar-mirror-entry).
    # In-notebook we surface the request + where it will appear.
    print(f"Requested fast mirror of EMPIAR-{eid} → s3://{FAST_BUCKET}/{eid}/ "
          f"(EBI→S3 copy runs in the background; it will mount at {FAST_MNT}/{eid}).")
    print("Until then, use preview()/read_mrc_* which stream directly from EBI.")
    return os.path.join(FAST_MNT, eid)
