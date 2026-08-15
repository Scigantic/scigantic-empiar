
# Internals now live in the package root (byte-identical to the copy that
# ships in the Scigantic notebook image), so patch there: setting the name on
# a compatibility shim does not change what the real functions resolve.
"""render.py — rendering, and the promise that preview() never raises."""
import matplotlib

matplotlib.use("Agg")  # headless
import numpy as np

import scigantic_empiar as se
from scigantic_empiar import render


def test_preview_renders_two_panels(monkeypatch):
    img = np.random.default_rng(0).standard_normal((256, 256)).astype(np.float32)
    header = {"nx": 256, "ny": 256, "apix": 1.05, "file": "m.mrc"}
    monkeypatch.setattr(se, "read_mrc_frame", lambda *a, **k: (img, header))

    fig = se.preview(10002, filename="m.mrc")
    assert fig is not None
    assert len(fig.axes) == 2          # micrograph + power spectrum


def test_preview_never_raises_on_read_error(monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("404 from EBI")

    monkeypatch.setattr(se, "read_mrc_frame", boom)
    # metadata lookup also unavailable -> still must not raise
    monkeypatch.setattr(
        "scigantic_empiar.catalog.EmpiarClient.summary",
        lambda self, eid: (_ for _ in ()).throw(RuntimeError("no api")),
    )

    result = se.preview(99999)
    assert result is None                          # graceful, returns None
    out = capsys.readouterr().out
    assert "list_files" in out                     # prints how to drill in
