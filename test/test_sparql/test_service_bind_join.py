"""
Tests for the optional bind-join batch evaluation of SERVICE clauses.

When rdflib.plugins.sparql.evaluate.SERVICE_BIND_JOIN_BATCH_SIZE is set to
a positive integer, the solutions of the pattern to the left of a SERVICE
clause are sent to the remote endpoint in batches (using a VALUES clause)
instead of one request per solution.  These tests use a local fake SPARQL
endpoint (no sockets involved; urlopen is replaced) to count requests and
to compare the batched evaluation against the default row-by-row
evaluation.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Optional, Union
from urllib.parse import parse_qs, urlparse

import pytest

import rdflib.plugins.sparql.evaluate as evaluate
from rdflib import BNode, Graph, Literal, URIRef, Variable
from rdflib.plugins.sparql.sparql import SPARQLError
from rdflib.term import Identifier

EX = "http://example.org/"


class _FakeResponse:
    """Minimal stand-in for the object returned by urllib.request.urlopen."""

    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class FakeSPARQLEndpoint:
    """
    A local fake SPARQL endpoint.

    Received queries are evaluated against a local graph and every request
    is recorded so tests can count how many requests were made.
    """

    def __init__(
        self,
        data: str = "",
        fail_predicate: Optional[Callable[[str], bool]] = None,
        constant_bnode_labels: bool = False,
    ):
        self.graph = Graph()
        if data:
            self.graph.parse(data=data, format="turtle")
        self.fail_predicate = fail_predicate
        # If true, all blank node labels in responses are rewritten to one
        # constant label, simulating an endpoint whose blank node labels are
        # only scoped to a single response.
        self.constant_bnode_labels = constant_bnode_labels
        self.queries: list[str] = []

    @property
    def request_count(self) -> int:
        return len(self.queries)

    def handle(self, request) -> _FakeResponse:
        query = self._extract_query(request)
        self.queries.append(query)
        if self.fail_predicate is not None and self.fail_predicate(query):
            return _FakeResponse(500, b"")
        try:
            result = self.graph.query(query)
            body = result.serialize(format="json")
        except Exception as error:
            return _FakeResponse(400, str(error).encode("utf-8"))
        if isinstance(body, str):
            body = body.encode("utf-8")
        if self.constant_bnode_labels:
            body = self._rewrite_bnode_labels(body)
        return _FakeResponse(200, body)

    @staticmethod
    def _extract_query(request) -> str:
        if request.data is not None:
            params = parse_qs(request.data.decode("utf-8"))
        else:
            params = parse_qs(urlparse(request.full_url).query)
        return params["query"][0]

    @staticmethod
    def _rewrite_bnode_labels(body: bytes) -> bytes:
        data = json.loads(body.decode("utf-8"))
        for binding in data["results"]["bindings"]:
            for value in binding.values():
                if value.get("type") == "bnode":
                    value["value"] = "constantlabel"
        return json.dumps(data).encode("utf-8")


@pytest.fixture
def fake_endpoints(monkeypatch) -> Callable[[FakeSPARQLEndpoint], str]:
    """
    Route urlopen calls to registered fake endpoints and return a registrar
    that maps a fresh URL to each endpoint.
    """
    endpoints: dict[str, FakeSPARQLEndpoint] = {}

    def register(endpoint: FakeSPARQLEndpoint) -> str:
        url = f"http://fake-endpoint-{len(endpoints)}.example.org/sparql"
        endpoints[url] = endpoint
        return url

    def fake_urlopen(request, *args, **kwargs):
        url = request.full_url
        for endpoint_url, endpoint in endpoints.items():
            if url.startswith(endpoint_url):
                return endpoint.handle(request)
        raise AssertionError(f"no fake endpoint registered for {url}")

    monkeypatch.setattr(evaluate, "urlopen", fake_urlopen)
    return register


@pytest.fixture
def batch_size(monkeypatch):
    def set_batch_size(size: Optional[int]) -> None:
        monkeypatch.setattr(evaluate, "SERVICE_BIND_JOIN_BATCH_SIZE", size)

    return set_batch_size


def _freeze(
    bindings: list[Mapping[Variable, Identifier]],
) -> Counter:
    return Counter(frozenset(binding.items()) for binding in bindings)


def _local_graph(data: str) -> Graph:
    graph = Graph()
    graph.parse(data=data, format="turtle")
    return graph


REMOTE_NAMES = """
@prefix ex: <http://example.org/> .
""" + "\n".join(
    f'ex:o{i} ex:name "name{i}" .' for i in range(10)
)

LOCAL_KNOWS = """
@prefix ex: <http://example.org/> .
""" + "\n".join(
    f"ex:p{i} ex:knows ex:o{i} ." for i in range(10)
)


@pytest.mark.parametrize(
    "service_body",
    [
        "?o ex:name ?name",
        "SELECT ?o ?name WHERE { ?o ex:name ?name }",
        "SELECT DISTINCT ?o ?name WHERE { ?o ex:name ?name }",
        "SELECT * WHERE { ?o ex:name ?name }",
    ],
)
@pytest.mark.parametrize(
    ("size", "expected_requests"),
    [(1, 10), (3, 4), (4, 3), (10, 1), (100, 1)],
)
def test_bind_join_matches_row_by_row(
    fake_endpoints,
    batch_size,
    service_body: str,
    size: int,
    expected_requests: int,
):
    """
    Batched evaluation must return the same solutions (with the same
    multiplicity) as row-by-row evaluation, while issuing fewer requests.
    """
    endpoint = FakeSPARQLEndpoint(REMOTE_NAMES)
    url = fake_endpoints(endpoint)
    graph = _local_graph(LOCAL_KNOWS)
    query = f"""
        PREFIX ex: <http://example.org/>
        SELECT ?s ?name WHERE {{
            ?s ex:knows ?o .
            SERVICE <{url}> {{ {service_body} }}
        }}
    """

    batch_size(None)
    baseline = graph.query(query).bindings
    assert endpoint.request_count == 10
    assert len(baseline) == 10

    endpoint.queries.clear()
    batch_size(size)
    batched = graph.query(query).bindings
    assert endpoint.request_count == expected_requests
    assert _freeze(batched) == _freeze(baseline)
    # The internal correlation variable must not leak into the results.
    internal = evaluate._SERVICE_BIND_JOIN_ROW_VARIABLE
    for binding in batched:
        assert all(internal not in str(var) for var in binding)


def test_bind_join_duplicate_left_rows(fake_endpoints, batch_size):
    """
    Duplicate input solutions must keep their multiplicity: the bag
    semantics of the join may not change with the batching.
    """
    endpoint = FakeSPARQLEndpoint(REMOTE_NAMES)
    url = fake_endpoints(endpoint)
    graph = Graph()
    query = f"""
        PREFIX ex: <http://example.org/>
        SELECT ?o ?name WHERE {{
            VALUES ?o {{ ex:o0 ex:o0 ex:o0 ex:o1 }}
            SERVICE <{url}> {{ ?o ex:name ?name }}
        }}
    """

    batch_size(None)
    baseline = graph.query(query).bindings
    assert endpoint.request_count == 4
    assert len(baseline) == 4

    endpoint.queries.clear()
    batch_size(10)
    batched = graph.query(query).bindings
    assert endpoint.request_count == 1
    assert len(batched) == 4
    assert _freeze(batched) == _freeze(baseline)


def test_bind_join_unbound_variables(fake_endpoints, batch_size):
    """
    Variables that are unbound in some input solutions must be sent as
    UNDEF and must not be filled in with any value.
    """
    endpoint = FakeSPARQLEndpoint(REMOTE_NAMES)
    url = fake_endpoints(endpoint)
    graph = _local_graph(
        """
        @prefix ex: <http://example.org/> .
        ex:a a ex:Person ; ex:knows ex:o0 .
        ex:b a ex:Person .
        ex:c a ex:Person ; ex:knows ex:o1 .
        """
    )
    query = f"""
        PREFIX ex: <http://example.org/>
        SELECT ?s ?o ?name WHERE {{
            ?s a ex:Person .
            OPTIONAL {{ ?s ex:knows ?o }}
            SERVICE <{url}> {{ ?o ex:name ?name }}
        }}
    """

    batch_size(None)
    baseline = graph.query(query).bindings
    assert endpoint.request_count == 3

    endpoint.queries.clear()
    batch_size(10)
    batched = graph.query(query).bindings
    assert endpoint.request_count == 1
    # The unbound ?o of ex:b must have been sent as UNDEF.
    assert "UNDEF" in endpoint.queries[0]
    assert _freeze(batched) == _freeze(baseline)
    # No binding may contain the string UNDEF or a made-up value.
    for binding in batched:
        for value in binding.values():
            assert value != Literal("UNDEF")


def test_bind_join_empty_remote_result(fake_endpoints, batch_size):
    """
    A batch whose remote result is empty contributes no solutions, but the
    requests must still have been made.
    """
    endpoint = FakeSPARQLEndpoint("")
    url = fake_endpoints(endpoint)
    graph = _local_graph(LOCAL_KNOWS)
    query = f"""
        PREFIX ex: <http://example.org/>
        SELECT ?s ?name WHERE {{
            ?s ex:knows ?o .
            SERVICE <{url}> {{ ?o ex:name ?name }}
        }}
    """

    batch_size(None)
    baseline = graph.query(query).bindings
    assert endpoint.request_count == 10
    assert baseline == []

    endpoint.queries.clear()
    batch_size(4)
    batched = graph.query(query).bindings
    assert endpoint.request_count == 3
    assert batched == []


def test_bind_join_partially_empty_remote_result(fake_endpoints, batch_size):
    """
    Batches without any remote match must not affect the results of the
    other batches.
    """
    endpoint = FakeSPARQLEndpoint(
        """
        @prefix ex: <http://example.org/> .
        ex:o0 ex:name "name0" .
        ex:o1 ex:name "name1" .
        """
    )
    url = fake_endpoints(endpoint)
    graph = _local_graph(LOCAL_KNOWS)
    query = f"""
        PREFIX ex: <http://example.org/>
        SELECT ?s ?name WHERE {{
            ?s ex:knows ?o .
            SERVICE <{url}> {{ ?o ex:name ?name }}
        }}
    """

    batch_size(None)
    baseline = graph.query(query).bindings
    assert len(baseline) == 2

    endpoint.queries.clear()
    batch_size(5)
    batched = graph.query(query).bindings
    assert endpoint.request_count == 2
    assert _freeze(batched) == _freeze(baseline)


def test_bind_join_falls_back_for_limit(fake_endpoints, batch_size):
    """
    A service query with solution modifiers (LIMIT/OFFSET/aggregates)
    cannot be batched without changing its semantics; it falls back to
    row-by-row evaluation so the results stay identical.
    """
    endpoint = FakeSPARQLEndpoint(
        """
        @prefix ex: <http://example.org/> .
        ex:o0 ex:name "a" .
        ex:o0 ex:name "b" .
        ex:o1 ex:name "c" .
        """
    )
    url = fake_endpoints(endpoint)
    graph = _local_graph(
        """
        @prefix ex: <http://example.org/> .
        ex:p0 ex:knows ex:o0 .
        ex:p1 ex:knows ex:o1 .
        """
    )
    query = f"""
        PREFIX ex: <http://example.org/>
        SELECT ?s ?name WHERE {{
            ?s ex:knows ?o .
            SERVICE <{url}> {{
                SELECT ?o ?name WHERE {{ ?o ex:name ?name }} LIMIT 1
            }}
        }}
    """

    batch_size(None)
    baseline = graph.query(query).bindings
    assert endpoint.request_count == 2

    endpoint.queries.clear()
    batch_size(10)
    batched = graph.query(query).bindings
    # Not batchable: one request per input solution, identical results.
    assert endpoint.request_count == 2
    assert _freeze(batched) == _freeze(baseline)


def test_bind_join_bnodes_distinct_across_requests(fake_endpoints, batch_size):
    """
    Blank node labels are only scoped to a single response: identical
    labels returned by different requests must not be conflated into one
    node.
    """
    endpoint = FakeSPARQLEndpoint(
        """
        @prefix ex: <http://example.org/> .
        _:b0 ex:p ex:o0 .
        _:b1 ex:p ex:o1 .
        """,
        constant_bnode_labels=True,
    )
    url = fake_endpoints(endpoint)
    graph = Graph()
    query = f"""
        PREFIX ex: <http://example.org/>
        SELECT ?s ?o WHERE {{
            VALUES ?o {{ ex:o0 ex:o1 }}
            SERVICE <{url}> {{ ?s ex:p ?o }}
        }}
    """

    batch_size(1)
    batched = graph.query(query).bindings
    assert endpoint.request_count == 2
    assert len(batched) == 2
    subjects = {binding[Variable("s")] for binding in batched}
    # Both responses used the same blank node label, but the nodes are
    # distinct.
    assert len(subjects) == 2
    assert all(isinstance(s, BNode) for s in subjects)


def test_bind_join_bnodes_stable_within_request(fake_endpoints, batch_size):
    """
    Identical blank node labels within a single response still denote the
    same node.
    """
    endpoint = FakeSPARQLEndpoint(
        """
        @prefix ex: <http://example.org/> .
        _:b0 ex:p ex:o0 .
        _:b0 ex:q ex:o0 .
        """
    )
    url = fake_endpoints(endpoint)
    graph = Graph()
    query = f"""
        PREFIX ex: <http://example.org/>
        SELECT ?s ?p WHERE {{
            VALUES ?o {{ ex:o0 }}
            SERVICE <{url}> {{ ?s ?p ?o }}
        }}
    """

    batch_size(10)
    batched = graph.query(query).bindings
    assert endpoint.request_count == 1
    assert len(batched) == 2
    subjects = [binding[Variable("s")] for binding in batched]
    assert subjects[0] == subjects[1]


def test_bind_join_silent_failure(fake_endpoints, batch_size):
    """
    With SILENT, a failed batch evaluates to its input solutions; without
    SILENT the error is propagated.
    """
    endpoint = FakeSPARQLEndpoint(REMOTE_NAMES, fail_predicate=lambda q: True)
    url = fake_endpoints(endpoint)
    graph = _local_graph(LOCAL_KNOWS)
    query_template = """
        PREFIX ex: <http://example.org/>
        SELECT ?s ?name WHERE {{
            ?s ex:knows ?o .
            SERVICE {silent} <{url}> {{ ?o ex:name ?name }}
        }}
    """

    batch_size(4)
    with pytest.raises(Exception):
        graph.query(query_template.format(silent="", url=url)).bindings

    endpoint.queries.clear()
    batched = graph.query(query_template.format(silent="SILENT", url=url)).bindings
    assert endpoint.request_count == 3
    # Every input solution is passed through unchanged.
    assert len(batched) == 10
    for binding in batched:
        assert Variable("name") not in binding
        assert Variable("s") in binding


def test_bind_join_silent_partial_failure(fake_endpoints, batch_size):
    """
    A partially failing endpoint must not be treated as if the whole
    remote result were empty: failed batches pass their input solutions
    through, successful batches are joined normally.
    """
    endpoint = FakeSPARQLEndpoint(
        REMOTE_NAMES,
        fail_predicate=lambda query: "o2" in query or "o3" in query,
    )
    url = fake_endpoints(endpoint)
    graph = _local_graph(
        """
        @prefix ex: <http://example.org/> .
        ex:p0 ex:knows ex:o0 .
        ex:p1 ex:knows ex:o1 .
        ex:p2 ex:knows ex:o2 .
        ex:p3 ex:knows ex:o3 .
        """
    )
    query = f"""
        PREFIX ex: <http://example.org/>
        SELECT ?s ?name WHERE {{
            ?s ex:knows ?o .
            SERVICE SILENT <{url}> {{ ?o ex:name ?name }}
        }}
    """

    batch_size(2)
    batched = graph.query(query).bindings
    assert endpoint.request_count == 2
    assert len(batched) == 4
    by_subject = {binding[Variable("s")]: binding for binding in batched}
    # The first batch (o0, o1) succeeded and was joined.
    assert by_subject[URIRef(EX + "p0")][Variable("name")] == Literal("name0")
    assert by_subject[URIRef(EX + "p1")][Variable("name")] == Literal("name1")
    # The second batch (o2, o3) failed and its solutions passed through.
    assert Variable("name") not in by_subject[URIRef(EX + "p2")]
    assert Variable("name") not in by_subject[URIRef(EX + "p3")]


def test_bind_join_dynamic_endpoint(fake_endpoints, batch_size):
    """
    With a dynamic SERVICE address, input solutions are batched separately
    per endpoint.
    """
    endpoint_a = FakeSPARQLEndpoint(
        """
        @prefix ex: <http://example.org/> .
        ex:s0 ex:name "name0" .
        ex:s2 ex:name "name2" .
        """
    )
    endpoint_b = FakeSPARQLEndpoint(
        """
        @prefix ex: <http://example.org/> .
        ex:s1 ex:name "name1" .
        """
    )
    url_a = fake_endpoints(endpoint_a)
    url_b = fake_endpoints(endpoint_b)
    graph = Graph()
    query = f"""
        PREFIX ex: <http://example.org/>
        SELECT ?s ?name WHERE {{
            VALUES (?s ?ep) {{
                (ex:s0 <{url_a}>)
                (ex:s1 <{url_b}>)
                (ex:s2 <{url_a}>)
            }}
            SERVICE ?ep {{ ?s ex:name ?name }}
        }}
    """

    batch_size(10)
    batched = graph.query(query).bindings
    assert endpoint_a.request_count == 1
    assert endpoint_b.request_count == 1
    assert _freeze(batched) == _freeze(
        [
            {Variable("s"): URIRef(EX + "s0"), Variable("name"): Literal("name0")},
            {Variable("s"): URIRef(EX + "s1"), Variable("name"): Literal("name1")},
            {Variable("s"): URIRef(EX + "s2"), Variable("name"): Literal("name2")},
        ]
    )


def test_bind_join_dynamic_endpoint_unbound(fake_endpoints, batch_size):
    """
    Input solutions without a bound endpoint IRI are a service failure:
    with SILENT they pass through, without SILENT an error is raised.
    """
    endpoint = FakeSPARQLEndpoint(
        """
        @prefix ex: <http://example.org/> .
        ex:s0 ex:name "name0" .
        """
    )
    url = fake_endpoints(endpoint)
    graph = Graph()
    query_template = """
        PREFIX ex: <http://example.org/>
        SELECT ?s ?name WHERE {{
            VALUES (?s ?ep) {{
                (ex:s0 <{url}>)
                (ex:s1 UNDEF)
            }}
            SERVICE {silent} ?ep {{ ?s ex:name ?name }}
        }}
    """

    batch_size(10)
    with pytest.raises(SPARQLError):
        graph.query(query_template.format(silent="", url=url)).bindings

    endpoint.queries.clear()
    batched = graph.query(query_template.format(silent="SILENT", url=url)).bindings
    assert endpoint.request_count == 1
    assert _freeze(batched) == _freeze(
        [
            {Variable("s"): URIRef(EX + "s0"), Variable("name"): Literal("name0")},
            {Variable("s"): URIRef(EX + "s1")},
        ]
    )


def test_bind_join_internal_variable_not_leaked(fake_endpoints, batch_size):
    """
    The internal correlation variable must not collide with user variables
    and must not appear in the final results.
    """
    endpoint = FakeSPARQLEndpoint(REMOTE_NAMES)
    url = fake_endpoints(endpoint)
    graph = _local_graph(LOCAL_KNOWS)
    row_variable = evaluate._SERVICE_BIND_JOIN_ROW_VARIABLE
    query = f"""
        PREFIX ex: <http://example.org/>
        SELECT ?s ?name ?{row_variable} WHERE {{
            VALUES ?{row_variable} {{ ex:userrow }}
            ?s ex:knows ?o .
            SERVICE <{url}> {{ ?o ex:name ?name }}
        }}
    """

    batch_size(None)
    baseline = graph.query(query).bindings
    assert len(baseline) == 10

    endpoint.queries.clear()
    batch_size(4)
    batched = graph.query(query).bindings
    assert endpoint.request_count == 3
    assert _freeze(batched) == _freeze(baseline)
    for binding in batched:
        # The user variable keeps its value ...
        assert binding[Variable(row_variable)] == URIRef(EX + "userrow")
        # ... and no internal variable leaks into the results.
        assert set(map(str, binding)) == {"s", "name", row_variable}
