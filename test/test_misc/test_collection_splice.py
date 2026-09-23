import pytest

from rdflib import BNode, Dataset, Graph, Literal, URIRef
from rdflib.collection import Collection, MalformedCollectionError, splice
from rdflib.namespace import RDF


def build_shared_tail_graph():
    """
    Build a graph where lists A and B share the tail chain s1 -> s2:

    A = (1 2 3 4):  a1 -> a2 -> s1 -> s2 -> rdf:nil
    B = (9 3 4):    b1 -------^
    """
    g = Graph()
    a1, a2, s1, s2, b1 = BNode(), BNode(), BNode(), BNode(), BNode()
    g.add((s1, RDF.first, Literal(3)))
    g.add((s1, RDF.rest, s2))
    g.add((s2, RDF.first, Literal(4)))
    g.add((s2, RDF.rest, RDF.nil))
    g.add((a1, RDF.first, Literal(1)))
    g.add((a1, RDF.rest, a2))
    g.add((a2, RDF.first, Literal(2)))
    g.add((a2, RDF.rest, s1))
    g.add((b1, RDF.first, Literal(9)))
    g.add((b1, RDF.rest, s1))
    return g, a1, a2, s1, s2, b1


def chain_nodes(g: Graph, head):
    """Return the rdf:rest chain nodes from head down to (excluding) rdf:nil."""
    nodes = []
    node = head
    while node != RDF.nil:
        nodes.append(node)
        node = g.value(node, RDF.rest)
    return nodes


def test_splice_replace_preserves_shared_tail():
    g, a1, a2, s1, s2, b1 = build_shared_tail_graph()
    before = set(g)

    # Replace elements [1, 3) of A, i.e. (2 3), with (20 30).
    new_head = splice(g, a1, 1, 3, [Literal(20), Literal(30)])

    # New list has the expected content.
    assert list(Collection(g, new_head)) == [
        Literal(1),
        Literal(20),
        Literal(30),
        Literal(4),
    ]

    # The tail node holding 4 is the very same node s2 that A and B shared.
    new_chain = chain_nodes(g, new_head)
    assert len(new_chain) == 4
    assert new_chain[3] is s2
    assert (new_chain[2], RDF.rest, s2) in g
    assert (s2, RDF.first, Literal(4)) in g
    assert (s2, RDF.rest, RDF.nil) in g

    # The first three nodes are freshly allocated blank nodes.
    for node in new_chain[:3]:
        assert isinstance(node, BNode)
        assert node not in (a1, a2, s1, s2, b1)

    # Nothing pre-existing was removed or altered: splice only added triples.
    assert before <= set(g)

    # Old A is fully intact, still sharing its tail with B.
    assert chain_nodes(g, a1) == [a1, a2, s1, s2]
    assert list(Collection(g, a1)) == [Literal(1), Literal(2), Literal(3), Literal(4)]
    assert (a2, RDF.rest, s1) in g

    # B is fully intact too.
    assert chain_nodes(g, b1) == [b1, s1, s2]
    assert list(Collection(g, b1)) == [Literal(9), Literal(3), Literal(4)]


def test_splice_delete_returns_shared_node():
    g, a1, a2, s1, s2, b1 = build_shared_tail_graph()
    before = set(g)

    # Delete the only own element of B; the result is exactly the shared tail.
    new_head = splice(g, b1, 0, 1)
    assert new_head is s1
    assert list(Collection(g, new_head)) == [Literal(3), Literal(4)]
    assert chain_nodes(g, new_head) == [s1, s2]

    # Delete the whole own prefix of A; again the shared node comes back.
    assert splice(g, a1, 0, 2) is s1

    # Both original lists are untouched and no new triples were even needed.
    assert set(g) == before
    assert list(Collection(g, a1)) == [Literal(1), Literal(2), Literal(3), Literal(4)]
    assert list(Collection(g, b1)) == [Literal(9), Literal(3), Literal(4)]


def test_splice_delete_middle_shares_suffix():
    g, a1, a2, s1, s2, b1 = build_shared_tail_graph()

    # Delete element 2 (index -2 .. -1) from A -> (1 2 4), tail s2 shared.
    new_head = splice(g, a1, -2, -1)
    assert list(Collection(g, new_head)) == [Literal(1), Literal(2), Literal(4)]
    new_chain = chain_nodes(g, new_head)
    assert new_chain[-1] is s2
    assert (new_chain[-2], RDF.rest, s2) in g

    # Old lists unchanged.
    assert chain_nodes(g, a1) == [a1, a2, s1, s2]
    assert chain_nodes(g, b1) == [b1, s1, s2]


def test_splice_insert_head_tail_and_empty():
    g, a1, a2, s1, s2, b1 = build_shared_tail_graph()

    # Insert at the head: the whole old chain is shared, nothing copied.
    new_head = splice(g, a1, 0, 0, [Literal(0)])
    assert list(Collection(g, new_head)) == [
        Literal(0),
        Literal(1),
        Literal(2),
        Literal(3),
        Literal(4),
    ]
    assert chain_nodes(g, new_head) == [chain_nodes(g, new_head)[0], a1, a2, s1, s2]
    assert (chain_nodes(g, new_head)[0], RDF.rest, a1) in g

    # Append at the tail via an out-of-range index.
    new_head = splice(g, a1, 100, 200, [Literal(5)])
    assert list(Collection(g, new_head)) == [
        Literal(1),
        Literal(2),
        Literal(3),
        Literal(4),
        Literal(5),
    ]
    new_chain = chain_nodes(g, new_head)
    assert len(new_chain) == 5
    assert (new_chain[3], RDF.rest, new_chain[4]) in g
    assert (new_chain[4], RDF.rest, RDF.nil) in g

    # Insert into the empty list.
    empty_head = splice(g, RDF.nil, 0, 0, [Literal(1), Literal(2)])
    assert list(Collection(g, empty_head)) == [Literal(1), Literal(2)]

    # Splicing nothing into the empty list stays the empty list.
    assert splice(g, RDF.nil, 0, 0) == RDF.nil

    # Deleting everything yields the empty list.
    assert splice(g, a1, 0, 4) == RDF.nil


def test_splice_index_rules():
    g, a1, a2, s1, s2, b1 = build_shared_tail_graph()

    # stop < start after normalization is a pure insertion at start.
    new_head = splice(g, a1, 3, 1, [Literal(25)])
    assert list(Collection(g, new_head)) == [
        Literal(1),
        Literal(2),
        Literal(3),
        Literal(25),
        Literal(4),
    ]

    # Indices below -len clamp to 0: insert at the head.
    new_head = splice(g, a1, -100, -100, [Literal(0)])
    assert list(Collection(g, new_head))[0] == Literal(0)
    assert len(Collection(g, new_head)) == 5

    # Negative out-of-range delete removes from the head.
    new_head = splice(g, a1, -100, 2)
    assert list(Collection(g, new_head)) == [Literal(3), Literal(4)]
    assert new_head is s1


def test_splice_values_may_be_any_term():
    g = Graph()
    b = BNode()
    head = splice(g, RDF.nil, 0, 0, [b, b, URIRef("urn:x"), b])
    assert list(Collection(g, head)) == [b, b, URIRef("urn:x"), b]


def test_splice_malformed_lists_report_bad_node():
    # Missing rdf:first.
    g = Graph()
    n1 = BNode()
    g.add((n1, RDF.rest, RDF.nil))
    before = set(g)
    with pytest.raises(MalformedCollectionError) as excinfo:
        splice(g, n1, 0, 0, [Literal(1)])
    assert excinfo.value.node == n1
    assert "first" in excinfo.value.reason
    assert set(g) == before, "failed splice left triples behind"

    # Multiple rdf:rest arcs.
    g = Graph()
    n1, n2 = BNode(), BNode()
    g.add((n1, RDF.first, Literal(1)))
    g.add((n1, RDF.rest, n2))
    g.add((n1, RDF.rest, BNode()))
    before = set(g)
    with pytest.raises(MalformedCollectionError) as excinfo:
        splice(g, n1, 0, 1)
    assert excinfo.value.node == n1
    assert "rest" in excinfo.value.reason
    assert set(g) == before

    # Cycle.
    g = Graph()
    n1, n2 = BNode(), BNode()
    g.add((n1, RDF.first, Literal(1)))
    g.add((n1, RDF.rest, n2))
    g.add((n2, RDF.first, Literal(2)))
    g.add((n2, RDF.rest, n1))
    before = set(g)
    with pytest.raises(MalformedCollectionError) as excinfo:
        splice(g, n1, 0, 1)
    assert excinfo.value.node in (n1, n2)
    assert "cycle" in excinfo.value.reason
    assert set(g) == before

    # Non-list tail node (a literal in rdf:rest position).
    g = Graph()
    n1 = BNode()
    g.add((n1, RDF.first, Literal(1)))
    g.add((n1, RDF.rest, Literal("not-a-list")))
    before = set(g)
    with pytest.raises(MalformedCollectionError) as excinfo:
        splice(g, n1, 0, 1)
    assert excinfo.value.node == Literal("not-a-list")
    assert set(g) == before


def test_splice_is_scoped_to_the_given_graph():
    ds = Dataset()
    g1 = ds.graph(URIRef("urn:g1"))
    g2 = ds.graph(URIRef("urn:g2"))

    head = BNode()
    Collection(g1, head, [Literal(1), Literal(2)])
    # Same node identifier used for something else in another graph.
    g2.add((head, RDF.first, Literal(999)))
    g2_before = set(g2)

    new_head = splice(g1, head, 0, 1, [Literal(10)])
    assert list(Collection(g1, new_head)) == [Literal(10), Literal(2)]

    # The other graph of the dataset is neither consulted nor modified.
    assert set(g2) == g2_before
    assert (head, RDF.first, Literal(999)) in g2
