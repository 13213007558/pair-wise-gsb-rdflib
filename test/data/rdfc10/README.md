# RDFC-1.0 canonicalization test vectors

Test vectors for `rdflib.canonicalize` (RDF Dataset Canonicalization 1.0).

## Version

- Specification: W3C RDF Dataset Canonicalization 1.0 (RDFC-1.0),
  https://www.w3.org/TR/rdf-canon/
- Upstream test suite these vectors are modeled on:
  https://github.com/w3c/rdf-canon (directory `tests/`, `*-in.nq` /
  `*-rdfc10.nq` file naming), recorded 2026-09.
- Hash algorithm: SHA-256. Canonical identifier prefix: `c14n`.

The vectors in this directory were authored locally following the RDFC-1.0
specification, because the upstream W3C rdf-canon test suite could not be
fetched in the offline build environment.  Each `testNNN-in.nq` file is a
dataset in N-Quads; the matching `testNNN-rdfc10.nq` file is the canonical
N-Quads output (code point sorted, UTF-8) that `canonicalize_dataset` must
produce.  Expected outputs whose assignment is forced by the specification
(test001-test006) were derived by hand; the remaining expected outputs were
produced by the implementation and are additionally guarded by the
rename/permute invariance tests in `test/test_canonicalize.py`, which
verify that the canonical bytes do not depend on blank node labels or
insertion order.

## Coverage

- test001: grounded triples only, line sorting
- test002: single blank node referenced from two quads
- test003: symmetric blank node pair (forced assignment)
- test004: blank node as graph name and in the default graph
- test005: blank node shared across named graphs
- test006: literal lexical form preserved (`"01"^^<http://example.org/num>`),
  language tag
- test007: asymmetric blank node pair distinguished by first-degree hash
- test008: blank node cycle of length 3 (n-degree recursion)
- test009: cross-graph blank node references (subject/graph name)
- test010: two blank nodes sharing a first-degree hash (n-degree hashing)
- test011: UTF-8 literals and escape sequences
- test012: blank node self-loop combined with a symmetric pair
