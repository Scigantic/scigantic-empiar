"""Tests for the EMPIAR catalog query layer.

Run with plain stdlib, no pytest and no imaging stack:
    python3 infrastructure/docker/notebook-images/cryoem/scigantic_empiar/_search_test.py

`_search.py` is loaded straight off disk so the package __init__ (numpy, PIL,
scigantic-headers) never has to import. Search correctness is the part that
decides whether a scientist finds anything, so it stays cheap to test.
"""
import importlib.util
import os
import unittest

_spec = importlib.util.spec_from_file_location(
    "_empiar_search", os.path.join(os.path.dirname(__file__), "..", "scigantic_empiar", "_search.py"))
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)


# Real records, trimmed, as the builder emits them. EMPIAR-10288 is the entry a
# KEK scientist was hunting for when search("GPCR") returned nothing.
CB1 = {
    "id": "10288",
    "title": "Cryo electron microscopy of Cannabinoid Receptor 1-G Protein Complex",
    "sample_name": "Cannabinoid Receptor 1-G Protein Complex",
    "proteins": ["Guanine nucleotide-binding protein G(i) subunit alpha-1",
                 "Cannabinoid receptor 1", "scFv16"],
    "ligands": ["CHOLESTEROL"],
    "organisms": ["Homo sapiens", "Mus musculus"],
    "emdb_ids": ["EMD-0339"],
    "method": "micrographs - multiframe",
    "size_gb": 476.0,
    "resolution_a": 3.0,
    "max_chain_kda": 55.7,
    "complex_kda": 169.5,
    "has_half_maps": False,
    "has_mask": False,
}
CGRP = {
    "id": "10668",
    "title": "CryoEM structure of the apo-CGRP receptor in a detergent micelle",
    "sample_name": "apo CGRP receptor",
    "proteins": ["Receptor activity-modifying protein 1",
                 "Calcitonin gene-related peptide type 1 receptor"],
    "ligands": [],
    "organisms": ["Homo sapiens"],
    "emdb_ids": ["EMD-22962"],
    "method": "micrographs - multiframe",
    "size_gb": 1740.8,
    "resolution_a": 3.15,
    "max_chain_kda": 56.3,
    "complex_kda": 73.4,
    "has_half_maps": True,
    "has_mask": True,
}
RIBOSOME = {
    "id": "10002",
    "title": "S.cereviseae 80S ribosome direct electron detetector dataset",
    "sample_name": "80S ribosome",
    "proteins": ["40S ribosomal protein S1"],
    "ligands": [],
    "organisms": ["Saccharomyces cerevisiae"],
    "emdb_ids": [],
    "method": "micrographs - multiframe",
    "size_gb": 260.0,
    "resolution_a": None,
    "max_chain_kda": None,
    "complex_kda": None,
    "has_half_maps": False,
    "has_mask": False,
}
CORPUS = [CB1, CGRP, RIBOSOME]


def find(query=None, **filters):
    terms = S.expand_query(query)
    return [r["id"] for r in CORPUS
            if (not terms or S.match_score(r, terms) > 0)
            and S.passes_filters(r, **filters)]


class TestQueryExpansion(unittest.TestCase):
    def test_gpcr_finds_receptor_complexes(self):
        # The regression this whole layer exists for. EMPIAR/EMDB never write
        # the word "GPCR", so a title-substring search returned zero across all
        # ~3,000 entries and the agent abandoned the library.
        self.assertIn("10288", find("GPCR"))
        self.assertIn("10668", find("GPCR"))

    def test_gpcr_does_not_match_a_ribosome(self):
        self.assertNotIn("10002", find("GPCR"))

    def test_query_is_case_insensitive(self):
        self.assertEqual(find("gpcr"), find("GPCR"))

    def test_synonyms_are_symmetric(self):
        self.assertEqual(set(S.expand_query("cryo-et")), set(S.expand_query("cryoet")))

    def test_unknown_term_expands_to_itself_only(self):
        self.assertEqual(S.expand_query("cannabinoid"), ["cannabinoid"])

    def test_empty_query_matches_everything(self):
        self.assertEqual(len(find(None)), len(CORPUS))
        self.assertEqual(len(find("   ")), len(CORPUS))


class TestFieldCoverage(unittest.TestCase):
    def test_matches_protein_name_absent_from_title(self):
        # "scFv16" appears only in the EMDB-derived protein list.
        self.assertEqual(find("scfv16"), ["10288"])

    def test_matches_organism(self):
        self.assertEqual(find("saccharomyces"), ["10002"])

    def test_matches_accession(self):
        self.assertEqual(find("EMD-22962"), ["10668"])

    def test_title_outranks_an_incidental_ligand_hit(self):
        terms = S.expand_query("ribosome")
        self.assertGreater(S.match_score(RIBOSOME, terms), S.match_score(CB1, terms))


class TestFilters(unittest.TestCase):
    def test_receptor_under_100_kda_uses_largest_chain_not_complex(self):
        # The distinction that made the original request hard: every GPCR-G
        # protein complex is >100 kDa assembled, while the receptor is ~50.
        self.assertEqual(sorted(find("GPCR", max_chain_kda=100)), ["10288", "10668"])
        self.assertEqual(find("GPCR", complex_kda_max=100), ["10668"])

    def test_half_map_filter_both_directions(self):
        self.assertEqual(find(half_maps=True), ["10668"])
        self.assertNotIn("10668", find(half_maps=False))

    def test_records_missing_the_filtered_field_are_excluded(self):
        # A null resolution must not sneak through a max_res bound.
        self.assertNotIn("10002", find(max_res=4.0))

    def test_resolution_and_size_bounds(self):
        self.assertEqual(find(max_res=3.05), ["10288"])
        self.assertEqual(find(max_gb=500), ["10288", "10002"])

    def test_has_emdb_filter(self):
        self.assertEqual(find(has_emdb=False), ["10002"])

    def test_filters_compose_with_query(self):
        self.assertEqual(find("GPCR", max_res=3.05, max_chain_kda=100), ["10288"])


class TestWordBoundaries(unittest.TestCase):
    """Substring matching made the GPCR expansion match "binding protein"."""

    HELICASE = {
        "id": "10213",
        "title": "MDA5-dsRNA filament",
        "sample_name": "MDA5-dsRNA helical filament",
        "proteins": ["Interferon-induced helicase C domain-containing protein 1"],
        "organisms": ["Homo sapiens"],
    }
    REMODELLER = {
        "id": "10465",
        "title": "ALC1 regulatory linker",
        "sample_name": "Crosslinked complex of ALC1 regulatory linker",
        "proteins": ["Chromodomain-helicase-DNA-binding protein 1-like"],
        "organisms": ["Homo sapiens"],
    }

    def test_g_protein_does_not_match_containing_protein(self):
        terms = S.expand_query("GPCR")
        self.assertEqual(S.match_score(self.HELICASE, terms), 0)

    def test_g_protein_does_not_match_binding_protein(self):
        terms = S.expand_query("GPCR")
        self.assertEqual(S.match_score(self.REMODELLER, terms), 0)

    def test_g_protein_still_matches_a_real_g_protein_complex(self):
        terms = S.expand_query("GPCR")
        self.assertGreater(S.match_score(CB1, terms), 0)

    def test_hyphenated_terms_still_match(self):
        self.assertGreater(
            S.match_score({"title": "cryo-EM of something"}, S.expand_query("cryo-em")), 0)

    def test_partial_words_do_not_match(self):
        # "actin" must not match "refraction" or "interacting".
        self.assertEqual(S.match_score({"title": "interacting partners"},
                                       S.expand_query("actin")), 0)


class TestPlurals(unittest.TestCase):
    """Boundary matching alone lost the plurals substring matching gave free."""

    def test_singular_query_matches_plural_corpus(self):
        rec = {"title": "Structures of nucleosomes bound to CHD1"}
        self.assertGreater(S.match_score(rec, S.expand_query("nucleosome")), 0)

    def test_plural_query_matches_singular_corpus(self):
        rec = {"title": "Cryo-EM of the nucleosome"}
        self.assertGreater(S.match_score(rec, S.expand_query("nucleosomes")), 0)

    def test_es_plural(self):
        rec = {"title": "Assembly of viruses in situ"}
        self.assertGreater(S.match_score(rec, S.expand_query("virus")), 0)

    def test_plural_tolerance_does_not_become_prefix_matching(self):
        # The reason the suffix set is s/es and not \\w*.
        self.assertEqual(S.match_score({"title": "acting on the substrate"},
                                       S.expand_query("actin")), 0)
        self.assertEqual(S.match_score({"title": "the reaction center"},
                                       S.expand_query("react")), 0)


class TestLiteralOutranksExpansion(unittest.TestCase):
    """Synonym groups are mutually substitutable, so without a literal boost
    every member scored the same and search("rhodopsin") ranked Cannabinoid
    Receptor 1 first, with no rhodopsin in the top 5 at all."""

    RHODOPSIN = {
        "id": "10926",
        "title": "ChRmine channelrhodopsin",
        "sample_name": "ChRmine rhodopsin",
        "proteins": ["Channelrhodopsin ChRmine"],
    }
    ADENOSINE = {
        "id": "10309",
        "title": "Adenosine A2A receptor bound to miniGs heterotrimer",
        "sample_name": "Adenosine A2A receptor",
        "proteins": ["Adenosine receptor A2a"],
    }

    def _ranked(self, query, corpus):
        terms = S.expand_query(query)
        primary = query.strip().lower()
        scored = [(S.match_score(r, terms, primary), r["id"]) for r in corpus]
        return [i for _, i in sorted(scored, key=lambda x: -x[0])]

    def test_literal_rhodopsin_outranks_a_generic_gpcr(self):
        self.assertEqual(self._ranked("rhodopsin", [CB1, self.RHODOPSIN])[0], "10926")

    def test_literal_adenosine_receptor_outranks_a_generic_gpcr(self):
        self.assertEqual(
            self._ranked("adenosine receptor", [CB1, self.ADENOSINE])[0], "10309")

    def test_expansion_still_finds_records_with_no_literal_hit(self):
        # The recall half must survive: CB1 has no "rhodopsin" anywhere, but a
        # GPCR query must still reach it.
        terms = S.expand_query("rhodopsin")
        self.assertGreater(S.match_score(CB1, terms, "rhodopsin"), 0)

    def test_no_primary_keeps_the_old_flat_scoring(self):
        terms = S.expand_query("GPCR")
        self.assertEqual(S.match_score(CB1, terms), S.match_score(CB1, terms, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
