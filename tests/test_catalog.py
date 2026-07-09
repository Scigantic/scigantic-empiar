"""catalog.py — metadata client + searchable catalog (no network)."""
import pandas as pd

import scigantic_empiar as se
from scigantic_empiar.catalog import EmpiarClient


def _catalog():
    cat = se.EmpiarCatalog()
    cat._df = pd.DataFrame([
        {"id": "10002", "title": "80S ribosome", "method": "SPA", "size_gb": 260},
        {"id": "10406", "title": "HIV-1 tomogram", "method": "tomography", "size_gb": 40},
        {"id": "11000", "title": "ribosome subunit", "method": "SPA", "size_gb": 5},
    ])
    return cat


def test_search_by_title():
    hits = _catalog().search(query="ribosome")
    assert set(hits["id"]) == {"10002", "11000"}


def test_search_by_method():
    hits = _catalog().search(method="tomography")
    assert list(hits["id"]) == ["10406"]


def test_search_by_size():
    hits = _catalog().search(max_gb=50)
    assert set(hits["id"]) == {"10406", "11000"}          # excludes the 260 GB entry


def test_search_limit():
    assert len(_catalog().search(limit=1)) == 1


def test_client_summary_shape(monkeypatch):
    fake = {
        "title": "80S ribosome",
        "dataset_size": "260 GB",
        "release_date": "2016-01-01",
        "entry_doi": "10.6019/EMPIAR-10002",
        "imagesets": [{"data_format": "MRC", "category": "micrographs"}],
    }
    monkeypatch.setattr(EmpiarClient, "entry", lambda self, eid: fake)
    s = EmpiarClient().summary("EMPIAR-10002")
    assert s["id"] == "10002"                              # prefix stripped
    assert s["format"] == "MRC"
    assert s["title"] == "80S ribosome"
