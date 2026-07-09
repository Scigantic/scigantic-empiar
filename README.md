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

## Design / prior art

The job splits in two: parse MRC, and read bytes from a remote file. Both have existing libraries; neither covers the specific case here.

- [`mrcfile`](https://github.com/ccpem/mrcfile) (CCP-EM) is the standard MRC reader. Its lazy mode is a numpy `memmap`, which needs a local filesystem path — it does not issue HTTP range requests. `scigantic_empiar` parses the 1024-byte header directly (`parse_mrc_header`) to seek to one frame of a remote file without a local copy.
- [`fsspec`](https://filesystem-spec.readthedocs.io/) `HTTPFileSystem` turns byte reads into HTTP range requests and can fetch many ranges concurrently ([`cat_ranges`](https://filesystem-spec.readthedocs.io/en/latest/async.html)). `pread` is a small equivalent, kept dependency-free and tuned to EBI's ~8-connection throttle; moving the transport onto `fsspec` is a reasonable later change.
- [`copick`](https://github.com/copick/copick) (CZI, [Protein Science 2026](https://onlinelibrary.wiley.com/doi/10.1002/pro.70578)) is the closest cryo-EM analog: an fsspec-backed, server-less dataset API with lazy reads. It assumes data stored as OME-Zarr (chunked, multiscale). EMPIAR entries are raw MRC/TIFF, so copick needs a per-entry zarr conversion first — the conversion that MRC's flat layout lets `scigantic_empiar` skip.

## Notes

- MRC/MRCS (movies, micrographs, tomograms, particle stacks) and some TIFF. Files often nest a couple subdir levels down; `find_mrc` handles that.
- Entry ids are opaque numbers — discover datasets by **metadata** (`EmpiarCatalog.search`, or the EMPIAR website), not by listing the tree.
- Inside a [Scigantic](https://scigantic.com) cryo-EM notebook this is preinstalled and the archive is also FUSE-mounted at `$SCIGANTIC_MOUNT_PATH`; standalone, it streams straight from EBI.

## License

MIT.
