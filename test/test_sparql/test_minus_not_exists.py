"""
Tests for the SPARQL 1.1 negation semantics of MINUS and
FILTER (NOT) EXISTS.

MINUS and FILTER NOT EXISTS are related but different:

* FILTER NOT EXISTS re-evaluates its pattern for every outer
  solution with the outer bindings substituted in (correlated);
  variables that are unbound in the outer solution stay unbound.
* MINUS only removes an outer solution if there is an inner solution
  that shares at least one variable with it and binds all shared
  variables to the same values (compatible). With no shared variables
  or only incompatible bindings, nothing is removed.

See https://www.w3.org/TR/sparql11-query/#negation
"""

import collections

from rdflib import Dataset, Graph, Literal, URIRef
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql.evaluate import (
    evalExists,
    evalMinus,
    evalNotExists,
    evalPart,
)
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.plugins.sparql.sparql import QueryContext
from rdflib.term import Variable

EX = "http://example.org/"
NAME = URIRef(EX + "name")
TAG = URIRef(EX + "tag")


def _rows(graph, query):
    """query results as a sorted list of stringified row tuples"""
    return sorted(tuple(str(v) for v in row) for row in graph.query(query))


def _graph(data):
    g = Graph()
    g.parse(data=data, format="turtle")
    return g


# -------------------------------------------------------------------
# group 1: only names - the inner pattern matches nothing at all
# -------------------------------------------------------------------

NAMES_ONLY = """
@prefix : <http://example.org/> .
:a :name "alice" .
:b :name "bob" .
"""


def test_names_only_minus_keeps_everything():
    g = _graph(NAMES_ONLY)
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      MINUS { ?s <http://example.org/tag> ?t }
    }
    """
    assert _rows(g, q) == [("alice",), ("bob",)]


def test_names_only_not_exists_keeps_everything():
    g = _graph(NAMES_ONLY)
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      FILTER NOT EXISTS { ?s <http://example.org/tag> ?t }
    }
    """
    assert _rows(g, q) == [("alice",), ("bob",)]


# -------------------------------------------------------------------
# group 2: shared variables - both forms remove the matching rows
# -------------------------------------------------------------------

SHARED = """
@prefix : <http://example.org/> .
:a :name "alice" ; :tag "x" .
:b :name "bob" ; :tag "y" .
"""


def test_shared_vars_minus():
    g = _graph(SHARED)
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      MINUS { ?s <http://example.org/tag> "x" }
    }
    """
    assert _rows(g, q) == [("bob",)]


def test_shared_vars_not_exists():
    g = _graph(SHARED)
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      FILTER NOT EXISTS { ?s <http://example.org/tag> "x" }
    }
    """
    assert _rows(g, q) == [("bob",)]


# -------------------------------------------------------------------
# group 3: no shared variables - MINUS removes nothing,
# NOT EXISTS removes everything (the pattern matches on its own)
# -------------------------------------------------------------------

DISJOINT = """
@prefix : <http://example.org/> .
:a :name "alice" .
:b :name "bob" .
:c :tag "x" .
"""


def test_no_shared_vars_minus_removes_nothing():
    g = _graph(DISJOINT)
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      MINUS { ?t <http://example.org/tag> ?v }
    }
    """
    assert _rows(g, q) == [("alice",), ("bob",)]


def test_no_shared_vars_not_exists_removes_all():
    g = _graph(DISJOINT)
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      FILTER NOT EXISTS { ?t <http://example.org/tag> ?v }
    }
    """
    assert _rows(g, q) == []


# -------------------------------------------------------------------
# group 4: conflicting bindings - shared variable, but the values
# differ, so MINUS must not remove the row
# -------------------------------------------------------------------

CONFLICTING = """
@prefix : <http://example.org/> .
:a :name "alice" .
:b :name "bob" ; :tag "x" .
"""


def test_conflicting_bindings_minus_keeps_row():
    g = _graph(CONFLICTING)
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      MINUS { ?s <http://example.org/tag> "x" }
    }
    """
    # :a is kept: the only MINUS solution binds ?s to :b, which is
    # incompatible with the outer binding ?s = :a
    assert _rows(g, q) == [("alice",)]


def test_conflicting_bindings_not_exists_keeps_row():
    g = _graph(CONFLICTING)
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      FILTER NOT EXISTS { ?s <http://example.org/tag> "x" }
    }
    """
    assert _rows(g, q) == [("alice",)]


# -------------------------------------------------------------------
# correlated subqueries: same-named variables inside a subselect
# -------------------------------------------------------------------


def test_not_exists_correlated_subselect():
    g = _graph(SHARED)
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      FILTER NOT EXISTS { SELECT ?s WHERE { ?s <http://example.org/tag> "x" } }
    }
    """
    assert _rows(g, q) == [("bob",)]


def test_minus_subselect():
    g = _graph(SHARED)
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      MINUS { SELECT ?s WHERE { ?s <http://example.org/tag> "x" } }
    }
    """
    assert _rows(g, q) == [("bob",)]


# -------------------------------------------------------------------
# unbound values from an inner OPTIONAL must not be treated as bound
# -------------------------------------------------------------------


def test_not_exists_optional_unbound_var():
    g = _graph(
        """
        @prefix : <http://example.org/> .
        :a :name "alice" .
        :c :knows :d .
        """
    )
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      OPTIONAL { ?s <http://example.org/tag> ?t }
      FILTER NOT EXISTS { ?x <http://example.org/knows> ?t }
    }
    """
    # ?t is unbound in the outer solution; inside NOT EXISTS it is a
    # fresh variable, so the pattern matches and the row is removed
    assert _rows(g, q) == []


def test_minus_optional_unbound_var():
    g = _graph(
        """
        @prefix : <http://example.org/> .
        :a :name "alice" .
        :c :knows :d .
        """
    )
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      OPTIONAL { ?s <http://example.org/tag> ?t }
      MINUS { ?x <http://example.org/knows> ?t }
    }
    """
    # ?t is unbound in the outer solution, so it is not part of the
    # outer solution's domain: no shared variables, row is kept
    assert _rows(g, q) == [("alice",)]


# -------------------------------------------------------------------
# GRAPH scoping: NOT EXISTS inside a GRAPH clause sees that graph
# -------------------------------------------------------------------


def test_not_exists_graph_scope():
    d = Dataset()
    d.parse(
        data="""
        @prefix : <http://example.org/> .
        GRAPH :g1 { :a :name "alice" . :a :tag "x" }
        GRAPH :g2 { :b :name "bob" . }
        """,
        format="trig",
    )
    q = """
    SELECT ?n WHERE {
      GRAPH ?g {
        ?s <http://example.org/name> ?n .
        FILTER NOT EXISTS { ?s <http://example.org/tag> ?t }
      }
    }
    """
    assert _rows(d, q) == [("bob",)]


# -------------------------------------------------------------------
# nested NOT EXISTS
# -------------------------------------------------------------------


def test_nested_not_exists():
    g = _graph(SHARED)
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      FILTER NOT EXISTS {
        ?s <http://example.org/tag> ?t .
        FILTER NOT EXISTS { ?s <http://example.org/tag> "x" }
      }
    }
    """
    # outer NOT EXISTS: subjects with a tag but no "x" tag -> only :b
    assert _rows(g, q) == [("alice",)]


# -------------------------------------------------------------------
# duplicates are preserved
# -------------------------------------------------------------------


def test_minus_preserves_duplicates():
    g = _graph(
        """
        @prefix : <http://example.org/> .
        :a :name "n" .
        :b :name "n" .
        """
    )
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      MINUS { ?z <http://example.org/absent> ?w }
    }
    """
    assert _rows(g, q) == [("n",), ("n",)]


# -------------------------------------------------------------------
# filter errors are handled per spec (error -> false)
# -------------------------------------------------------------------


def test_not_exists_inner_filter_error():
    g = _graph(
        """
        @prefix : <http://example.org/> .
        :a :name "alice" .
        :c :knows :d .
        """
    )
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      FILTER NOT EXISTS { ?x <http://example.org/knows> ?y . FILTER(?y + 1 > 2) }
    }
    """
    # ?y is a URI, ?y + 1 is a type error -> inner filter is false ->
    # the pattern has no solution -> NOT EXISTS is true
    assert _rows(g, q) == [("alice",)]


# -------------------------------------------------------------------
# laziness: the right-hand side of MINUS is not evaluated up front
# -------------------------------------------------------------------


class _CountingGraph(Graph):
    """A graph counting the triples yielded per predicate"""

    def __init__(self):
        super().__init__()
        self.counts = collections.Counter()

    def triples(self, triple_pattern):
        pred = triple_pattern[1]
        for t in super().triples(triple_pattern):
            self.counts[pred] += 1
            yield t


def _counting_graph():
    g = _CountingGraph()
    data = """
    @prefix : <http://example.org/> .
    :s0 :name "n0" .
    :s1 :name "n1" .
    :s2 :name "n2" .
    :s0 :tag "t0" .
    :s1 :tag "t1" .
    :s2 :tag "t2" .
    """
    filler = [':x%d <http://example.org/tag> "u%d" .' % (i, i) for i in range(47)]
    g.parse(data=data + chr(10).join(filler), format="turtle")
    return g


def test_minus_does_not_evaluate_rhs_when_lhs_is_empty():
    g = _counting_graph()
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/absent> ?n .
      MINUS { ?s <http://example.org/tag> ?t }
    }
    """
    assert _rows(g, q) == []
    # the left side is empty, so the right side must never run
    assert g.counts[TAG] == 0


def test_minus_limit_truncation_does_not_exhaust_rhs():
    g = _counting_graph()
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      MINUS { ?s <http://example.org/tag> ?t }
    }
    LIMIT 1
    """
    list(g.query(q))
    # every left row is removed by its own early :tag triple, so only a
    # few of the 50 tag triples may be pulled; materializing the right
    # side into a set would have pulled all 50
    assert g.counts[TAG] <= 3


def test_not_exists_limit_truncation_stops_at_first_match():
    g = _counting_graph()
    q = """
    SELECT ?n WHERE {
      ?s <http://example.org/name> ?n .
      FILTER NOT EXISTS { ?s <http://example.org/tag> ?t }
    }
    LIMIT 1
    """
    list(g.query(q))
    # each NOT EXISTS evaluation stops at the first matching triple;
    # nothing ever scans all 50 tag triples
    assert g.counts[TAG] < 50


# -------------------------------------------------------------------
# evalExists / evalNotExists / evalMinus are directly usable
# -------------------------------------------------------------------


def _find_part(part, name):
    """recursively find the first algebra part with the given name"""
    if isinstance(part, CompValue):
        if part.name == name:
            return part
        for value in part.values():
            found = _find_part(value, name)
            if found is not None:
                return found
    return None


def test_eval_exists_functions_per_outer_solution():
    g = _graph(SHARED)
    q = parseQuery(
        "SELECT ?n WHERE { ?s <http://example.org/name> ?n . "
        'FILTER NOT EXISTS { ?s <http://example.org/tag> "x" } }'
    )
    algebra = translateQuery(q).algebra
    filter_part = _find_part(algebra, "Filter")
    assert filter_part is not None
    expr = filter_part.expr
    assert expr.name == "Builtin_NOTEXISTS"

    ctx = QueryContext(g, initBindings={})
    results = []
    for solution in evalPart(ctx, filter_part.p):
        thawed = solution.ctx.thaw(solution)
        results.append(evalNotExists(thawed, expr.graph))
        assert evalExists(thawed, expr.graph) != results[-1]
    # :a has tag "x" -> False, :b has tag "y" -> True
    assert sorted(results) == [False, True]


def test_eval_minus_is_lazy_generator():
    g = _CountingGraph()
    g.parse(
        data="""
        @prefix : <http://example.org/> .
        :s0 :name "n0" .
        :s1 :name "n1" ; :tag "t1" .
        """,
        format="turtle",
    )
    q = parseQuery(
        "SELECT ?n WHERE { ?s <http://example.org/name> ?n . "
        "MINUS { ?s <http://example.org/tag> ?t } }"
    )
    algebra = translateQuery(q).algebra
    minus = _find_part(algebra, "Minus")
    assert minus is not None
    ctx = QueryContext(g, initBindings={})
    gen = evalMinus(ctx, minus)
    # nothing is evaluated before the generator is consumed
    assert g.counts[TAG] == 0
    assert g.counts[NAME] == 0
    first = next(gen)
    assert isinstance(first[Variable("n")], Literal)
    rest = list(gen)
    # :s0 has no tag and survives, :s1 is removed by its own tag
    assert [str(first[Variable("n")])] + [str(r[Variable("n")]) for r in rest] == [
        "n0"
    ]
