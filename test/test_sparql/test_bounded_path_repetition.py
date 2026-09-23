"""
Tests for the non-standard bounded-repetition property path extension,
i.e. the ``elt{n}`` / ``elt{n,m}`` modifiers and the corresponding
``(min, max)`` tuple modifier of :class:`rdflib.paths.MulPath`.

The acceptance graph contains a self-loop, a fork that joins again
(a diamond) and a four node cycle:

::

    ex:loop -next-> ex:loop                              (self-loop)

    ex:a -next-> ex:b -next-> ex:d                       (fork ...
    ex:a -next-> ex:c -next-> ex:d                       ... and join)

    ex:n1 -next-> ex:n2 -next-> ex:n3 -next-> ex:n4 -next-> ex:n1
                                                           (four node cycle)
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pyparsing import ParseBaseException

from rdflib import Graph, Namespace
from rdflib.paths import MulPath, ZeroOrOne
from rdflib.plugins.sparql import parser, prepareQuery
from rdflib.plugins.sparql.parser import parseQuery

EX = Namespace("http://example.org/")

GRAPH_DATA = """
@prefix ex: <http://example.org/> .

# a self-loop
ex:loop ex:next ex:loop .

# a fork that joins again
ex:a ex:next ex:b , ex:c .
ex:b ex:next ex:d .
ex:c ex:next ex:d .

# a four node cycle
ex:n1 ex:next ex:n2 .
ex:n2 ex:next ex:n3 .
ex:n3 ex:next ex:n4 .
ex:n4 ex:next ex:n1 .
"""

PREFIXES = "PREFIX ex: <http://example.org/>\n"

ALL_NODES = {EX.loop, EX.a, EX.b, EX.c, EX.d, EX.n1, EX.n2, EX.n3, EX.n4}


@pytest.fixture()
def graph() -> Graph:
    g = Graph()
    g.parse(data=GRAPH_DATA, format="turtle")
    return g


@pytest.fixture()
def extension_enabled() -> Iterator[None]:
    parser.enable_bounded_path_repetition(True)
    yield
    parser.enable_bounded_path_repetition(False)


# ----------------------------------------------------------------------
# construction from Python path objects
# ----------------------------------------------------------------------


def test_python_api_range_construction() -> None:
    path = EX.next * (2, 5)  # type: ignore[operator]
    assert isinstance(path, MulPath)
    assert path.mod == (2, 5)
    assert path.n3() == f"<{EX.next}>{{2,5}}"
    # {n} is shorthand for {n,n}
    assert (EX.next * (3, 3)).n3() == f"<{EX.next}>{{3}}"  # type: ignore[operator]


@pytest.mark.parametrize(
    "mod",
    [
        (-1, 3),  # negative lower bound
        (2, -1),  # negative upper bound
        (5, 2),  # inverted range
        (2, None),  # missing upper bound
        (2,),  # not a (min, max) pair
        (1, 2, 3),  # not a (min, max) pair
    ],
)
def test_python_api_invalid_ranges_rejected_before_execution(mod) -> None:
    with pytest.raises(ValueError):
        MulPath(EX.next, mod)


# ----------------------------------------------------------------------
# evaluation through the Python path API
# ----------------------------------------------------------------------


def test_fork_join_endpoints(graph: Graph) -> None:
    # depth 1: b, c; depth 2: d (reached via both b and c, returned once)
    assert set(graph.objects(EX.a, EX.next * (1, 2))) == {EX.b, EX.c, EX.d}  # type: ignore[operator]
    assert list(graph.objects(EX.a, EX.next * (2, 2))) == [EX.d]  # type: ignore[operator]
    # no path of length 3 exists in the diamond
    assert list(graph.objects(EX.a, EX.next * (3, 3))) == []  # type: ignore[operator]


def test_self_loop_endpoint_returned_once(graph: Graph) -> None:
    # ex:loop is reachable with 2, 3, 4 and 5 steps, but returned only once
    assert list(graph.objects(EX.loop, EX.next * (2, 5))) == [EX.loop]  # type: ignore[operator]


def test_cycle_all_endpoints_within_range(graph: Graph) -> None:
    # depths 2..5 around the 4-cycle reach every node, including n1 itself
    assert set(graph.objects(EX.n1, EX.next * (2, 5))) == {  # type: ignore[operator]
        EX.n1,
        EX.n2,
        EX.n3,
        EX.n4,
    }


def test_finite_upper_bound_is_not_unbounded_closure(graph: Graph) -> None:
    # an unbounded modifier (+) would reach all four nodes from n1;
    # {1,2} must only reach n2 and n3
    assert set(graph.objects(EX.n1, EX.next * (1, 2))) == {EX.n2, EX.n3}  # type: ignore[operator]


def test_zero_repetition_matches_star_semantics(graph: Graph) -> None:
    # zero applications give the identity pair, like *
    assert list(graph.objects(EX.a, EX.next * (0, 0))) == [EX.a]  # type: ignore[operator]
    # {0,1} must return exactly the same pairs as the standard ? modifier
    range_pairs = {
        (s, o) for s, _, o in graph.triples((None, EX.next * (0, 1), None))  # type: ignore[operator]
    }
    zero_or_one_pairs = {
        (s, o) for s, _, o in graph.triples((None, EX.next * ZeroOrOne, None))
    }
    assert range_pairs == zero_or_one_pairs
    # the identity pairs cover every node in the graph, like *
    assert {(n, n) for n in ALL_NODES} <= range_pairs


def test_variable_start_and_fixed_endpoints(graph: Graph) -> None:
    # variable start, fixed end: walk backwards from ex:n1
    assert set(graph.subjects(EX.next * (2, 5), EX.n1)) == {  # type: ignore[operator]
        EX.n1,
        EX.n2,
        EX.n3,
        EX.n4,
    }
    # both ends bound
    assert (EX.n1, EX.next * (4, 4), EX.n1) in graph  # type: ignore[operator]
    assert (EX.n1, EX.next * (3, 3), EX.n1) not in graph  # type: ignore[operator]
    assert (EX.a, EX.next * (2, 2), EX.d) in graph  # type: ignore[operator]


def test_both_ends_unbound(graph: Graph) -> None:
    pairs = {
        (s, o) for s, _, o in graph.triples((None, EX.next * (3, 3), None))  # type: ignore[operator]
    }
    assert pairs == {
        (EX.loop, EX.loop),
        (EX.n1, EX.n4),
        (EX.n2, EX.n1),
        (EX.n3, EX.n2),
        (EX.n4, EX.n3),
    }


# ----------------------------------------------------------------------
# nesting: the counts apply to the whole parenthesized path
# ----------------------------------------------------------------------


def test_repetition_of_inverse_path(graph: Graph) -> None:
    assert list(graph.objects(EX.d, (~EX.next) * (2, 2))) == [EX.a]  # type: ignore[operator]


def test_repetition_of_alternative_path(graph: Graph) -> None:
    assert set(graph.objects(EX.b, (EX.next | ~EX.next) * (1, 1))) == {EX.a, EX.d}  # type: ignore[operator]


def test_repetition_of_sequence_path(graph: Graph) -> None:
    # one application of the sequence is two hops; {2} is four hops,
    # i.e. exactly once around the four node cycle
    assert list(graph.objects(EX.n1, (EX.next / EX.next) * (2, 2))) == [EX.n1]  # type: ignore[operator]
    assert set(graph.objects(EX.n1, (EX.next / EX.next) * (2, 5))) == {EX.n1, EX.n3}  # type: ignore[operator]


# ----------------------------------------------------------------------
# SPARQL syntax: rejected unless the extension is enabled
# ----------------------------------------------------------------------


def test_sparql_extension_rejected_by_default() -> None:
    assert parser.BOUNDED_PATH_REPETITION_ENABLED is False
    with pytest.raises(ParseBaseException):
        parseQuery(PREFIXES + "SELECT ?o WHERE { ?s ex:next{2,5} ?o }")


@pytest.mark.usefixtures("extension_enabled")
class TestSparqlExtensionEnabled:
    def test_range_queries(self, graph: Graph) -> None:
        # variable start, variable end
        q = PREFIXES + "SELECT ?o WHERE { ex:a ex:next{1,2} ?o }"
        assert {row[0] for row in graph.query(q)} == {EX.b, EX.c, EX.d}

        # repetition of a parenthesized sequence
        q = PREFIXES + "SELECT ?o WHERE { ex:n1 (ex:next/ex:next){2,5} ?o }"
        assert {row[0] for row in graph.query(q)} == {EX.n1, EX.n3}

        # {n} exact count
        q = PREFIXES + "SELECT ?o WHERE { ex:n1 ex:next{3} ?o }"
        assert {row[0] for row in graph.query(q)} == {EX.n4}

        # {0,4} includes the zero-length identity
        q = PREFIXES + "SELECT ?o WHERE { ex:n1 ex:next{0,4} ?o }"
        assert {row[0] for row in graph.query(q)} == {EX.n1, EX.n2, EX.n3, EX.n4}

    def test_fixed_end_reverse_query(self, graph: Graph) -> None:
        q = PREFIXES + "SELECT ?s WHERE { ?s ex:next{2,5} ex:n1 }"
        assert {row[0] for row in graph.query(q)} == {EX.n1, EX.n2, EX.n3, EX.n4}

    def test_both_ends_bound(self, graph: Graph) -> None:
        q = PREFIXES + "ASK { ex:n1 ex:next{4} ex:n1 }"
        assert bool(graph.query(q)) is True
        q = PREFIXES + "ASK { ex:n1 ex:next{3} ex:n1 }"
        assert bool(graph.query(q)) is False

    def test_nested_inverse_and_alternative(self, graph: Graph) -> None:
        q = PREFIXES + "SELECT ?o WHERE { ex:d (^ex:next){2} ?o }"
        assert [row[0] for row in graph.query(q)] == [EX.a]
        q = PREFIXES + "SELECT ?o WHERE { ex:b (ex:next|^ex:next){1} ?o }"
        assert {row[0] for row in graph.query(q)} == {EX.a, EX.d}

    @pytest.mark.parametrize("mod", ["{2,}", "{-1,3}", "{2,-3}"])
    def test_missing_upper_bound_and_negatives_rejected(self, mod: str) -> None:
        with pytest.raises(ParseBaseException):
            parseQuery(PREFIXES + f"SELECT ?o WHERE {{ ?s ex:next{mod} ?o }}")

    def test_inverted_range_rejected_before_execution(self) -> None:
        with pytest.raises(ValueError):
            prepareQuery(PREFIXES + "SELECT ?o WHERE { ?s ex:next{5,2} ?o }")
