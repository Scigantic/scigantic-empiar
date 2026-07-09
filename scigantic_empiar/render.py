"""Inline visualization — micrograph/slice + power spectrum (needs matplotlib).

Named ``render`` (not ``preview``) to avoid colliding with the ``preview()``
function it exports, which the package surfaces as ``scigantic_empiar.preview``.
"""
from __future__ import annotations

import numpy as np

from .catalog import EmpiarClient
from .mrc import downsample, power_spectrum, read_mrc_average, read_mrc_frame


def preview(entry_id, filename=None, average=False, n_frames=4, cmap="gray",
            apix=None, nthreads=8, figsize=(11, 5.3)):
    """Render a micrograph (or tomogram slice) + its power spectrum inline.

    Reads only what it needs over parallel range requests — a single frame is a
    few seconds even on a multi-hundred-GB entry, nothing downloaded. Never
    raises: on any read error it prints the entry's metadata + how to drill in.
    Pass ``average=True`` for a cleaner (heavier) mean-of-frames image.
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
    except Exception as e:  # noqa: BLE001 - never crash the caller/notebook
        try:
            s = EmpiarClient().summary(eid)
            print(f"EMPIAR-{eid}: {s.get('title', '')}  ({s.get('size', '?')}, {s.get('format', '?')})")
        except Exception:
            pass
        print(f"Couldn't auto-locate an MRC to preview ({e}).")
        print(f"Explore the layout:  list_files({eid})  then  list_files({eid}, 'data/<subdir>')")
        print(f"Preview a specific file:  preview({eid}, filename='<subdir>/<file>.mrc')")
        return None

    if not apix:
        apix = h["apix"] if h["apix"] else None
    px = f", {apix} Å/px" if apix else ""
    disp = downsample(img, 900)
    lo, hi = np.percentile(disp, [2, 98])

    fig, ax = plt.subplots(1, 2, figsize=figsize)
    ax[0].imshow(np.clip(disp, lo, hi), cmap=cmap)
    ax[0].set_title(f"EMPIAR-{eid} · {h['file']}\n{h['nx']}×{h['ny']}{px} · {sub}", fontsize=9)
    ax[0].axis("off")
    ax[1].imshow(power_spectrum(img), cmap="magma")
    ax[1].set_title("power spectrum (FFT) — Thon rings", fontsize=9)
    ax[1].axis("off")
    fig.tight_layout()
    return fig
