"""
Tests for the RDFLib bounded path repetition extension: elt{n} / elt{n,m}

The extension is available both through the Python path API
(rdflib.paths.BoundedPath, or multiplying a path with a (min, max) tuple)
and through the SPARQL parser when the bounded_path_repeat switch is
enabled. Plain SPARQL 1.1 mode rejects the syntax.
"""

from __future__ import annotations

import pytest
from pyparsing.exceptions import ParseException

from rdflib import Graph, Namespace
from rdflib.paths import BoundedPath, eval_path
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.processor import prepareQuery

EX = Namespace("http://example.org/")

# Acceptance graph:
# - a self-loop: ex:loop
# - a fork that joins again: ex:a -> ex:b/ex:c -> ex:d
# - a four node cycle: ex:n1 -> ex:n2 -> ex:n3 -> ex:n4 -> ex:n1
GRAPH_DATA = """
@prefix ex: <http://example.org/> .

ex:loop ex:next ex:loop .

ex:a ex:next ex:b ;
     ex:hop  ex:c .
ex:b ex:next ex:d .
ex:c ex:hop  ex:d .

ex:n1 ex:next ex:n2 .
ex:n2 ex:next ex:n3 .
ex:n3 ex:next ex:n4 .
ex:n4 ex:next ex:n1 .
"""


@pytest.fixture
def graph() -> Graph:
    g = Graph()
    g.parse(data=GRAPH_DATA, format="turtle")
    return g


# -------------------------------------------------------------------
# Python path API
# -------------------------------------------------------------------


def test_python_mul_tuple_constructors():
    assert isinstance(EX.next * (2, 5), BoundedPath)
    assert (EX.next * (3, 3)).n3() == f"<{EX.next}>{{3}}"
    assert (EX.next * (2, 5)).n3() == f"<{EX.next}>{{2,5}}"
    assert repr(EX.next * (3, 3)) == f"Path({EX.next}{{3}})"
    assert repr(EX.next * (2, 5)) == f"Path({EX.next}{{2,5}})"


def test_python_variable_start(graph: Graph):
    # fork/join: two steps from ex:a meet again in ex:d
    assert set(eval_path(graph, (EX.a, EX.next * (2, 5), None))) == {(EX.a, EX.d)}
    # self-loop: reachable at every depth, returned once
    assert set(eval_path(graph, (EX.loop, EX.next * (2, 5), None))) == {
        (EX.loop, EX.loop)
    }
    # four node cycle: all four nodes within 1..4 steps
    assert set(eval_path(graph, (EX.n1, EX.next * (1, 4), None))) == {
        (EX.n1, EX.n2),
        (EX.n1, EX.n3),
        (EX.n1, EX.n4),
        (EX.n1, EX.n1),
    }


def test_python_fixed_object_reverse(graph: Graph):
    assert set(eval_path(graph, (None, EX.next * (2, 2), EX.d))) == {(EX.a, EX.d)}
    assert set(eval_path(graph, (None, EX.next * (1, 2), EX.d))) == {
        (EX.a, EX.d),
        (EX.b, EX.d),
    }


def test_python_both_bound(graph: Graph):
    assert (EX.a, EX.next * (2, 2), EX.d) in graph
    assert (EX.a, EX.next * (2, 2), EX.b) not in graph
    assert (EX.loop, EX.next * (2, 5), EX.loop) in graph
    # zero repetitions connect a node to itself, like "*"
    assert (EX.a, EX.next * (0, 2), EX.a) in graph
    assert (EX.a, EX.next * (0, 0), EX.b) not in graph
    assert (EX.d, EX.next * (0, 2), EX.a) not in graph


def test_python_cycle_dedupes_endpoints(graph: Graph):
    # each endpoint is returned exactly once, even though the cycle
    # reaches the same node at several different lengths
    result = list(eval_path(graph, (EX.n1, EX.next * (1, 8), None)))
    assert sorted(result) == sorted(
        [(EX.n1, EX.n1), (EX.n1, EX.n2), (EX.n1, EX.n3), (EX.n1, EX.n4)]
    )
    assert len(result) == len(set(result))


def test_python_zero_repetitions(graph: Graph):
    # zero occurrences behave like the zero case of "*"
    assert set(eval_path(graph, (EX.a, EX.next * (0, 1), None))) == {
        (EX.a, EX.a),
        (EX.a, EX.b),
    }
    # with nothing bound, all nodes pair with themselves
    zero_only = set(eval_path(graph, (None, EX.next * (0, 0), None)))
    assert (EX.a, EX.a) in zero_only
    assert (EX.loop, EX.loop) in zero_only
    assert all(s == o for s, o in zero_only)


def test_python_finite_upper_bound(graph: Graph):
    # a finite upper bound must not become an unbounded closure:
    # ex:n4 and ex:n1 are only reachable in 3 and 4 steps
    assert set(eval_path(graph, (EX.n1, EX.next * (1, 2), None))) == {
        (EX.n1, EX.n2),
        (EX.n1, EX.n3),
    }


def test_python_nested_paths(graph: Graph):
    # repetition applies to the whole grouped path
    assert set(eval_path(graph, (EX.n1, (EX.next / EX.next) * (2, 2), None))) == {
        (EX.n1, EX.n1)
    }
    # inverse and alternative paths nest inside the repetition
    assert set(eval_path(graph, (EX.d, (~EX.next) * (2, 2), None))) == {(EX.d, EX.a)}
    assert set(eval_path(graph, (EX.a, (EX.next | EX.hop) * (2, 2), None))) == {
        (EX.a, EX.d)
    }


@pytest.mark.parametrize(
    "min_max",
    [(-1, 3), (2, -1), (5, 2), (1, None), (None, 2)],  # type: ignore[list-item]
)
def test_python_invalid_bounds_rejected(min_max):
    with pytest.raises(ValueError):
        BoundedPath(EX.next, *min_max)


# -------------------------------------------------------------------
# SPARQL: the extension is off by default
# -------------------------------------------------------------------


@pytest.mark.parametrize("mod", ["{3}", "{2,5}", "{0,4}"])
def test_sparql_rejected_by_default(graph: Graph, mod: str):
    q = f"PREFIX ex: <http://example.org/> SELECT ?o WHERE {{ ex:a ex:next{mod} ?o }}"
    with pytest.raises(ParseException):
        parseQuery(q)
    with pytest.raises(ParseException):
        graph.query(q)
    with pytest.raises(ParseException):
        parseQuery(q, bounded_path_repeat=False)


# -------------------------------------------------------------------
# SPARQL: enabled via the bounded_path_repeat switch
# -------------------------------------------------------------------


def test_sparql_variable_start(graph: Graph):
    # the count applies to the whole grouped sequence: 2-5 repetitions
    # of two ex:next steps need at least 4 edges, impossible from ex:a
    res = graph.query(
        "PREFIX ex: <http://example.org/> "
        "SELECT ?o WHERE { ex:a (ex:next/ex:next){2,5} ?o }",
        bounded_path_repeat=True,
    )
    assert {r[0] for r in res} == set()

    # 1-2 repetitions of two steps: ex:b/ex:c join again in ex:d
    res = graph.query(
        "PREFIX ex: <http://example.org/> "
        "SELECT ?o WHERE { ex:a (ex:next/ex:next){1,2} ?o }",
        bounded_path_repeat=True,
    )
    assert {r[0] for r in res} == {EX.d}


def test_sparql_exact_count(graph: Graph):
    res = graph.query(
        "PREFIX ex: <http://example.org/> SELECT ?o WHERE { ex:n1 ex:next{3} ?o }",
        bounded_path_repeat=True,
    )
    assert {r[0] for r in res} == {EX.n4}


def test_sparql_range_with_zero(graph: Graph):
    # {0,4} includes the zero-length path, like "*"
    res = graph.query(
        "PREFIX ex: <http://example.org/> SELECT ?o WHERE { ex:n1 ex:next{0,4} ?o }",
        bounded_path_repeat=True,
    )
    assert {r[0] for r in res} == {EX.n1, EX.n2, EX.n3, EX.n4}


def test_sparql_self_loop(graph: Graph):
    res = graph.query(
        "PREFIX ex: <http://example.org/> SELECT ?o WHERE { ex:loop ex:next{2,5} ?o }",
        bounded_path_repeat=True,
    )
    assert [r[0] for r in res] == [EX.loop]


def test_sparql_cycle_dedupes_endpoints(graph: Graph):
    res = graph.query(
        "PREFIX ex: <http://example.org/> SELECT ?o WHERE { ex:n1 ex:next{1,8} ?o }",
        bounded_path_repeat=True,
    )
    objects = [r[0] for r in res]
    assert sorted(objects) == sorted([EX.n1, EX.n2, EX.n3, EX.n4])
    assert len(objects) == len(set(objects))


def test_sparql_fixed_object_reverse(graph: Graph):
    res = graph.query(
        "PREFIX ex: <http://example.org/> SELECT ?s WHERE { ?s ex:next{2,2} ex:d }",
        bounded_path_repeat=True,
    )
    assert {r[0] for r in res} == {EX.a}


def test_sparql_both_bound(graph: Graph):
    res = graph.query(
        "PREFIX ex: <http://example.org/> ASK { ex:a ex:next{2,2} ex:d }",
        bounded_path_repeat=True,
    )
    assert bool(res) is True

    res = graph.query(
        "PREFIX ex: <http://example.org/> ASK { ex:a ex:next{2,2} ex:b }",
        bounded_path_repeat=True,
    )
    assert bool(res) is False

    # both ends bound through initBindings
    res = graph.query(
        "PREFIX ex: <http://example.org/> SELECT ?s ?o WHERE { ?s ex:next{2,2} ?o }",
        initBindings={"s": EX.a, "o": EX.d},
        bounded_path_repeat=True,
    )
    assert len(res) == 1


def test_sparql_nested_inverse_and_alternative(graph: Graph):
    res = graph.query(
        "PREFIX ex: <http://example.org/> SELECT ?o WHERE { ex:d (^ex:next){2} ?o }",
        bounded_path_repeat=True,
    )
    assert {r[0] for r in res} == {EX.a}

    res = graph.query(
        "PREFIX ex: <http://example.org/> "
        "SELECT ?o WHERE { ex:a (ex:next|ex:hop){2} ?o }",
        bounded_path_repeat=True,
    )
    assert {r[0] for r in res} == {EX.d}


def test_sparql_prepare_query(graph: Graph):
    q = prepareQuery(
        "PREFIX ex: <http://example.org/> SELECT ?o WHERE { ex:a ex:next{2,5} ?o }",
        bounded_path_repeat=True,
    )
    assert {r[0] for r in graph.query(q)} == {EX.d}


@pytest.mark.parametrize("mod", ["{2,}", "{-1,2}", "{5,2}", "{,4}"])
def test_sparql_invalid_bounds_rejected(graph: Graph, mod: str):
    # negative counts, inverted ranges and missing bounds are rejected
    # before the query is executed
    q = f"PREFIX ex: <http://example.org/> SELECT ?o WHERE {{ ex:a ex:next{mod} ?o }}"
    with pytest.raises(ParseException):
        parseQuery(q, bounded_path_repeat=True)
    with pytest.raises(ParseException):
        graph.query(q, bounded_path_repeat=True)


def test_sparql_prepared_query_rejects_inverted_range():
    with pytest.raises(ParseException):
        prepareQuery(
            "PREFIX ex: <http://example.org/> SELECT ?o WHERE { ex:a ex:next{5,2} ?o }",
            bounded_path_repeat=True,
        )
