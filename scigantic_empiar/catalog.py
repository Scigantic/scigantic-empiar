"""EMPIAR metadata client + a searchable, visual catalog across all entries."""
from __future__ import annotations
import functools
import os

from .config import API, CATALOG_URL, MOUNT, session


class EmpiarClient:
    """Per-entry metadata from EMPIAR's REST API (cached)."""

    @functools.lru_cache(maxsize=4096)
    def entry(self, entry_id):
        eid = str(entry_id).replace("EMPIAR-", "")
        r = session.get(f"{API}/{eid}/", timeout=30)
        r.raise_for_status()
        d = r.json()
        e = d.get(f"EMPIAR-{eid}") or (list(d.values())[0] if d else {})
        return e if isinstance(e, dict) else {}

    def summary(self, entry_id):
        e = self.entry(entry_id)
        iss = e.get("imagesets") or [{}]
        i0 = iss[0] if isinstance(iss[0], dict) else {}
        return dict(
            id=str(entry_id).replace("EMPIAR-", ""),
            title=e.get("title", ""),
            size=e.get("dataset_size", ""),
            format=i0.get("data_format") or i0.get("header_format"),
            category=i0.get("category"),
            release_date=e.get("release_date"),
            doi=e.get("entry_doi"),
        )


class EmpiarCatalog:
    """Searchable, visual catalog across the whole archive.

    Loads a prebuilt index (id, title, size, method, thumbnail per entry) so
    search/filter over all ~3,000 entries is instant. Falls back to the mount
    listing when no index is available.
    """

    def __init__(self, url=CATALOG_URL):
        self.url = url
        self._df = None

    def load(self):
        import pandas as pd
        if self._df is not None:
            return self._df
        try:
            self._df = pd.DataFrame(session.get(self.url, timeout=30).json())
        except Exception:
            ids = sorted(os.listdir(MOUNT)) if os.path.isdir(MOUNT) else []
            self._df = pd.DataFrame({"id": ids})
        return self._df

    def search(self, query=None, method=None, max_gb=None, limit=50):
        df = self.load().copy()
        if query and "title" in df:
            df = df[df["title"].str.contains(query, case=False, na=False)]
        if method and "method" in df:
            df = df[df["method"].str.contains(method, case=False, na=False)]
        if max_gb and "size_gb" in df:
            df = df[df["size_gb"].fillna(1e9) <= max_gb]
        return df.head(limit)

    def gallery(self, df=None, cols=4):
        """Render a thumbnail gallery (HTML) for a set of entries."""
        from IPython.display import HTML
        df = self.load() if df is None else df
        cells = []
        for _, r in df.iterrows():
            thumb = r.get("thumbnail_url") or ""
            img = (
                f'<img src="{thumb}" style="width:100%;border-radius:6px">' if thumb
                else '<div style="height:120px;background:#eee;border-radius:6px"></div>'
            )
            cells.append(
                f'<div style="width:{100 // cols - 2}%;display:inline-block;vertical-align:top;'
                f'margin:1%;font:11px sans-serif">{img}'
                f'<b>EMPIAR-{r.get("id", "")}</b><br>{str(r.get("title", ""))[:70]}'
                f'<br><span style="color:#888">{r.get("size", "")}</span></div>'
            )
        return HTML("<div>" + "".join(cells) + "</div>")
