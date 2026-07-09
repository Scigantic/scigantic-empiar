# scigantic_empiar

Explore [EMPIAR](https://www.ebi.ac.uk/empiar/) — EMBL-EBI's public archive of **raw cryo-EM / cryo-ET image data** (~3,000 datasets, ~8.9 PiB) — from Python, **without downloading anything**.

EMPIAR is served over EBI's public HTTPS at ~1.5 MB/s per connection. `scigantic_empiar` parallelises HTTP **range** reads (8-way ≈ 5–10 MB/s) so you can pull a single frame from a 260 GB entry in seconds, decode the MRC, and render the micrograph + its power spectrum — nothing is copied to disk.

```python
import scigantic_empiar as se

se.preview(10002)                      # a real S. cerevisiae 80S ribosome micrograph + FFT, in seconds
se.EmpiarClient().summary(10002)       # title, pixel size, method, DOI, EMDB/PDB cross-refs
se.EmpiarCatalog().search("ribosome")  # search the whole archive by metadata (instant)
```

## Install

```bash
pip install "scigantic-empiar[viz] @ git+https://github.com/aaronkanzer/scigantic-empiar"
```

Core (`numpy`, `requests`) is enough for the readers; `[viz]` adds `matplotlib` / `pandas` / `pillow` for `preview()` and the catalog gallery.

## What it does

| | |
|---|---|
| `preview(id)` | micrograph / tomogram-slice + power spectrum, rendered from a lazy parallel-range read |
| `read_mrc_frame(id)` / `read_mrc_average(id)` | one frame / a mean of frames as a NumPy array + header |
| `thumbnail(id)` | small preview array (a few-MB central-strip read) — used to build catalogs |
| `find_mrc(id)` | resolve an entry's first MRC, recursing the (often nested) `data/` layout |
| `pread(url, off, len)` | the 8-way parallel HTTP range reader under it all |
| `EmpiarClient` | per-entry metadata from EMPIAR's REST API (cached) |
| `EmpiarCatalog` | search + a visual thumbnail gallery across all entries (from a prebuilt index) |
| `add_to_fast_workspace(id)` | mirror an entry to S3 for full-speed reprocessing (RELION/EMAN2) |

## Why parallel range reads

EBI throttles per connection (~1.5 MB/s) and past ~8 concurrent connections. `pread` splits a read into ~8 concurrent range requests, which aggregates to ~5–10 MB/s — enough to *look* at any entry interactively. For heavy reprocessing of a whole multi-hundred-GB dataset, mirror it to fast storage first (`add_to_fast_workspace`); streaming a full entry at 1.5 MB/s isn't practical.

## Notes

- MRC/MRCS (movies, micrographs, tomograms, particle stacks) and some TIFF. Files often nest a couple subdir levels down; `find_mrc` handles that.
- Entry ids are opaque numbers — discover datasets by **metadata** (`EmpiarCatalog.search`, or the EMPIAR website), not by listing the tree.
- Inside a [Scigantic](https://scigantic.com) cryo-EM notebook this is preinstalled and the archive is also FUSE-mounted at `$SCIGANTIC_MOUNT_PATH`; standalone, it streams straight from EBI.

## License

MIT.
