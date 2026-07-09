"""Locations + the shared HTTP session for scigantic_empiar.

Everything is overridable via env vars so a Scigantic notebook (mounted at
``$SCIGANTIC_MOUNT_PATH``, with its own catalog/fast buckets) and a standalone
install behave sensibly without config.
"""
from __future__ import annotations
import os
import requests

# The whole EMPIAR tree is FUSE-mounted here inside a Scigantic notebook. When
# absent (standalone use), the readers stream straight from EBI over HTTPS.
MOUNT = os.environ.get("SCIGANTIC_MOUNT_PATH", "/mnt/http-archive/data")

# EBI's public data endpoints.
EBI = "https://ftp.ebi.ac.uk/empiar/world_availability"
API = "https://www.ebi.ac.uk/empiar/api/entry"

# Prebuilt per-entry metadata + thumbnail index (id, title, size, method,
# thumbnail_url) that powers EmpiarCatalog search/gallery.
CATALOG_URL = os.environ.get(
    "SCIGANTIC_EMPIAR_CATALOG",
    "https://scigantic-empiar-catalog.s3.amazonaws.com/catalog.json",
)

# S3 fast-workspace: full-speed mirrored copies of chosen entries.
FAST_BUCKET = os.environ.get("SCIGANTIC_EMPIAR_FAST_BUCKET", "scigantic-empiar-fast")
FAST_MNT = os.environ.get("SCIGANTIC_EMPIAR_FAST_MNT", "/mnt/empiar-fast")

# Descriptive UA (contact) is the polite convention for automated EBI/NCBI
# access and avoids some abuse filters.
USER_AGENT = "scigantic-empiar/0.2 (+https://github.com/scigantic/scigantic-empiar; mailto:support@scigantic.com)"

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})
