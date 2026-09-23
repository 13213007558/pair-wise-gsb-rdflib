"""
Tests for atomic execution of SPARQL update requests, i.e.
`Graph.update(..., atomic=True)`.
"""

import pytest

from rdflib import BNode, Dataset, Graph, Literal, Namespace

EX = Namespace("http://example.org/")


def _graph_names(dataset: Dataset) -> set:
    return {g.identifier for g in dataset.graphs()}


def _quads(dataset: Dataset) -> set:
    return set(dataset.quads((None, None, None, None)))


@pytest.fixture()
def dataset() -> Dataset:
    ds = Dataset()
    g1 = ds.graph(EX.g1)
    g1.add((EX.s, EX.p, BNode("bn1")))
    g1.add((EX.s, EX.q, Literal("x")))
    ds.graph(EX.empty)  # pre-existing empty named graph
    ds.graph(EX.g2)  # pre-existing empty named graph
    return ds


def test_atomic_update_rolls_back_on_failure(dataset: Dataset) -> None:
    """
    COPY a named graph, DROP the source, then CREATE an already existing
    graph: the last operation fails and all preceding effects of the same
    call are rolled back.
    """
    before = _quads(dataset)
    names_before = _graph_names(dataset)

    with pytest.raises(Exception, match="already exists"):
        dataset.update(
            f"""
            CREATE GRAPH <{EX.new}> ;
            COPY <{EX.g1}> TO <{EX.g2}> ;
            DROP GRAPH <{EX.g1}> ;
            CREATE GRAPH <{EX.g2}>
            """,
            atomic=True,
        )

    # triples are back to the state before the call
    assert _quads(dataset) == before
    # graph existence is back to the state before the call: the source
    # graph was not dropped, the graph created mid-request is gone and
    # the pre-existing empty named graph was not lost
    assert _graph_names(dataset) == names_before
    assert EX.g1 in names_before
    assert EX.empty in names_before
    assert EX.new not in _graph_names(dataset)


def test_atomic_update_rolls_back_bnode_identity(dataset: Dataset) -> None:
    """Rollback restores the very same blank node objects."""
    bn = dataset.graph(EX.g1).value(subject=EX.s, predicate=EX.p)
    assert isinstance(bn, BNode)

    with pytest.raises(Exception):
        dataset.update(
            f"""
            COPY <{EX.g1}> TO <{EX.g2}> ;
            DROP GRAPH <{EX.g1}> ;
            CREATE GRAPH <{EX.g2}>
            """,
            atomic=True,
        )

    assert any(o is bn for _, _, o in dataset.graph(EX.g1))


def test_atomic_update_commits_in_order_with_silent(dataset: Dataset) -> None:
    """
    With CREATE SILENT the ignorable error is swallowed and the preceding
    operations of the same call are committed in lexical order.
    """
    bn = dataset.graph(EX.g1).value(subject=EX.s, predicate=EX.p)
    g2_view = dataset.graph(EX.g2)

    dataset.update(
        f"""
        COPY <{EX.g1}> TO <{EX.g2}> ;
        DROP GRAPH <{EX.g1}> ;
        CREATE SILENT GRAPH <{EX.g2}>
        """,
        atomic=True,
    )

    # the COPY read the source before it was dropped
    assert (EX.s, EX.p, bn) in g2_view
    assert (EX.s, EX.q, Literal("x")) in g2_view
    # blank node identity is unchanged by the copy
    assert any(o is bn for _, _, o in g2_view)
    # the source graph was dropped
    assert EX.g1 not in _graph_names(dataset)


def test_atomic_update_existing_views_observe_commit_and_rollback() -> None:
    """
    Code holding the Dataset or Graph views of it sees new content after
    a commit and the old content after a rollback.
    """
    ds = Dataset()
    source_view = ds.graph(EX.g1)
    source_view.add((EX.s, EX.p, EX.o))
    dest_view = ds.graph(EX.g2)

    ds.update(f"COPY <{EX.g1}> TO <{EX.g2}> ; DROP GRAPH <{EX.g1}>", atomic=True)
    assert (EX.s, EX.p, EX.o) in dest_view
    assert len(source_view) == 0

    with pytest.raises(Exception):
        ds.update(
            f"MOVE <{EX.g2}> TO <{EX.g3}> ; CREATE GRAPH <{EX.g3}>",
            atomic=True,
        )
    assert (EX.s, EX.p, EX.o) in dest_view
    assert EX.g3 not in _graph_names(ds)


def test_atomic_update_where_sees_prior_operations() -> None:
    """Later operations in the same call read the effects of earlier ones."""
    ds = Dataset()
    ds.update(
        f"""
        INSERT DATA {{ GRAPH <{EX.g1}> {{ <{EX.a}> <{EX.p}> "1" }} }} ;
        INSERT {{ GRAPH <{EX.g2}> {{ <{EX.b}> <{EX.p}> ?x }} }}
        WHERE {{ GRAPH <{EX.g1}> {{ <{EX.a}> <{EX.p}> ?x }} }}
        """,
        atomic=True,
    )
    assert (EX.b, EX.p, Literal("1")) in ds.graph(EX.g2)

    graph = Graph()
    graph.update(
        f"""
        INSERT DATA {{ <{EX.a}> <{EX.p}> "1" }} ;
        INSERT {{ <{EX.b}> <{EX.p}> ?x }} WHERE {{ <{EX.a}> <{EX.p}> ?x }}
        """,
        atomic=True,
    )
    assert (EX.b, EX.p, Literal("1")) in graph


@pytest.mark.parametrize(
    "failing_update",
    [
        # INSERT DATA
        f"INSERT DATA {{ GRAPH <{EX.g3}> {{ <{EX.a}> <{EX.p}> <{EX.o}> }} }} ;"
        f" CREATE GRAPH <{EX.g1}>",
        # DELETE DATA
        f'DELETE DATA {{ GRAPH <{EX.g1}> {{ <{EX.s}> <{EX.q}> "x" }} }} ;'
        f" CREATE GRAPH <{EX.g1}>",
        # DELETE/INSERT ... WHERE
        f"DELETE {{ GRAPH <{EX.g1}> {{ ?s ?p ?o }} }} WHERE"
        f" {{ GRAPH <{EX.g1}> {{ ?s ?p ?o }} }} ; CREATE GRAPH <{EX.g1}>",
        # CLEAR
        f"CLEAR GRAPH <{EX.g1}> ; CREATE GRAPH <{EX.g1}>",
        # ADD
        f"ADD <{EX.g1}> TO <{EX.g3}> ; CREATE GRAPH <{EX.g1}>",
        # MOVE
        f"MOVE <{EX.g1}> TO <{EX.g3}> ; CREATE GRAPH <{EX.g3}>",
        # DROP
        f"DROP GRAPH <{EX.g1}> ; CREATE GRAPH <{EX.g2}>",
    ],
)
def test_atomic_update_rolls_back_all_operation_types(
    dataset: Dataset, failing_update: str
) -> None:
    """All covered operation types are rolled back on failure."""
    before = _quads(dataset)
    names_before = _graph_names(dataset)

    with pytest.raises(Exception):
        dataset.update(failing_update, atomic=True)

    assert _quads(dataset) == before
    assert _graph_names(dataset) == names_before


def test_atomic_update_rejects_load(dataset: Dataset) -> None:
    """LOAD is not accepted in atomic mode, even with SILENT."""
    before = _quads(dataset)

    with pytest.raises(ValueError, match="LOAD"):
        dataset.update("LOAD <http://example.org/data.ttl>", atomic=True)
    with pytest.raises(ValueError, match="LOAD"):
        dataset.update("LOAD SILENT <http://example.org/data.ttl>", atomic=True)

    assert _quads(dataset) == before


def test_atomic_update_syntax_error_is_not_success(dataset: Dataset) -> None:
    """Syntax errors are raised, not swallowed, and change nothing."""
    before = _quads(dataset)

    with pytest.raises(Exception):
        dataset.update("INSERT DATA { <http://example.org/s> }", atomic=True)

    assert _quads(dataset) == before


def test_atomic_update_rejects_unsupported_store() -> None:
    """Stores without support refuse atomic updates before the first write."""
    graph = Graph(store="SimpleMemory")

    with pytest.raises(NotImplementedError, match="[Aa]tomic"):
        graph.update(
            f"INSERT DATA {{ <{EX.s}> <{EX.p}> <{EX.o}> }}", atomic=True
        )

    assert len(graph) == 0


def test_non_atomic_update_keeps_partial_effects() -> None:
    """Without atomic=True, behaviour is unchanged: partial effects persist."""
    ds = Dataset()
    ds.graph(EX.g1).add((EX.s, EX.p, EX.o))

    with pytest.raises(Exception):
        ds.update(
            f"COPY <{EX.g1}> TO <{EX.g2}> ; DROP GRAPH <{EX.g1}> ;"
            f" CREATE GRAPH <{EX.g2}>"
        )

    assert (EX.s, EX.p, EX.o) in ds.graph(EX.g2)
    assert len(ds.graph(EX.g1)) == 0
