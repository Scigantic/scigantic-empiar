# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.4.1] - 2026-08-31

### Changed
- Added PyPI classifiers (license, supported Python versions, development
  status, audience, topic).
- Added `Repository` and `Issues` links to `[project.urls]`.
- Added upper bounds to `numpy`, `requests`, and `scigantic-headers`
  dependencies so a future breaking major release doesn't silently break
  installs.
- Added CI/PyPI/license/Python-version badges to the README.

## [0.4.0] - 2026-08-29
### Fixed
- `catalog.load()` now raises instead of silently degrading on a failed
  fetch.
- Metadata reads retry instead of failing on the first transient error.

## [0.3.1] - 2026-08-16
### Changed
- Stopped advertising a mirroring feature (`add_to_fast_workspace`) that did
  not exist as described.

## [0.3.0] - 2026-08-15
### Changed
- Brought the package to parity with the shipping source used inside the
  Scigantic notebook image; `__init__.py` and `_search.py` are now
  byte-identical to that copy.

## [0.2.0] - 2026-08-15
### Fixed
- `EmpiarCatalog.search()` now matches sample name, protein/organism names,
  method, and cross-referenced accessions, not just the dataset title.

## [0.1.0] - 2026-07-09
### Added
- First public release: stream EMPIAR cryo-EM/cryo-ET entries over parallel
  HTTP range reads (`pread`, MRC/TIFF frame readers, `preview()`,
  `EmpiarClient`, `EmpiarCatalog`).
