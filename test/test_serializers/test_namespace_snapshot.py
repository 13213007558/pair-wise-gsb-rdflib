"""Tests for isolated, explicitly passable namespace snapshots.

A service that serves Turtle and JSON-LD in parallel can hand each
serializer its own ``NamespaceSnapshot`` so that per-request prefix
bindings never leak between serializers or across requests. These tests
pin that contract down, including conflict classification, copy semantics
for sub-requests and concurrent output.
"""

import io
import json
import threading

import pytest

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import (
    NamespaceConflict,
    NamespaceConflictError,
    NamespaceConflictKind,
    NamespaceConflictPolicy,
    NamespaceSnapshot,
)
from rdflib.plugins.serializers.jsonld import JsonLDSerializer
from rdflib.plugins.serializers.turtle import TurtleSerializer


class TestConflictClassification:
    def test_three_conflict_kinds_are_distinguished(self):
        snapshot = NamespaceSnapshot()
        assert snapshot.bind("ex", "http://example.org/") is None

        same_uri_new_prefix = snapshot.bind("alt", "http://example.org/")
        assert isinstance(same_uri_new_prefix, NamespaceConflict)
        assert same_uri_new_prefix.kind is NamespaceConflictKind.SAME_URI_NEW_PREFIX
        assert same_uri_new_prefix.existing_prefix == "ex"

        same_prefix_new_uri = snapshot.bind("ex", "http://changed.example/")
        assert (
            same_prefix_new_uri.kind is NamespaceConflictKind.SAME_PREFIX_NEW_URI
        )
        assert same_prefix_new_uri.existing_namespace == URIRef(
            "http://example.org/"
        )

        snapshot.bind("", "http://default.example/")
        default_change = snapshot.bind("", "http://other-default.example/")
        assert default_change.kind is NamespaceConflictKind.DEFAULT_PREFIX_CHANGED
        assert len(snapshot.conflicts) == 3

    def test_keep_first_policy_is_default_and_keeps_existing(self):
        snapshot = NamespaceSnapshot()
        snapshot.bind("ex", "http://example.org/")
        snapshot.bind("alt", "http://example.org/")
        snapshot.bind("ex", "http://changed.example/")
        snapshot.bind("", "http://default.example/")
        snapshot.bind("", "http://other-default.example/")

        assert snapshot.prefix("http://example.org/") == "ex"
        assert snapshot.namespace("alt") is None
        assert snapshot.namespace("ex") == URIRef("http://example.org/")
        assert snapshot.default_namespace == URIRef("http://default.example/")

    def test_override_policy_rebinds(self):
        snapshot = NamespaceSnapshot(
            conflict_policy=NamespaceConflictPolicy.OVERRIDE
        )
        snapshot.bind("ex", "http://example.org/")
        snapshot.bind("alt", "http://example.org/")
        assert snapshot.prefix("http://example.org/") == "alt"
        assert snapshot.namespace("ex") is None

        snapshot.bind("ex", "http://changed.example/")
        assert snapshot.namespace("ex") == URIRef("http://changed.example/")

        snapshot.bind("", "http://default.example/")
        snapshot.bind("", "http://other-default.example/")
        assert snapshot.default_namespace == URIRef(
            "http://other-default.example/"
        )

    def test_error_policy_raises_for_each_kind(self):
        snapshot = NamespaceSnapshot(conflict_policy="error")
        snapshot.bind("ex", "http://example.org/")
        with pytest.raises(NamespaceConflictError) as exc_info:
            snapshot.bind("alt", "http://example.org/")
        assert (
            exc_info.value.conflict.kind
            is NamespaceConflictKind.SAME_URI_NEW_PREFIX
        )
        with pytest.raises(NamespaceConflictError) as exc_info:
            snapshot.bind("ex", "http://changed.example/")
        assert (
            exc_info.value.conflict.kind
            is NamespaceConflictKind.SAME_PREFIX_NEW_URI
        )
        snapshot.bind("", "http://default.example/")
        with pytest.raises(NamespaceConflictError) as exc_info:
            snapshot.bind("", "http://other-default.example/")
        assert (
            exc_info.value.conflict.kind
            is NamespaceConflictKind.DEFAULT_PREFIX_CHANGED
        )

    def test_rebinding_same_pair_is_not_a_conflict(self):
        snapshot = NamespaceSnapshot()
        assert snapshot.bind("ex", "http://example.org/") is None
        assert snapshot.bind("ex", "http://example.org/") is None
        assert snapshot.conflicts == []


class TestSnapshotIsolation:
    @pytest.fixture
    def graph(self):
        graph = Graph()
        graph.add(
            (
                URIRef("http://example.org/s"),
                URIRef("http://example.org/p"),
                URIRef("http://example.org/o"),
            )
        )
        graph.add(
            (
                URIRef("https://schema.org/Person"),
                URIRef("http://example.org/p"),
                Literal("someone"),
            )
        )
        return graph

    def test_snapshot_is_detached_from_manager(self, graph):
        snapshot = graph.namespace_manager.snapshot()
        graph.bind("leaked", "http://leaked.example/")
        assert snapshot.namespace("leaked") is None
        assert "leaked" not in dict(snapshot.namespaces())

    def test_bindings_added_to_snapshot_do_not_reach_manager(self, graph):
        snapshot = graph.namespace_manager.snapshot()
        snapshot.bind("req", "http://request.example/")
        assert graph.namespace_manager.store.namespace("req") is None

    def test_namespaces_returns_frozen_view(self):
        snapshot = NamespaceSnapshot({"ex": "http://example.org/"})
        pairs = snapshot.namespaces()
        snapshot.bind("late", "http://late.example/")
        # binding while iterating must not disturb the started iteration
        assert pairs == (("ex", URIRef("http://example.org/")),)

    def test_copy_for_subrequest_does_not_write_back(self):
        parent = NamespaceSnapshot({"ex": "http://example.org/"})
        child = parent.copy()
        child.bind("sub", "http://subrequest.example/")
        assert child.namespace("ex") == URIRef("http://example.org/")
        assert child.namespace("sub") == URIRef("http://subrequest.example/")
        assert parent.namespace("sub") is None
        # parent bindings added afterwards do not leak into the child
        parent.bind("parent_only", "http://parent.example/")
        assert child.namespace("parent_only") is None
        # the conflict log starts fresh for the child
        assert child.conflicts == []

    def test_default_namespace_is_captured(self):
        snapshot = NamespaceSnapshot(default_namespace="http://default.example/")
        assert snapshot.default_namespace == URIRef("http://default.example/")
        assert snapshot.namespace("") == URIRef("http://default.example/")
        assert snapshot.qname("http://default.example/thing") == "thing"

    def test_generated_qnames_stay_inside_the_snapshot(self, graph):
        snapshot = NamespaceSnapshot()
        prefix, namespace, name = snapshot.compute_qname(
            "http://generated.example/Thing"
        )
        assert (prefix, name) == ("ns1", "Thing")
        assert namespace == URIRef("http://generated.example/")
        assert graph.namespace_manager.store.namespace("ns1") is None
        with pytest.raises(KeyError):
            snapshot.compute_qname(
                "http://unbound.example/Thing", generate=False
            )


class TestSerializerIsolation:
    @pytest.fixture
    def graph(self):
        graph = Graph()
        graph.add(
            (
                URIRef("http://example.org/s"),
                URIRef("http://example.org/type"),
                URIRef("http://example.org/T"),
            )
        )
        graph.add(
            (
                URIRef("https://schema.org/Person"),
                URIRef("http://example.org/type"),
                URIRef("http://example.org/s"),
            )
        )
        return graph

    def test_turtle_uses_only_its_snapshot(self, graph):
        snapshot = NamespaceSnapshot(
            {"ex": "http://example.org/", "schema": "https://schema.org/"}
        )
        output = graph.serialize(format="turtle", namespace_snapshot=snapshot)
        assert "@prefix ex: <http://example.org/> ." in output
        assert "@prefix schema: <https://schema.org/> ." in output
        assert "ex:s ex:type ex:T ." in output
        assert "schema:Person" in output
        # rdflib's default bindings must not bleed into the declarations
        assert "@prefix rdf:" not in output
        # serializer-generated bindings stay out of the manager
        assert graph.namespace_manager.store.namespace("ex") is None

    def test_jsonld_uses_only_its_snapshot(self, graph):
        snapshot = NamespaceSnapshot(
            {"ex": "http://example.org/", "schema": "https://schema.org/"}
        )
        output = graph.serialize(format="json-ld", namespace_snapshot=snapshot)
        document = json.loads(output)
        assert document["@context"] == {
            "ex": "http://example.org/",
            "schema": "https://schema.org/",
        }
        compact = json.dumps(document)
        assert "ex:s" in compact and "ex:type" in compact and "ex:T" in compact
        assert "schema:Person" in compact
        assert "dcterms" not in compact
        assert graph.namespace_manager.store.namespace("ex") is None

    def test_jsonld_default_namespace_becomes_vocab(self):
        graph = Graph()
        graph.add(
            (
                URIRef("http://default.example/s"),
                URIRef("http://default.example/p"),
                URIRef("http://default.example/o"),
            )
        )
        snapshot = NamespaceSnapshot(default_namespace="http://default.example/")
        document = json.loads(
            graph.serialize(format="json-ld", namespace_snapshot=snapshot)
        )
        assert document["@context"] == {"@vocab": "http://default.example/"}

    def test_interleaved_serializers_do_not_leak(self, graph):
        snap1 = NamespaceSnapshot({"one": "http://example.org/"})
        snap2 = NamespaceSnapshot({"two": "http://example.org/"})
        snap3 = NamespaceSnapshot({"three": "http://example.org/"})

        # create all serializers first, then serialize in a different order
        turtle1 = TurtleSerializer(graph, namespace_snapshot=snap1)
        jsonld2 = JsonLDSerializer(graph, namespace_snapshot=snap2)
        turtle3 = TurtleSerializer(graph, namespace_snapshot=snap3)

        stream3 = io.BytesIO()
        turtle3.serialize(stream3)
        out3 = stream3.getvalue().decode()
        stream2 = io.BytesIO()
        jsonld2.serialize(stream2)
        out2 = stream2.getvalue().decode()
        stream1 = io.BytesIO()
        turtle1.serialize(stream1)
        out1 = stream1.getvalue().decode()

        assert "@prefix one: <http://example.org/> ." in out1
        assert "two:" not in out1 and "three:" not in out1
        assert '"two": "http://example.org/"' in out2
        assert '"one"' not in out2 and '"three"' not in out2
        assert "@prefix three: <http://example.org/> ." in out3
        assert "one:" not in out3 and "two:" not in out3

        for output, format_name in (
            (out1, "turtle"),
            (out2, "json-ld"),
            (out3, "turtle"),
        ):
            reparsed = Graph().parse(data=output, format=format_name)
            assert (
                URIRef("http://example.org/s"),
                URIRef("http://example.org/type"),
                URIRef("http://example.org/T"),
            ) in reparsed

    def test_declaration_orders_are_independently_stable(self, graph):
        snapshot = NamespaceSnapshot(
            {
                "zoo": "http://zoo.example/",
                "alpha": "http://example.org/",
                "mid": "http://mid.example/",
            }
        )
        first_turtle = graph.serialize(format="turtle", namespace_snapshot=snapshot)
        second_turtle = graph.serialize(
            format="turtle", namespace_snapshot=snapshot.copy()
        )
        assert first_turtle == second_turtle
        declared = [
            line.split()[1].rstrip(":")
            for line in first_turtle.splitlines()
            if line.startswith("@prefix")
        ]
        assert declared == sorted(declared)

        first_jsonld = json.loads(
            graph.serialize(format="json-ld", namespace_snapshot=snapshot)
        )
        second_jsonld = json.loads(
            graph.serialize(
                format="json-ld", namespace_snapshot=snapshot.copy()
            )
        )
        assert first_jsonld["@context"] == second_jsonld["@context"]
        assert list(first_jsonld["@context"]) == sorted(first_jsonld["@context"])

    def test_default_manager_behavior_is_unchanged_without_snapshot(self):
        graph = Graph()
        graph.add(
            (
                URIRef("http://generated.example/s"),
                URIRef("http://generated.example/p"),
                URIRef("http://generated.example/o"),
            )
        )
        output = graph.serialize(format="turtle")
        reparsed = Graph().parse(data=output, format="turtle")
        assert (
            URIRef("http://generated.example/s"),
            URIRef("http://generated.example/p"),
            URIRef("http://generated.example/o"),
        ) in reparsed
        # the shared manager keeps mutating as before
        assert graph.namespace_manager.store.namespace("ns1") is not None


def test_parallel_threads_do_not_leak_prefixes():
    # One shared graph, many concurrent requests, each with its own
    # per-request prefixes and alternating output formats.
    shared_graph = Graph()
    errors: list[BaseException] = []

    def worker(index: int):
        try:
            subject = URIRef(f"http://example{index}.org/s")
            predicate = URIRef(f"http://example{index}.org/p")
            obj = URIRef(f"http://example{index}.org/o")
            shared_graph.add((subject, predicate, obj))

            # a distinct per-request prefix for the same URI shape
            snapshot = NamespaceSnapshot(
                {f"pre{index}": f"http://example{index}.org/"}
            )
            format_name = "turtle" if index % 2 == 0 else "json-ld"
            output = shared_graph.serialize(
                format=format_name, namespace_snapshot=snapshot.copy()
            )

            # re-parse and check the request's URI survived verbatim
            reparsed = Graph().parse(data=output, format=format_name)
            assert (subject, predicate, obj) in reparsed

            # this request's own prefix is present ...
            if format_name == "turtle":
                assert f"@prefix pre{index}:" in output
            else:
                assert f'"pre{index}"' in output
            # ... and no other request's prefix leaked in
            for other in range(8):
                if other == index:
                    continue
                assert f"pre{other}" not in output
            # the shared manager never saw any per-request prefix
            assert (
                shared_graph.namespace_manager.store.namespace(f"pre{index}")
                is None
            )
        except BaseException as error:  # pragma: no cover - failure path
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
