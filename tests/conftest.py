"""Shared helpers. No test in this suite touches the network."""
import struct

import numpy as np


def make_mrc_header(nx=4, ny=3, nz=5, mode=2, mx=200, xlen=210.0, nsymbt=0):
    """Build a valid 1024-byte MRC header for parser tests.

    apix = xlen / mx (so the defaults give 210/200 = 1.05 A/px).
    """
    b = bytearray(1024)
    struct.pack_into("<4i", b, 0, nx, ny, nz, mode)
    struct.pack_into("<3i", b, 28, mx, mx, mx)          # grid mx,my,mz
    struct.pack_into("<3f", b, 40, xlen, xlen, xlen)    # cell xlen,ylen,zlen
    struct.pack_into("<i", b, 92, nsymbt)
    return bytes(b)


# A patterned 3 MB buffer (byte value == offset mod 256) so range reassembly
# in pread() is order-sensitive: a mis-ordered concat won't match a slice.
BUFFER = np.arange(3_000_000, dtype=np.uint8).tobytes()
