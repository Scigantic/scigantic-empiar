"""Tier 2: mirror a whole entry to fast storage for heavy reprocessing.

Streaming a full multi-hundred-GB entry from EBI at ~1.5 MB/s isn't practical,
so for RELION/EMAN2-style work you copy it once (via ``rclone``, which does its
own multi-connection transfer) to an S3 fast bucket, then read at local speed.
"""
from __future__ import annotations
import shutil
import subprocess

from .config import EBI, FAST_BUCKET, FAST_MNT
from .reader import entry_url, fast_path


def add_to_fast_workspace(entry_id, bucket=FAST_BUCKET, dry_run=False):
    """Mirror EMPIAR-<id> from EBI into the S3 fast bucket with ``rclone``.

    Returns the ``s3://`` destination. If the entry is already visible under the
    local fast mount, returns its path without recopying. ``dry_run=True`` returns
    the command it would run (a list) instead of executing.
    """
    eid = str(entry_id).replace("EMPIAR-", "")
    existing = fast_path(eid)
    if existing:
        return existing

    src = entry_url(eid).replace("https://", ":http:")  # rclone :http: backend
    dst = f":s3:{bucket}/{eid}"
    cmd = ["rclone", "copy", "--http-url", EBI.rsplit("/", 1)[0],
           "--transfers", "8", "--checkers", "8", src, dst]
    if dry_run:
        return cmd
    if shutil.which("rclone") is None:
        raise RuntimeError(
            "rclone not found. Inside a Scigantic notebook this runs server-side; "
            "standalone, install rclone and configure an :s3: remote."
        )
    subprocess.run(cmd, check=True)
    return f"s3://{bucket}/{eid}  (mount at {FAST_MNT}/{eid} to read locally)"
