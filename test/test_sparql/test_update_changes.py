from __future__ import annotations

import pytest

from rdflib import BNode, Dataset, Graph, Literal, URIRef
from rdflib.exceptions import UnsupportedUpdateError
from rdflib.plugins.sparql.operators import (
    register_custom_function,
    unregister_custom_function,
)
from rdflib.plugins.sparql.processor import SPARQLUpdateProcessor, prepareUpdate
from rdflib.plugins.sparql.update import (
    Quad,
    UpdateReport,
    evalUpdateWithChanges,
)
from rdflib.plugins.stores.memory import Memory

S = URIRef("urn:s")
P = URIRef("urn:p")
O = URIRef("urn:o")
G1 = URIRef("urn:g1")
G2 = URIRef("urn:g2")


def _quads(graph):
    if isinstance(graph, Dataset):
        return {
            (s, p, o, g.identifier if hasattr(g, "identifier") else g)
            for s, p, o, g in graph.quads()
        }
    return {(s, p, o, graph.identifier) for s, p, o in graph}


def test_update_return_convention_is_preserved():
    graph = Graph()
    assert graph.update("INSERT DATA { <urn:s> <urn:p> <urn:o> }") is None
    assert (S, P, O) in graph


def test_insert_data_reports_inserted_quads():
    dataset = Dataset()
    report = dataset.update_with_changes(
        "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> } }"
    )
    assert isinstance(report, UpdateReport)
    assert len(report.operations) == 1
    operation = report.operations[0]
    assert operation.name == "InsertData"
    assert operation.inserted == {Quad(S, P, O, G1)}
    assert operation.deleted == set()
    assert report.inserted == {Quad(S, P, O, G1)}
    assert report.deleted == set()


def test_insert_data_into_default_graph_reports_graph_identity():
    graph = Graph()
    report = graph.update_with_changes("INSERT DATA { <urn:s> <urn:p> <urn:o> }")
    assert report.inserted == {Quad(S, P, O, graph.identifier)}


def test_inserting_existing_quad_is_not_an_insertion():
    dataset = Dataset()
    dataset.update_with_changes(
        "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> } }"
    )
    report = dataset.update_with_changes(
        "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> } }"
    )
    assert report.operations[0].inserted == set()
    assert report.operations[0].deleted == set()
    assert report.inserted == set()
    assert report.deleted == set()


def test_delete_data_reports_only_quads_that_existed():
    dataset = Dataset()
    dataset.update_with_changes(
        "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> } }"
    )
    report = dataset.update_with_changes(
        "DELETE DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> ."
        " <urn:x> <urn:y> <urn:z> } }"
    )
    assert report.operations[0].name == "DeleteData"
    assert report.operations[0].deleted == {Quad(S, P, O, G1)}
    assert report.operations[0].inserted == set()
    assert report.deleted == {Quad(S, P, O, G1)}


def test_delete_where():
    graph = Graph()
    graph.update("INSERT DATA { <urn:s> <urn:p> <urn:o> . <urn:s> <urn:q> <urn:o> }")
    report = graph.update_with_changes("DELETE WHERE { ?s <urn:p> ?o }")
    assert report.operations[0].name == "DeleteWhere"
    assert report.deleted == {Quad(S, P, O, graph.identifier)}
    assert (S, URIRef("urn:q"), O) in graph


def test_delete_insert_where():
    graph = Graph()
    graph.update("INSERT DATA { <urn:s> <urn:p> <urn:o> }")
    report = graph.update_with_changes(
        "DELETE { ?s <urn:p> ?o } INSERT { ?s <urn:q> ?o } WHERE { ?s <urn:p> ?o }"
    )
    assert len(report.operations) == 1
    operation = report.operations[0]
    assert operation.name == "Modify"
    assert operation.deleted == {Quad(S, P, O, graph.identifier)}
    assert operation.inserted == {Quad(S, URIRef("urn:q"), O, graph.identifier)}
    assert report.inserted == operation.inserted
    assert report.deleted == operation.deleted


def test_delete_then_insert_is_visible_per_operation_and_in_net_change():
    dataset = Dataset()
    dataset.update_with_changes(
        "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> } }"
    )
    report = dataset.update_with_changes(
        "DELETE DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> } } ;"
        "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> } }"
    )
    assert [operation.name for operation in report.operations] == [
        "DeleteData",
        "InsertData",
    ]
    assert report.operations[0].deleted == {Quad(S, P, O, G1)}
    assert report.operations[1].inserted == {Quad(S, P, O, G1)}
    assert report.inserted == set()
    assert report.deleted == set()


def test_insert_template_bnodes_are_instantiated_per_solution():
    graph = Graph()
    graph.update(
        "INSERT DATA { <urn:s1> <urn:val> 1 . <urn:s2> <urn:val> 2 }"
    )
    report = graph.update_with_changes(
        "INSERT { _:b <urn:val> ?v } WHERE { ?s <urn:val> ?v }"
    )
    inserted = report.inserted
    assert len(inserted) == 2
    bnodes = {quad.subject for quad in inserted}
    assert len(bnodes) == 2
    assert all(isinstance(bnode, BNode) for bnode in bnodes)
    # the reported blank nodes are the identities actually written
    for quad in inserted:
        assert (quad.subject, quad.predicate, quad.object) in graph


def test_clear_graph():
    dataset = Dataset()
    dataset.update_with_changes(
        "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> }"
        " GRAPH <urn:g2> { <urn:a> <urn:b> <urn:c> } }"
    )
    report = dataset.update_with_changes("CLEAR GRAPH <urn:g1>")
    assert report.operations[0].name == "Clear"
    assert report.deleted == {Quad(S, P, O, G1)}
    assert (URIRef("urn:a"), URIRef("urn:b"), URIRef("urn:c"), G2) in _quads(dataset)


def test_clear_named():
    dataset = Dataset()
    dataset.update_with_changes(
        "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> }"
        " GRAPH <urn:g2> { <urn:a> <urn:b> <urn:c> } }"
    )
    report = dataset.update_with_changes("CLEAR NAMED")
    assert report.deleted == {
        Quad(S, P, O, G1),
        Quad(URIRef("urn:a"), URIRef("urn:b"), URIRef("urn:c"), G2),
    }
    assert len(dataset) == 0


def test_clear_all():
    dataset = Dataset()
    dataset.update_with_changes(
        "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> } }"
    )
    report = dataset.update_with_changes("CLEAR ALL")
    assert report.deleted == {Quad(S, P, O, G1)}
    assert len(dataset) == 0


def test_clear_default_on_plain_graph():
    graph = Graph()
    graph.update("INSERT DATA { <urn:s> <urn:p> <urn:o> }")
    report = graph.update_with_changes("CLEAR DEFAULT")
    assert report.deleted == {Quad(S, P, O, graph.identifier)}
    assert len(graph) == 0


def test_add():
    dataset = Dataset()
    dataset.update_with_changes(
        "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> } }"
    )
    report = dataset.update_with_changes("ADD <urn:g1> TO <urn:g2>")
    assert report.operations[0].name == "Add"
    assert report.inserted == {Quad(S, P, O, G2)}
    assert report.deleted == set()
    assert (S, P, O, G1) in _quads(dataset)


def test_copy():
    dataset = Dataset()
    dataset.update_with_changes(
        "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> }"
        " GRAPH <urn:g2> { <urn:a> <urn:b> <urn:c> } }"
    )
    report = dataset.update_with_changes("COPY <urn:g1> TO <urn:g2>")
    assert report.operations[0].name == "Copy"
    assert report.inserted == {Quad(S, P, O, G2)}
    assert report.deleted == {
        Quad(URIRef("urn:a"), URIRef("urn:b"), URIRef("urn:c"), G2)
    }
    assert (S, P, O, G1) in _quads(dataset)


def test_move():
    dataset = Dataset()
    dataset.update_with_changes(
        "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> }"
        " GRAPH <urn:g2> { <urn:a> <urn:b> <urn:c> } }"
    )
    report = dataset.update_with_changes("MOVE <urn:g1> TO <urn:g2>")
    assert report.operations[0].name == "Move"
    assert report.inserted == {Quad(S, P, O, G2)}
    assert report.deleted == {
        Quad(S, P, O, G1),
        Quad(URIRef("urn:a"), URIRef("urn:b"), URIRef("urn:c"), G2),
    }
    assert (S, P, O, G1) not in _quads(dataset)
    assert (S, P, O, G2) in _quads(dataset)


def test_report_matches_dataset_diff_with_duplicate_solutions():
    dataset = Dataset()
    dataset.update_with_changes(
        "INSERT DATA { GRAPH <urn:g1> { <urn:s1> <urn:p> <urn:o> ."
        " <urn:s2> <urn:p> <urn:o> . <urn:s1> <urn:q> <urn:gone> } }"
    )
    before = _quads(dataset)
    report = dataset.update_with_changes(
        # two solutions instantiate the same INSERT template quad
        "INSERT { GRAPH <urn:g2> { <urn:x> <urn:p> ?o } }"
        " WHERE { GRAPH <urn:g1> { ?s <urn:p> ?o } } ;"
        # two solutions delete the same quad
        "DELETE { GRAPH <urn:g1> { ?s <urn:q> <urn:gone> } }"
        " WHERE { GRAPH <urn:g1> { ?s <urn:p> ?o } }"
    )
    after = _quads(dataset)
    assert report.inserted == after - before
    assert report.deleted == before - after
    assert report.inserted == {Quad(URIRef("urn:x"), P, O, G2)}
    assert report.deleted == {
        Quad(URIRef("urn:s1"), URIRef("urn:q"), URIRef("urn:gone"), G1)
    }


def test_where_clause_is_not_evaluated_again_for_the_report():
    calls = []

    def counting_function(*args):
        calls.append(args)
        return Literal(True)

    function_iri = URIRef("urn:count")
    register_custom_function(function_iri, counting_function, override=True)
    try:
        update_string = (
            "DELETE { ?s <urn:p> ?o } INSERT { ?s <urn:q> ?o }"
            " WHERE { ?s <urn:p> ?o . FILTER(<urn:count>(?s)) }"
        )
        data = "INSERT DATA { <urn:s1> <urn:p> <urn:o> . <urn:s2> <urn:p> <urn:o> }"

        graph = Graph()
        graph.update(data)
        calls.clear()
        report = graph.update_with_changes(update_string)
        calls_with_report = len(calls)

        reference_graph = Graph()
        reference_graph.update(data)
        calls.clear()
        reference_graph.update(update_string)
        calls_without_report = len(calls)
    finally:
        unregister_custom_function(function_iri)

    assert calls_with_report > 0
    assert calls_with_report == calls_without_report
    assert report.inserted == {
        Quad(URIRef("urn:s1"), URIRef("urn:q"), O, graph.identifier),
        Quad(URIRef("urn:s2"), URIRef("urn:q"), O, graph.identifier),
    }


def test_load_is_rejected_before_anything_is_executed():
    dataset = Dataset()
    with pytest.raises(UnsupportedUpdateError):
        dataset.update_with_changes(
            "INSERT DATA { GRAPH <urn:g1> { <urn:s> <urn:p> <urn:o> } } ;"
            "LOAD <file:///nonexistent> INTO GRAPH <urn:g2>"
        )
    assert len(dataset) == 0


def test_store_provided_update_is_rejected_before_anything_is_executed():
    class OpaqueUpdateStore(Memory):
        def update(self, update, initNs, initBindings, queryGraph, **kwargs):
            if update:
                raise AssertionError("update was executed")

    graph = Graph(store=OpaqueUpdateStore())
    with pytest.raises(UnsupportedUpdateError):
        graph.update_with_changes("INSERT DATA { <urn:s> <urn:p> <urn:o> }")
    assert len(graph) == 0


def test_eval_and_processor_entry_points():
    graph = Graph()
    prepared = prepareUpdate("INSERT DATA { <urn:s> <urn:p> <urn:o> }")
    report = evalUpdateWithChanges(graph, prepared)
    assert report.inserted == {Quad(S, P, O, graph.identifier)}

    other = Graph()
    processor = SPARQLUpdateProcessor(other)
    report = processor.update_with_changes(
        "INSERT DATA { <urn:s> <urn:p> <urn:o> }", initNs={}
    )
    assert report.inserted == {Quad(S, P, O, other.identifier)}
