"""Quickstart: explore EMPIAR from anywhere with internet — nothing downloaded.

    pip install "scigantic-empiar[viz] @ git+https://github.com/aaronkanzer/scigantic-empiar"
    python examples/quickstart.py
"""
import scigantic_empiar as se

# 1) Metadata for a famous entry (the S. cerevisiae 80S ribosome dataset).
print(se.EmpiarClient().summary(10002))

# 2) Search the whole archive by keyword (instant — metadata only).
print(se.EmpiarCatalog().search("ribosome"))

# 3) Read one frame of a 260 GB movie stack over parallel range reads (~seconds).
arr, hdr = se.read_mrc_frame(10002)
print("frame:", arr.shape, arr.dtype, "| pixel size (A):", hdr.get("apix"))

# 4) Render a preview (needs the [viz] extra). Saves a PNG here; in a notebook
#    `se.preview(10002)` displays inline.
fig = se.preview(10002)
if fig is not None:
    fig.savefig("empiar_10002_preview.png", dpi=100, bbox_inches="tight")
    print("wrote empiar_10002_preview.png")
