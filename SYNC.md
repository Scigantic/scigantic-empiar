# Keeping `_search.py` in sync

`scigantic_empiar/_search.py` is the query layer: synonym vocabulary, whole-word
plural-tolerant matching, literal-over-synonym ranking, and the structured
filters. It is **vendored verbatim** from the Scigantic monorepo copy that ships
inside the notebook image, and the two must stay byte-identical.

Verify:

    shasum -a 256 scigantic_empiar/_search.py

Why it matters: every fix in that file came from a real user hitting a real
dead end. `search("GPCR")` returning zero across the whole archive. The term
"g protein" matching inside "bindin[g protein]" so a helicase scored as a GPCR.
Word boundaries then losing plurals, so "nucleosome" stopped matching
"nucleosomes". Synonym expansion outranking the literal query, so
`search("rhodopsin")` led with a cannabinoid receptor. A second, drifting copy
means finding each of those twice, and the copy nobody remembers is the one
users get.

The durable fix is for the monorepo image to `pip install scigantic-empiar`
rather than `COPY` its own copy, the way it already does for `scigantic-headers`
and `scigantic-wwpdb`. Until then, sync by hand and check the hash.
