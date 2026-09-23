"""Tests for the incrementally maintained RDFS closure view."""

from __future__ import annotations

from rdflib import Graph, Namespace
from rdflib.extras.incremental_rdfs import IncrementalRDFSView
from rdflib.namespace import RDF, RDFS

EX = Namespace("http://example.org/")
L = Namespace("http://left.example/")
R = Namespace("http://right.example/")


def _view() -> IncrementalRDFSView:
    return IncrementalRDFSView(Graph())


def test_subclass_transitivity_and_type_propagation() -> None:
    view = _view()
    view.update(
        adds=[
            (EX.A, RDFS.subClassOf, EX.B),
            (EX.B, RDFS.subClassOf, EX.C),
            (EX.x, RDF.type, EX.A),
        ]
    )
    assert (EX.A, RDFS.subClassOf, EX.C) in view
    assert (EX.x, RDF.type, EX.B) in view
    assert (EX.x, RDF.type, EX.C) in view
    # Derived triples are classified as derived, not explicit.
    assert view.is_derived((EX.x, RDF.type, EX.C))
    assert not view.is_explicit((EX.x, RDF.type, EX.C))

    view.remove((EX.B, RDFS.subClassOf, EX.C))
    assert (EX.A, RDFS.subClassOf, EX.C) not in view
    assert (EX.x, RDF.type, EX.C) not in view
    assert (EX.x, RDF.type, EX.B) in view


def test_subproperty_domain_range_rules() -> None:
    view = _view()
    view.update(
        adds=[
            (EX.p, RDFS.subPropertyOf, EX.q),
            (EX.q, RDFS.subPropertyOf, EX.r),
            (EX.q, RDFS.domain, EX.Q),
            (EX.r, RDFS.range, EX.RR),
            (EX.s, EX.p, EX.o),
        ]
    )
    # Sub-property instance propagation, including the transitive step.
    assert (EX.s, EX.q, EX.o) in view
    assert (EX.s, EX.r, EX.o) in view
    # Domain of the reached super-property applies through the chain.
    assert (EX.s, RDF.type, EX.Q) in view
    # Range of r applies through the propagated (s r o) instance.
    assert (EX.o, RDF.type, EX.RR) in view

    view.remove((EX.q, RDFS.subPropertyOf, EX.r))
    assert (EX.s, EX.r, EX.o) not in view
    assert (EX.o, RDF.type, EX.RR) not in view
    assert (EX.s, EX.q, EX.o) in view
    assert (EX.s, RDF.type, EX.Q) in view

    view.remove((EX.p, RDFS.subPropertyOf, EX.q))
    assert (EX.s, EX.q, EX.o) not in view
    assert (EX.s, RDF.type, EX.Q) not in view
    assert (EX.s, EX.p, EX.o) in view


def test_domain_range_directly() -> None:
    view = _view()
    view.add((EX.p, RDFS.domain, EX.D))
    view.add((EX.p, RDFS.range, EX.E))
    view.add((EX.s, EX.p, EX.o))
    assert (EX.s, RDF.type, EX.D) in view
    assert (EX.o, RDF.type, EX.E) in view
    view.remove((EX.p, RDFS.domain, EX.D))
    assert (EX.s, RDF.type, EX.D) not in view
    assert (EX.o, RDF.type, EX.E) in view


def test_diamond_keeps_conclusion_with_two_proofs() -> None:
    view = _view()
    view.update(
        adds=[
            (EX.A, RDFS.subClassOf, EX.B),
            (EX.A, RDFS.subClassOf, EX.C),
            (EX.B, RDFS.subClassOf, EX.D),
            (EX.C, RDFS.subClassOf, EX.D),
            (EX.x, RDF.type, EX.A),
        ]
    )
    instance = (EX.x, RDF.type, EX.D)
    schema = (EX.A, RDFS.subClassOf, EX.D)
    assert instance in view
    assert schema in view
    # The instance conclusion has three immediate proofs: via B, via C and
    # via the transitively derived (A subClassOf D).
    assert view.proofs(instance) == 3
    assert view.proofs(schema) == 2

    # Losing one proof leaves the conclusion standing on the other one.
    view.remove((EX.B, RDFS.subClassOf, EX.D))
    assert instance in view
    assert schema in view
    assert view.proofs(instance) == 2
    assert view.proofs(schema) == 1

    # Losing the second proof retracts the conclusion.
    view.remove((EX.C, RDFS.subClassOf, EX.D))
    assert instance not in view
    assert schema not in view
    assert view.proofs(instance) == 0


def test_cyclic_inheritance_collapses_when_base_support_gone() -> None:
    view = _view()
    view.update(
        adds=[
            (EX.A, RDFS.subClassOf, EX.B),
            (EX.B, RDFS.subClassOf, EX.A),
            (EX.x, RDF.type, EX.A),
        ]
    )
    # The cycle produces reflexive closure and cross-type conclusions.
    assert (EX.A, RDFS.subClassOf, EX.A) in view
    assert (EX.B, RDFS.subClassOf, EX.B) in view
    assert (EX.x, RDF.type, EX.B) in view

    view.remove((EX.A, RDFS.subClassOf, EX.B))
    view.remove((EX.B, RDFS.subClassOf, EX.A))
    view.remove((EX.x, RDF.type, EX.A))

    # Nothing may survive through self-supporting circular proofs.
    assert len(view) == 0
    assert list(view.derived_triples()) == []


def test_fact_that_is_both_explicit_and_derived() -> None:
    view = _view()
    view.update(
        adds=[
            (EX.A, RDFS.subClassOf, EX.B),
            (EX.x, RDF.type, EX.A),
        ]
    )
    derived = (EX.x, RDF.type, EX.B)
    assert view.is_derived(derived)
    assert not view.is_explicit(derived)

    # Asserting the derived conclusion explicitly does not duplicate it.
    view.add(derived)
    assert view.is_explicit(derived)
    assert view.is_derived(derived)
    assert len(view) == len(set(view))

    # Losing the proof keeps the explicit fact.
    view.remove((EX.A, RDFS.subClassOf, EX.B))
    assert derived in view
    assert view.is_explicit(derived)
    assert not view.is_derived(derived)

    # Retracting the explicit fact as well removes it.
    view.remove(derived)
    assert derived not in view


def test_duplicate_base_addition_has_no_effect() -> None:
    view = _view()
    fact = (EX.A, RDFS.subClassOf, EX.B)
    view.add(fact)
    view.add(fact)
    assert view.stats["adds"] == 1
    assert len(list(view.graph.triples(fact))) == 1


def test_removing_absent_or_purely_derived_triple_is_noop() -> None:
    view = _view()
    view.add((EX.A, RDFS.subClassOf, EX.B))
    view.remove((EX.Nope, RDFS.subClassOf, EX.B))
    assert view.stats["removes"] == 0

    view.add((EX.A, RDFS.subClassOf, EX.Z))
    view.add((EX.Z, RDFS.subClassOf, EX.W))
    jump = (EX.A, RDFS.subClassOf, EX.W)
    assert view.is_derived(jump)
    before = set(view)
    view.remove(jump)  # not explicit: cannot retract a derived fact
    assert set(view) == before


def test_base_graph_never_receives_derived_triples() -> None:
    base = Graph()
    view = IncrementalRDFSView(base)
    explicit = [
        (EX.A, RDFS.subClassOf, EX.B),
        (EX.B, RDFS.subClassOf, EX.C),
        (EX.x, RDF.type, EX.A),
        (EX.p, RDFS.domain, EX.P),
        (EX.s, EX.p, EX.o),
    ]
    view.update(adds=explicit)
    assert set(base) == set(explicit)

    view.remove((EX.A, RDFS.subClassOf, EX.B))
    expected = set(explicit) - {(EX.A, RDFS.subClassOf, EX.B)}
    assert set(base) == expected
    # The view still shows derived triples that survive.
    assert (EX.s, RDF.type, EX.P) in view


def test_preloaded_graph_is_closed_by_constructor() -> None:
    base = Graph()
    base.add((EX.A, RDFS.subClassOf, EX.B))
    base.add((EX.x, RDF.type, EX.A))
    view = IncrementalRDFSView(base)
    assert (EX.x, RDF.type, EX.B) in view
    assert view.is_derived((EX.x, RDF.type, EX.B))


def test_triples_pattern_query_and_batch_update() -> None:
    view = _view()
    view.update(
        adds=[
            (EX.A, RDFS.subClassOf, EX.B),
            (EX.B, RDFS.subClassOf, EX.C),
            (EX.x, RDF.type, EX.A),
        ]
    )
    types = set(view.triples((None, RDF.type, None)))
    assert (EX.x, RDF.type, EX.B) in types
    assert (EX.x, RDF.type, EX.C) in types
    assert set(view.triples((EX.x, RDF.type, EX.C))) == {
        (EX.x, RDF.type, EX.C)
    }

    # Batch update: extend the chain and cut its lower link at once.
    view.update(
        adds=[(EX.C, RDFS.subClassOf, EX.D)],
        removes=[(EX.A, RDFS.subClassOf, EX.B)],
    )
    assert (EX.x, RDF.type, EX.B) not in view
    assert (EX.B, RDFS.subClassOf, EX.D) in view


def _region_facts(prefix: Namespace) -> set:
    return {
        (prefix.A, RDFS.subClassOf, prefix.B),
        (prefix.B, RDFS.subClassOf, prefix.C),
        (prefix.x, RDF.type, prefix.A),
        (prefix.x, RDF.type, prefix.B),
        (prefix.x, RDF.type, prefix.C),
        (prefix.A, RDFS.subClassOf, prefix.C),
    }


def test_updates_do_not_recompute_disconnected_region() -> None:
    view = _view()
    view.update(
        adds=[
            (L.A, RDFS.subClassOf, L.B),
            (L.x, RDF.type, L.A),
            (R.A, RDFS.subClassOf, R.B),
            (R.B, RDFS.subClassOf, R.C),
            (R.x, RDF.type, R.A),
        ]
    )
    right = _region_facts(R)

    def right_access() -> int:
        return sum(view.access_counts[t] for t in right)

    baseline = right_access()
    assert baseline > 0  # the region was touched while it was being built

    # An addition entirely inside the left region.
    view.add((L.B, RDFS.subClassOf, L.C))
    assert right_access() == baseline
    assert (L.x, RDF.type, L.C) in view

    # A deletion entirely inside the left region.
    view.remove((L.B, RDFS.subClassOf, L.C))
    assert right_access() == baseline
    assert (L.x, RDF.type, L.C) not in view
    # The right closure is intact.
    assert (R.x, RDF.type, R.C) in view

    # A deletion cascade within the right region never visits the left one.
    left = _region_facts(L)
    left_baseline = sum(view.access_counts[t] for t in left)
    view.remove((R.B, RDFS.subClassOf, R.C))
    assert sum(view.access_counts[t] for t in left) == left_baseline
    assert (R.x, RDF.type, R.C) not in view
    assert (R.x, RDF.type, R.B) in view
