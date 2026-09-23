"""
Tests for the SPARQL VALUES clause (inline and top level):
multiset semantics, UNDEF handling, variable scoping and
pre-execution validation.

All expected results below are hand-computed from the SPARQL 1.1
semantics (https://www.w3.org/TR/sparql11-query/#sparqlQuery).
"""

from collections import Counter
from typing import FrozenSet, Optional, Tuple

import pytest

from rdflib import BNode, Graph, Literal, URIRef, Variable
from rdflib.plugins.sparql.algebra import translateQuery, translateValues
from rdflib.plugins.sparql.evaluate import evalPart
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.plugins.sparql.sparql import QueryContext, SPARQLError

EX = "http://example.org/"
S = URIRef(EX + "s")
P = URIRef(EX + "p")
ONE = Literal(1)
TWO = Literal(2)


def make_graph() -> Graph:
    g = Graph()
    g.add((S, P, ONE))
    return g


def solution_set(rows) -> Counter:
    """
    The multiset of solutions of a query result, as a counter of
    frozensets of (variable, value) pairs. Unbound variables are simply
    absent, so this captures both the variable set of every solution
    and the multiplicity of every distinct solution.
    """
    return Counter(
        frozenset((Variable(k), v) for k, v in row.asdict().items()) for row in rows
    )


def sol(*pairs: Tuple[str, Optional[URIRef]]) -> FrozenSet:
    return frozenset((Variable(k), v) for k, v in pairs if v is not None)


def eval_where(graph: Graph, query_string: str) -> Counter:
    """
    Evaluate the translated WHERE clause of a query directly, returning
    the intermediate multiset of solution mappings (before projection).
    """
    algebra = translateQuery(parseQuery(query_string)).algebra
    assert algebra.name == "SelectQuery"
    project = algebra.p
    assert project.name == "Project"
    ctx = QueryContext(graph, initBindings={})
    return Counter(frozenset(row.items()) for row in evalPart(ctx, project.p))


# ---------------------------------------------------------------------
# multiset semantics
# ---------------------------------------------------------------------


def test_single_variable_duplicates_preserved():
    """Duplicate VALUES rows are a multiset and must be preserved."""
    g = make_graph()
    res = solution_set(g.query("SELECT ?x WHERE { VALUES ?x { 1 1 2 } }"))
    assert res == Counter({sol(("x", ONE)): 2, sol(("x", TWO)): 1})


def test_duplicate_rows_multiply_join():
    """Joining with a BGP multiplies duplicate VALUES rows."""
    g = make_graph()
    res = solution_set(g.query("""
            SELECT ?x ?y WHERE {
                VALUES ?x { <http://example.org/s> <http://example.org/s> }
                ?x <http://example.org/p> ?y .
            }
            """))
    assert res == Counter({sol(("x", S), ("y", ONE)): 2})


def test_union_of_values_keeps_duplicates():
    g = make_graph()
    res = solution_set(g.query("""
            SELECT ?x WHERE {
                { VALUES ?x { 1 1 } }
                UNION
                { VALUES ?x { 2 } }
            }
            """))
    assert res == Counter({sol(("x", ONE)): 2, sol(("x", TWO)): 1})


def test_values_in_subquery_keeps_duplicates():
    g = make_graph()
    res = solution_set(
        g.query("SELECT ?x WHERE { { SELECT ?x WHERE { VALUES ?x { 1 1 2 } } } }")
    )
    assert res == Counter({sol(("x", ONE)): 2, sol(("x", TWO)): 1})


def test_values_joined_with_distinct_subquery_keeps_duplicates():
    """
    A VALUES clause joined against a sub-query with DISTINCT takes the
    non-lazy join path; duplicates must survive that path as well.
    """
    g = make_graph()
    res = solution_set(g.query("""
            SELECT ?x ?y WHERE {
                { SELECT DISTINCT ?y WHERE { ?s <http://example.org/p> ?y } }
                VALUES ?x { 7 7 }
            }
            """))
    assert res == Counter({sol(("x", Literal(7)), ("y", ONE)): 2})


def test_distinct_over_values():
    """DISTINCT is an outer operator and dedups the VALUES multiset."""
    g = make_graph()
    res = solution_set(g.query("SELECT DISTINCT ?x WHERE { VALUES ?x { 1 1 2 } }"))
    assert res == Counter({sol(("x", ONE)): 1, sol(("x", TWO)): 1})


def test_order_by_limit_over_values():
    g = make_graph()
    rows = list(g.query("SELECT ?x WHERE { VALUES ?x { 3 1 2 } } ORDER BY ?x LIMIT 2"))
    assert [row[0] for row in rows] == [ONE, TWO]


# ---------------------------------------------------------------------
# UNDEF
# ---------------------------------------------------------------------


def test_undef_row_is_unbound_not_literal():
    """
    An all-UNDEF row yields a solution in which the variable is simply
    not bound; it is not bound to any literal.
    """
    g = make_graph()
    res = solution_set(g.query("SELECT ?x WHERE { VALUES ?x { UNDEF 5 } }"))
    assert res == Counter({sol(): 1, sol(("x", Literal(5))): 1})


def test_undef_keyword_is_case_insensitive():
    g = make_graph()
    res = solution_set(g.query("SELECT ?x WHERE { VALUES ?x { undef 5 } }"))
    assert res == Counter({sol(): 1, sol(("x", Literal(5))): 1})


def test_undef_string_literal_is_an_ordinary_literal():
    """The plain literal "UNDEF" is data, not the UNDEF keyword."""
    g = make_graph()
    res = solution_set(g.query('SELECT ?x WHERE { VALUES ?x { "UNDEF" } }'))
    assert res == Counter({sol(("x", Literal("UNDEF"))): 1})


def test_undef_in_multi_column_values():
    g = make_graph()
    res = solution_set(
        g.query("SELECT ?a ?b WHERE { VALUES (?a ?b) { (1 UNDEF) (UNDEF 2) } }")
    )
    assert res == Counter({sol(("a", ONE)): 1, sol(("b", TWO)): 1})


def test_undef_does_not_block_optional():
    """
    UNDEF leaves the variable unbound, so the OPTIONAL may still bind it.
    """
    g = make_graph()
    res = solution_set(g.query("""
            SELECT ?x ?y WHERE {
                VALUES ?x { UNDEF <http://example.org/s> }
                OPTIONAL { ?x <http://example.org/p> ?y }
            }
            """))
    assert res == Counter({sol(("x", S), ("y", ONE)): 2})


def test_optional_without_match_preserves_values_row():
    """
    A VALUES row with no compatible OPTIONAL match is preserved with the
    optional variables unbound.
    """
    g = make_graph()
    res = solution_set(g.query("""
            SELECT ?x ?y WHERE {
                VALUES ?x { <http://example.org/other> <http://example.org/s> }
                OPTIONAL { ?x <http://example.org/p> ?y }
            }
            """))
    assert res == Counter(
        {
            sol(("x", URIRef(EX + "other"))): 1,
            sol(("x", S), ("y", ONE)): 1,
        }
    )


# ---------------------------------------------------------------------
# variable scoping
# ---------------------------------------------------------------------


def test_values_var_same_name_as_outer_var():
    """A VALUES variable shared with the outer pattern joins normally."""
    g = make_graph()
    res = solution_set(g.query("""
            SELECT ?x ?y WHERE {
                ?x <http://example.org/p> ?y .
                VALUES ?x { <http://example.org/s> <http://example.org/other> }
            }
            """))
    assert res == Counter({sol(("x", S), ("y", ONE)): 1})


def test_values_var_same_name_as_initbindings():
    g = make_graph()
    res = solution_set(
        g.query(
            "SELECT ?x WHERE { VALUES ?x { 1 2 } }",
            initBindings={"x": ONE},
        )
    )
    assert res == Counter({sol(("x", ONE)): 1})


def test_top_level_values_clause():
    g = make_graph()
    res = solution_set(g.query("""
            SELECT ?x ?y WHERE { ?x <http://example.org/p> ?y }
            VALUES ?x { <http://example.org/s> <http://example.org/other> }
            """))
    assert res == Counter({sol(("x", S), ("y", ONE)): 1})


def test_subquery_scopes_its_values_variable():
    """
    A variable projected out of a sub-query joins with the outer
    variable of the same name.
    """
    g = make_graph()
    res = solution_set(g.query("""
            SELECT ?x WHERE {
                { SELECT ?x WHERE { VALUES ?x { 1 2 } } }
                { SELECT ?x WHERE { VALUES ?x { 2 3 } } }
            }
            """))
    assert res == Counter({sol(("x", TWO)): 1})


def test_empty_values_and_empty_rows():
    g = make_graph()
    # VALUES () { () } is a single solution with no bindings
    assert solution_set(g.query("SELECT ?x WHERE { VALUES () { () } }")) == Counter(
        {sol(): 1}
    )
    # VALUES () {} and VALUES ?x {} have no rows, hence no solutions
    assert solution_set(g.query("SELECT ?x WHERE { VALUES () { } }")) == Counter()
    assert solution_set(g.query("SELECT ?x WHERE { VALUES ?x { } }")) == Counter()


# ---------------------------------------------------------------------
# filters see spec-compliant bindings
# ---------------------------------------------------------------------


def test_bound_and_not_bound_over_undef():
    g = make_graph()
    res = solution_set(
        g.query("SELECT ?x WHERE { VALUES ?x { UNDEF 5 } FILTER(!BOUND(?x)) }")
    )
    assert res == Counter({sol(): 1})
    res = solution_set(
        g.query("SELECT ?x WHERE { VALUES ?x { UNDEF 5 } FILTER(BOUND(?x)) }")
    )
    assert res == Counter({sol(("x", Literal(5))): 1})


def test_unbound_variable_in_expression_is_an_error():
    """
    An unbound variable makes a filter expression an error (hence false);
    it must not leak into a Python-level None comparison, where
    None = None would be true.
    """
    g = make_graph()
    assert (
        solution_set(g.query("SELECT ?x WHERE { VALUES ?x { UNDEF } FILTER(?x = ?x) }"))
        == Counter()
    )
    assert (
        solution_set(g.query("SELECT ?x WHERE { VALUES ?x { UNDEF } FILTER(?x > 1) }"))
        == Counter()
    )


# ---------------------------------------------------------------------
# validation happens before evaluation
# ---------------------------------------------------------------------


def test_column_count_mismatch_raises_at_prepare_time():
    from rdflib.plugins.sparql import prepareQuery

    with pytest.raises(SPARQLError):
        prepareQuery("SELECT * WHERE { VALUES (?a ?b) { (1 2) (3) } }")


def test_duplicate_variable_raises_at_prepare_time():
    from rdflib.plugins.sparql import prepareQuery

    with pytest.raises(SPARQLError):
        prepareQuery("SELECT * WHERE { VALUES (?a ?a) { (1 2) } }")


def test_illegal_term_raises_at_translation():
    # the parser rejects blank nodes and variables in a data block;
    # the translation validates terms as well, before any evaluation
    v = CompValue("InlineData", var=[Variable("x")], value=[BNode("b1")])
    with pytest.raises(SPARQLError):
        translateValues(v)
    v = CompValue("InlineData", var=[Variable("x")], value=[Variable("y")])
    with pytest.raises(SPARQLError):
        translateValues(v)


# ---------------------------------------------------------------------
# intermediate results and laziness
# ---------------------------------------------------------------------


def test_intermediate_multiset_of_values():
    """
    The intermediate multiset produced by a VALUES clause matches the
    hand-computed one, in variable sets and multiplicities.
    """
    g = make_graph()
    res = eval_where(g, "SELECT ?x WHERE { VALUES ?x { UNDEF 1 1 } }")
    assert res == Counter(
        {
            frozenset(): 1,
            frozenset({(Variable("x"), ONE)}): 2,
        }
    )


def test_intermediate_multiset_of_values_optional_join():
    g = make_graph()
    res = eval_where(
        g,
        """
        SELECT ?x ?y WHERE {
            VALUES ?x { UNDEF <http://example.org/s> <http://example.org/s> }
            OPTIONAL { ?x <http://example.org/p> ?y }
        }
        """,
    )
    joined = frozenset({(Variable("x"), S), (Variable("y"), ONE)})
    assert res == Counter({joined: 3})


def test_limit_does_not_materialize_all_values(monkeypatch):
    """
    LIMIT only pulls as many solutions as it needs; the VALUES clause is
    not fully materialized first.
    """
    from rdflib.plugins.sparql import evaluate

    yielded = 0
    original = evaluate.evalValues

    def counting_eval_values(ctx, part):
        nonlocal yielded
        for row in original(ctx, part):
            yielded += 1
            yield row

    monkeypatch.setattr(evaluate, "evalValues", counting_eval_values)

    g = make_graph()
    values = " ".join(str(i) for i in range(1000))
    rows = list(g.query("SELECT ?x WHERE { VALUES ?x { %s } } LIMIT 3" % values))
    assert len(rows) == 3
    assert yielded == 3


def test_limit_with_join_does_not_materialize_all_values(monkeypatch):
    from rdflib.plugins.sparql import evaluate

    yielded = 0
    original = evaluate.evalValues

    def counting_eval_values(ctx, part):
        nonlocal yielded
        for row in original(ctx, part):
            yielded += 1
            yield row

    monkeypatch.setattr(evaluate, "evalValues", counting_eval_values)

    g = make_graph()
    values = " ".join("<http://example.org/s>" for _ in range(1000))
    rows = list(g.query("""
            SELECT ?x ?y WHERE {
                VALUES ?x { %s }
                ?x <http://example.org/p> ?y .
            } LIMIT 1
            """ % values))
    assert len(rows) == 1
    assert yielded == 1
