import logging

import pytest

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, RDFS

EX = Namespace("http://example.org/")


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


def make_shared_tail():
    """
    Two lists sharing their last two chain nodes (values item3/item4):
    A = [item1, item2, item3, item4], B = [item9, item3, item4].
    """
    g = Graph()
    items = [EX.item1, EX.item2, EX.item3, EX.item4]
    for index, item in enumerate(items, start=1):
        g.add((item, RDFS.label, Literal("item%d label" % index)))
    list_a = Collection(g, BNode(), items)
    shared_node = list_a._get_container(2)
    list_b = Collection(g, BNode(), [EX.item9])
    g.set((list_b._end(), RDF.rest, shared_node))
    g.add((EX.config, EX.items, list_a.uri))
    g.add((EX.otherConfig, EX.items, list_b.uri))
    g.add((list_a.uri, RDFS.label, Literal("list A")))
    g.add((shared_node, EX.note, Literal("shared node")))
    return g, list_a, list_b, shared_node


def test_splice_shared_tail_default_refuses() -> None:
    g, list_a, list_b, _ = make_shared_tail()
    before = set(g)

    with pytest.raises(ValueError):
        list_a.splice(2, 1, [EX.new1, EX.new2])

    assert set(g) == before, "graph was left half-edited"
    assert list(list_a) == [EX.item1, EX.item2, EX.item3, EX.item4]
    assert list(list_b) == [EX.item9, EX.item3, EX.item4]


def test_splice_shared_tail_detach() -> None:
    g, list_a, list_b, shared_node = make_shared_tail()

    removed = list_a.splice(2, 1, [EX.new1, EX.new2], detach=True)

    assert removed == [EX.item3]
    assert list(list_a) == [EX.item1, EX.item2, EX.new1, EX.new2, EX.item4]
    assert list(list_b) == [EX.item9, EX.item3, EX.item4]
    # The shared node stays in place for the other list.
    assert list_b._get_container(1) == shared_node
    assert (shared_node, EX.note, Literal("shared node")) in g
    # External references to the entry still designate the edited list.
    assert g.value(EX.config, EX.items) == list_a.uri
    assert list(Collection(g, g.value(EX.config, EX.items))) == list(list_a)
    # Entry annotations are preserved.
    assert (list_a.uri, RDFS.label, Literal("list A")) in g
    # List values are graph resources: never copied or removed.
    assert (EX.item3, RDFS.label, Literal("item3 label")) in g
    assert len(list(g.triples((EX.item3, None, None)))) == 1


def test_splice_shared_tail_append_requires_detach() -> None:
    g, list_a, list_b, shared_node = make_shared_tail()

    before = set(g)
    with pytest.raises(ValueError):
        list_a.splice(4, 0, [EX.item5])
    assert set(g) == before

    removed = list_a.splice(4, 0, [EX.item5], detach=True)

    assert removed == []
    assert list(list_a) == [EX.item1, EX.item2, EX.item3, EX.item4, EX.item5]
    assert list(list_b) == [EX.item9, EX.item3, EX.item4]
    # The shared segment was copied; the original chain is untouched.
    copied_node = list_a._get_container(2)
    assert copied_node != shared_node
    assert list_b._get_container(1) == shared_node
    # Annotations are duplicated onto the copy, kept on the original.
    assert (copied_node, EX.note, Literal("shared node")) in g
    assert (shared_node, EX.note, Literal("shared node")) in g
    # Values are the same resources, not copies.
    assert g.value(copied_node, RDF.first) == EX.item3


def test_splice_append_at_end_private() -> None:
    g = Graph()
    c = Collection(g, BNode(), [EX.item1, EX.item2])

    assert c.splice(2, 0, [EX.item3]) == []
    assert list(c) == [EX.item1, EX.item2, EX.item3]


def test_splice_clear() -> None:
    g = Graph()
    c = Collection(g, BNode(), [EX.item1, EX.item2, EX.item3])
    g.add((c.uri, RDFS.label, Literal("keep me")))

    removed = c.splice(0, 3, [])

    assert removed == [EX.item1, EX.item2, EX.item3]
    assert list(c) == []
    assert len(c) == 0
    # The entry survives as a bare node and keeps its annotations.
    assert (c.uri, RDFS.label, Literal("keep me")) in g
    assert (None, RDF.first, None) not in g
    assert (None, RDF.rest, None) not in g


def test_splice_clear_shared_tail_detach() -> None:
    g, list_a, list_b, shared_node = make_shared_tail()

    with pytest.raises(ValueError):
        list_a.splice(0, 4, [])

    removed = list_a.splice(0, 4, [], detach=True)

    assert removed == [EX.item1, EX.item2, EX.item3, EX.item4]
    assert list(list_a) == []
    assert list(list_b) == [EX.item9, EX.item3, EX.item4]
    assert (shared_node, EX.note, Literal("shared node")) in g


def test_splice_empty_list() -> None:
    g = Graph()
    c = Collection(g, BNode())

    assert c.splice(0, 0, []) == []
    assert set(g) == set()

    assert c.splice(0, 0, [EX.item1, EX.item2]) == []
    assert list(c) == [EX.item1, EX.item2]

    with pytest.raises(IndexError):
        c.splice(5, 0, [EX.item3])

    nil = Collection(g, RDF.nil)
    with pytest.raises(ValueError):
        nil.splice(0, 0, [EX.item1])


def test_splice_out_of_range_leaves_graph_untouched() -> None:
    g = Graph()
    c = Collection(g, BNode(), [EX.item1, EX.item2])
    before = set(g)

    with pytest.raises(IndexError):
        c.splice(3, 0, [EX.item3])
    with pytest.raises(IndexError):
        c.splice(0, 3, [])
    with pytest.raises(IndexError):
        c.splice(-1, 0, [EX.item3])
    with pytest.raises(ValueError):
        c.splice(0, -1, [])

    assert set(g) == before


def test_splice_malformed_lists_leave_graph_untouched() -> None:
    # Missing rdf:rest mid-chain.
    g = Graph()
    c = Collection(g, BNode(), [EX.item1, EX.item2, EX.item3])
    g.remove((c._get_container(1), RDF.rest, None))
    before = set(g)
    with pytest.raises(ValueError):
        c.splice(1, 1, [EX.item9])
    assert set(g) == before

    # Multiple rdf:first arcs.
    g = Graph()
    c = Collection(g, BNode(), [EX.item1, EX.item2])
    g.add((c._get_container(1), RDF.first, EX.item3))
    before = set(g)
    with pytest.raises(ValueError):
        c.splice(0, 1, [])
    assert set(g) == before

    # Cycle.
    g = Graph()
    c = Collection(g, BNode(), [EX.item1, EX.item2])
    g.set((c._get_container(1), RDF.rest, c.uri))
    before = set(g)
    with pytest.raises(ValueError):
        c.splice(0, 1, [])
    assert set(g) == before
