"""
Tests for the SPARQL 1.1 semantics of FILTER (NOT) EXISTS and MINUS.

FILTER NOT EXISTS is correlated: its pattern is re-evaluated for every
outer solution and sees the bindings of that solution.  MINUS is not
correlated: it only removes a left-hand solution when the two sides
share at least one variable AND bind it compatibly.

https://www.w3.org/TR/sparql11-query/#func-filter-exists
https://www.w3.org/TR/sparql11-query/#defn_algMinus
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Optional

import pytest

import rdflib.plugins.sparql.evaluate as evaluate
from rdflib import Dataset, Graph, URIRef
from rdflib.plugins.sparql.sparql import QueryContext
from rdflib.term import Identifier

EX = "http://example.org/"

PEOPLE_DATA = """
@prefix ex: <http://example.org/> .
ex:alice ex:name "Alice" ; ex:role ex:admin .
ex:bob ex:name "Bob" .
ex:carol ex:name "Carol" ; ex:role ex:user .
ex:admin ex:perm ex:read .
"""


@pytest.fixture
def people() -> Graph:
    graph = Graph()
    graph.parse(data=PEOPLE_DATA, format="turtle")
    return graph


def query_strings(graph: Graph, query: str) -> list[tuple[str, ...]]:
    """Run a query and return the result rows as tuples of strings."""
    return sorted(
        tuple(str(term) for term in row)
        for row in graph.query("PREFIX ex: <http://example.org/>\n" + query)
    )


ALICE = (EX + "alice",)
BOB = (EX + "bob",)
CAROL = (EX + "carol",)
EVERYONE = sorted([ALICE, BOB, CAROL])


# --------------------------------------------------------------------------
# Group 1: patterns with no variables at all (ground triples / empty)
# --------------------------------------------------------------------------


def test_ground_pattern_not_exists(people: Graph) -> None:
    # The ground pattern matches the data, so NOT EXISTS is false for
    # every outer solution.
    assert query_strings(
        people,
        "SELECT ?s { ?s ex:name ?n . FILTER NOT EXISTS { ex:admin ex:perm ex:read } }",
    ) == []
    # The ground pattern does not match, so NOT EXISTS is true for
    # every outer solution.
    assert (
        query_strings(
            people,
            "SELECT ?s { ?s ex:name ?n ."
            " FILTER NOT EXISTS { ex:nobody ex:perm ex:read } }",
        )
        == EVERYONE
    )


def test_ground_pattern_minus(people: Graph) -> None:
    # A ground pattern shares no variables with the left side, so
    # MINUS never removes anything, no matter whether it matches.
    assert (
        query_strings(
            people,
            "SELECT ?s { ?s ex:name ?n . MINUS { ex:admin ex:perm ex:read } }",
        )
        == EVERYONE
    )
    assert (
        query_strings(
            people,
            "SELECT ?s { ?s ex:name ?n . MINUS { ex:nobody ex:perm ex:read } }",
        )
        == EVERYONE
    )


def test_empty_pattern(people: Graph) -> None:
    # The empty group pattern yields one empty solution: EXISTS is
    # always true, NOT EXISTS always false.
    assert query_strings(
        people, "SELECT ?s { ?s ex:name ?n . FILTER EXISTS { } }"
    ) == EVERYONE
    assert (
        query_strings(people, "SELECT ?s { ?s ex:name ?n . FILTER NOT EXISTS { } }")
        == []
    )
    # The empty solution shares no bindings with any left-hand row, so
    # MINUS with an empty pattern removes nothing.
    assert (
        query_strings(people, "SELECT ?s { ?s ex:name ?n . MINUS { } }") == EVERYONE
    )


# --------------------------------------------------------------------------
# Group 2: shared variables with compatible bindings
# --------------------------------------------------------------------------


def test_shared_variable_not_exists(people: Graph) -> None:
    # Correlated per row: only carol has role ex:user.
    assert query_strings(
        people, "SELECT ?s { ?s ex:name ?n . FILTER NOT EXISTS { ?s ex:role ex:user } }"
    ) == sorted([ALICE, BOB])
    assert query_strings(
        people, "SELECT ?s { ?s ex:name ?n . FILTER EXISTS { ?s ex:role ex:admin } }"
    ) == [ALICE]


def test_shared_variable_minus(people: Graph) -> None:
    # Same shape as NOT EXISTS here: carol's binding of ?s is
    # compatible with the right side, so her row is removed.
    assert query_strings(
        people, "SELECT ?s { ?s ex:name ?n . MINUS { ?s ex:role ex:user } }"
    ) == sorted([ALICE, BOB])


# --------------------------------------------------------------------------
# Group 3: no shared variables
# --------------------------------------------------------------------------


def test_disjoint_variables(people: Graph) -> None:
    # NOT EXISTS only asks whether the pattern matches at all: the
    # permission triple exists, so every row is filtered out.
    assert (
        query_strings(
            people, "SELECT ?s { ?s ex:name ?n . FILTER NOT EXISTS { ?x ex:perm ?y } }"
        )
        == []
    )
    # MINUS with no shared variables removes nothing.
    assert (
        query_strings(people, "SELECT ?s { ?s ex:name ?n . MINUS { ?x ex:perm ?y } }")
        == EVERYONE
    )


# --------------------------------------------------------------------------
# Group 4: shared variables with conflicting bindings
# --------------------------------------------------------------------------


def test_conflicting_bindings(people: Graph) -> None:
    # ?s is shared, but the right side binds it to ex:bob: only bob's
    # row is compatible and removed, every conflicting binding stays.
    expected = sorted([ALICE, CAROL])
    assert (
        query_strings(
            people, "SELECT ?s { ?s ex:name ?n . MINUS { ?s ex:name \"Bob\" } }"
        )
        == expected
    )
    assert (
        query_strings(
            people,
            "SELECT ?s { ?s ex:name ?n . FILTER NOT EXISTS { ?s ex:name \"Bob\" } }",
        )
        == expected
    )


# --------------------------------------------------------------------------
# Correlation details
# --------------------------------------------------------------------------


def test_not_exists_and_minus_in_one_query(people: Graph) -> None:
    # The permission report from the issue: people that have no role
    # with any permission, and are not plain users.  Only bob remains:
    # alice has a permitted role, carol is a user.
    assert query_strings(
        people,
        """
        SELECT ?s {
          ?s ex:name ?n .
          FILTER NOT EXISTS { ?s ex:role ?r . ?r ex:perm ?p }
          MINUS { ?s ex:role ex:user }
        }
        """,
    ) == [BOB]


def test_not_exists_outer_variable_unbound(people: Graph) -> None:
    # ?r is unbound for bob; an unbound outer variable must not be
    # treated as bound inside the pattern, so bob's row survives.
    assert (
        query_strings(
            people,
            """
            SELECT ?s {
              ?s ex:name ?n .
              OPTIONAL { ?s ex:role ?r }
              FILTER NOT EXISTS { ?s ex:perm ?r }
            }
            """,
        )
        == EVERYONE
    )


def test_not_exists_with_optional_unbound_inside(people: Graph) -> None:
    # Inside the pattern, ?p stays unbound for ex:user (no permission):
    # carol's row matches the pattern and is removed.
    assert query_strings(
        people,
        """
        SELECT ?s {
          ?s ex:name ?n .
          FILTER NOT EXISTS {
            ?s ex:role ?r .
            OPTIONAL { ?r ex:perm ?p }
            FILTER(!BOUND(?p))
          }
        }
        """,
    ) == sorted([ALICE, BOB])


def test_not_exists_correlated_subquery(people: Graph) -> None:
    # A subquery inside NOT EXISTS re-uses the same variable name as
    # the outer pattern; the compatibility judgement must apply to it
    # just like to a plain pattern, so only carol is removed.
    assert query_strings(
        people,
        """
        SELECT ?s {
          ?s ex:name ?n .
          FILTER NOT EXISTS { SELECT ?s WHERE { ?s ex:role ex:user } }
        }
        """,
    ) == sorted([ALICE, BOB])


def test_nested_not_exists(people: Graph) -> None:
    # Roles without any permission: ex:user.  People whose roles all
    # have no permission are removed, i.e. carol.
    assert query_strings(
        people,
        """
        SELECT ?s {
          ?s ex:name ?n .
          FILTER NOT EXISTS {
            ?s ex:role ?r .
            FILTER NOT EXISTS { ?r ex:perm ?p }
          }
        }
        """,
    ) == sorted([ALICE, BOB])


def test_not_exists_graph_scope() -> None:
    dataset = Dataset()
    dataset.parse(
        data="""
@prefix ex: <http://example.org/> .
ex:alice ex:name "Alice" .
ex:bob ex:name "Bob" .
ex:g1 { ex:alice ex:role ex:admin }
""",
        format="trig",
    )
    # The GRAPH pattern only sees the named graph: alice has a role
    # there, bob does not.
    assert query_strings(
        dataset,
        """
        SELECT ?s {
          ?s ex:name ?n .
          FILTER NOT EXISTS { GRAPH ex:g1 { ?s ex:role ex:admin } }
        }
        """,
    ) == [BOB]


def test_minus_nested_in_join_keeps_rows() -> None:
    # The MINUS pattern shares no variables with its left side; outer
    # bindings pushed in by the enclosing (lazy) join must not make
    # the domains overlap.
    graph = Graph()
    graph.parse(
        data="""
@prefix ex: <http://example.org/> .
ex:a ex:p ex:b .
ex:c ex:q ex:d .
""",
        format="turtle",
    )
    result = list(
        graph.query(
            "PREFIX ex: <http://example.org/>\n"
            "SELECT ?s ?o ?o2 { ?s ex:p ?o ."
            " { ?s ex:p ?o2 . MINUS { ?x ex:q ?y } } }"
        )
    )
    assert sorted(tuple(str(term) for term in row) for row in result) == [
        (EX + "a", EX + "b", EX + "b")
    ]


def test_minus_preserves_duplicates() -> None:
    graph = Graph()
    graph.parse(
        data="""
@prefix ex: <http://example.org/> .
ex:a ex:p ex:b, ex:c .
ex:d ex:p ex:b .
""",
        format="turtle",
    )
    # ?o = ex:b is produced twice by the left side; the disjoint MINUS
    # pattern must not deduplicate it.
    result = [
        str(row[0])
        for row in graph.query(
            "PREFIX ex: <http://example.org/>\n"
            "SELECT ?o { ex:a ex:p ?o . ?s ex:p ?o . MINUS { ex:d ex:q ?w } }"
        )
    ]
    assert sorted(result) == [EX + "b", EX + "b", EX + "c"]


def test_filter_error_inside_not_exists(people: Graph) -> None:
    # A filter error inside the pattern is not a match (errors are
    # false), so NOT EXISTS stays true for every row.
    assert (
        query_strings(
            people,
            """
            SELECT ?s {
              ?s ex:name ?n .
              FILTER NOT EXISTS { ?s ex:role ?r . FILTER(?n + 1 > 0) }
            }
            """,
        )
        == EVERYONE
    )


# --------------------------------------------------------------------------
# Laziness: a LIMIT downstream must not trigger unneeded evaluation
# --------------------------------------------------------------------------


def _count_bgp_evals(
    monkeypatch: pytest.MonkeyPatch, predicate: URIRef
) -> dict[str, int]:
    """Count evalBGP calls whose triple block mentions the predicate."""
    counts = {"count": 0}
    original = evaluate.evalBGP

    def counting(
        ctx: QueryContext,
        bgp: Optional[list[tuple[Identifier, Identifier, Identifier]]] = None,
    ) -> Generator:
        if bgp is not None and any(triple[1] == predicate for triple in bgp):
            counts["count"] += 1
        return original(ctx, bgp)

    monkeypatch.setattr(evaluate, "evalBGP", counting)
    return counts


def test_minus_disjoint_right_side_never_evaluated(
    people: Graph, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The MINUS pattern shares no variables with the left side, so it
    # can never remove a row and must not be evaluated at all, even
    # without a LIMIT.
    counts = _count_bgp_evals(monkeypatch, URIRef(EX + "perm"))
    result = list(
        people.query(
            "PREFIX ex: <http://example.org/>\n"
            "SELECT ?s { ?s ex:name ?n . MINUS { ?x ex:perm ?y } } LIMIT 1"
        )
    )
    assert len(result) == 1
    assert counts["count"] == 0


def test_minus_shared_right_side_evaluated_once(
    people: Graph, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With shared variables the right side is needed, but only once:
    # it is not re-run per left-hand row and not run again after the
    # LIMIT stops iteration.
    counts = _count_bgp_evals(monkeypatch, URIRef(EX + "role"))
    result = list(
        people.query(
            "PREFIX ex: <http://example.org/>\n"
            "SELECT ?s { ?s ex:name ?n . MINUS { ?s ex:role ex:user } } LIMIT 2"
        )
    )
    assert len(result) == 2
    assert counts["count"] == 1


def test_not_exists_evaluated_per_consumed_row(
    people: Graph, monkeypatch: pytest.MonkeyPatch
) -> None:
    # NOT EXISTS is correlated: the pattern runs once per outer row
    # that is actually examined - with LIMIT 1 that is a single row.
    counts = _count_bgp_evals(monkeypatch, URIRef(EX + "role"))
    result = list(
        people.query(
            "PREFIX ex: <http://example.org/>\n"
            "SELECT ?s { ?s ex:name ?n ."
            " FILTER NOT EXISTS { ?s ex:role ex:user } } LIMIT 1"
        )
    )
    assert len(result) == 1
    assert counts["count"] == 1
