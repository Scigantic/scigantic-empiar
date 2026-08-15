"""EMPIAR metadata client + a searchable, visual catalog across all entries."""
from __future__ import annotations
import functools
import os

from ._search import expand_query, field_text, match_score, passes_filters
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

    def search(self, query=None, method=None, max_gb=None, limit=50, *,
               min_gb=None, max_res=None, min_res=None,
               max_chain_kda=None, min_chain_kda=None, complex_kda_max=None,
               organism=None, half_maps=None, mask=None, has_emdb=None,
               sort="relevance"):
        """Find entries by scientific content, not just dataset title.

        query           free text — matched across title, sample name, protein
                        and organism names, method and accessions, with synonym
                        expansion (so "GPCR" and "cryoET" work).
        method          imageset category substring (e.g. "micrographs").
        min_gb/max_gb   dataset size bounds.
        min_res/max_res reconstruction resolution in Å (lower is better).
        max_chain_kda   largest single polymer, in kDa. This is the filter for
                        "a receptor under 100 kDa" — the assembled complex is
                        almost always heavier than the molecule of interest.
        complex_kda_max total assembly weight bound, when you do mean the complex.
        organism        source organism substring (e.g. "Homo sapiens").
        half_maps/mask  require deposited half-maps / a mask (True), or exclude
                        them (False). Needed for FSC work and postprocessing.
        has_emdb        require (or exclude) an EMDB cross-reference.
        sort            "relevance" (default), "size", "resolution", or "id".

        Prior to 0.2.0 this matched `query` against the dataset TITLE only, which
        is the one field that never carries the vocabulary people search by:
        EMPIAR titles describe the experiment ("Cryo electron microscopy of ..."),
        while protein and organism names live in the EMDB cross-reference. The
        practical effect was that search("GPCR") returned nothing at all across
        the entire archive.

        Returns a DataFrame, so .head()/.to_csv()/plotting all work as usual.
        """
        df = self.load().copy()
        if df.empty:
            return df
        records = df.to_dict("records")
        terms = expand_query(query)
        # The literal query, so a record that actually matches what the user
        # typed sorts above one pulled in only by synonym expansion.
        primary = str(query or "").strip().lower() or None
        hits = [
            r for r in records
            if (not terms or match_score(r, terms, primary) > 0)
            and passes_filters(
                r, method=method, organism=organism, max_gb=max_gb, min_gb=min_gb,
                max_res=max_res, min_res=min_res, max_chain_kda=max_chain_kda,
                min_chain_kda=min_chain_kda, complex_kda_max=complex_kda_max,
                half_maps=half_maps, mask=mask, has_emdb=has_emdb)
        ]
        if sort == "relevance" and terms:
            hits.sort(key=lambda r: -match_score(r, terms, primary))
        elif sort == "size":
            hits.sort(key=lambda r: -(r.get("size_gb") or 0))
        elif sort == "resolution":
            hits.sort(key=lambda r: (r.get("resolution_a") is None, r.get("resolution_a") or 0))
        elif sort == "id":
            hits.sort(key=lambda r: int(r.get("id", 0) or 0))

        import pandas as pd
        out = pd.DataFrame(hits[:limit] if limit else hits)
        if out.empty:
            return out
        # Lead with what a structural biologist scans for; keep everything else.
        preferred = ["id", "title", "sample_name", "resolution_a", "max_chain_kda",
                     "complex_kda", "size_gb", "n_images", "has_half_maps", "has_mask",
                     "emdb_ids", "organisms", "method"]
        cols = [c for c in preferred if c in out.columns]
        return out[cols + [c for c in out.columns if c not in cols]]

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
