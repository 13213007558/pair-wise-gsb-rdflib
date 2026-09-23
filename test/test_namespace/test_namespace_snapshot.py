from __future__ import annotations

import json
import threading

import pytest

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import (
    NamespaceConflict,
    NamespaceConflictError,
    NamespaceSnapshot,
)

EX = Namespace("http://example.org/")
EX_ALT = Namespace("http://alt.example.org/")
SCHEMA = Namespace("http://schema.org/")


def make_graph() -> Graph:
    graph = Graph()
    graph.add((EX.s, EX.p, EX.o))
    graph.add((EX.s, SCHEMA.name, URIRef("http://yet-another.example.org/x")))
    return graph


def test_snapshot_captures_manager_state():
    graph = Graph()
    graph.bind("ex", EX)
    graph.bind("", SCHEMA)

    snap = NamespaceSnapshot(graph.namespace_manager)

    assert ("ex", URIRef(EX)) in snap.namespaces()
    assert snap.namespace("ex") == URIRef(EX)
    assert snap.prefix(EX) == "ex"
    assert snap.default_namespace == URIRef(SCHEMA)
    assert snap.conflict_policy == "replace"


def test_manager_snapshot_method():
    graph = Graph()
    graph.bind("ex", EX)
    snap = graph.namespace_manager.snapshot()
    assert snap.namespace("ex") == URIRef(EX)


def test_snapshot_isolated_from_source_manager():
    graph = Graph()
    snap = NamespaceSnapshot(graph.namespace_manager)

    # bindings on the snapshot do not leak into the source manager
    snap.bind("ex", EX)
    assert dict(graph.namespaces()).get("ex") is None

    # bindings on the source manager after the snapshot are not visible
    graph.bind("late", EX_ALT)
    assert snap.namespace("late") is None


def test_snapshot_standalone_bind_namespaces():
    empty = NamespaceSnapshot(bind_namespaces="none")
    assert len(empty) == 0

    core = NamespaceSnapshot(bind_namespaces="core")
    assert core.namespace("rdf") is not None
    assert core.namespace("brick") is None

    full = NamespaceSnapshot(bind_namespaces="rdflib")
    assert full.namespace("brick") is not None

    with pytest.raises(ValueError):
        NamespaceSnapshot(bind_namespaces="bogus")


def test_snapshot_invalid_conflict_policy():
    with pytest.raises(ValueError):
        NamespaceSnapshot(bind_namespaces="none", conflict_policy="bogus")


def test_conflict_same_uri_new_prefix():
    snap = NamespaceSnapshot(bind_namespaces="none")
    assert snap.bind("ex", EX) is NamespaceConflict.NONE
    assert snap.bind("ex2", EX) is NamespaceConflict.NAMESPACE_REBOUND
    # default "replace" policy with override=True rebinds, like the manager
    assert snap.prefix(EX) == "ex2"
    assert snap.namespace("ex") is None


def test_conflict_same_prefix_new_uri():
    snap = NamespaceSnapshot(bind_namespaces="none")
    snap.bind("ex", EX)
    assert snap.bind("ex", EX_ALT) is NamespaceConflict.PREFIX_REBOUND
    # default policy derives a fresh prefix, like the default manager
    assert snap.namespace("ex") == URIRef(EX)
    assert snap.namespace("ex1") == URIRef(EX_ALT)


def test_conflict_same_prefix_new_uri_replace():
    snap = NamespaceSnapshot(bind_namespaces="none")
    snap.bind("ex", EX)
    conflict = snap.bind("ex", EX_ALT, replace=True)
    assert conflict is NamespaceConflict.PREFIX_REBOUND
    assert snap.namespace("ex") == URIRef(EX_ALT)


def test_conflict_default_prefix_change():
    snap = NamespaceSnapshot(bind_namespaces="none")
    snap.bind("", EX)
    assert snap.default_namespace == URIRef(EX)
    assert snap.bind("", EX_ALT) is NamespaceConflict.DEFAULT_CHANGED
    # without replace the default binding is kept and a derived prefix made
    assert snap.default_namespace == URIRef(EX)
    assert snap.namespace("default1") == URIRef(EX_ALT)
    # with replace the default namespace changes
    assert snap.bind("", EX_ALT, replace=True) is NamespaceConflict.DEFAULT_CHANGED
    assert snap.default_namespace == URIRef(EX_ALT)


def test_conflict_policy_keep():
    snap = NamespaceSnapshot(bind_namespaces="none", conflict_policy="keep")
    snap.bind("ex", EX)
    assert snap.bind("ex", EX_ALT) is NamespaceConflict.PREFIX_REBOUND
    assert snap.bind("ex2", EX) is NamespaceConflict.NAMESPACE_REBOUND
    assert snap.namespace("ex") == URIRef(EX)
    assert snap.namespace("ex2") is None


def test_conflict_policy_error():
    snap = NamespaceSnapshot(bind_namespaces="none", conflict_policy="error")
    snap.bind("ex", EX)
    snap.bind("", SCHEMA)

    with pytest.raises(NamespaceConflictError) as excinfo:
        snap.bind("ex", EX_ALT)
    assert excinfo.value.conflict is NamespaceConflict.PREFIX_REBOUND

    with pytest.raises(NamespaceConflictError) as excinfo:
        snap.bind("ex2", EX)
    assert excinfo.value.conflict is NamespaceConflict.NAMESPACE_REBOUND

    with pytest.raises(NamespaceConflictError) as excinfo:
        snap.bind("", EX)
    assert excinfo.value.conflict is NamespaceConflict.DEFAULT_CHANGED

    # failed bindings leave the snapshot unchanged
    assert snap.namespace("ex") == URIRef(EX)
    assert snap.default_namespace == URIRef(SCHEMA)


def test_namespaces_iteration_is_stable():
    snap = NamespaceSnapshot(bind_namespaces="none")
    snap.bind("a", "http://a.example.org/")
    snap.bind("b", "http://b.example.org/")

    captured = snap.namespaces()
    seen = []
    for prefix, _namespace in captured:
        seen.append(prefix)
        # appending prefixes during an ongoing iteration must not
        # modify the iteration
        snap.bind("c" + prefix, "http://c.example.org/" + prefix + "/")

    assert seen == ["a", "b"]
    assert len(captured) == 2
    assert len(snap.namespaces()) == 4


def test_copy_does_not_write_back():
    snap = NamespaceSnapshot(bind_namespaces="none")
    snap.bind("ex", EX)

    child = snap.copy()
    assert child.namespace("ex") == URIRef(EX)
    assert child.conflict_policy == snap.conflict_policy

    child.bind("childonly", EX_ALT)
    child.bind("ex", EX_ALT, replace=True)

    assert snap.namespace("childonly") is None
    assert snap.namespace("ex") == URIRef(EX)


def test_compute_qname_generates_into_snapshot_only():
    graph = Graph()
    snap = NamespaceSnapshot(graph.namespace_manager)
    prefix, namespace, name = snap.compute_qname(str(EX.p))
    assert (namespace, name) == (URIRef(EX), "p")
    assert prefix == snap.prefix(EX)
    # the generated binding stays inside the snapshot
    assert dict(graph.namespaces()).get(prefix) is None
    assert snap.qname(str(EX.p)) == "%s:p" % prefix
    assert snap.curie(str(EX.p)) == "%s:p" % prefix


def test_turtle_serializer_uses_own_snapshot():
    graph = make_graph()
    snap = NamespaceSnapshot(bind_namespaces="none")
    snap.bind("ex", EX)
    snap.bind("schema", SCHEMA)

    before = set(graph.namespaces())
    output = graph.serialize(format="turtle", namespace_snapshot=snap)

    assert "@prefix ex: <http://example.org/>" in output
    assert "ex:p" in output
    # nothing leaked into the graph's shared namespace manager
    assert set(graph.namespaces()) == before

    # reparse and verify the URIs survive the round trip
    reparsed = Graph().parse(data=output, format="turtle")
    assert (EX.s, EX.p, EX.o) in reparsed
    assert (EX.s, SCHEMA.name, URIRef("http://yet-another.example.org/x")) in reparsed


def test_turtle_declaration_order_is_stable():
    graph = make_graph()
    snap = NamespaceSnapshot(bind_namespaces="none")
    snap.bind("schema", SCHEMA)
    snap.bind("ex", EX)

    first = graph.serialize(format="turtle", namespace_snapshot=snap)
    second = graph.serialize(format="turtle", namespace_snapshot=snap)
    assert first == second

    declarations = [line for line in first.splitlines() if line.startswith("@prefix")]
    assert declarations == sorted(declarations)
    assert "@prefix ex: <http://example.org/> ." in declarations
    assert "@prefix schema: <http://schema.org/> ." in declarations


def test_jsonld_serializer_uses_own_snapshot():
    graph = make_graph()
    snap = NamespaceSnapshot(bind_namespaces="none")
    snap.bind("ex", EX)
    snap.bind("schema", SCHEMA)

    before = set(graph.namespaces())
    output = graph.serialize(format="json-ld", namespace_snapshot=snap)
    assert set(graph.namespaces()) == before

    data = json.loads(output)
    context = data["@context"]
    assert context["ex"] == str(EX)
    assert context["schema"] == str(SCHEMA)
    # context ordering follows the snapshot's binding order
    assert list(context) == ["ex", "schema"]

    reparsed = Graph().parse(data=output, format="json-ld")
    assert (EX.s, EX.p, EX.o) in reparsed
    assert (EX.s, SCHEMA.name, URIRef("http://yet-another.example.org/x")) in reparsed


def test_jsonld_context_order_is_stable():
    graph = make_graph()
    snap = NamespaceSnapshot(bind_namespaces="none")
    snap.bind("zeta", "http://zeta.example.org/")
    snap.bind("alpha", "http://alpha.example.org/")

    first = graph.serialize(format="json-ld", namespace_snapshot=snap)
    second = graph.serialize(format="json-ld", namespace_snapshot=snap)
    assert first == second
    assert list(snap.to_jsonld_context()) == ["zeta", "alpha"]


def test_jsonld_snapshot_default_namespace_becomes_vocab():
    graph = make_graph()
    snap = NamespaceSnapshot(bind_namespaces="none")
    snap.bind("", EX)

    output = graph.serialize(format="json-ld", namespace_snapshot=snap)
    data = json.loads(output)
    assert data["@context"]["@vocab"] == str(EX)

    reparsed = Graph().parse(data=output, format="json-ld")
    assert (EX.s, EX.p, EX.o) in reparsed


def test_caller_appends_prefixes_during_output():
    graph = make_graph()
    snap = NamespaceSnapshot(bind_namespaces="none")
    snap.bind("ex", EX)

    # prefixes appended by the caller after a snapshot view was taken do
    # not modify the view an ongoing output relies on
    started_view = snap.namespaces()
    snap.bind("late", EX_ALT)
    assert ("late", URIRef(EX_ALT)) not in started_view

    output = graph.serialize(format="turtle", namespace_snapshot=snap)
    reparsed = Graph().parse(data=output, format="turtle")
    assert (EX.s, EX.p, EX.o) in reparsed


def test_default_manager_behavior_unchanged():
    graph = make_graph()
    before = set(graph.namespaces())
    output = graph.serialize(format="turtle")
    # existing behavior: generated prefixes are bound on the shared manager
    after = set(graph.namespaces())
    assert after != before
    reparsed = Graph().parse(data=output, format="turtle")
    assert (EX.s, EX.p, EX.o) in reparsed


def test_interleaved_serializers_no_cross_request_leak():
    graph = make_graph()

    snap_a = NamespaceSnapshot(bind_namespaces="none")
    snap_a.bind("reqa", EX)
    snap_b = NamespaceSnapshot(bind_namespaces="none")
    snap_b.bind("reqb", EX)

    # interleave creation and serialization of both "requests"
    out_a1 = graph.serialize(format="turtle", namespace_snapshot=snap_a)
    out_b1 = graph.serialize(format="json-ld", namespace_snapshot=snap_b)
    out_a2 = graph.serialize(format="json-ld", namespace_snapshot=snap_a)
    out_b2 = graph.serialize(format="turtle", namespace_snapshot=snap_b)

    assert "@prefix reqa:" in out_a1
    assert "reqb" not in out_a1
    assert "@prefix reqb:" in out_b2
    assert "reqa" not in out_b2

    context_a = json.loads(out_a2)["@context"]
    context_b = json.loads(out_b1)["@context"]
    assert "reqa" in context_a and "reqb" not in context_a
    assert "reqb" in context_b and "reqa" not in context_b

    # neither request's prefixes leaked into the shared manager
    shared_prefixes = {prefix for prefix, _ in graph.namespaces()}
    assert "reqa" not in shared_prefixes
    assert "reqb" not in shared_prefixes


def test_parallel_threads_distinct_prefixes_and_formats():
    graph = make_graph()
    shared_before = set(graph.namespaces())
    errors: list[BaseException] = []

    def work(index: int) -> None:
        try:
            prefix = "thr%d" % index
            snap = NamespaceSnapshot(bind_namespaces="none")
            snap.bind(prefix, EX)
            snap.bind("schema", SCHEMA)
            if index % 2 == 0:
                output = graph.serialize(format="turtle", namespace_snapshot=snap)
                assert "@prefix %s:" % prefix in output
                reparsed = Graph().parse(data=output, format="turtle")
            else:
                output = graph.serialize(format="json-ld", namespace_snapshot=snap)
                assert json.loads(output)["@context"][prefix] == str(EX)
                reparsed = Graph().parse(data=output, format="json-ld")
            assert (EX.s, EX.p, EX.o) in reparsed
            expected = (EX.s, SCHEMA.name, URIRef("http://yet-another.example.org/x"))
            assert expected in reparsed
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    # no per-thread prefix leaked into the shared manager
    shared_after = {prefix for prefix, _ in graph.namespaces()}
    assert shared_after == {prefix for prefix, _ in shared_before}
