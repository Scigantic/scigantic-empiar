"""The fast lane over EBI: parallel HTTP range reads, plus path/URL helpers.

EBI serves ~1.5 MB/s per connection and throttles past ~8, so ``pread`` splits a
read into up to 8 concurrent range requests, which aggregates to ~5-10 MB/s.
"""
from __future__ import annotations
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor

from .config import EBI, FAST_MNT, MOUNT, session


def entry_url(entry_id, *parts) -> str:
    """EBI HTTPS URL for an entry (optionally a file/dir under it)."""
    eid = str(entry_id).replace("EMPIAR-", "").lstrip("0") or "0"
    tail = "/".join(str(p).strip("/") for p in parts if p is not None)
    return f"{EBI}/{eid}/" + (tail if tail else "")


def fast_path(entry_id):
    """Local path to a mirrored (fast) copy of an entry, or None if not mirrored."""
    eid = str(entry_id).replace("EMPIAR-", "")
    p = os.path.join(FAST_MNT, eid)
    return p if os.path.isdir(p) else None


def _get_range(url, start, end, retries=2) -> bytes:
    last = None
    for attempt in range(retries + 1):
        try:
            r = session.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=60)
            r.raise_for_status()
            return r.content
        except Exception as exc:  # noqa: BLE001 - retry any transient error
            last = exc
    raise last  # type: ignore[misc]


def pread(url, offset, length, nthreads=8) -> bytes:
    """Read ``length`` bytes at ``offset`` from ``url`` via up to ``nthreads``
    parallel range GETs. A local/``file://`` path is read directly (used when an
    entry is mirrored to the fast workspace)."""
    if url.startswith("/") or url.startswith("file:"):
        with open(url.replace("file://", ""), "rb") as fh:
            fh.seek(offset)
            return fh.read(length)
    if length <= 0:
        return b""
    n = max(1, min(nthreads, math.ceil(length / (1 << 20))))
    step = math.ceil(length / n)
    spans, o = [], offset
    while o < offset + length:
        end = min(o + step, offset + length) - 1
        spans.append((o, end))
        o = end + 1
    if len(spans) == 1:
        return _get_range(url, *spans[0])
    with ThreadPoolExecutor(max_workers=len(spans)) as ex:
        parts = list(ex.map(lambda s: _get_range(url, s[0], s[1]), spans))
    return b"".join(parts)


def list_files(entry_id, subdir="data"):
    """Filenames under an entry's subdir — from the mount if present, else by
    parsing EBI's autoindex HTML."""
    eid = str(entry_id).replace("EMPIAR-", "")
    local = os.path.join(MOUNT, eid, subdir)
    if os.path.isdir(local):
        return sorted(os.listdir(local))
    html = session.get(entry_url(eid, subdir) + "/", timeout=30).text
    out = [m for m in re.findall(r'href="([^"?/][^"]*)"', html) if not m.startswith("..")]
    return sorted(set(out))
