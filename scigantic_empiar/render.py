"""Compatibility shim.

Everything now lives in the package root, which is byte-identical to the copy
that ships inside the Scigantic notebook image. Keeping one file is the whole
point: this package and that image previously drifted into two forks of the
same library, differing in 23 functions, and the published half quietly shipped
readers that had already been fixed on the other side.

These modules re-export the public names so `from scigantic_empiar.mrc import
read_mrc` and friends keep working.
"""
from . import preview, preview_imagesets, find_image

__all__ = ["preview", "preview_imagesets", "find_image"]
