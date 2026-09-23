"""Tests for the incremental RDFS closure view (rdfs_view.RDFSView)."""

import random

from rdflib import Graph, Namespace
from rdflib.namespace import RDF, RDFS
from rdfs_view import RDFSView

TYPE = RDF.type
SC = RDFS.subClassOf
SP = RDFS.subPropertyOf
DOM = RDFS.domain
RNG = RDFS.range

EX = Namespace("http://example.org/")
R1 = Namespace("http://example.org/region1#")
R2 = Namespace("http://example.org/region2#")


def naive_closure(base):
    """Reference implementation: recompute the full closure from scratch."""
    believed = set(base)
    while True:
        sc = [(s, o) for (s, p, o) in believed if p == SC]
        sp = [(s, o) for (s, p, o) in believed if p == SP]
        new = set()
        for a, b in sc:
            for b2, c in sc:
                if b == b2:
                    new.add((a, SC, c))
        for a, b in sp:
            for b2, c in sp:
                if b == b2:
                    new.add((a, SP, c))
        for x, tp, c in believed:
            if tp != TYPE:
                continue
            for a, b in sc:
                if c == a:
                    new.add((x, TYPE, b))
        for pp, dp, c in believed:
            if dp == DOM:
                for x, p2, y in believed:
                    if p2 == pp:
                        new.add((x, TYPE, c))
            elif dp == RNG:
                for x, p2, y in believed:
                    if p2 == pp:
                        new.add((y, TYPE, c))
        for a, b in sp:
            for x, p2, y in believed:
                if p2 == a:
                    new.add((x, b, y))
        if new <= believed:
            return believed
        believed |= new


def make_view():
    return RDFSView(Graph())


def test_subclass_transitivity_and_type_propagation():
    v = make_view()
    v.add((EX.x, TYPE, EX.A))
    v.add((EX.A, SC, EX.B))
    v.add((EX.B, SC, EX.C))
    assert (EX.A, SC, EX.C) in v
    assert (EX.x, TYPE, EX.B) in v
    assert (EX.x, TYPE, EX.C) in v
    # explicit vs derived distinction
    assert v.is_explicit((EX.A, SC, EX.B))
    assert not v.is_derived((EX.A, SC, EX.B))
    assert v.is_derived((EX.A, SC, EX.C))
    assert not v.is_explicit((EX.A, SC, EX.C))
    assert v.is_explicit((EX.x, TYPE, EX.A))
    assert v.is_derived((EX.x, TYPE, EX.C))
    # the wrapped graph only holds explicit triples
    assert set(v.graph) == v.explicit_facts()
    assert len(v.graph) == 3
    # pattern queries see the closure
    assert set(v.triples((EX.x, TYPE, None))) == {
        (EX.x, TYPE, EX.A),
        (EX.x, TYPE, EX.B),
        (EX.x, TYPE, EX.C),
    }


def test_subproperty_transitivity_and_propagation():
    v = make_view()
    v.add((EX.p, SP, EX.q))
    v.add((EX.q, SP, EX.r))
    v.add((EX.x, EX.p, EX.y))
    assert (EX.p, SP, EX.r) in v
    assert (EX.x, EX.q, EX.y) in v
    assert (EX.x, EX.r, EX.y) in v
    assert set(v.graph) == v.explicit_facts()


def test_domain_and_range():
    v = make_view()
    v.add((EX.p, DOM, EX.C))
    v.add((EX.p, RNG, EX.D))
    v.add((EX.x, EX.p, EX.y))
    assert (EX.x, TYPE, EX.C) in v
    assert (EX.y, TYPE, EX.D) in v
    # derived type facts propagate further over subClassOf
    v.add((EX.C, SC, EX.E))
    assert (EX.x, TYPE, EX.E) in v


def test_diamond_survives_single_proof_removal():
    v = make_view()
    for t in [
        (EX.A, SC, EX.B),
        (EX.A, SC, EX.C),
        (EX.B, SC, EX.D),
        (EX.C, SC, EX.D),
    ]:
        v.add(t)
    assert (EX.A, SC, EX.D) in v
    # two independent proofs exist; cutting one keeps the conclusion
    v.remove((EX.B, SC, EX.D))
    assert (EX.A, SC, EX.D) in v
    assert v.is_derived((EX.A, SC, EX.D))
    # cutting the second proof retracts it
    v.remove((EX.C, SC, EX.D))
    assert (EX.A, SC, EX.D) not in v
    assert set(v) == {(EX.A, SC, EX.B), (EX.A, SC, EX.C)}


def test_cycle_collapses_without_base_support():
    v = make_view()
    v.add((EX.A, SC, EX.B))
    v.add((EX.B, SC, EX.A))
    assert (EX.A, SC, EX.A) in v
    assert (EX.B, SC, EX.B) in v
    v.remove((EX.A, SC, EX.B))
    # cyclic proofs must not keep the reflexive conclusions alive
    assert (EX.A, SC, EX.A) not in v
    assert (EX.B, SC, EX.B) not in v
    assert (EX.B, SC, EX.A) in v
    v.remove((EX.B, SC, EX.A))
    assert len(v) == 0
    assert set(v.graph) == set()


def test_explicit_and_derived_same_triple():
    v = make_view()
    for t in [
        (EX.A, SC, EX.B),
        (EX.B, SC, EX.D),
        (EX.A, SC, EX.D),  # explicit *and* derivable
    ]:
        v.add(t)
    assert v.is_explicit((EX.A, SC, EX.D))
    assert v.is_derived((EX.A, SC, EX.D))
    # retracting the explicit fact leaves the derived conclusion in place
    v.remove((EX.A, SC, EX.D))
    assert (EX.A, SC, EX.D) in v
    assert not v.is_explicit((EX.A, SC, EX.D))
    assert v.is_derived((EX.A, SC, EX.D))
    # retracting the derivation path removes it entirely
    v.remove((EX.B, SC, EX.D))
    assert (EX.A, SC, EX.D) not in v


def test_retract_all_base_facts_leaves_empty_view():
    v = make_view()
    base = [
        (EX.A, SC, EX.B),
        (EX.B, SC, EX.A),  # cycle
        (EX.A, SC, EX.C),
        (EX.C, SC, EX.D),
        (EX.B, SC, EX.D),
        (EX.x, TYPE, EX.A),
        (EX.p, SP, EX.q),
        (EX.q, SP, EX.r),
        (EX.u, EX.p, EX.v),
        (EX.p, DOM, EX.C),
        (EX.p, RNG, EX.D),
    ]
    for t in base:
        v.add(t)
    assert len(v) > len(base)
    for t in base:
        v.remove(t)
    assert len(v) == 0
    assert set(v.graph) == set()
    assert v.derived_facts() == frozenset()


def test_duplicate_add_is_noop():
    v = make_view()
    v.add((EX.A, SC, EX.B))
    v.add((EX.B, SC, EX.C))
    before = (len(v), v.derived_facts(), len(v.graph))
    v.add((EX.A, SC, EX.B))
    v.add((EX.A, SC, EX.B))
    assert (len(v), v.derived_facts(), len(v.graph)) == before
    # a duplicate add performs no maintenance work at all
    assert v.last_visited == frozenset()


def test_disconnected_regions_are_not_visited():
    v = make_view()
    region1 = [
        (R1.A, SC, R1.B),
        (R1.A, SC, R1.C),
        (R1.B, SC, R1.D),
        (R1.C, SC, R1.D),
        (R1.x, TYPE, R1.A),
    ]
    region2 = [
        (R2.P, SC, R2.Q),
        (R2.Q, SC, R2.R),
        (R2.y, TYPE, R2.P),
        (R2.p, SP, R2.q),
        (R2.u, R2.p, R2.v),
        (R2.q, DOM, R2.W),
    ]
    for t in region1 + region2:
        v.add(t)

    def touches_region2():
        return any(str(term).startswith(str(R2)) for t in v.last_visited for term in t)

    # addition confined to region 1
    v.add((R1.D, SC, R1.E))
    assert len(v.last_visited) > 0
    assert not touches_region2()
    # removal with over-delete + rederive confined to region 1
    v.remove((R1.B, SC, R1.D))
    assert (R1.A, SC, R1.D) in v  # still derivable via R1.C
    assert len(v.last_visited) > 0
    assert not touches_region2()
    # region 2 closure is intact
    assert (R2.P, SC, R2.R) in v
    assert (R2.y, TYPE, R2.R) in v
    assert (R2.u, R2.q, R2.v) in v
    assert (R2.u, TYPE, R2.W) in v


def test_batch_updates_match_fresh_closure_and_graph_stays_clean():
    v = make_view()
    base = {
        (EX.A, SC, EX.B),
        (EX.B, SC, EX.C),
        (EX.C, SC, EX.D),
        (EX.x, TYPE, EX.A),
        (EX.p, SP, EX.q),
        (EX.u, EX.p, EX.v),
        (EX.q, RNG, EX.D),
    }
    for t in base:
        v.add(t)
    # batch of mixed changes
    v.remove((EX.B, SC, EX.C))
    v.add((EX.B, SC, EX.D))
    v.add((EX.y, TYPE, EX.B))
    v.remove((EX.p, SP, EX.q))
    expected_base = (base - {(EX.B, SC, EX.C), (EX.p, SP, EX.q)}) | {
        (EX.B, SC, EX.D),
        (EX.y, TYPE, EX.B),
    }
    assert set(v.graph) == expected_base
    assert v.explicit_facts() == expected_base
    assert set(v) == naive_closure(expected_base)


def test_view_over_prefilled_graph():
    g = Graph()
    g.add((EX.A, SC, EX.B))
    g.add((EX.x, TYPE, EX.A))
    size_before = len(g)
    v = RDFSView(g)
    assert (EX.x, TYPE, EX.B) in v
    assert len(g) == size_before  # the source graph was not polluted


def test_randomized_differential_against_naive_closure():
    rng = random.Random(20260923)
    classes = [EX["c%d" % i] for i in range(6)]
    props = [EX["p%d" % i] for i in range(4)]
    individuals = [EX["i%d" % i] for i in range(4)]
    pool = []
    for a in classes:
        for b in classes:
            if a != b:
                pool.append((a, SC, b))
    for a in props:
        for b in props:
            if a != b:
                pool.append((a, SP, b))
    for p in props:
        for c in classes[:3]:
            pool.append((p, DOM, c))
            pool.append((p, RNG, c))
    for i in individuals:
        for c in classes:
            pool.append((i, TYPE, c))
        for p in props:
            for j in individuals:
                pool.append((i, p, j))
    rng.shuffle(pool)
    pool = pool[:80]

    v = make_view()
    base = set()
    for step in range(300):
        t = rng.choice(pool)
        if t in base and rng.random() < 0.5:
            v.remove(t)
            base.discard(t)
        else:
            v.add(t)
            base.add(t)
        assert set(v.graph) == base
        assert set(v) == naive_closure(base), "mismatch at step %d" % step
    # retracting everything must not leave cycle-supported residue
    for t in list(base):
        v.remove(t)
    assert len(v) == 0
    assert set(v.graph) == set()
