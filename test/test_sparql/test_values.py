"""
Tests for SPARQL VALUES (inline data) semantics.

A VALUES block is a multiset of solution mappings:

* UNDEF leaves the variable unbound for that row; it is not an RDF term
  and it does not prevent other parts of the query (e.g. OPTIONAL) from
  binding the same variable.
* Duplicate rows are preserved; de-duplication is up to outer operators
  (DISTINCT, REDUCED) and row limiting is up to ORDER BY/LIMIT.
* Static errors (duplicate variables, wrong number of values in a row,
  illegal terms) are raised before any evaluation happens.
"""

from collections import Counter
from typing import Optional

import pytest

from rdflib import BNode, Graph, Literal, URIRef, Variable
from rdflib.plugins.sparql import evaluate as evaluate_mod
from rdflib.plugins.sparql.algebra import translateQuery, translateValues
from rdflib.plugins.sparql.evaluate import evalPart
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.plugins.sparql.sparql import QueryContext, SPARQLError

EX_A = URIRef("http://example.org/a")
EX_B = URIRef("http://example.org/b")
EX_P = URIRef("http://example.org/p")

X = Variable("x")
Y = Variable("y")
V = Variable("v")


@pytest.fixture
def graph() -> Graph:
    g = Graph()
    g.add((EX_A, EX_P, Literal(1)))
    g.add((EX_B, EX_P, Literal(2)))
    return g


def bindings_of(graph: Graph, query: str) -> Counter:
    """Evaluate a query and return the multiset of solution dicts."""
    res = graph.query(query)
    return Counter(
        frozenset((Variable(k), v) for k, v in row.asdict().items()) for row in res
    )


def eval_node(graph: Graph, query: str, node_name: str) -> Counter:
    """
    Evaluate the first algebra node with the given name and return the
    multiset of its solutions, so intermediate results (variable sets and
    duplicate counts) can be checked directly.
    """
    algebra = translateQuery(parseQuery(query)).algebra

    node: Optional[CompValue] = None

    def visit(n):
        nonlocal node
        if isinstance(n, CompValue):
            if n.name == node_name and node is None:
                node = n
                return
            for value in n.values():
                visit(value)
        elif isinstance(n, (list, tuple)):
            for value in n:
                visit(value)

    visit(algebra)
    assert node is not None, "no %r node in algebra" % node_name
    return Counter(frozenset(sol.items()) for sol in evalPart(QueryContext(graph), node))


def solution(*pairs) -> frozenset:
    return frozenset(pairs)


def test_undef_stays_unbound_in_join(graph: Graph) -> None:
    # the UNDEF row leaves ?x unbound, so it joins with every BGP match
    res = bindings_of(
        graph,
        "SELECT * WHERE {"
        "  VALUES ?x { <http://example.org/a> UNDEF <http://example.org/a> }"
        "  ?x <http://example.org/p> ?v . }",
    )
    assert res == Counter(
        {
            solution((X, EX_A), (V, Literal(1))): 3,
            solution((X, EX_B), (V, Literal(2))): 1,
        }
    )


def test_undef_does_not_block_optional(graph: Graph) -> None:
    # UNDEF is not a value: OPTIONAL must still bind ?x
    res = bindings_of(
        graph,
        "SELECT * WHERE {"
        "  VALUES ?x { UNDEF }"
        "  OPTIONAL { <http://example.org/a> <http://example.org/p> ?x } }",
    )
    assert res == Counter({solution((X, Literal(1))): 1})


def test_all_undef_rows_are_kept(graph: Graph) -> None:
    res = bindings_of(graph, "SELECT * WHERE { VALUES ?x { UNDEF } }")
    assert res == Counter({solution(): 1})

    res = bindings_of(graph, "SELECT * WHERE { VALUES (?x ?y) { (UNDEF UNDEF) } }")
    assert res == Counter({solution(): 1})

    res = bindings_of(graph, "SELECT * WHERE { VALUES ?x { <http://example.org/a> UNDEF } }")
    assert res == Counter({solution((X, EX_A)): 1, solution(): 1})


def test_duplicate_rows_are_preserved(graph: Graph) -> None:
    res = bindings_of(
        graph,
        "SELECT * WHERE { VALUES ?x { <http://example.org/a> <http://example.org/a> } }",
    )
    assert res == Counter({solution((X, EX_A)): 2})

    # duplicates survive a join with a BGP
    res = bindings_of(
        graph,
        "SELECT * WHERE {"
        "  VALUES ?x { <http://example.org/a> <http://example.org/a> }"
        "  ?x <http://example.org/p> ?v . }",
    )
    assert res == Counter({solution((X, EX_A), (V, Literal(1))): 2})

    # duplicates survive OPTIONAL
    res = bindings_of(
        graph,
        "SELECT * WHERE {"
        "  VALUES ?x { <http://example.org/a> <http://example.org/a> }"
        "  OPTIONAL { ?x <http://example.org/p> ?v } }",
    )
    assert res == Counter({solution((X, EX_A), (V, Literal(1))): 2})

    # duplicates survive a non-lazy join (VALUES on the right of a
    # sub-query with ORDER BY/LIMIT, which cannot be joined lazily)
    res = bindings_of(
        graph,
        "SELECT * WHERE {"
        "  { SELECT ?x WHERE { VALUES ?x { <http://example.org/a> } }"
        "    ORDER BY ?x LIMIT 1 }"
        "  VALUES ?x { <http://example.org/a> <http://example.org/a> } }",
    )
    assert res == Counter({solution((X, EX_A)): 2})


def test_union_keeps_duplicates_and_undef(graph: Graph) -> None:
    res = bindings_of(
        graph,
        "SELECT * WHERE {"
        "  { VALUES ?x { <http://example.org/a> UNDEF } }"
        "  UNION"
        "  { VALUES ?x { <http://example.org/a> <http://example.org/b> } } }",
    )
    assert res == Counter(
        {
            solution((X, EX_A)): 2,
            solution((X, EX_B)): 1,
            solution(): 1,
        }
    )


def test_distinct_order_by_limit(graph: Graph) -> None:
    # DISTINCT collapses the duplicates produced by VALUES
    res = bindings_of(
        graph,
        "SELECT DISTINCT ?x WHERE { VALUES ?x { <http://example.org/a> <http://example.org/a> } }",
    )
    assert res == Counter({solution((X, EX_A)): 1})

    # LIMIT applies after the multiset has been built
    res = bindings_of(
        graph,
        "SELECT ?x WHERE { VALUES ?x { 1 1 2 } } ORDER BY ?x LIMIT 2",
    )
    assert res == Counter({solution((X, Literal(1))): 2})


def test_values_in_subquery(graph: Graph) -> None:
    res = bindings_of(
        graph,
        "SELECT * WHERE {"
        "  { SELECT * WHERE { VALUES ?x { <http://example.org/a> UNDEF } } }"
        "  OPTIONAL { ?x <http://example.org/p> ?v } }",
    )
    assert res == Counter(
        {
            solution((X, EX_A), (V, Literal(1))): 2,
            solution((X, EX_B), (V, Literal(2))): 1,
        }
    )


def test_values_variable_same_name_as_outer(graph: Graph) -> None:
    # VALUES restricts the already bound variable: only matching rows
    # survive, UNDEF rows keep the outer binding
    res = bindings_of(
        graph,
        "SELECT * WHERE {"
        "  ?x <http://example.org/p> ?v ."
        "  VALUES ?x { <http://example.org/a> UNDEF } }",
    )
    assert res == Counter(
        {
            solution((X, EX_A), (V, Literal(1))): 2,
            solution((X, EX_B), (V, Literal(2))): 1,
        }
    )


def test_query_level_values_clause(graph: Graph) -> None:
    # a VALUES clause after the WHERE pattern joins with it
    res = bindings_of(
        graph,
        "SELECT * WHERE { ?x <http://example.org/p> ?v . }"
        " VALUES ?x { <http://example.org/a> UNDEF }",
    )
    assert res == Counter(
        {
            solution((X, EX_A), (V, Literal(1))): 2,
            solution((X, EX_B), (V, Literal(2))): 1,
        }
    )


def test_empty_values(graph: Graph) -> None:
    # no rows at all: the join result is empty
    res = bindings_of(graph, "SELECT * WHERE { VALUES ?x {} }")
    assert res == Counter()

    res = bindings_of(graph, "SELECT * WHERE { VALUES () {} }")
    assert res == Counter()

    # one row with no variables: a single empty solution
    res = bindings_of(graph, "SELECT * WHERE { VALUES () { () } }")
    assert res == Counter({solution(): 1})


def test_undef_is_not_a_term(graph: Graph) -> None:
    # the literal "UNDEF" and the IRI <UNDEF> are ordinary terms
    res = bindings_of(graph, 'SELECT * WHERE { VALUES ?x { "UNDEF" } }')
    assert res == Counter({solution((X, Literal("UNDEF"))): 1})

    res = bindings_of(graph, "SELECT * WHERE { VALUES ?x { <UNDEF> } }")
    assert res == Counter({solution((X, URIRef("UNDEF"))): 1})


def test_filter_sees_unbound_not_none(graph: Graph) -> None:
    # an unbound variable is an error in a filter, i.e. the row is
    # dropped; it must not compare equal to anything (including None)
    res = bindings_of(
        graph,
        "SELECT * WHERE { VALUES (?x ?y) { (UNDEF 1) (2 2) } FILTER(?x = 2) }",
    )
    assert res == Counter({solution((X, Literal(2)), (Y, Literal(2))): 1})

    res = bindings_of(
        graph,
        "SELECT * WHERE { VALUES (?x ?y) { (UNDEF 1) (2 2) } FILTER(!BOUND(?x)) }",
    )
    assert res == Counter({solution((Y, Literal(1))): 1})

    # unbound variables are not equal to each other either
    res = bindings_of(
        graph,
        "SELECT * WHERE { VALUES (?x ?y) { (UNDEF UNDEF) (1 1) } FILTER(?x = ?y) }",
    )
    assert res == Counter({solution((X, Literal(1)), (Y, Literal(1))): 1})


def test_intermediate_multiset(graph: Graph) -> None:
    # check the variable sets and duplicate counts of the VALUES node
    # itself, before any outer operator runs
    query = (
        "SELECT * WHERE { VALUES (?x ?y) {"
        "  (<http://example.org/a> 1) (UNDEF 2) (<http://example.org/a> 1) } }"
    )
    res = eval_node(graph, query, "ToMultiSet")
    assert res == Counter(
        {
            solution((X, EX_A), (Y, Literal(1))): 2,
            solution((Y, Literal(2))): 1,
        }
    )


def test_column_count_mismatch_raises(graph: Graph) -> None:
    with pytest.raises(SPARQLError):
        graph.query("SELECT * WHERE { VALUES (?x ?y) { (1 2) (3) } }")
    with pytest.raises(SPARQLError):
        graph.query("SELECT * WHERE { VALUES (?x ?y) { (1 2 3) } }")


def test_duplicate_variable_raises(graph: Graph) -> None:
    with pytest.raises(SPARQLError):
        graph.query("SELECT * WHERE { VALUES (?x ?x) { (1 2) } }")


def test_illegal_term_raises(graph: Graph) -> None:
    # variables and blank nodes are not valid DataBlockValues; the parser
    # rejects them before anything is evaluated
    with pytest.raises(Exception):
        graph.query("SELECT * WHERE { VALUES ?x { ?y } }")
    with pytest.raises(Exception):
        graph.query("SELECT * WHERE { VALUES ?x { _:b } }")

    # translateValues itself also validates the terms it is given
    with pytest.raises(SPARQLError):
        translateValues(
            CompValue("InlineData", var=[X], value=[BNode("b")])
        )


def test_limit_does_not_materialize_values(
    graph: Graph, monkeypatch: pytest.MonkeyPatch
) -> None:
    yielded = 0
    real_eval_values = evaluate_mod.evalValues

    def counting_eval_values(ctx, part):
        nonlocal yielded
        for row in real_eval_values(ctx, part):
            yielded += 1
            yield row

    monkeypatch.setattr(evaluate_mod, "evalValues", counting_eval_values)

    res = list(
        graph.query("SELECT * WHERE { VALUES ?x { 1 2 3 4 5 6 7 8 9 10 } } LIMIT 3")
    )
    assert len(res) == 3
    assert yielded == 3


def test_limit_does_not_evaluate_second_union_branch(
    graph: Graph, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluated = 0
    real_eval_values = evaluate_mod.evalValues

    def counting_eval_values(ctx, part):
        nonlocal evaluated
        evaluated += 1
        yield from real_eval_values(ctx, part)

    monkeypatch.setattr(evaluate_mod, "evalValues", counting_eval_values)

    res = list(
        graph.query(
            "SELECT * WHERE {"
            "  { VALUES ?x { 1 } } UNION { VALUES ?x { 2 } } } LIMIT 1"
        )
    )
    assert len(res) == 1
    assert evaluated == 1
