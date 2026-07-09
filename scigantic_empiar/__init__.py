"""scigantic_empiar — explore EMPIAR cryo-EM data over HTTP, without downloading.

    import scigantic_empiar as se
    se.preview(10002)                      # micrograph + power spectrum, in seconds
    se.EmpiarCatalog().search("ribosome")  # search the whole archive

The package is split into focused modules; this file re-exports the public API so
the flat ``se.<name>`` calls above keep working:

    config     locations + shared HTTP session (env-overridable)
    reader     parallel HTTP range reads (pread) + path/URL helpers
    mrc        MRC/MRCS parsing, file discovery, NumPy readers
    catalog    metadata client (EmpiarClient) + searchable catalog (EmpiarCatalog)
    render     inline micrograph + power-spectrum rendering (needs matplotlib)
    workspace  mirror an entry to fast storage for reprocessing
"""
from __future__ import annotations

from .catalog import EmpiarCatalog, EmpiarClient
from .config import API, CATALOG_URL, EBI, FAST_BUCKET, FAST_MNT, MOUNT
from .mrc import (
    find_mrc,
    parse_mrc_header,
    power_spectrum,
    read_mrc,
    read_mrc_average,
    read_mrc_frame,
    thumbnail,
)
from .reader import entry_url, fast_path, list_files, pread
from .render import preview
from .workspace import add_to_fast_workspace

__version__ = "0.1.0"

__all__ = [
    # rendering
    "preview",
    # readers
    "read_mrc", "read_mrc_frame", "read_mrc_average", "thumbnail",
    "find_mrc", "parse_mrc_header", "power_spectrum",
    # transport
    "pread", "list_files", "entry_url", "fast_path",
    # metadata / catalog
    "EmpiarClient", "EmpiarCatalog",
    # fast workspace
    "add_to_fast_workspace",
    # config constants
    "MOUNT", "EBI", "API", "CATALOG_URL", "FAST_BUCKET", "FAST_MNT",
    "__version__",
]
