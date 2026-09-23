"""
Tests for SPARQL query execution statistics (rdflib.plugins.sparql.stats).
"""

import json
from collections import Counter

import pytest

import rdflib.plugins.sparql
from rdflib import Graph, URIRef, Variable
from rdflib.plugins.sparql.stats import (
    CLOSED,
    COMPLETED,
    FAILED,
    NOT_STARTED,
    RUNNING,
    QueryStats,
)
from rdflib.plugins.stores.memory import Memory

DATA = """
@prefix : <http://ex.org/> .
:a a :Person ; :name "a" ; :age 1 ; :knows :b .
:b a :Person ; :name "b" ; :age 2 .
:c a :Person ; :name "c" ; :age 3 ; :knows :a .
:d a :Person ; :name "d" ; :age 4 .
"""

# a query with an OPTIONAL, a sub-query and an outer LIMIT
QUERY = """
PREFIX : <http://ex.org/>
SELECT ?s ?name ?k WHERE {
  ?s a :Person ; :name ?name .
  OPTIONAL { ?s :knows ?k }
  { SELECT ?s WHERE { ?s :age ?age } }
}
LIMIT 2
"""


def make_graph() -> Graph:
    return Graph().parse(data=DATA, format="turtle")


def nodes_by_name(stats: QueryStats, name: str):
    return [n for n in stats.nodes.values() if n.name == name]


def test_stats_not_enabled_by_default() -> None:
    res = make_graph().query(QUERY)
    assert res.stats is None
    assert len(list(res)) == 2


def test_stats_report_optional_subquery_limit() -> None:
    res = make_graph().query(QUERY, stats=True)
    rows = list(res)
    assert len(rows) == 2

    stats = res.stats
    assert isinstance(stats, QueryStats)
    assert stats.state == COMPLETED

    # every operator has a stable, unique id and consistent parent/child links
    assert len(stats.nodes) > 0
    assert len({n.node_id for n in stats.nodes.values()}) == len(stats.nodes)
    root = stats.nodes[stats.root_id]
    assert root.parent_id is None
    for node in stats.nodes.values():
        for child_id in node.children:
            assert stats.nodes[child_id].parent_id == node.node_id
        if node is not root:
            assert node.parent_id in stats.nodes

    # the expected operators are all there
    assert len(nodes_by_name(stats, "LeftJoin")) == 1  # the OPTIONAL
    assert len(nodes_by_name(stats, "Join")) == 1
    assert len(nodes_by_name(stats, "Slice")) == 1  # the LIMIT
    assert len(nodes_by_name(stats, "BGP")) == 3

    # the LIMIT delivered exactly the result rows
    (slice_node,) = nodes_by_name(stats, "Slice")
    assert slice_node.rows_out == 2
    assert slice_node.state == COMPLETED

    # the OPTIONAL right hand side was evaluated once per left row, plus
    # re-evaluated for left rows without a match
    (left_join,) = nodes_by_name(stats, "LeftJoin")
    optional_bgp = stats.nodes[left_join.children[1]]
    assert optional_bgp.calls >= 2
    assert optional_bgp.rows_out >= 1

    # the sub-query BGP was evaluated once per row of its left side
    (to_multiset,) = nodes_by_name(stats, "ToMultiSet")
    sub_bgp = nodes_by_name(stats, "BGP")[2]
    assert sub_bgp.calls >= 1
    assert to_multiset.calls == sub_bgp.calls

    # rows_in of every non-opaque operator is the sum of its children's
    # output rows (or, for BGPs, the triples read from the store)
    for node in stats.nodes.values():
        if node.name == "BGP":
            assert node.rows_in == node.source_rows
            assert node.source_rows > 0
        elif node.children:
            assert node.rows_in == sum(
                stats.nodes[c].rows_out for c in node.children
            )

    # the report mentions the operators and the query id
    report = stats.report()
    assert stats.query_id in report
    assert "LeftJoin" in report
    assert "Slice" in report
    # to_dict is JSON serializable
    json.dumps(stats.to_dict())


class CountingMemory(Memory):
    """A Memory store that counts triples() calls and yielded triples."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.triple_calls = 0
        self.triple_reads = 0

    def triples(self, triple_pattern, context=None):
        self.triple_calls += 1
        for triple in super().triples(triple_pattern, context=context):
            self.triple_reads += 1
            yield triple


def result_multiset(res) -> Counter:
    return Counter(tuple(str(v) for v in row) for row in res)


def test_stats_adds_no_triple_reads_and_keeps_results() -> None:
    store = CountingMemory()
    graph = Graph(store=store)
    graph.parse(data=DATA, format="turtle")

    store.triple_calls = store.triple_reads = 0
    plain = result_multiset(graph.query(QUERY))
    plain_calls, plain_reads = store.triple_calls, store.triple_reads

    store.triple_calls = store.triple_reads = 0
    res = graph.query(QUERY, stats=True)
    with_stats = result_multiset(res)
    stats_calls, stats_reads = store.triple_calls, store.triple_reads

    # enabling statistics must not read more triples ...
    assert (stats_calls, stats_reads) == (plain_calls, plain_reads)
    assert plain_reads > 0
    # ... and must not change the result multiset
    assert with_stats == plain
    # the BGP source_rows account for the triples actually read
    bgp_reads = sum(
        n.source_rows for n in res.stats.nodes.values() if n.name == "BGP"
    )
    assert bgp_reads == stats_reads


def test_stats_limit_truncation_states() -> None:
    res = make_graph().query(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person } LIMIT 2",
        stats=True,
    )
    rows = list(res)
    assert len(rows) == 2

    stats = res.stats
    (slice_node,) = nodes_by_name(stats, "Slice")
    (bgp,) = nodes_by_name(stats, "BGP")
    # the LIMIT completed normally ...
    assert slice_node.state == COMPLETED
    assert slice_node.rows_out == 2
    # ... while the operators below were cut short: only 2 of the 4
    # matching triples were ever read
    assert bgp.state == CLOSED
    assert bgp.source_rows == 2
    assert bgp.rows_out == 2
    assert stats.state == COMPLETED


def test_stats_partial_consumption_and_close() -> None:
    res = make_graph().query(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person }",
        stats=True,
    )
    stats = res.stats

    # nothing has been iterated yet: no operator has started
    assert all(n.state == NOT_STARTED for n in stats.nodes.values())
    assert stats.state == NOT_STARTED

    # consume exactly one row: work has started but not finished
    iterator = iter(res)
    next(iterator)
    assert stats.state == RUNNING
    (bgp,) = nodes_by_name(stats, "BGP")
    assert bgp.state == RUNNING
    assert bgp.rows_out == 1
    assert bgp.source_rows == 1

    # explicitly closing the result marks the interrupted operators as
    # closed; the final statistics remain readable afterwards
    res.close()
    assert stats.state == CLOSED
    assert bgp.state == CLOSED
    assert bgp.rows_out == 1
    assert "closed" in stats.report()


def test_stats_aggregation() -> None:
    res = make_graph().query(
        """
        PREFIX : <http://ex.org/>
        SELECT ?s (COUNT(?p) AS ?c) WHERE { ?s ?p ?o } GROUP BY ?s
        """,
        stats=True,
    )
    rows = list(res)
    stats = res.stats
    (agg,) = nodes_by_name(stats, "AggregateJoin")
    # one output row per group, all input rows were read
    assert agg.rows_out == len(rows) == 4
    assert agg.state == COMPLETED
    (bgp,) = nodes_by_name(stats, "BGP")
    assert agg.rows_in == bgp.rows_out == len(make_graph())


def test_stats_subquery_and_join_calls() -> None:
    res = make_graph().query(QUERY, stats=True)
    list(res)
    stats = res.stats
    (join,) = nodes_by_name(stats, "Join")
    # join input is everything read from its two sides
    assert join.rows_in == sum(stats.nodes[c].rows_out for c in join.children)
    # the sub-query side was evaluated once per row of the left side
    (to_multiset,) = nodes_by_name(stats, "ToMultiSet")
    left = stats.nodes[join.children[0]]
    assert to_multiset.calls == left.rows_out


def test_stats_custom_eval_is_opaque() -> None:
    def custom_eval(ctx, part):
        if part.name == "BGP":

            def two_solutions():
                for i in (1, 2):
                    c = ctx.push()
                    c[Variable("s")] = URIRef(f"http://ex.org/x{i}")
                    yield c.solution()

            return two_solutions()
        raise NotImplementedError()

    rdflib.plugins.sparql.CUSTOM_EVALS["test_stats_opaque"] = custom_eval
    try:
        res = make_graph().query(
            "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person }",
            stats=True,
        )
        rows = list(res)
    finally:
        del rdflib.plugins.sparql.CUSTOM_EVALS["test_stats_opaque"]

    assert len(rows) == 2
    (bgp,) = nodes_by_name(res.stats, "BGP")
    assert bgp.opaque is True
    assert bgp.handler == "test_stats_opaque"
    # the output of the custom evaluator is counted ...
    assert bgp.rows_out == 2
    # ... but its internals are not observable: no fabricated rows_in
    assert bgp.rows_in is None
    assert bgp.source_rows == 0
    assert bgp.to_dict()["rows_in"] is None


def test_stats_failure_state() -> None:
    def custom_eval(ctx, part):
        if part.name == "BGP":

            def failing():
                yield ctx.solution()
                raise RuntimeError("boom")

            return failing()
        raise NotImplementedError()

    rdflib.plugins.sparql.CUSTOM_EVALS["test_stats_failing"] = custom_eval
    try:
        res = make_graph().query(
            "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person }",
            stats=True,
        )
        with pytest.raises(RuntimeError, match="boom"):
            list(res)
    finally:
        del rdflib.plugins.sparql.CUSTOM_EVALS["test_stats_failing"]

    (bgp,) = nodes_by_name(res.stats, "BGP")
    assert bgp.state == FAILED
    assert bgp.failed_calls == 1
    assert res.stats.state == FAILED


def test_stats_interleaved_queries_are_independent() -> None:
    graph = make_graph()
    res1 = graph.query(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person }",
        stats=True,
    )
    res2 = graph.query(
        "PREFIX : <http://ex.org/> SELECT ?name WHERE { ?s :name ?name }",
        stats=True,
    )
    assert res1.stats is not res2.stats
    assert res1.stats.query_id != res2.stats.query_id

    it1, it2 = iter(res1), iter(res2)
    # interleave the iteration of both results
    next(it1)
    next(it2)
    next(it1)
    (bgp1,) = nodes_by_name(res1.stats, "BGP")
    (bgp2,) = nodes_by_name(res2.stats, "BGP")
    assert bgp1.rows_out == 2
    assert bgp2.rows_out == 1

    rows1 = list(it1)
    rows2 = list(it2)
    assert len(rows1) == 2
    assert len(rows2) == 3
    assert bgp1.rows_out == 4
    assert bgp2.rows_out == 4
    assert res1.stats.state == COMPLETED
    assert res2.stats.state == COMPLETED


def test_stats_ask() -> None:
    res = make_graph().query(
        "PREFIX : <http://ex.org/> ASK { ?s a :Person }", stats=True
    )
    assert res.askAnswer is True
    stats = res.stats
    (ask,) = nodes_by_name(stats, "AskQuery")
    assert ask.state == COMPLETED
    assert ask.rows_out == 1
    # the pattern was stopped after the first match
    (bgp,) = nodes_by_name(stats, "BGP")
    assert bgp.state == CLOSED
    assert bgp.rows_out == 1


def test_stats_prepared_query() -> None:
    from rdflib.plugins.sparql import prepareQuery

    res = make_graph().query(prepareQuery(QUERY), stats=True)
    assert len(list(res)) == 2
    assert res.stats is not None
    assert res.stats.state == COMPLETED
