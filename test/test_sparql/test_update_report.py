"""
Tests for Graph.update_with_report and the underlying UpdateReport machinery.
"""

from __future__ import annotations

import pytest

from rdflib import BNode, Dataset, Graph, Literal, URIRef
from rdflib.plugins.sparql.operators import (
    register_custom_function,
    unregister_custom_function,
)
from rdflib.plugins.sparql.processor import prepareUpdate, processUpdateWithReport
from rdflib.plugins.sparql.update import Quad, UpdateReport

S = URIRef("urn:s")
P = URIRef("urn:p")
OBJ = URIRef("urn:o")
G1 = URIRef("urn:g1")
G2 = URIRef("urn:g2")
G3 = URIRef("urn:g3")


def _quads(graph):
    """All quads of a Dataset/ConjunctiveGraph as plain (s, p, o, graph) tuples."""
    return {
        (s, p, o, getattr(c, "identifier", c))
        for s, p, o, c in graph.quads((None, None, None, None))
    }


def _tuples(quads):
    """Report quads as plain (s, p, o, graph) tuples."""
    return {(q.subject, q.predicate, q.object, q.graph) for q in quads}


def test_update_return_contract_preserved():
    """The classic update() entry point still returns None."""
    graph = Graph()
    assert graph.update("INSERT DATA { <urn:s> <urn:p> <urn:o> }") is None
    assert len(graph) == 1


def test_insert_data_report():
    dataset = Dataset()
    dataset.default_graph.add((S, P, OBJ))

    report = dataset.update_with_report(
        "INSERT DATA { <urn:s> <urn:p> <urn:o> . <urn:a> <urn:b> <urn:c> . "
        "GRAPH <urn:g1> { <urn:x> <urn:p> <urn:y> } }"
    )

    assert isinstance(report, UpdateReport)
    assert len(report.operations) == 1
    operation = report.operations[0]
    assert operation.name == "InsertData"
    # the already present triple is not reported again
    assert _tuples(operation.inserted) == {
        (
            URIRef("urn:a"),
            URIRef("urn:b"),
            URIRef("urn:c"),
            dataset.default_graph.identifier,
        ),
        (URIRef("urn:x"), P, URIRef("urn:y"), G1),
    }
    assert operation.deleted == []
    assert _tuples(report.inserted) == _tuples(operation.inserted)
    assert report.deleted == set()


def test_delete_data_report():
    dataset = Dataset()
    dataset.default_graph.add((S, P, OBJ))
    dataset.graph(G1).add((S, P, OBJ))

    report = dataset.update_with_report(
        "DELETE DATA { <urn:s> <urn:p> <urn:o> . <urn:no> <urn:no> <urn:no> . "
        "GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> } }"
    )

    operation = report.operations[0]
    assert operation.name == "DeleteData"
    # the non-existing triple is not reported
    assert _tuples(operation.deleted) == {
        (S, P, OBJ, dataset.default_graph.identifier),
        (S, P, OBJ, G1),
    }
    assert operation.inserted == []
    assert len(dataset) == 0


def test_delete_insert_where_report():
    dataset = Dataset()
    dataset.default_graph.add((S, P, OBJ))
    dataset.default_graph.add((S, P, URIRef("urn:other")))
    dataset.graph(G1).add((S, P, OBJ))

    report = dataset.update_with_report(
        "DELETE { ?s <urn:p> ?o } INSERT { ?s <urn:q> ?o } WHERE { ?s <urn:p> ?o }"
    )

    assert [op.name for op in report.operations] == ["Modify"]
    operation = report.operations[0]
    default = dataset.default_graph.identifier
    # the WHERE clause matches against the union of all graphs, but the
    # deletes and inserts only affect the default graph
    assert _tuples(operation.deleted) == {
        (S, P, OBJ, default),
        (S, P, URIRef("urn:other"), default),
    }
    assert _tuples(operation.inserted) == {
        (S, URIRef("urn:q"), OBJ, default),
        (S, URIRef("urn:q"), URIRef("urn:other"), default),
    }
    assert _tuples(report.inserted) == _tuples(operation.inserted)
    assert _tuples(report.deleted) == _tuples(operation.deleted)
    # the named graph is untouched
    assert _quads(dataset) == {
        (S, P, OBJ, G1),
        (S, URIRef("urn:q"), OBJ, default),
        (S, URIRef("urn:q"), URIRef("urn:other"), default),
    }


def test_delete_where_report():
    graph = Graph()
    graph.add((S, P, OBJ))
    graph.add((S, P, URIRef("urn:other")))

    report = graph.update_with_report("DELETE WHERE { <urn:s> <urn:p> ?o }")

    assert [op.name for op in report.operations] == ["DeleteWhere"]
    assert _tuples(report.operations[0].deleted) == {
        (S, P, OBJ, graph.identifier),
        (S, P, URIRef("urn:other"), graph.identifier),
    }
    assert len(graph) == 0


def test_clear_report():
    dataset = Dataset()
    dataset.default_graph.add((S, P, OBJ))
    dataset.graph(G1).add((S, P, URIRef("urn:x")))
    dataset.graph(G2).add((S, P, URIRef("urn:y")))

    report = dataset.update_with_report("CLEAR GRAPH <urn:g1> ; CLEAR NAMED")

    assert [op.name for op in report.operations] == ["Clear", "Clear"]
    clear_graph, clear_named = report.operations
    assert _tuples(clear_graph.deleted) == {(S, P, URIRef("urn:x"), G1)}
    # CLEAR NAMED clears the remaining named graph; on a Dataset the default
    # graph is cleared as well (existing rdflib behaviour)
    assert _tuples(clear_named.deleted) == {
        (S, P, URIRef("urn:y"), G2),
        (S, P, OBJ, dataset.default_graph.identifier),
    }
    assert _quads(dataset) == set()
    assert report.inserted == set()
    assert _tuples(report.deleted) == _tuples(clear_graph.deleted) | _tuples(
        clear_named.deleted
    )


def test_clear_default_report():
    graph = Graph()
    graph.add((S, P, OBJ))

    report = graph.update_with_report("CLEAR DEFAULT")

    assert _tuples(report.operations[0].deleted) == {(S, P, OBJ, graph.identifier)}
    assert len(graph) == 0


def test_add_copy_move_report():
    dataset = Dataset()
    dataset.graph(G1).add((S, P, OBJ))
    dataset.graph(G2).add((S, P, URIRef("urn:old")))

    report = dataset.update_with_report(
        "ADD <urn:g1> TO <urn:g2> ; "
        "COPY <urn:g2> TO <urn:g3> ; "
        "MOVE <urn:g3> TO <urn:g4>"
    )

    assert [op.name for op in report.operations] == ["Add", "Copy", "Move"]
    add, copy, move = report.operations

    # ADD only reports the triples that were not already in the target
    assert _tuples(add.inserted) == {(S, P, OBJ, G2)}
    assert add.deleted == []

    # COPY clears the target first, then inserts the source triples
    assert _tuples(copy.inserted) == {
        (S, P, OBJ, G3),
        (S, P, URIRef("urn:old"), G3),
    }
    assert copy.deleted == []

    # MOVE clears the target, inserts the source triples and empties the source
    assert _tuples(move.inserted) == {
        (S, P, OBJ, URIRef("urn:g4")),
        (S, P, URIRef("urn:old"), URIRef("urn:g4")),
    }
    assert _tuples(move.deleted) == {
        (S, P, OBJ, G3),
        (S, P, URIRef("urn:old"), G3),
    }

    # net change of the whole request
    assert _tuples(report.inserted) == {
        (S, P, OBJ, G2),
        (S, P, OBJ, URIRef("urn:g4")),
        (S, P, URIRef("urn:old"), URIRef("urn:g4")),
    }
    assert report.deleted == set()
    assert _quads(dataset) == {
        (S, P, OBJ, G1),
        (S, P, OBJ, G2),
        (S, P, URIRef("urn:old"), G2),
        (S, P, OBJ, URIRef("urn:g4")),
        (S, P, URIRef("urn:old"), URIRef("urn:g4")),
    }


def test_delete_then_insert_net_change():
    """A quad deleted and re-added in one request shows up per operation,
    but not in the net change."""
    graph = Graph()
    graph.add((S, P, OBJ))

    report = graph.update_with_report(
        "DELETE DATA { <urn:s> <urn:p> <urn:o> } ; "
        "INSERT DATA { <urn:s> <urn:p> <urn:o> . <urn:a> <urn:b> <urn:c> }"
    )

    delete_op, insert_op = report.operations
    assert _tuples(delete_op.deleted) == {(S, P, OBJ, graph.identifier)}
    assert _tuples(insert_op.inserted) == {
        (S, P, OBJ, graph.identifier),
        (URIRef("urn:a"), URIRef("urn:b"), URIRef("urn:c"), graph.identifier),
    }
    # net change only contains the genuinely new triple
    assert _tuples(report.inserted) == {
        (URIRef("urn:a"), URIRef("urn:b"), URIRef("urn:c"), graph.identifier)
    }
    assert report.deleted == set()
    assert set(graph) == {
        (S, P, OBJ),
        (URIRef("urn:a"), URIRef("urn:b"), URIRef("urn:c")),
    }


def test_insert_template_bnode_identities():
    """Blank nodes in INSERT templates are instantiated per solution and the
    report carries the identities actually written to the graph."""
    graph = Graph()
    graph.add((URIRef("urn:s1"), URIRef("urn:a"), URIRef("urn:T")))
    graph.add((URIRef("urn:s2"), URIRef("urn:a"), URIRef("urn:T")))

    report = graph.update_with_report(
        "INSERT { _:b <urn:member> ?s } WHERE { ?s <urn:a> <urn:T> }"
    )

    inserted = report.operations[0].inserted
    assert len(inserted) == 2
    bnodes = {quad.subject for quad in inserted}
    assert len(bnodes) == 2
    assert all(isinstance(bnode, BNode) for bnode in bnodes)
    # the reported blank nodes are exactly the ones in the graph
    assert set(graph.subject_objects(URIRef("urn:member"))) == {
        (quad.subject, quad.object) for quad in inserted
    }


def test_duplicate_solutions_match_dataset_diff():
    """With duplicate WHERE solutions the report stays in sync with the
    before/after dataset diff."""
    dataset = Dataset()
    default = dataset.default_graph
    default.add((S, URIRef("urn:p1"), OBJ))
    default.add((S, URIRef("urn:p2"), OBJ))  # duplicate (?s, ?o) solution
    default.add((S, URIRef("urn:q"), OBJ))
    dataset.graph(G1).add((S, URIRef("urn:p1"), OBJ))

    before = _quads(dataset)
    report = dataset.update_with_report(
        "DELETE { ?s <urn:q> ?o } INSERT { ?s <urn:r> ?o } WHERE { ?s ?p ?o }"
    )
    after = _quads(dataset)

    # the duplicate solution does not lead to duplicate report entries
    operation = report.operations[0]
    assert len(operation.inserted) == len(set(_tuples(operation.inserted)))
    assert len(operation.deleted) == len(set(_tuples(operation.deleted)))

    assert _tuples(report.inserted) == after - before
    assert _tuples(report.deleted) == before - after


def test_report_does_not_reevaluate_where():
    """A custom function in the WHERE clause is evaluated exactly as often
    as with a plain update(); the report does not re-run the query."""
    function_uri = URIRef("urn:counting-function")
    calls = []

    def counting_function(argument):
        calls.append(argument)
        return argument

    update_string = (
        "INSERT { ?s <urn:counted> ?c } "
        "WHERE { ?s <urn:p> ?o . BIND(<urn:counting-function>(?o) AS ?c) }"
    )

    def make_graph():
        graph = Graph()
        graph.add((S, P, OBJ))
        graph.add((S, P, URIRef("urn:o2")))
        return graph

    register_custom_function(function_uri, counting_function)
    try:
        report_graph = make_graph()
        report_graph.update_with_report(update_string)
        report_calls = len(calls)

        calls.clear()
        plain_graph = make_graph()
        plain_graph.update(update_string)
        plain_calls = len(calls)
    finally:
        unregister_custom_function(function_uri)

    assert report_calls == 2
    assert report_calls == plain_calls
    assert set(report_graph) == set(plain_graph)


def test_load_not_supported_and_nothing_executes():
    graph = Graph()

    with pytest.raises(NotImplementedError, match="Load"):
        graph.update_with_report(
            "INSERT DATA { <urn:s> <urn:p> <urn:o> } ; LOAD <file:///nonexistent>"
        )

    # the failure is reported before anything is executed
    assert len(graph) == 0


def test_store_provided_update_not_supported():
    graph = Graph(store="SPARQLUpdateStore")

    with pytest.raises(NotImplementedError, match="cannot be observed"):
        graph.update_with_report("INSERT DATA { <urn:s> <urn:p> <urn:o> }")


def test_prepared_update_and_processor_entry_points():
    graph = Graph()

    prepared = prepareUpdate("INSERT DATA { <urn:s> <urn:p> <urn:o> }")
    report = graph.update_with_report(prepared)
    assert _tuples(report.inserted) == {(S, P, OBJ, graph.identifier)}

    report = processUpdateWithReport(graph, "DELETE DATA { <urn:s> <urn:p> <urn:o> }")
    assert _tuples(report.deleted) == {(S, P, OBJ, graph.identifier)}
    assert len(graph) == 0


def test_quad_triple_property():
    quad = Quad(S, P, Literal("o"), G1)
    assert quad.triple == (S, P, Literal("o"))
    assert quad.graph == G1
