"""reader.py — URL/path helpers and the parallel range reader."""
import numpy as np
import pytest

from conftest import BUFFER

import scigantic_empiar as se
from scigantic_empiar import reader


def test_entry_url_normalises_ids():
    assert reader.entry_url(10002).endswith("/10002/")
    # EMPIAR- prefix stripped, leading zeros trimmed
    assert reader.entry_url("EMPIAR-00123").endswith("/123/")
    # extra parts joined, no trailing slash
    assert reader.entry_url(10002, "data", "m.mrc").endswith("/10002/data/m.mrc")


def test_fast_path(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "FAST_MNT", str(tmp_path))
    (tmp_path / "10002").mkdir()
    assert reader.fast_path("10002") == str(tmp_path / "10002")
    assert reader.fast_path("99999") is None


def test_pread_local_file(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(BUFFER[:10000])
    got = se.pread(str(p), 100, 250)          # local path branch (no threads)
    assert got == BUFFER[100:350]


def test_pread_splits_into_parallel_ranges(monkeypatch):
    calls = []

    def fake_get_range(url, start, end, retries=2):
        calls.append((start, end))
        return BUFFER[start:end + 1]

    monkeypatch.setattr(reader, "_get_range", fake_get_range)

    offset, length = 500, 2_500_000        # > 2 MB -> must fan out
    got = se.pread("http://ebi.example/x.mrc", offset, length, nthreads=8)

    assert got == BUFFER[offset:offset + length]     # correct + correctly ordered
    assert len(calls) > 1                             # actually parallelised
    spans = sorted(calls)
    assert spans[0][0] == offset                      # covers the whole request...
    assert spans[-1][1] == offset + length - 1
    for (_, e), (s2, _) in zip(spans, spans[1:]):     # ...contiguously, no gaps/overlap
        assert s2 == e + 1


def test_pread_zero_length():
    assert se.pread("http://x/y", 0, 0) == b""


def test_get_range_retries_then_raises(monkeypatch):
    attempts = {"n": 0}

    def always_fail(*a, **k):
        attempts["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(reader.session, "get", always_fail)
    with pytest.raises(RuntimeError):
        reader._get_range("http://x/y", 0, 10, retries=2)
    assert attempts["n"] == 3          # initial try + 2 retries
