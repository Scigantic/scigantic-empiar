# scigantic_empiar

Explore [EMPIAR](https://www.ebi.ac.uk/empiar/), EMBL-EBI's public archive of raw cryo-EM and cryo-ET image data (~3,000 datasets, ~8.9 PiB), from Python without downloading anything.

EMPIAR is served over EBI's public HTTPS at roughly 1.5 MB/s per connection. `scigantic_empiar` parallelises HTTP range reads, which aggregates to around 5 to 10 MB/s, so you can pull a single frame from a many-GB entry in seconds, decode the MRC, and render the micrograph and its power spectrum. Nothing is copied to disk.

```python
import scigantic_empiar as se

se.preview(10406)                      # render the micrograph below, in seconds
se.EmpiarClient().summary(10406)       # title, pixel size, method, DOI, EMDB/PDB cross-refs
se.EmpiarCatalog().search("ribosome")            # search the whole archive (instant)
se.EmpiarCatalog().search("GPCR", max_res=3.0)   # by science, not just by title
```

![A motion-corrected 70S-ribosome micrograph (EMPIAR-10406) and its power spectrum, rendered by se.preview(10406)](https://raw.githubusercontent.com/scigantic/scigantic-empiar/main/docs/preview_10406.png)

One frame of EMPIAR-10406, a 70S-ribosome dataset, pulled straight from EBI over parallel range reads. The carbon-foil edge, ice and particles are visible at left; the FFT is at right. Nothing was downloaded to disk.

## Install

```bash
pip install "scigantic-empiar[viz]"
```

Core (`numpy`, `requests`) is enough for the readers. `[viz]` adds `matplotlib`, `pandas` and `pillow` for `preview()` and the catalog gallery.

## What it does

| | |
|---|---|
| `preview(id)` | micrograph or tomogram slice, plus power spectrum, from a lazy parallel-range read |
| `read_mrc_frame(id)` / `read_mrc_average(id)` | one frame, or a mean of frames, as a NumPy array with header |
| `read_tiff_frame(id)` | the same for TIFF entries, over HTTP ranges |
| `thumbnail(id)` | small preview array from a few-MB read, used to build catalogs |
| `find_mrc(id)` | resolve an entry's first MRC, recursing the often-nested `data/` layout |
| `pread(url, off, len)` | the parallel HTTP range reader under all of it |
| `EmpiarClient` | per-entry metadata from EMPIAR's REST API, cached |
| `EmpiarCatalog` | search and a gallery across all entries, from a prebuilt index |
| `add_to_fast_workspace(id)` | report whether an entry already has an S3 mirror. It does not create one |

## Search

`EmpiarCatalog.search()` matches across the dataset title, the deposited sample name, protein and organism names, method and cross-referenced accessions, with a small cryo-EM synonym vocabulary. It filters on size, resolution, molecular weight, half-map and mask availability, and EMDB cross-reference.

```python
cat = se.EmpiarCatalog()
cat.search("GPCR", max_res=3.0, max_chain_kda=100)
cat.search("cryoET", min_gb=100, sort="size")
cat.search("ribosome", half_maps=True, sort="resolution")
```

`max_chain_kda` is the largest single polymer, which is what you want for "a receptor under 100 kDa". The assembled complex carries the G protein and often a nanobody, so it is almost always heavier than the molecule of interest.

Changed in 0.2.0. Before that release `search(query=...)` matched the dataset title only. EMPIAR titles describe the experiment ("Cryo electron microscopy of ..."), while the vocabulary people search by lives in the EMDB cross-reference, so `search("GPCR")` returned nothing at all across the whole archive. It now returns 81 entries. If you pinned 0.1.0 because search seemed to return too little, that is why.

## Reading a whole dataset

You cannot, and this library does not pretend otherwise.

`pread` splits a read into concurrent range requests, which is enough to look at any entry interactively. It is not enough to reprocess one. A dataset runs from 100 GB to 6 TB, and EMPIAR documents HTTPS for datasets "no more than four gigabytes", steering bulk transfer to Globus or Aspera.

`add_to_fast_workspace(id)` reports whether an entry already has an S3 mirror and returns its path. **It does not create one, and queues nothing.** An earlier version printed "copy runs in the background" and returned a path that never appeared, so callers waited for data that was never coming. For a full dataset, use EMPIAR's own bulk channels.

On concurrency: 8-way is the default and works well, but not because EBI throttles past it. Measured, stalls happen at 1, 8 and 12 readers alike, and 16-way completed fine. The real behaviour is that a small fraction of requests hang rather than slow down, so a short timeout matters more than the thread count. Dropping the read timeout from 60s to 12s cut wall clock by 2.5x.

## Existing work

The job splits in two: parse MRC, and read bytes from a remote file. Both have existing libraries; neither covers this case.

- [`mrcfile`](https://github.com/ccpem/mrcfile) (CCP-EM) is the standard MRC reader. Its lazy mode is a numpy `memmap`, which needs a local filesystem path and does not issue HTTP range requests. `scigantic_empiar` parses the 1024-byte header directly (`parse_mrc_header`) to seek to one frame of a remote file without a local copy.
- [`fsspec`](https://filesystem-spec.readthedocs.io/) `HTTPFileSystem` turns byte reads into HTTP range requests and can fetch many concurrently ([`cat_ranges`](https://filesystem-spec.readthedocs.io/en/latest/async.html)). `pread` is a small equivalent, kept dependency-free. Moving the transport onto `fsspec` is a reasonable later change.
- [`copick`](https://github.com/copick/copick) (CZI, [Protein Science 2026](https://onlinelibrary.wiley.com/doi/10.1002/pro.70578)) is the closest cryo-EM analog: an fsspec-backed, server-less dataset API with lazy reads. It assumes OME-Zarr, chunked and multiscale. EMPIAR entries are raw MRC and TIFF, so copick needs a per-entry zarr conversion first, which is the step MRC's flat layout lets this skip.

## Companion library

[`scigantic-emdb`](https://pypi.org/project/scigantic-emdb/) does the same job for [EMDB](https://www.ebi.ac.uk/emdb/), the reconstructions computed from this raw data. It imports its query layer from this package, so both archives share one search implementation, and `EmdbCatalog.with_empiar()` joins the two.

## Notes

- MRC and MRCS (movies, micrographs, tomograms, particle stacks) plus some TIFF. Files often nest a couple of subdirectories down; `find_mrc` handles that.
- Entry ids are opaque numbers, so discover datasets by metadata (`EmpiarCatalog.search`, or the EMPIAR website) rather than by listing the tree.
- Inside a [Scigantic](https://scigantic.com) cryo-EM notebook this is preinstalled and the archive is also FUSE-mounted at `$SCIGANTIC_MOUNT_PATH`. Standalone, it streams straight from EBI.

## Relationship to the Scigantic notebook image

This package and the copy preinstalled in Scigantic's cryo-EM notebook image are the same source. They were not always. The two drifted into forks differing in 23 functions, and the published half kept shipping readers that had already been fixed on the other side, including an MRC-only `thumbnail` that used stride decimation and could not read TIFF micrographs at all.

0.3.0 collapsed that. `scigantic_empiar/__init__.py` and `_search.py` here are byte-identical to the shipping copy (see `SYNC.md`), and the per-topic modules remain as thin re-export shims so `from scigantic_empiar.mrc import read_mrc` keeps working.

## License

MIT.
