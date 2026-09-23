import logging

import pytest

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.collection import Collection, CollectionError
from rdflib.namespace import RDF

EX_POINTS = URIRef("http://example.org/points")
EX_NOTE = URIRef("http://example.org/note")


def _shared_tail_graph():
    """Two collections sharing the last two chain nodes.

    A = [1, 2, 3, 4] with cells a0 -> a1 -> s0 -> s1
    B = [9, 3, 4]    with cells b0 -> s0 -> s1
    """
    g = Graph()
    a0, a1, s0, s1, b0 = (BNode() for _ in range(5))
    for cell, value, rest in [
        (a0, 1, a1),
        (a1, 2, s0),
        (s0, 3, s1),
        (s1, 4, RDF.nil),
        (b0, 9, s0),
    ]:
        g.add((cell, RDF.first, Literal(value)))
        g.add((cell, RDF.rest, rest))
    return g, a0, b0, s0, s1


def test_scenario() -> None:
    # Taken from https://github.com/RDFLib/rdflib/blob/8a92d3565bf2e502a7c4cadb34b29db72c89d623/rdflib/collection.py#L272-L304
    g = Graph()

    c = Collection(g, BNode())

    assert len(c) == 0

    c = Collection(g, BNode(), [Literal("1"), Literal("2"), Literal("3"), Literal("4")])

    assert len(c) == 4

    assert c[1] == Literal("2"), c[1]

    del c[1]

    assert list(c) == [Literal("1"), Literal("3"), Literal("4")], list(c)

    with pytest.raises(IndexError):
        del c[500]

    c.append(Literal("5"))

    logging.debug("list(c) = %s", list(c))

    for i in c:
        logging.debug("i = %s", i)

    del c[3]

    c.clear()

    assert len(c) == 0


def test_empty_list() -> None:
    nil = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#nil")
    g = Graph()

    c = Collection(g, nil)

    assert set(g) == set(), "Collection changed the graph"

    assert len(c) == 0


def test_splice_shared_tail_middle_edit() -> None:
    g, a0, b0, s0, s1 = _shared_tail_graph()
    a, b = Collection(g, a0), Collection(g, b0)

    deleted = a.splice(1, 1, [Literal(7), Literal(8)])

    assert deleted == [Literal(2)]
    assert list(a) == [Literal(1), Literal(7), Literal(8), Literal(3), Literal(4)]
    assert list(b) == [Literal(9), Literal(3), Literal(4)]
    assert (s0, RDF.first, Literal(3)) in g
    assert (s1, RDF.first, Literal(4)) in g


def test_splice_shared_tail_rewrite_refused_by_default() -> None:
    g, a0, b0, s0, s1 = _shared_tail_graph()
    a, b = Collection(g, a0), Collection(g, b0)
    size_before = len(g)

    with pytest.raises(CollectionError):
        a.splice(3, 1, [Literal(7)])  # would rewrite s0's rest and remove s1
    with pytest.raises(CollectionError):
        a.splice(2, 2)  # would remove the shared s0 and s1

    assert len(g) == size_before
    assert list(a) == [Literal(1), Literal(2), Literal(3), Literal(4)]
    assert list(b) == [Literal(9), Literal(3), Literal(4)]


def test_splice_shared_tail_detach() -> None:
    g, a0, b0, s0, s1 = _shared_tail_graph()
    ref = BNode()
    g.add((ref, EX_POINTS, s0))  # external reference to a shared cell
    a, b = Collection(g, a0), Collection(g, b0)

    deleted = a.splice(3, 1, [Literal(7)], detach=True)

    assert deleted == [Literal(4)]
    assert list(a) == [Literal(1), Literal(2), Literal(3), Literal(7)]
    assert list(b) == [Literal(9), Literal(3), Literal(4)]
    # the original shared cells stay put for b and the external reference
    assert (s0, RDF.first, Literal(3)) in g
    assert (s0, RDF.rest, s1) in g
    assert (s1, RDF.first, Literal(4)) in g
    assert (ref, EX_POINTS, s0) in g


def test_splice_shared_tail_detach_delete() -> None:
    g, a0, b0, s0, s1 = _shared_tail_graph()
    a, b = Collection(g, a0), Collection(g, b0)

    deleted = a.splice(2, 2, [], detach=True)

    assert deleted == [Literal(3), Literal(4)]
    assert list(a) == [Literal(1), Literal(2)]
    assert list(b) == [Literal(9), Literal(3), Literal(4)]


def test_splice_detach_copies_shared_prefix_and_annotations() -> None:
    g = Graph()
    a0, a1, s0, s1, b0 = (BNode() for _ in range(5))
    # A = [1, 2, 3, 4]; B = [9, 2, 3, 4] sharing every cell from a1 on
    for cell, value, rest in [
        (a0, 1, a1),
        (a1, 2, s0),
        (s0, 3, s1),
        (s1, 4, RDF.nil),
        (b0, 9, a1),
    ]:
        g.add((cell, RDF.first, Literal(value)))
        g.add((cell, RDF.rest, rest))
    g.add((a1, EX_NOTE, Literal("annotated")))
    a, b = Collection(g, a0), Collection(g, b0)

    assert a.splice(2, 0, [Literal(7)], detach=True) == []

    assert list(a) == [Literal(1), Literal(2), Literal(7), Literal(3), Literal(4)]
    assert list(b) == [Literal(9), Literal(2), Literal(3), Literal(4)]
    # the original shared cell keeps its annotation for b
    assert (a1, EX_NOTE, Literal("annotated")) in g
    # the copy in a's chain replicates the annotation
    a1_copy = g.value(a0, RDF.rest)
    assert a1_copy is not None and a1_copy != a1
    assert (a1_copy, RDF.first, Literal(2)) in g
    assert (a1_copy, EX_NOTE, Literal("annotated")) in g


def test_splice_append_at_end() -> None:
    g = Graph()
    c = Collection(g, BNode(), [Literal(1), Literal(2)])

    assert c.splice(2, 0, [Literal(3)]) == []

    assert list(c) == [Literal(1), Literal(2), Literal(3)]


def test_splice_clear() -> None:
    g = Graph()
    head = BNode()
    c = Collection(g, head, [Literal(1), Literal(2)])

    assert c.splice(0, 2) == [Literal(1), Literal(2)]

    assert list(c) == []
    assert c.uri == head  # the entry resource is preserved
    # the emptied collection can be spliced again
    assert c.splice(0, 0, [Literal(5)]) == []
    assert list(c) == [Literal(5)]


def test_splice_empty_list() -> None:
    g = Graph()
    c = Collection(g, BNode())

    assert c.splice(0, 0, []) == []
    assert list(c) == []
    assert c.splice(0, 0, [Literal(1), Literal(2)]) == []
    assert list(c) == [Literal(1), Literal(2)]
    with pytest.raises(IndexError):
        c.splice(3, 0, [Literal(9)])


def test_splice_nil_rejected() -> None:
    g = Graph()
    c = Collection(g, RDF.nil)
    with pytest.raises(ValueError):
        c.splice(0, 0, [Literal(1)])


def test_splice_head_insert_and_delete_preserve_entry() -> None:
    g = Graph()
    head = BNode()
    g.add((head, EX_NOTE, Literal("keep me")))
    c = Collection(g, head, [Literal(1), Literal(2), Literal(3)])

    assert c.splice(0, 0, [Literal(0)]) == []
    assert list(c) == [Literal(0), Literal(1), Literal(2), Literal(3)]
    assert c.splice(0, 2) == [Literal(0), Literal(1)]
    assert list(c) == [Literal(2), Literal(3)]
    assert c.uri == head
    assert (head, EX_NOTE, Literal("keep me")) in g


def test_splice_does_not_copy_inserted_resources() -> None:
    g = Graph()
    c = Collection(g, BNode(), [Literal(1)])
    resource = URIRef("http://example.org/resource")
    g.add((resource, RDF.type, URIRef("http://example.org/Type")))
    size_before = len(g)

    c.splice(1, 0, [resource])

    assert list(c)[1] == resource
    # exactly one new chain node: one rdf:first and one rdf:rest triple
    assert len(g) == size_before + 2
    assert len(list(g.objects(resource, RDF.type))) == 1


def test_splice_malformed_chains_leave_graph_untouched() -> None:
    # missing rdf:rest
    g = Graph()
    head = BNode()
    g.add((head, RDF.first, Literal(1)))
    with pytest.raises(CollectionError):
        Collection(g, head).splice(0, 0, [Literal(2)])
    assert len(g) == 1

    # multiple rdf:first
    g = Graph()
    head = BNode()
    g.add((head, RDF.first, Literal(1)))
    g.add((head, RDF.first, Literal(2)))
    g.add((head, RDF.rest, RDF.nil))
    with pytest.raises(CollectionError):
        Collection(g, head).splice(0, 1)
    assert len(g) == 3

    # cycle
    g = Graph()
    a, b = BNode(), BNode()
    g.add((a, RDF.first, Literal(1)))
    g.add((a, RDF.rest, b))
    g.add((b, RDF.first, Literal(2)))
    g.add((b, RDF.rest, a))
    with pytest.raises(CollectionError):
        Collection(g, a).splice(0, 1)
    assert len(g) == 4


def test_splice_out_of_bounds() -> None:
    g = Graph()
    c = Collection(g, BNode(), [Literal(1), Literal(2)])
    size_before = len(g)
    for start, count in [(-1, 0), (3, 0), (0, 3), (1, 2), (0, -1)]:
        with pytest.raises(IndexError):
            c.splice(start, count)
    assert len(g) == size_before


def test_splice_shared_entry_cannot_detach() -> None:
    g = Graph()
    a0, a1, x = (BNode() for _ in range(3))
    g.add((a0, RDF.first, Literal(1)))
    g.add((a0, RDF.rest, a1))
    g.add((a1, RDF.first, Literal(2)))
    g.add((a1, RDF.rest, RDF.nil))
    g.add((x, RDF.rest, a0))  # another list's tail includes our entry node
    c = Collection(g, a0)

    with pytest.raises(CollectionError):
        c.splice(0, 1, [Literal(9)], detach=True)

    assert list(c) == [Literal(1), Literal(2)]
