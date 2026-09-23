from __future__ import annotations

import pytest

from rdflib import BNode, Dataset, Graph, Literal, URIRef
from rdflib.collection import Collection, MalformedListError
from rdflib.namespace import RDF
from rdflib.term import Node


def make_shared_graph() -> tuple[Graph, Node, Node, Node, Node, Node]:
    """Two lists sharing one tail chain.

    A = ["a", "s1", "s2"]          a1 -> t1 -> t2 -> rdf:nil
    B = ["b1", "b2", "s1", "s2"]  b1 -> b2 -> t1 -> t2 -> rdf:nil
    """
    g = Graph()
    a1, b1, b2, t1, t2 = BNode(), BNode(), BNode(), BNode(), BNode()
    g.add((t1, RDF.first, Literal("s1")))
    g.add((t1, RDF.rest, t2))
    g.add((t2, RDF.first, Literal("s2")))
    g.add((t2, RDF.rest, RDF.nil))
    g.add((a1, RDF.first, Literal("a")))
    g.add((a1, RDF.rest, t1))
    g.add((b1, RDF.first, Literal("b1")))
    g.add((b1, RDF.rest, b2))
    g.add((b2, RDF.first, Literal("b2")))
    g.add((b2, RDF.rest, t1))
    return g, a1, b1, b2, t1, t2


def values(graph: Graph, head: Node) -> list[Node]:
    return list(Collection(graph, head))


def test_splice_replace_keeps_old_lists_and_shares_tail():
    g, a1, b1, b2, t1, t2 = make_shared_graph()
    before = set(g)

    new_head = Collection(g, a1).splice(0, 1, [Literal("n1"), Literal("n2")])

    # New list has the replacement spliced in.
    assert values(g, new_head) == [
        Literal("n1"),
        Literal("n2"),
        Literal("s1"),
        Literal("s2"),
    ]
    # Old A and old B are untouched.
    assert values(g, a1) == [Literal("a"), Literal("s1"), Literal("s2")]
    assert values(g, b1) == [
        Literal("b1"),
        Literal("b2"),
        Literal("s1"),
        Literal("s2"),
    ]
    # Every original triple is still present; nothing was removed or mutated.
    assert before <= set(g)
    # Exactly two new cells were allocated (two first + two rest triples).
    assert len(set(g) - before) == 4

    # Triple-level identity checks: the new chain is fresh blank nodes ...
    old_nodes = {a1, b1, b2, t1, t2}
    assert isinstance(new_head, BNode)
    assert new_head not in old_nodes
    mid = g.value(new_head, RDF.rest)
    assert mid is not None and mid not in old_nodes
    assert (new_head, RDF.first, Literal("n1")) in g
    assert (mid, RDF.first, Literal("n2")) in g
    # ... and it re-joins the shared tail at t1 itself.
    assert (mid, RDF.rest, t1) in g
    # t1 is now shared by old A, old B and the new list.
    assert (a1, RDF.rest, t1) in g
    assert (b2, RDF.rest, t1) in g
    assert set(g.subjects(RDF.rest, t1)) == {a1, b2, mid}
    # The shared tail cells themselves were not copied.
    assert set(g.subjects(RDF.rest, t2)) == {t1}


def test_splice_delete_keeps_old_lists_and_shares_tail():
    g, a1, b1, b2, t1, t2 = make_shared_graph()
    before = set(g)

    # Delete ["b2", "s1"] from B.
    new_head = Collection(g, b1).splice(1, 3)

    assert values(g, new_head) == [Literal("b1"), Literal("s2")]
    assert values(g, a1) == [Literal("a"), Literal("s1"), Literal("s2")]
    assert values(g, b1) == [
        Literal("b1"),
        Literal("b2"),
        Literal("s1"),
        Literal("s2"),
    ]
    assert before <= set(g)
    # Only the single changed prefix cell was copied.
    assert len(set(g) - before) == 2

    # The new head is a fresh node whose rest is the shared cell t2.
    assert isinstance(new_head, BNode)
    assert new_head not in {a1, b1, b2, t1, t2}
    assert (new_head, RDF.first, Literal("b1")) in g
    assert (new_head, RDF.rest, t2) in g
    # Old chain links are all still in place.
    assert (b1, RDF.rest, b2) in g
    assert (b2, RDF.rest, t1) in g
    assert (t1, RDF.rest, t2) in g
    # t2 is shared by the old shared tail and the new list.
    assert set(g.subjects(RDF.rest, t2)) == {t1, new_head}


def test_splice_delete_everything_returns_nil_and_adds_nothing():
    g, a1, b1, b2, t1, t2 = make_shared_graph()
    before = set(g)

    new_head = Collection(g, a1).splice(0, 3)

    assert new_head == RDF.nil
    assert set(g) == before
    assert values(g, a1) == [Literal("a"), Literal("s1"), Literal("s2")]


def test_splice_insert_at_head_shares_whole_chain():
    g, a1, b1, b2, t1, t2 = make_shared_graph()
    before = set(g)

    new_head = Collection(g, a1).splice(0, 0, [Literal("h")])

    assert values(g, new_head) == [
        Literal("h"),
        Literal("a"),
        Literal("s1"),
        Literal("s2"),
    ]
    # Pure head insertion allocates exactly one cell and reuses the old head.
    assert len(set(g) - before) == 2
    assert (new_head, RDF.rest, a1) in g
    assert values(g, a1) == [Literal("a"), Literal("s1"), Literal("s2")]


def test_splice_insert_at_tail():
    g, a1, b1, b2, t1, t2 = make_shared_graph()

    new_head = Collection(g, a1).splice(3, 3, [Literal("z")])

    assert values(g, new_head) == [
        Literal("a"),
        Literal("s1"),
        Literal("s2"),
        Literal("z"),
    ]
    assert values(g, a1) == [Literal("a"), Literal("s1"), Literal("s2")]
    # The old chain still ends in rdf:nil; the new last cell does too.
    assert (t2, RDF.rest, RDF.nil) in g
    last = next(g.subjects(RDF.first, Literal("z")))
    assert (last, RDF.rest, RDF.nil) in g


def test_splice_insert_into_empty_list():
    g = Graph()

    new_head = Collection(g, RDF.nil).splice(0, 0, [Literal("only")])

    assert values(g, new_head) == [Literal("only")]
    assert (new_head, RDF.rest, RDF.nil) in g
    # Inserting nothing into the empty list is a no-op.
    assert Collection(g, RDF.nil).splice(0, 0) == RDF.nil
    assert len(g) == 2


def test_splice_replacement_may_contain_any_terms():
    g, a1, b1, b2, t1, t2 = make_shared_graph()
    bnode = BNode()
    replacement = [
        bnode,
        bnode,  # duplicates are allowed
        URIRef("http://example.org/u"),
        Literal("lit"),
        Literal("lit"),
    ]

    new_head = Collection(g, a1).splice(1, 1, replacement)

    assert values(g, new_head) == [
        Literal("a"),
        bnode,
        bnode,
        URIRef("http://example.org/u"),
        Literal("lit"),
        Literal("lit"),
        Literal("s1"),
        Literal("s2"),
    ]


@pytest.mark.parametrize(
    ("start", "stop", "expected"),
    [
        # Negative indices count back from the end.
        (-2, -1, ["a", "N", "s2"]),
        (-1, 3, ["a", "s1", "N"]),
        # Out-of-range values are clamped.
        (0, 100, ["N"]),
        (-100, 1, ["N", "s1", "s2"]),
        (100, 100, ["a", "s1", "s2", "N"]),
        # stop < start after normalization is an empty range: pure insert.
        (2, 1, ["a", "s1", "N", "s2"]),
    ],
)
def test_splice_python_slice_index_rules(
    start: int, stop: int, expected: list[str]
):
    g, a1, b1, b2, t1, t2 = make_shared_graph()

    new_head = Collection(g, a1).splice(start, stop, [Literal("N")])

    assert values(g, new_head) == [Literal(v) for v in expected]
    assert values(g, a1) == [Literal("a"), Literal("s1"), Literal("s2")]


def test_splice_noop_returns_same_head():
    g, a1, b1, b2, t1, t2 = make_shared_graph()
    before = set(g)

    assert Collection(g, a1).splice(2, 2) == a1
    assert set(g) == before


def malformed_graphs() -> dict[str, tuple[Node, Node]]:
    """(head, bad node) pairs, one per structural defect."""
    cases: dict[str, tuple[Node, Node]] = {}

    head, bad = BNode(), BNode()
    g = Graph()
    g.add((head, RDF.first, Literal(1)))
    g.add((head, RDF.rest, bad))
    g.add((bad, RDF.rest, RDF.nil))  # missing rdf:first
    cases["missing rdf:first"] = (head, bad)
    _graphs["missing rdf:first"] = g

    head, bad = BNode(), BNode()
    g = Graph()
    g.add((head, RDF.first, Literal(1)))
    g.add((head, RDF.rest, bad))
    g.add((bad, RDF.first, Literal(2)))  # missing rdf:rest
    cases["missing rdf:rest"] = (head, bad)
    _graphs["missing rdf:rest"] = g

    head = BNode()
    g = Graph()
    g.add((head, RDF.first, Literal(1)))
    g.add((head, RDF.first, Literal(2)))  # multiple rdf:first
    g.add((head, RDF.rest, RDF.nil))
    cases["multiple rdf:first"] = (head, head)
    _graphs["multiple rdf:first"] = g

    head, other = BNode(), BNode()
    g = Graph()
    g.add((head, RDF.first, Literal(1)))
    g.add((head, RDF.rest, other))
    g.add((head, RDF.rest, RDF.nil))  # multiple rdf:rest
    cases["multiple rdf:rest"] = (head, head)
    _graphs["multiple rdf:rest"] = g

    head, bad = BNode(), BNode()
    g = Graph()
    g.add((head, RDF.first, Literal(1)))
    g.add((head, RDF.rest, bad))
    g.add((bad, RDF.first, Literal(2)))
    g.add((bad, RDF.rest, head))  # cycle
    cases["cycle"] = (head, head)
    _graphs["cycle"] = g

    head = BNode()
    bad = URIRef("http://example.org/not-a-list")
    g = Graph()
    g.add((head, RDF.first, Literal(1)))
    g.add((head, RDF.rest, bad))  # tail is not a list node
    cases["non-list tail node"] = (head, bad)
    _graphs["non-list tail node"] = g

    return cases


_graphs: dict[str, Graph] = {}
_cases = malformed_graphs()


@pytest.mark.parametrize("case", list(_cases), ids=list(_cases))
def test_splice_malformed_list_reports_bad_node_and_changes_nothing(case: str):
    head, bad_node = _cases[case]
    g = _graphs[case]
    before = set(g)

    with pytest.raises(MalformedListError) as excinfo:
        Collection(g, head).splice(0, 1, [Literal("x")])

    assert excinfo.value.node == bad_node
    assert str(bad_node.n3()) in str(excinfo.value)
    # A failed splice must not leave a half-built new chain behind.
    assert set(g) == before


def test_splice_is_scoped_to_the_passed_graph():
    dataset = Dataset()
    g1 = dataset.graph(URIRef("http://example.org/g1"))
    g2 = dataset.graph(URIRef("http://example.org/g2"))
    head = URIRef("http://example.org/head")
    Collection(g1, head, [Literal(1), Literal(2)])
    Collection(g2, head, [Literal(9)])
    g2_before = set(g2)
    default_before = set(dataset.default_graph)

    new_head = Collection(g1, head).splice(0, 1, [Literal(7)])

    assert values(g1, new_head) == [Literal(7), Literal(2)]
    # The same-named head and the chain in the other graph are untouched.
    assert set(g2) == g2_before
    assert values(g2, head) == [Literal(9)]
    assert set(dataset.default_graph) == default_before
    assert (new_head, None, None) not in g2
