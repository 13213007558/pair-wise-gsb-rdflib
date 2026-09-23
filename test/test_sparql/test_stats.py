"""
Tests for SPARQL query execution statistics
(rdflib.plugins.sparql.stats).
"""

from __future__ import annotations

import pytest

from rdflib import Graph
from rdflib.plugins.sparql import CUSTOM_EVALS
from rdflib.plugins.sparql.stats import (
    CLOSED,
    COMPLETED,
    FAILED,
    NOT_STARTED,
    RUNNING,
    QueryStats,
)
from rdflib.plugins.stores.memory import Memory


class CountingMemory(Memory):
    """A Memory store that counts triple reads."""

    def __init__(self):
        super().__init__()
        self.triples_calls = 0
        self.triples_yielded = 0

    def triples(self, triple_pattern, context=None):
        self.triples_calls += 1
        for triple in super().triples(triple_pattern, context):
            self.triples_yielded += 1
            yield triple


DATA = """
@prefix : <http://example.org/> .
:alice a :Person ; :name "Alice" ; :knows :bob .
:bob a :Person ; :name "Bob" ; :knows :carol .
:carol a :Person ; :knows :alice .
:dave a :Person .
"""

# one OPTIONAL, one subquery (with aggregation) and an outer LIMIT
QUERY = """
PREFIX : <http://example.org/>
SELECT ?s ?name ?n WHERE {
  ?s a :Person .
  OPTIONAL { ?s :name ?name }
  { SELECT ?s (COUNT(?o) AS ?n) WHERE { ?s ?p ?o } GROUP BY ?s }
} LIMIT 2
"""


def make_graph() -> Graph:
    graph = Graph(store=CountingMemory())
    graph.parse(data=DATA, format="turtle")
    return graph


def by_name(stats: QueryStats, name: str):
    return [op for op in stats.operators if op.name == name]


def test_stats_do_not_change_results_or_triple_reads():
    """Enabling stats must not add triple reads nor change the result."""
    graph = make_graph()
    store = graph.store

    plain_rows = sorted(map(repr, graph.query(QUERY)))
    plain_calls = store.triples_calls
    plain_yielded = store.triples_yielded

    store.triples_calls = 0
    store.triples_yielded = 0

    traced = graph.query(QUERY, stats=True)
    traced_rows = sorted(map(repr, traced))

    assert traced_rows == plain_rows
    assert store.triples_calls == plain_calls
    assert store.triples_yielded == plain_yielded
    assert traced.stats is not None


def test_stats_do_not_change_result_multiset():
    """Same multiset with and without stats, without LIMIT truncation."""
    query = QUERY.replace(" LIMIT 2", "")
    graph = make_graph()

    plain = sorted(map(repr, graph.query(query)))
    traced = sorted(map(repr, graph.query(query, stats=True)))

    assert len(plain) == 4
    assert traced == plain


def test_operator_tree_and_counts():
    graph = make_graph()
    result = graph.query(QUERY, stats=True)
    rows = list(result)
    assert len(rows) == 2

    stats = result.stats
    names = {op.name for op in stats.operators}
    # OPTIONAL, LIMIT, join with the subquery, aggregation, subquery itself
    assert "LeftJoin" in names
    assert "Slice" in names
    assert "Join" in names
    assert "AggregateJoin" in names
    assert "Group" in names
    assert "BGP" in names
    # the subquery shows up as a nested Project below ToMultiSet
    assert len(by_name(stats, "SelectQuery")) == 1
    assert len(by_name(stats, "Project")) == 2

    # operator ids are stable and unique within this query execution
    ids = [op.id for op in stats.operators]
    assert len(set(ids)) == len(ids)
    assert stats.operator(ids[0]).id == ids[0]

    # parent/child relations are consistent
    for op in stats.operators:
        if op.parent is None:
            assert op.parent_id is None
        else:
            assert op.parent_id == op.parent.id
            assert op in stats.children_of(op.parent)
    assert len(stats.roots) == 1
    assert stats.roots[0].name == "SelectQuery"

    # the LIMIT slice emitted exactly LIMIT rows, read from below
    slice_op = by_name(stats, "Slice")[0]
    assert slice_op.rows_out == 2
    assert slice_op.rows_in == 2
    assert slice_op.state == COMPLETED

    # rows emitted by children are the rows read by their parents
    for op in stats.operators:
        assert op.rows_in == sum(child.rows_out for child in stats.children_of(op))

    # OPTIONAL: the left join read rows from its left BGP and evaluated
    # its right hand side once per left row
    left_join = by_name(stats, "LeftJoin")[0]
    left_bgp, right_bgp = stats.children_of(left_join)
    assert left_bgp.rows_out >= 2
    assert right_bgp.calls == left_bgp.rows_out

    # aggregation: one group per ?s, per (correlated) subquery call
    aggregate = by_name(stats, "AggregateJoin")[0]
    group = by_name(stats, "Group")[0]
    assert aggregate.calls >= 1
    assert group.rows_out == aggregate.rows_in
    assert aggregate.rows_out <= group.rows_out

    # the subquery was evaluated once per outer row (lazy join)
    to_multiset = by_name(stats, "ToMultiSet")[0]
    (subquery,) = stats.children_of(to_multiset)
    assert subquery.name == "Project"
    assert subquery.calls >= 1
    assert subquery.rows_out == aggregate.rows_out


def test_limit_truncation_reports_partial_work():
    graph = make_graph()
    result = graph.query(QUERY, stats=True)
    assert len(list(result)) == 2

    stats = result.stats
    # the slice completed, operators below it were closed early
    assert by_name(stats, "Slice")[0].state == COMPLETED
    assert by_name(stats, "LeftJoin")[0].state == CLOSED
    assert by_name(stats, "Join")[0].state == CLOSED
    # the outer BGP only produced the rows that were actually pulled
    left_join = by_name(stats, "LeftJoin")[0]
    left_bgp = stats.children_of(left_join)[0]
    assert left_bgp.rows_out < 4


def test_partial_consumption_then_close():
    graph = make_graph()
    result = graph.query(QUERY, stats=True)
    rows = iter(result)
    next(rows)

    stats = result.stats
    root = stats.roots[0]
    assert root.state == RUNNING
    assert root.rows_out == 1

    # explicitly closing the result finalizes the report
    result.close()
    assert root.state == CLOSED
    assert any(op.state == CLOSED for op in stats.operators)

    # the report is still readable after closing
    assert root.rows_out == 1
    report = stats.to_dict()
    assert len(report["operators"]) == len(stats.operators)
    assert str(stats)


def test_not_started_operators():
    graph = make_graph()
    result = graph.query("SELECT ?s WHERE { ?s ?p ?o } LIMIT 0", stats=True)
    assert list(result) == []

    stats = result.stats
    assert by_name(stats, "Slice")[0].state == COMPLETED
    not_started = [op for op in stats.operators if op.state == NOT_STARTED]
    # operators below the LIMIT 0 slice were created but never iterated
    assert not_started
    for op in not_started:
        assert op.calls == 1
        assert op.starts == 0
        assert op.rows_in == 0
        assert op.rows_out == 0


def test_custom_eval_is_an_opaque_node():
    def custom_eval(ctx, part):
        if part.name != "BGP":
            raise NotImplementedError()
        return iter([ctx.solution(), ctx.solution()])

    CUSTOM_EVALS["test_stats_custom_eval"] = custom_eval
    try:
        graph = make_graph()
        result = graph.query("SELECT * WHERE { ?s ?p ?o }", stats=True)
        list(result)
    finally:
        del CUSTOM_EVALS["test_stats_custom_eval"]

    bgp = by_name(result.stats, "BGP")[0]
    # the custom evaluator participates, but its internals are opaque:
    # only the rows crossing the operator boundary are counted
    assert bgp.opaque is True
    assert bgp.rows_out == 2
    assert bgp.rows_in == 0
    assert bgp.state == COMPLETED


def test_failed_operator_is_reported():
    def boom(ctx, part):
        if part.name != "BGP":
            raise NotImplementedError()

        def gen():
            raise RuntimeError("boom")
            yield

        return gen()

    CUSTOM_EVALS["test_stats_failing_eval"] = boom
    try:
        graph = make_graph()
        result = graph.query("SELECT * WHERE { ?s ?p ?o }", stats=True)
        with pytest.raises(RuntimeError):
            list(result)
    finally:
        del CUSTOM_EVALS["test_stats_failing_eval"]

    stats = result.stats
    bgp = by_name(stats, "BGP")[0]
    assert bgp.state == FAILED
    assert bgp.failed == 1
    assert stats.roots[0].state == FAILED


def test_interleaved_queries_have_independent_stats():
    graph = make_graph()
    result_a = graph.query(
        "SELECT * WHERE { ?s <http://example.org/knows> ?o }", stats=True
    )
    result_b = graph.query(
        "SELECT * WHERE { ?s a <http://example.org/Person> }", stats=True
    )

    iter_a = iter(result_a)
    iter_b = iter(result_b)
    next(iter_a)
    next(iter_b)
    next(iter_a)

    # each query evaluation has its own report, no shared global state
    assert result_a.stats is not result_b.stats
    assert result_a.stats.roots[0].rows_out == 2
    assert result_b.stats.roots[0].rows_out == 1

    assert len(list(iter_a)) == 1
    assert len(list(iter_b)) == 3
    assert result_a.stats.roots[0].rows_out == 3
    assert result_b.stats.roots[0].rows_out == 4
    assert result_a.stats.roots[0].state == COMPLETED
    assert result_b.stats.roots[0].state == COMPLETED


def test_user_supplied_stats_object():
    graph = make_graph()
    stats = QueryStats()
    result = graph.query("SELECT * WHERE { ?s ?p ?o }", stats=stats)
    assert result.stats is stats
    assert len(list(result)) == len(graph)
    assert stats.roots[0].rows_out == len(graph)


def test_no_stats_by_default():
    graph = make_graph()
    result = graph.query("SELECT * WHERE { ?s ?p ?o }")
    assert result.stats is None
    assert len(list(result)) == len(graph)
