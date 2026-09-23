"""Tests for the atomic SPARQL Update mode (``Graph.update(..., atomic=True)``)."""

from __future__ import annotations

import pytest

from rdflib import BNode, Dataset, Graph, Namespace
from rdflib.term import Identifier

EX = Namespace("http://example.org/")

PREFIXES = "PREFIX : <http://example.org/>"


def _graph_ids(dataset: Dataset) -> set[Identifier]:
    return {g.identifier for g in dataset.graphs()}


def test_atomic_rolls_back_on_failure() -> None:
    """COPY a named graph, DROP the source, then CREATE an already existing
    graph: the last operation fails and the whole request is rolled back."""
    dataset = Dataset()
    dataset.graph(EX.g1).add((EX.s, EX.p, EX.o))
    dataset.graph(EX.empty)

    with pytest.raises(Exception, match="already exists"):
        dataset.update(
            PREFIXES + " COPY :g1 TO :g2 ; DROP GRAPH :g1 ; CREATE GRAPH :g2",
            atomic=True,
        )

    # triples are back
    assert (EX.s, EX.p, EX.o) in dataset.get_context(EX.g1)
    # the copied graph was never published
    assert EX.g2 not in _graph_ids(dataset)
    # the empty named graph is not lost
    assert EX.empty in _graph_ids(dataset)


def test_atomic_silent_create_commits_in_order() -> None:
    """Same request with CREATE SILENT: the ignorable operation error is
    swallowed and the preceding operations are committed in order."""
    dataset = Dataset()
    bnode = BNode()
    dataset.graph(EX.g1).add((bnode, EX.p, EX.o))

    dataset.update(
        PREFIXES + " COPY :g1 TO :g2 ; DROP GRAPH :g1 ; CREATE SILENT GRAPH :g2",
        atomic=True,
    )

    assert EX.g1 not in _graph_ids(dataset)
    g2 = dataset.get_context(EX.g2)
    subjects = list(g2.subjects(EX.p, EX.o))
    assert len(subjects) == 1
    # blank node identity is preserved
    assert subjects[0] is bnode


def test_atomic_views_observe_commit_and_rollback() -> None:
    """Code holding the Dataset or Graph views of it sees the new content
    after a commit and the original content after a rollback."""
    dataset = Dataset()
    dataset.graph(EX.g1).add((EX.s, EX.p, EX.o))
    view = dataset.get_context(EX.g1)

    dataset.update(PREFIXES + " INSERT DATA { GRAPH :g1 { :s2 :p :o } }", atomic=True)
    assert (EX.s2, EX.p, EX.o) in view

    with pytest.raises(Exception, match="already exists"):
        dataset.update(
            PREFIXES + " INSERT DATA { GRAPH :g1 { :s3 :p :o } } ; CREATE GRAPH :g1",
            atomic=True,
        )
    assert (EX.s3, EX.p, EX.o) not in view
    assert (EX.s, EX.p, EX.o) in view
    assert (EX.s2, EX.p, EX.o) in view


def test_atomic_later_operations_see_earlier_writes() -> None:
    """Within one request, a WHERE clause reads the results of the
    operations that came before it."""
    dataset = Dataset()
    dataset.update(
        PREFIXES + " INSERT DATA { GRAPH :g1 { :s :p :o } } ;"
        " INSERT { GRAPH :g2 { ?s ?p ?o } } WHERE { GRAPH :g1 { ?s ?p ?o } }",
        atomic=True,
    )
    assert (EX.s, EX.p, EX.o) in dataset.get_context(EX.g2)


def test_atomic_rollback_covers_all_operations() -> None:
    """INSERT/DELETE, CLEAR, DROP, ADD, COPY and MOVE effects are all
    undone when a later operation fails."""
    dataset = Dataset()
    dataset.graph(EX.g1).add((EX.a, EX.p, EX.o))
    dataset.graph(EX.g2).add((EX.b, EX.p, EX.o))
    dataset.graph(EX.g5).add((EX.c, EX.p, EX.o))
    before = set(dataset.quads((None, None, None)))
    before_ids = _graph_ids(dataset)

    with pytest.raises(Exception, match="already exists"):
        dataset.update(
            PREFIXES + " INSERT DATA { GRAPH :g1 { :x :p :o } } ;"
            " DELETE DATA { GRAPH :g2 { :b :p :o } } ;"
            " ADD :g1 TO :g3 ;"
            " COPY :g1 TO :g6 ;"
            " MOVE :g2 TO :g4 ;"
            " CLEAR GRAPH :g5 ;"
            " DROP GRAPH :g6 ;"
            " CREATE GRAPH :g3",
            atomic=True,
        )

    assert set(dataset.quads((None, None, None))) == before
    assert _graph_ids(dataset) == before_ids


def test_atomic_commit_applies_all_operations() -> None:
    """The same operations are all published when nothing fails."""
    dataset = Dataset()
    dataset.graph(EX.g1).add((EX.a, EX.p, EX.o))
    dataset.graph(EX.g2).add((EX.b, EX.p, EX.o))

    dataset.update(
        PREFIXES + " INSERT DATA { GRAPH :g1 { :x :p :o } } ;"
        " DELETE DATA { GRAPH :g2 { :b :p :o } } ;"
        " ADD :g1 TO :g3 ;"
        " MOVE :g2 TO :g4 ;"
        " CLEAR GRAPH :g1",
        atomic=True,
    )

    assert len(dataset.get_context(EX.g1)) == 0
    assert (EX.a, EX.p, EX.o) in dataset.get_context(EX.g3)
    assert (EX.x, EX.p, EX.o) in dataset.get_context(EX.g3)
    assert len(dataset.get_context(EX.g2)) == 0
    assert EX.g2 not in _graph_ids(dataset)
    assert len(dataset.get_context(EX.g4)) == 0


def test_atomic_rejects_load_before_first_write() -> None:
    """LOAD may access the network and is rejected in atomic mode, before
    any operation of the request is carried out."""
    dataset = Dataset()
    with pytest.raises(ValueError, match="LOAD"):
        dataset.update(
            PREFIXES + " INSERT DATA { :s :p :o } ; LOAD <file:///nonexistent.ttl>",
            atomic=True,
        )
    assert len(dataset.default_graph) == 0


def test_atomic_unsupported_store_rejected_before_first_write() -> None:
    """A store without atomic update support refuses the request before
    the first write."""
    graph = Graph(store="SimpleMemory")
    with pytest.raises(NotImplementedError, match="atomic update mode"):
        graph.update(PREFIXES + " INSERT DATA { :s :p :o }", atomic=True)
    assert len(graph) == 0


def test_atomic_silent_does_not_hide_syntax_errors() -> None:
    """SILENT only swallows operation errors that the spec allows to be
    ignored; syntax errors still fail the request."""
    dataset = Dataset()
    with pytest.raises(Exception):
        dataset.update(
            PREFIXES + " CREATE SILENT GRAPH :g1 ; THIS IS NOT SPARQL",
            atomic=True,
        )
    assert EX.g1 not in _graph_ids(dataset)


def test_atomic_plain_graph() -> None:
    """Atomic mode also works for a plain Graph on the Memory store."""
    graph = Graph()
    graph.update(PREFIXES + " INSERT DATA { :s :p :o }", atomic=True)
    assert (EX.s, EX.p, EX.o) in graph

    # a plain Graph has no dataset, so CREATE fails; the earlier insert
    # of the same request is rolled back
    with pytest.raises(Exception):
        graph.update(
            PREFIXES + " INSERT DATA { :s2 :p :o } ; CREATE GRAPH :g1",
            atomic=True,
        )
    assert (EX.s2, EX.p, EX.o) not in graph


def test_non_atomic_update_remains_unchanged() -> None:
    """Without atomic=True the default behavior is kept: a failing
    operation aborts the request but earlier effects remain."""
    dataset = Dataset()
    with pytest.raises(Exception, match="already exists"):
        dataset.update(
            PREFIXES + " INSERT DATA { GRAPH :g1 { :s :p :o } } ; CREATE GRAPH :g1"
        )
    assert (EX.s, EX.p, EX.o) in dataset.get_context(EX.g1)
