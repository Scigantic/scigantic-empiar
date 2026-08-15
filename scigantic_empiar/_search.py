"""Query vocabulary and record matching for the EMPIAR catalog.

Kept free of numpy/pandas/scigantic-headers on purpose: this is the part that
decides whether a scientist's query finds anything, so it must be testable
without the imaging stack (see _search_test.py, which loads this file directly).
"""
from __future__ import annotations

import functools
import re

# Deliberately small and high-confidence, in the spirit of the platform's
# archive-search synonym map: every group is a mapping a structural biologist
# would call a synonym, not a topical association. Broad expansions ("protein"
# → "biology") wreck precision for no recall gain.
#
# The one judgement call is the GPCR group. Neither EMPIAR nor EMDB tags a
# structure as "a GPCR", and deposited sample names say "Cannabinoid Receptor
# 1-G Protein Complex", never "G protein-coupled receptor". Since these
# structures are nearly always solved as a receptor-G protein complex, the
# co-purified G protein IS the usable marker. Expanding to it is a recall
# trade: it also matches the handful of G protein structures without a
# receptor. That beats the alternative, which is what shipped before —
# search("GPCR") returning zero across the entire archive.
#
# The co-purified G protein only helps for complexes. An apo receptor solved in
# a micelle (EMPIAR-10668, the CGRP receptor) carries no G protein at all, so
# nothing in its deposited metadata marks it as a GPCR. The remaining honest
# lever is naming: these receptor families ARE GPCRs, and listing them is
# curated vocabulary, not invented data. Multi-word forms where the bare word
# is ambiguous ("adenosine" alone would match every ATP-binding structure).
# Recall-oriented by design, and imprecise at the edges in two known ways:
# "g protein" also surfaces G-protein effectors and GEFs (phosphodiesterase 6,
# P-Rex1), and the serotonin entries include 5-HT3, the one serotonin receptor
# that is a ligand-gated ion channel rather than a GPCR. Both are visible at a
# glance in `sample_name`. Measured on the first ~950 indexed entries, "GPCR"
# went from 4 title-only hits to 31, of which roughly three quarters are true
# GPCRs. That trade is worth it against the previous behaviour, which was zero.
GPCR_NAMES = [
    "adrenergic", "beta-adrenergic", "muscarinic", "opioid receptor",
    "serotonin receptor", "5-ht receptor", "adenosine receptor",
    "dopamine receptor", "cannabinoid receptor", "chemokine receptor",
    "rhodopsin", "glucagon receptor", "glp-1", "glp-1 receptor", "cgrp",
    "calcitonin receptor", "parathyroid hormone receptor", "smoothened",
    "frizzled", "orexin receptor", "angiotensin receptor", "apelin receptor",
    "neurotensin receptor", "vasopressin receptor", "oxytocin receptor",
    "histamine receptor", "melatonin receptor", "sphingosine 1-phosphate receptor",
    "secretin receptor", "corticotropin-releasing factor receptor",
    "metabotropic glutamate", "gabab", "taste receptor", "olfactory receptor",
]

# Two different relations live in this vocabulary, and conflating them costs
# either precision or recall depending on archive size.
#
# FAMILY_GROUPS relate an umbrella term to its members. "GPCR" and "rhodopsin"
# are not synonyms: one contains the other, and the group has 30+ members. Over
# EMDB's ~60,900 entries, expanding "rhodopsin" to the whole family turned 40
# real hits into 1,189, so 1,149 results were some other receptor. These expand
# only as a fallback (see adaptive_terms).
#
# VARIANT_GROUPS relate different spellings of the same concept — "cryoet" /
# "cryo-et", "ribosome" / "ribosomal". Suppressing these never helps: a user who
# types "cryoET" wants the "cryo-ET" entries too, and gating that behind a hit
# threshold silently drops ~900 correct results. These always expand.
FAMILY_GROUPS = [
    ["gpcr", "gpcrs", "g protein-coupled receptor", "g-protein-coupled receptor",
     "g protein coupled receptor", "g protein", "guanine nucleotide-binding protein",
     *GPCR_NAMES],
]

VARIANT_GROUPS = [
    ["cryoem", "cryo-em", "cryo em", "electron cryo-microscopy"],
    ["cryoet", "cryo-et", "cryo et", "electron cryo-tomography", "tomogram",
     "tilt series", "tilt-series"],
    ["spa", "single particle analysis", "single-particle", "single particle"],
    ["ribosome", "ribosomal"],
    ["proteasome", "proteasomal"],
    ["nucleosome", "chromatin"],
    ["membrane protein", "transmembrane", "integral membrane"],
    ["ion channel", "channelopathy"],
    ["transporter", "abc transporter", "solute carrier"],
    ["virus", "viral", "virion", "capsid"],
    ["antibody", "fab", "nanobody", "immunoglobulin"],
    ["helicase", "helicases"],
    ["polymerase", "polymerases"],
    ["kinase", "kinases"],
    ["amyloid", "fibril", "fibrils"],
    ["microtubule", "tubulin"],
    ["actin", "f-actin"],
    ["mitochondria", "mitochondrial"],
    ["photosystem", "photosynthetic"],
    ["ligand-gated", "ligand gated", "neurotransmitter receptor"],
]

# Preserved so expand_query keeps its existing contract: full expansion, family
# included. Callers that want the precision/recall trade use adaptive_terms.
SYNONYM_GROUPS = FAMILY_GROUPS + VARIANT_GROUPS

SYNONYM_INDEX: dict[str, set[str]] = {}
for _grp in SYNONYM_GROUPS:
    for _term in _grp:
        SYNONYM_INDEX.setdefault(_term, set()).update(_grp)

VARIANT_INDEX: dict[str, set[str]] = {}
for _grp in VARIANT_GROUPS:
    for _term in _grp:
        VARIANT_INDEX.setdefault(_term, set()).update(_grp)

# Fields the text query runs over. `title` alone is what shipped originally,
# and it is the one field that never carries the scientific vocabulary users
# search by: EMPIAR titles describe the experiment ("Cryo electron microscopy
# of ..."), while protein and organism names live in the EMDB cross-reference.
SEARCH_FIELDS = ("title", "sample_name", "proteins", "organisms", "ligands",
                 "method", "experiment_type", "pdb_ids", "emdb_ids")
FIELD_WEIGHT = {"title": 3, "sample_name": 3, "proteins": 2, "emdb_ids": 2,
                "pdb_ids": 2, "organisms": 1, "ligands": 1, "method": 1,
                "experiment_type": 1}


def expand_query(query):
    """Query → the set of surface forms to match, including known synonyms."""
    q = str(query or "").strip().lower()
    if not q:
        return []
    terms = {q}
    terms.update(SYNONYM_INDEX.get(q, ()))
    return sorted(terms)


# Expansion earns its keep on a small archive and costs precision on a large
# one. EMPIAR has ~3,000 entries, where search("rhodopsin") matches 2 literally
# and 31 expanded — the expansion is the difference between a dead end and a
# useful browse. EMDB has ~60,900, where the same query matches 40 literally and
# 1,189 expanded, so 1,149 results are some other GPCR and the user is worse off.
#
# The rule that serves both is the one this module already claims to follow:
# expansion is a RECALL FALLBACK, not a query rewrite. Engage it only when the
# literal query is too thin to stand on its own. 25 is the threshold: below it a
# scientist is plausibly under-served and wants the wider net; above it they
# already have more precise hits than they will read.
EXPANSION_MIN_HITS = 25


def variant_terms(query):
    """Query → the query plus alternative spellings of the same concept.

    Never widens to a different concept, so it is always safe to apply.
    """
    q = str(query or "").strip().lower()
    if not q:
        return []
    return sorted({q} | VARIANT_INDEX.get(q, set()))


def adaptive_terms(query, count_fn, min_hits=EXPANSION_MIN_HITS):
    """Terms to search with: spelling variants always, family only if needed.

    `count_fn(terms)` returns how many records the given terms match. Returns
    (terms, widened) so a caller can tell the user which happened — a silently
    widened query is how "rhodopsin" comes back full of cannabinoid receptors
    with nothing on screen to explain why.
    """
    q = str(query or "").strip().lower()
    if not q:
        return [], False
    variants = variant_terms(q)
    full = expand_query(q)
    if len(full) <= len(variants):      # no family expansion available anyway
        return variants, False
    if count_fn(variants) >= min_hits:  # already enough without the family
        return variants, False
    return full, True


def field_text(value):
    """Flatten a record field (scalar or list) to searchable text."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(v) for v in value if v is not None)
    return str(value)


@functools.lru_cache(maxsize=512)
def _pattern(term):
    """Whole-word, plural-tolerant matcher for one term.

    Plain substring matching is wrong here in a way that quietly produces
    nonsense: the GPCR expansion term "g protein" occurs inside "bindin[g
    protein]" and "containin[g protein]", so an MDA5 helicase and a
    chromatin remodeller both scored as GPCR hits. Lookarounds rather than \\b
    so terms that start or end on punctuation ("cryo-em", "f-actin") behave.

    Enforcing the boundary alone then lost the plurals substring matching had
    given away for free — "nucleosome" stopped matching "nucleosomes", and the
    hit count for that query actually dropped. Matching has to work in both
    directions (singular query over a plural corpus and the reverse), which no
    single stem does: strip "virus" to "viru" and you lose "viruses"; strip
    "viruses" to "viruse" and you lose "virus". So enumerate a small set of
    surface forms instead. Deliberately just s/es — a general \\w* suffix would
    make "actin" match "acting".
    """
    forms = {term}
    if len(term) > 4 and term.endswith("es"):
        forms |= {term[:-2], term[:-1]}
    elif len(term) > 3 and term.endswith("s"):
        forms |= {term[:-1], term + "es"}
    else:
        forms |= {term + "s", term + "es"}
    alt = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    return re.compile(r"(?<!\w)(?:" + alt + r")(?!\w)")


# What the user literally typed must outrank anything expansion dragged in.
# Without this, every member of a synonym group scores identically, so
# search("rhodopsin") ranked Cannabinoid Receptor 1 first and returned no
# rhodopsin at all in the top 5 — the GPCR group is mutually substitutable, and
# a record matching "g protein" scored the same as one actually about rhodopsin.
# Expansion is a RECALL FALLBACK, not a query rewrite: a query that matches
# literally keeps its exact-match ranking, which is the same rule the platform's
# archive search follows (see backend searchSynonyms.ts).
LITERAL_BOOST = 10


def match_score(record, terms, primary=None, fields=None, weights=None):
    """How well one catalog record matches already-expanded query terms.

    0 means no match. Higher is better. Two things shape the ranking: a hit in
    the title or the deposited sample name outranks one in an organism or ligand
    name, and a hit on `primary` (what the user actually typed) outranks any
    number of synonym hits.

    `fields`/`weights` default to the EMPIAR record shape. They are parameters
    so scigantic_emdb can reuse this scorer over EMDB's differently-named fields
    rather than copying it. Copying is the real hazard: every bug fixed in this
    module (substring bleed through "bindin[g protein]", lost plurals, synonym
    expansion outranking the literal query) would have to be found and fixed
    twice, and the second copy is the one nobody remembers to fix.
    """
    if not terms:
        return 1
    fields = fields or SEARCH_FIELDS
    weights = weights if weights is not None else FIELD_WEIGHT
    score = 0
    for field in fields:
        if field not in record:
            continue
        text = field_text(record.get(field)).lower()
        if not text:
            continue
        weight = weights.get(field, 1)
        for term in terms:
            if term and _pattern(term).search(text):
                score += weight * (LITERAL_BOOST if primary and term == primary else 1)
    return score


def passes_filters(r, *, method=None, organism=None, max_gb=None, min_gb=None,
                   max_res=None, min_res=None, max_chain_kda=None,
                   min_chain_kda=None, complex_kda_max=None, half_maps=None,
                   mask=None, has_emdb=None):
    """Structured filters. A None bound is 'don't care'; a record missing the
    field it is filtered on is excluded, never silently kept."""
    if method and method.lower() not in field_text(r.get("method")).lower():
        return False
    if organism and organism.lower() not in field_text(r.get("organisms")).lower():
        return False
    gb = r.get("size_gb")
    if max_gb is not None and (gb is None or gb > max_gb):
        return False
    if min_gb is not None and (gb is None or gb < min_gb):
        return False
    res = r.get("resolution_a")
    if max_res is not None and (res is None or res > max_res):
        return False
    if min_res is not None and (res is None or res < min_res):
        return False
    mx = r.get("max_chain_kda")
    if max_chain_kda is not None and (mx is None or mx > max_chain_kda):
        return False
    if min_chain_kda is not None and (mx is None or mx < min_chain_kda):
        return False
    cx = r.get("complex_kda")
    if complex_kda_max is not None and (cx is None or cx > complex_kda_max):
        return False
    if half_maps is not None and bool(r.get("has_half_maps")) != bool(half_maps):
        return False
    if mask is not None and bool(r.get("has_mask")) != bool(mask):
        return False
    if has_emdb is not None and bool(r.get("emdb_ids")) != bool(has_emdb):
        return False
    return True
